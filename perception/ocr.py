"""Text extraction.

Runs independently of the detector: OCR finds *all* text, including labels that
sit outside any interactable box (headings, prices, status lines). Those become
static `text` elements the planner can read for context.

Backends are pluggable -- PaddleOCR on the dev machine today, ML Kit on-device
later -- so only this module changes when the backend does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from .schema import BoundingBox

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TextRun:
    text: str
    box: BoundingBox
    confidence: float


class OCRBackend(Protocol):
    def read(self, image: np.ndarray) -> list[TextRun]: ...


def _quad_to_box(quad, width: int, height: int) -> BoundingBox | None:
    """Axis-aligned bounds of a (possibly rotated) OCR quadrilateral."""
    pts = np.asarray(quad, dtype=float).reshape(-1, 2)
    try:
        return BoundingBox(
            int(pts[:, 0].min()), int(pts[:, 1].min()),
            int(np.ceil(pts[:, 0].max())), int(np.ceil(pts[:, 1].max())),
        ).clamp(width, height)
    except ValueError:
        return None


class NullOCR:
    """No-op backend.

    Lets the pipeline run detector-only when no OCR is installed. Everything
    still works; elements just arrive unlabelled.
    """

    def read(self, image: np.ndarray) -> list[TextRun]:
        return []


class RapidOCRBackend:
    """RapidOCR (ONNX runtime). Default backend.

    Chosen over PaddleOCR after measuring box accuracy on a 1080x2400 capture:
    RapidOCR was off by (-3, -14) px on a controlled single-word test where
    PaddleOCR 3.7 was off by (-67, -59) and worsened with image size. Errors of
    that magnitude push a label outside its own button, which breaks fusion
    silently -- the text is read correctly but attached to nothing. See
    decisions.md D12.

    Also has no paddlepaddle dependency, and ONNX is the likelier road to an
    on-device build.
    """

    def __init__(self, min_confidence: float = 0.5) -> None:
        self.min_confidence = min_confidence
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            from rapidocr import RapidOCR

            log.info("initialising RapidOCR")
            self._engine = RapidOCR()
        return self._engine

    def read(self, image: np.ndarray) -> list[TextRun]:
        result = self.engine(image)
        if result is None or not getattr(result, "txts", None):
            return []

        h, w = image.shape[:2]
        runs: list[TextRun] = []
        for text, quad, score in zip(result.txts, result.boxes, result.scores):
            text = (text or "").strip()
            conf = float(score)
            if not text or conf < self.min_confidence:
                continue
            box = _quad_to_box(quad, w, h)
            if box is not None:
                runs.append(TextRun(text=text, box=box, confidence=conf))

        log.debug("ocr produced %d text runs", len(runs))
        return runs


class PaddleOCRBackend:
    """PaddleOCR, supporting both the 2.x and 3.x APIs.

    The two majors differ enough to matter: 3.x dropped `use_angle_cls` and
    `show_log`, renamed the entry point to `predict()`, and returns a dict of
    parallel arrays instead of a list of per-line tuples. Which one is installed
    is detected at load time rather than pinned, because 3.x is what pip
    resolves to today and 2.x is what most tutorials still show.
    """

    def __init__(self, lang: str = "en", min_confidence: float = 0.5) -> None:
        self.lang = lang
        self.min_confidence = min_confidence
        self._engine = None
        self._is_v3 = False

    @property
    def engine(self):
        if self._engine is None:
            self._engine = self._build()
        return self._engine

    def _build(self):
        from paddleocr import PaddleOCR

        log.info("initialising PaddleOCR (lang=%s)", self.lang)
        try:
            # 3.x. oneDNN is disabled deliberately: paddlepaddle 3.3.1 on
            # Windows raises "ConvertPirAttribute2RuntimeAttribute not support"
            # from the oneDNN executor on ordinary screenshots. The CPU path is
            # slower but works.
            engine = PaddleOCR(
                lang=self.lang,
                use_textline_orientation=False,
                enable_mkldnn=False,
            )
            self._is_v3 = True
        except TypeError:
            # 2.x
            engine = PaddleOCR(use_angle_cls=True, lang=self.lang, show_log=False)
            self._is_v3 = False
        log.debug("paddleocr api: %s", "3.x" if self._is_v3 else "2.x")
        return engine

    def read(self, image: np.ndarray) -> list[TextRun]:
        engine = self.engine  # resolves self._is_v3
        raw = engine.predict(image) if self._is_v3 else engine.ocr(image, cls=True)
        if not raw:
            return []
        parsed = self._parse_v3(raw) if self._is_v3 else self._parse_v2(raw)

        h, w = image.shape[:2]
        runs: list[TextRun] = []
        for text, quad, conf in parsed:
            text = text.strip()
            if not text or conf < self.min_confidence:
                continue
            box = _quad_to_box(quad, w, h)
            if box is not None:
                runs.append(TextRun(text=text, box=box, confidence=conf))

        log.debug("ocr produced %d text runs", len(runs))
        return runs

    @staticmethod
    def _parse_v3(raw) -> list[tuple[str, object, float]]:
        out = []
        for page in raw:
            texts = page.get("rec_texts", [])
            scores = page.get("rec_scores", [])
            polys = page.get("rec_polys")
            if polys is None:
                polys = page.get("dt_polys", [])
            for text, poly, score in zip(texts, polys, scores):
                out.append((text, poly, float(score)))
        return out

    @staticmethod
    def _parse_v2(raw) -> list[tuple[str, object, float]]:
        if raw[0] is None:
            return []
        return [(line[1][0], line[0], float(line[1][1])) for line in raw[0]]


def default_backend() -> OCRBackend:
    """First available of RapidOCR, PaddleOCR, NullOCR.

    RapidOCR first on measured box accuracy (see D12) -- PaddleOCR remains
    supported because it is what most Android/OCR tutorials assume.
    """
    try:
        import rapidocr  # noqa: F401

        return RapidOCRBackend()
    except ImportError:
        pass

    try:
        import paddleocr  # noqa: F401

        log.warning("rapidocr unavailable - falling back to PaddleOCR")
        return PaddleOCRBackend()
    except ImportError:
        pass

    log.warning("no OCR backend installed - continuing without text extraction")
    return NullOCR()
