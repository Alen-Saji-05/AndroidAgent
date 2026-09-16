"""Integration tests against a real screenshot and the real detector.

Skipped when weights are absent so the model-free suite still runs anywhere.
Assertions are on coordinate *sanity* and on the specific regressions found
during bring-up -- not on exact boxes, which move with every threshold change.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from perception.detector import DEFAULT_WEIGHTS
from perception.ocr import NullOCR
from perception.pipeline import ScreenParser, load_image
from perception.schema import ElementType

FIXTURE = Path(__file__).parent / "fixtures" / "sample_cab.png"

pytestmark = [
    pytest.mark.skipif(not FIXTURE.exists(), reason="fixture missing"),
    pytest.mark.skipif(
        not DEFAULT_WEIGHTS.exists(),
        reason="detector weights missing -- run python -m perception.download_weights",
    ),
]


@pytest.fixture(scope="module")
def image():
    return load_image(FIXTURE)


@pytest.fixture(scope="module")
def state_no_ocr(image):
    return ScreenParser(ocr=NullOCR()).parse(image)


@pytest.fixture(scope="module")
def state(image):
    return ScreenParser().parse(image)


class TestDetector:
    def test_finds_a_plausible_number_of_elements(self, state_no_ocr):
        # 8 on this capture; band guards against a silent collapse to 0 or an
        # explosion from a threshold typo, without pinning the exact count.
        assert 4 <= len(state_no_ocr.elements) <= 40

    def test_all_boxes_lie_inside_the_image(self, state_no_ocr):
        for el in state_no_ocr.elements:
            b = el.bounds
            assert 0 <= b.x1 < b.x2 <= state_no_ocr.width
            assert 0 <= b.y1 < b.y2 <= state_no_ocr.height

    def test_every_tap_centre_is_inside_its_own_box(self, state_no_ocr):
        # The executor taps these coordinates; a centre outside its box is a
        # tap on something else.
        for el in state_no_ocr.elements:
            cx, cy = el.center
            b = el.bounds
            assert b.x1 <= cx <= b.x2 and b.y1 <= cy <= b.y2

    def test_detects_the_primary_action_button(self, state_no_ocr):
        # Confirm button drawn at y 2050-2180.
        assert any(2000 < el.center[1] < 2230 for el in state_no_ocr.elements)


class TestOCRFusion:
    def test_confirm_label_fuses_into_its_button(self, state):
        """Regression: PaddleOCR 3.7 mislocated this label by ~100px, so it
        landed outside the button and fused with nothing -- leaving a blank
        'icon' plus orphan text. See decisions.md D12."""
        hits = state.find_by_text("Confirm Booking")
        assert hits, "label not found at all"
        el = hits[0]
        assert el.source == "fused", f"label did not attach to a box (source={el.source})"
        assert el.interactable
        cx, cy = el.center
        assert 2000 < cy < 2230, f"tap centre {cy} is not on the button"

    def test_text_is_attached_not_orphaned(self, state):
        """Most text on this screen sits inside a detected widget. A spike in
        'ocr'-sourced elements means coordinates drifted again."""
        orphan = [e for e in state.elements if e.source == "ocr"]
        assert len(orphan) <= 4, [e.text for e in orphan]

    def test_reads_expected_strings(self, state):
        found = " | ".join(e.text for e in state.elements)
        for expected in ("Confirm Booking", "Enter destination", "Total"):
            assert expected in found, f"{expected!r} missing from {found!r}"


class TestClassification:
    def test_full_width_cards_are_list_items(self, state):
        """Regression: 180px cards (0.075 of a 2400px screen) were typed as
        buttons until LIST_ROW_MIN_HEIGHT dropped from 0.09 to 0.06."""
        cards = [e for e in state.elements if e.text.startswith(("Mini", "Sedan", "SUV"))]
        assert len(cards) == 3, [e.text for e in state.elements]
        for card in cards:
            assert card.type is ElementType.LIST_ITEM, f"{card.text!r} -> {card.type}"

    def test_static_text_is_not_interactable(self, state):
        for el in state.elements:
            if el.type is ElementType.TEXT:
                assert not el.interactable

    def test_no_element_left_unknown(self, state):
        unknown = [e.text or f"<blank {e.bounds.to_dict()}>"
                   for e in state.elements if e.type is ElementType.UNKNOWN]
        assert not unknown, f"unclassified: {unknown}"


def test_json_serialises(state):
    import json
    payload = json.loads(state.to_json())
    assert payload["element_count"] == len(payload["elements"])
    assert payload["metadata"]["ocr_backend"] in {"RapidOCRBackend", "PaddleOCRBackend", "NullOCR"}
