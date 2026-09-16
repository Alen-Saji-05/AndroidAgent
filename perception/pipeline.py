"""ScreenParser: screenshot in, ScreenState out."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .classify import classify_all
from .detector import DetectorConfig, IconDetector
from .fusion import fuse
from .ocr import OCRBackend, default_backend
from .schema import ScreenState

log = logging.getLogger(__name__)


class PerceptionError(RuntimeError):
    pass


def load_image(path: str | Path) -> np.ndarray:
    """Read an image as BGR."""
    path = Path(path)
    if not path.exists():
        raise PerceptionError(f"image not found: {path}")
    # imread chokes on non-ASCII paths on Windows; go through numpy.
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise PerceptionError(f"could not decode image: {path}")
    return image


class ScreenParser:
    """Detector + OCR + fusion + classification."""

    def __init__(
        self,
        detector: Optional[IconDetector] = None,
        ocr: Optional[OCRBackend] = None,
        *,
        include_static_text: bool = True,
        detector_config: Optional[DetectorConfig] = None,
    ) -> None:
        self.detector = detector or IconDetector(config=detector_config)
        self.ocr = ocr if ocr is not None else default_backend()
        self.include_static_text = include_static_text

    def parse(self, image: np.ndarray) -> ScreenState:
        if image is None or image.size == 0:
            raise PerceptionError("empty image")
        h, w = image.shape[:2]

        t0 = time.perf_counter()
        detections = self.detector.detect(image)
        t_detect = time.perf_counter() - t0

        t0 = time.perf_counter()
        runs = self.ocr.read(image)
        t_ocr = time.perf_counter() - t0

        elements = fuse(detections, runs, include_static_text=self.include_static_text)

        state = ScreenState(
            width=w,
            height=h,
            elements=elements,
            metadata={
                "raw_detections": len(detections),
                "text_runs": len(runs),
                "detect_ms": round(t_detect * 1000, 1),
                "ocr_ms": round(t_ocr * 1000, 1),
                "ocr_backend": type(self.ocr).__name__,
                "image_sha1": hashlib.sha1(image.tobytes()).hexdigest()[:12],
            },
        )
        classify_all(state)
        log.info(
            "parsed %dx%d -> %d elements (detect %.0fms, ocr %.0fms)",
            w, h, len(state.elements), t_detect * 1000, t_ocr * 1000,
        )
        return state

    def parse_file(self, path: str | Path) -> ScreenState:
        return self.parse(load_image(path))
