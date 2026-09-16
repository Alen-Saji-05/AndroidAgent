"""End-to-end wiring tests using a stub detector (no weights required)."""

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from perception import overlay
from perception.detector import Detection
from perception.ocr import NullOCR, TextRun
from perception.pipeline import PerceptionError, ScreenParser, load_image
from perception.schema import BoundingBox, ElementType

W, H = 1080, 2400


class StubDetector:
    def __init__(self, detections):
        self._d = detections

    def detect(self, image):
        return self._d


class StubOCR:
    def __init__(self, runs):
        self._r = runs

    def read(self, image):
        return self._r


@pytest.fixture
def screenshot():
    """Synthetic phone screen: dark bg, a button, a bottom bar."""
    img = np.full((H, W, 3), 24, np.uint8)
    cv2.rectangle(img, (48, 2050), (1032, 2180), (60, 170, 60), -1)
    cv2.rectangle(img, (0, 2300), (W, H), (40, 40, 40), -1)
    return img


def make_parser(detections=(), runs=()):
    return ScreenParser(detector=StubDetector(list(detections)), ocr=StubOCR(list(runs)))


def test_parse_produces_expected_state(screenshot):
    parser = make_parser(
        detections=[Detection(BoundingBox(48, 2050, 1032, 2180), 0.93)],
        runs=[TextRun("Confirm", BoundingBox(450, 2090, 630, 2140), 0.99)],
    )
    state = parser.parse(screenshot)

    assert (state.width, state.height) == (W, H)
    assert len(state.elements) == 1
    el = state.elements[0]
    assert el.type is ElementType.BUTTON
    assert el.text == "Confirm"
    assert el.center == (540, 2115)
    assert state.metadata["raw_detections"] == 1
    assert "detect_ms" in state.metadata


def test_empty_screen_yields_no_elements(screenshot):
    state = make_parser().parse(screenshot)
    assert state.elements == []
    assert state.to_json()  # still serialises


def test_null_ocr_still_produces_elements(screenshot):
    parser = ScreenParser(
        detector=StubDetector([Detection(BoundingBox(40, 100, 120, 180), 0.8)]),
        ocr=NullOCR(),
    )
    state = parser.parse(screenshot)
    assert len(state.elements) == 1
    assert state.elements[0].type is ElementType.ICON


def test_empty_image_rejected():
    with pytest.raises(PerceptionError):
        make_parser().parse(np.zeros((0, 0, 3), np.uint8))


def test_missing_file_rejected(tmp_path):
    with pytest.raises(PerceptionError):
        load_image(tmp_path / "nope.png")


def test_overlay_writes_a_readable_image(screenshot, tmp_path):
    state = make_parser(
        detections=[Detection(BoundingBox(48, 2050, 1032, 2180), 0.9)],
        runs=[TextRun("Pay", BoundingBox(500, 2090, 580, 2140), 0.9)],
    ).parse(screenshot)

    out = overlay.save(screenshot, state, tmp_path / "overlay.png")
    assert out.exists()
    written = load_image(out)
    assert written.shape == screenshot.shape
    assert not np.array_equal(written, screenshot)  # boxes actually drawn


def test_roundtrip_via_file(screenshot, tmp_path):
    path = tmp_path / "screen.png"
    cv2.imwrite(str(path), screenshot)
    state = make_parser([Detection(BoundingBox(48, 2050, 1032, 2180), 0.9)]).parse_file(path)
    assert state.width == W and len(state.elements) == 1
