"""Merge detector boxes with OCR text runs into a single element list.

The three cases:
  box + text  -> labelled interactable (button, field, tab...)
  box, no text-> icon
  text, no box-> static text (headings, prices, captions)

No model involved. This is where most perception quality is won or lost, so it
is kept deterministic and unit-testable.
"""

from __future__ import annotations

import logging

from .detector import Detection
from .ocr import TextRun
from .schema import BoundingBox, ElementType, UIElement

log = logging.getLogger(__name__)

# Fraction of a text run that must fall inside a box to be claimed by it.
CONTAINMENT_THRESHOLD = 0.6
# Two detections above this IoU are treated as the same widget.
DEDUPE_IOU = 0.80


def deduplicate(detections: list[Detection], iou_threshold: float = DEDUPE_IOU) -> list[Detection]:
    """Drop near-duplicate boxes, keeping the most confident of each cluster.

    The detector's own NMS runs per-class; with a class-agnostic model that
    still leaves stacked boxes over the same widget.
    """
    ordered = sorted(detections, key=lambda d: d.confidence, reverse=True)
    kept: list[Detection] = []
    for det in ordered:
        if any(det.box.iou(k.box) > iou_threshold for k in kept):
            continue
        kept.append(det)
    dropped = len(detections) - len(kept)
    if dropped:
        log.debug("deduplicate dropped %d overlapping boxes", dropped)
    return kept


def _assign_text(
    detections: list[Detection], runs: list[TextRun]
) -> tuple[dict[int, list[TextRun]], list[TextRun]]:
    """Assign each text run to the smallest box that substantially contains it.

    Smallest, not highest-IoU: UI boxes nest (a button inside a card inside a
    list row), and the tightest container is the one the label belongs to.
    """
    assigned: dict[int, list[TextRun]] = {}
    orphans: list[TextRun] = []

    for run in runs:
        candidates = [
            (i, d)
            for i, d in enumerate(detections)
            if d.box.contains_ratio(run.box) >= CONTAINMENT_THRESHOLD
        ]
        if not candidates:
            orphans.append(run)
            continue
        idx, _ = min(candidates, key=lambda pair: pair[1].box.area)
        assigned.setdefault(idx, []).append(run)

    return assigned, orphans


def _merge_text(runs: list[TextRun]) -> str:
    """Join multiple runs inside one box in reading order."""
    ordered = sorted(runs, key=lambda r: (r.box.y1, r.box.x1))
    return " ".join(r.text for r in ordered).strip()


def _reading_order(elements: list[UIElement], row_tolerance: int = 20) -> list[UIElement]:
    """Sort top-to-bottom, then left-to-right within a row band.

    Plain y-sort scrambles side-by-side elements whose tops differ by a pixel,
    which makes ids jump around between otherwise identical captures.
    """
    def key(e: UIElement) -> tuple[int, int]:
        return (e.bounds.y1 // row_tolerance, e.bounds.x1)

    return sorted(elements, key=key)


def fuse(
    detections: list[Detection],
    runs: list[TextRun],
    *,
    include_static_text: bool = True,
) -> list[UIElement]:
    """Build the element list. Types are provisional; classify.py refines them."""
    detections = deduplicate(detections)
    assigned, orphans = _assign_text(detections, runs)

    elements: list[UIElement] = []

    for idx, det in enumerate(detections):
        runs_here = assigned.get(idx, [])
        text = _merge_text(runs_here)
        elements.append(
            UIElement(
                id="",  # assigned after sorting
                type=ElementType.UNKNOWN if text else ElementType.ICON,
                bounds=det.box,
                text=text,
                interactable=True,
                confidence=det.confidence,
                source="fused" if runs_here else "vision",
            )
        )

    if include_static_text:
        for run in orphans:
            elements.append(
                UIElement(
                    id="",
                    type=ElementType.TEXT,
                    bounds=run.box,
                    text=run.text,
                    interactable=False,  # label, not a tap target
                    confidence=run.confidence,
                    source="ocr",
                )
            )

    elements = _reading_order(elements)
    for i, element in enumerate(elements):
        element.id = f"e{i}"
    return elements
