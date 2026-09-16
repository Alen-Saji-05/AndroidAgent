"""OmniParser icon detector: YOLOv8 localisation of interactable regions.

Weights: microsoft/OmniParser-v2.0 (MIT), ~3.0M params / ~12MB. The model is
class-agnostic -- it answers "is this region interactable?", not "what kind of
widget is it?". Typing happens in classify.py.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from .schema import BoundingBox

log = logging.getLogger(__name__)

HF_REPO = "microsoft/OmniParser-v2.0"
HF_WEIGHT_PATH = "icon_detect/model.pt"
DEFAULT_WEIGHTS = Path("weights/icon_detect.pt")


class DetectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Detection:
    box: BoundingBox
    confidence: float


@dataclass
class DetectorConfig:
    """Thresholds.

    Defaults are tuned down from ultralytics' stock 0.25/0.7 because mobile UIs
    are far denser than the desktop captures OmniParser ships tuned for: a 0.25
    floor silently drops small icons, and a loose NMS IoU merges adjacent list
    rows into one box. Re-tune against real fixtures before trusting these.
    """

    conf_threshold: float = 0.05
    iou_threshold: float = 0.10
    max_detections: int = 300
    # Long edge the image is resized to for inference. 1280 keeps small mobile
    # icons above the detector's minimum feature size; 640 loses them.
    imgsz: int = 1280
    device: str = "cpu"


class IconDetector:
    """Thin wrapper over the ultralytics runtime.

    Loads lazily so importing the package (and running schema/fusion tests)
    does not require torch.
    """

    def __init__(
        self,
        weights: str | Path = DEFAULT_WEIGHTS,
        config: Optional[DetectorConfig] = None,
    ) -> None:
        self.weights = Path(weights)
        self.config = config or DetectorConfig()
        self._model = None

    @property
    def model(self):
        if self._model is None:
            self._model = self._load()
        return self._model

    def _load(self):
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise DetectorError(
                "ultralytics is not installed. `pip install -r requirements.txt`"
            ) from e

        if not self.weights.exists():
            raise DetectorError(
                f"detector weights not found at {self.weights}.\n"
                f"Fetch them with:  python -m perception.download_weights"
            )

        log.info("loading icon detector from %s", self.weights)
        # ultralytics chatters on every call otherwise.
        os.environ.setdefault("YOLO_VERBOSE", "False")
        return YOLO(str(self.weights))

    def detect(self, image: np.ndarray) -> list[Detection]:
        """Detect interactable regions in a BGR image.

        Returns boxes in absolute pixel coordinates of the *input* image;
        ultralytics handles the letterbox/rescale round-trip internally.
        """
        h, w = image.shape[:2]
        cfg = self.config

        results = self.model.predict(
            source=image,
            conf=cfg.conf_threshold,
            iou=cfg.iou_threshold,
            max_det=cfg.max_detections,
            imgsz=cfg.imgsz,
            device=cfg.device,
            verbose=False,
        )
        if not results:
            return []

        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return []

        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()

        detections: list[Detection] = []
        for (x1, y1, x2, y2), conf in zip(xyxy, confs):
            try:
                box = BoundingBox(
                    int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))
                ).clamp(w, h)
            except ValueError:
                continue  # degenerate after rounding
            if box.area < 16:  # sub-4x4px: noise, never a tap target
                continue
            detections.append(Detection(box=box, confidence=float(conf)))

        log.debug("detector produced %d boxes", len(detections))
        return detections


def download_weights(dest: Path = DEFAULT_WEIGHTS) -> Path:
    """Pull the icon detector from the Hugging Face hub."""
    from huggingface_hub import hf_hub_download

    dest.parent.mkdir(parents=True, exist_ok=True)
    cached = hf_hub_download(repo_id=HF_REPO, filename=HF_WEIGHT_PATH)
    dest.write_bytes(Path(cached).read_bytes())
    log.info("weights written to %s", dest)
    return dest
