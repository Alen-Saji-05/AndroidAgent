"""Unit tests for the model-free half of the pipeline.

Fusion, classification, geometry and serialisation are all deterministic, so
they are tested exhaustively here without torch or weights. Detector accuracy
needs real fixtures and is covered by test_fixtures.py instead.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from perception.classify import classify_all, classify_element
from perception.detector import Detection
from perception.fusion import deduplicate, fuse
from perception.ocr import TextRun
from perception.schema import BoundingBox, ElementType, ScreenState, UIElement

W, H = 1080, 2400


def det(x1, y1, x2, y2, conf=0.9):
    return Detection(box=BoundingBox(x1, y1, x2, y2), confidence=conf)


def run(text, x1, y1, x2, y2, conf=0.95):
    return TextRun(text=text, box=BoundingBox(x1, y1, x2, y2), confidence=conf)


class TestBoundingBox:
    def test_center_is_inside_box(self):
        b = BoundingBox(10, 20, 110, 220)
        cx, cy = b.center
        assert b.x1 <= cx <= b.x2 and b.y1 <= cy <= b.y2

    def test_degenerate_box_rejected(self):
        with pytest.raises(ValueError):
            BoundingBox(10, 10, 10, 50)

    def test_clamp_keeps_box_in_bounds(self):
        b = BoundingBox(-30, -10, W + 200, H + 500).clamp(W, H)
        assert (b.x1, b.y1) == (0, 0) and (b.x2, b.y2) == (W, H)

    def test_containment_beats_iou_for_nested_text(self):
        button = BoundingBox(48, 2050, 1032, 2180)
        label = BoundingBox(400, 2090, 680, 2140)
        assert button.contains_ratio(label) == 1.0
        assert button.iou(label) < 0.2  # why fusion uses containment


class TestDeduplicate:
    def test_keeps_highest_confidence_of_overlapping_pair(self):
        kept = deduplicate([det(0, 0, 100, 100, 0.6), det(2, 2, 102, 102, 0.95)])
        assert len(kept) == 1
        assert kept[0].confidence == 0.95

    def test_preserves_distinct_boxes(self):
        assert len(deduplicate([det(0, 0, 100, 100), det(500, 500, 600, 600)])) == 2


class TestFusion:
    def test_text_attaches_to_containing_box(self):
        els = fuse([det(48, 2050, 1032, 2180)], [run("Confirm Booking", 400, 2090, 680, 2140)])
        assert len(els) == 1
        assert els[0].text == "Confirm Booking"
        assert els[0].source == "fused"

    def test_box_without_text_is_icon(self):
        els = fuse([det(40, 100, 120, 180)], [])
        assert els[0].type is ElementType.ICON

    def test_orphan_text_becomes_static_text(self):
        els = fuse([], [run("Total: 450", 60, 900, 400, 950)])
        assert els[0].type is ElementType.TEXT
        assert els[0].interactable is False
        assert els[0].source == "ocr"

    def test_orphan_text_dropped_when_disabled(self):
        assert fuse([], [run("Total", 60, 900, 400, 950)], include_static_text=False) == []

    def test_text_claimed_by_smallest_containing_box(self):
        # A button nested inside a card: the label belongs to the button.
        card = det(20, 1000, 1060, 1400, 0.7)
        button = det(700, 1300, 1040, 1380, 0.9)
        els = fuse([card, button], [run("Book", 800, 1320, 900, 1360)])
        labelled = [e for e in els if e.text == "Book"]
        assert len(labelled) == 1
        assert labelled[0].bounds == button.box

    def test_multiple_runs_join_in_reading_order(self):
        els = fuse(
            [det(0, 0, 500, 200)],
            [run("World", 100, 120, 200, 160), run("Hello", 100, 20, 200, 60)],
        )
        assert els[0].text == "Hello World"

    def test_ids_are_unique_and_in_reading_order(self):
        els = fuse([det(0, 900, 100, 1000), det(0, 100, 100, 200), det(600, 100, 700, 200)], [])
        assert [e.id for e in els] == ["e0", "e1", "e2"]
        # row band first, then left-to-right inside it
        assert els[0].bounds.y1 == 100 and els[0].bounds.x1 == 0
        assert els[1].bounds.x1 == 600
        assert els[2].bounds.y1 == 900


class TestClassify:
    def test_bottom_strip_is_nav_item(self):
        e = UIElement(id="e0", type=ElementType.UNKNOWN, bounds=BoundingBox(40, 2300, 240, 2390))
        assert classify_element(e, W, H) is ElementType.NAV_ITEM

    def test_action_word_is_button(self):
        e = UIElement(id="e0", type=ElementType.UNKNOWN,
                      bounds=BoundingBox(300, 1200, 780, 1300), text="Confirm")
        assert classify_element(e, W, H) is ElementType.BUTTON

    def test_placeholder_text_is_field(self):
        e = UIElement(id="e0", type=ElementType.UNKNOWN,
                      bounds=BoundingBox(60, 800, 1020, 900), text="Enter your email")
        assert classify_element(e, W, H) is ElementType.TEXT_FIELD

    def test_small_square_blank_box_is_icon(self):
        e = UIElement(id="e0", type=ElementType.UNKNOWN, bounds=BoundingBox(900, 600, 980, 680))
        assert classify_element(e, W, H) is ElementType.ICON

    def test_ocr_sourced_element_stays_text(self):
        e = UIElement(id="e0", type=ElementType.UNKNOWN,
                      bounds=BoundingBox(60, 1000, 400, 1050), text="Confirm", source="ocr")
        assert classify_element(e, W, H) is ElementType.TEXT

    def test_classify_all_marks_text_non_interactable(self):
        state = ScreenState(W, H, fuse([det(48, 2050, 1032, 2180)],
                                       [run("Heading", 60, 300, 500, 360)]))
        classify_all(state)
        by_type = {e.type: e for e in state.elements}
        assert by_type[ElementType.TEXT].interactable is False
        assert all(e.interactable for e in state.elements if e.type is not ElementType.TEXT)


class TestSerialisation:
    def test_json_is_valid_and_coordinates_are_sane(self):
        state = ScreenState(W, H, fuse([det(48, 2050, 1032, 2180)],
                                       [run("Pay", 500, 2090, 580, 2140)]))
        classify_all(state)
        payload = json.loads(state.to_json())

        assert payload["image_size"] == {"width": W, "height": H}
        assert payload["element_count"] == len(payload["elements"])

        for el in payload["elements"]:
            b, c = el["bounds"], el["center"]
            assert 0 <= b["x1"] < b["x2"] <= W
            assert 0 <= b["y1"] < b["y2"] <= H
            assert b["x1"] <= c["x"] <= b["x2"]
            assert b["y1"] <= c["y"] <= b["y2"]
            assert el["type"] in {t.value for t in ElementType}

    def test_find_by_text_is_case_insensitive(self):
        state = ScreenState(W, H, fuse([det(48, 2050, 1032, 2180)],
                                       [run("Confirm Booking", 400, 2090, 680, 2140)]))
        assert len(state.find_by_text("confirm")) == 1
