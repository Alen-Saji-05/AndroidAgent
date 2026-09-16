"""Screen perception: mobile UI screenshot -> structured JSON of UI elements.

    from perception import ScreenParser
    state = ScreenParser().parse_file("screen.png")
    print(state.to_json())
"""

from .schema import BoundingBox, ElementState, ElementType, ScreenState, UIElement
from .detector import Detection, DetectorConfig, DetectorError, IconDetector
from .ocr import NullOCR, OCRBackend, PaddleOCRBackend, RapidOCRBackend, TextRun
from .pipeline import PerceptionError, ScreenParser, load_image

__all__ = [
    "BoundingBox", "ElementState", "ElementType", "ScreenState", "UIElement",
    "Detection", "DetectorConfig", "DetectorError", "IconDetector",
    "NullOCR", "OCRBackend", "PaddleOCRBackend", "RapidOCRBackend", "TextRun",
    "PerceptionError", "ScreenParser", "load_image",
]
