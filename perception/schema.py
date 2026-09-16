"""Data model for screen perception output.

This is the contract between the perception layer and everything downstream
(planner, executor). Keep it stable: the accessibility-tree path will emit the
same `UIElement` shape so the two sources can be reconciled later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class ElementType(str, Enum):
    """Closed vocabulary of element kinds.

    Deliberately closed: open-ended type strings drift between runs and break
    downstream matching. Anything unrecognised becomes UNKNOWN rather than a
    novel string.
    """

    BUTTON = "button"
    TEXT_FIELD = "text_field"
    TEXT = "text"
    IMAGE = "image"
    ICON = "icon"
    CHECKBOX = "checkbox"
    TOGGLE = "toggle"
    RADIO = "radio"
    LIST_ITEM = "list_item"
    TAB = "tab"
    NAV_ITEM = "nav_item"
    DROPDOWN = "dropdown"
    SLIDER = "slider"
    CONTAINER = "container"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BoundingBox:
    """Axis-aligned box in absolute image pixels. x1,y1 = top-left."""

    x1: int
    y1: int
    x2: int
    y2: int

    def __post_init__(self) -> None:
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError(f"degenerate box: {self}")

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def center(self) -> tuple[int, int]:
        """Tap target. The executor issues `input tap x y` with exactly this."""
        return (self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height

    def clamp(self, width: int, height: int) -> "BoundingBox":
        """Clip to image bounds. Detectors routinely emit slightly OOB boxes."""
        return BoundingBox(
            x1=max(0, min(self.x1, width - 1)),
            y1=max(0, min(self.y1, height - 1)),
            x2=max(1, min(self.x2, width)),
            y2=max(1, min(self.y2, height)),
        )

    def iou(self, other: "BoundingBox") -> float:
        """Intersection over union."""
        inter = self.intersection_area(other)
        if inter == 0:
            return 0.0
        return inter / (self.area + other.area - inter)

    def intersection_area(self, other: "BoundingBox") -> int:
        dx = min(self.x2, other.x2) - max(self.x1, other.x1)
        dy = min(self.y2, other.y2) - max(self.y1, other.y1)
        return dx * dy if dx > 0 and dy > 0 else 0

    def contains_ratio(self, other: "BoundingBox") -> float:
        """Fraction of `other` that lies inside `self`.

        Used for OCR-to-detection assignment: a text run is "inside" a button
        even though their IoU is low (the text is much smaller than the box).
        """
        if other.area == 0:
            return 0.0
        return self.intersection_area(other) / other.area

    def to_dict(self) -> dict[str, int]:
        return {"x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2}


@dataclass
class ElementState:
    enabled: bool = True
    selected: bool = False
    # None = undetermined. Vision cannot always tell, and guessing False is a lie.
    checked: Optional[bool] = None


@dataclass
class UIElement:
    id: str
    type: ElementType
    bounds: BoundingBox
    text: str = ""
    interactable: bool = True
    confidence: float = 0.0
    source: str = "vision"  # "vision" | "ocr" | "accessibility" | "fused"
    state: ElementState = field(default_factory=ElementState)

    @property
    def center(self) -> tuple[int, int]:
        return self.bounds.center

    def to_dict(self) -> dict[str, Any]:
        cx, cy = self.center
        return {
            "id": self.id,
            "type": self.type.value,
            "text": self.text,
            "bounds": self.bounds.to_dict(),
            "center": {"x": cx, "y": cy},
            "interactable": self.interactable,
            "confidence": round(self.confidence, 4),
            "source": self.source,
            "state": asdict(self.state),
        }


@dataclass
class ScreenState:
    """Everything perceived from one screenshot."""

    width: int
    height: int
    elements: list[UIElement] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_size": {"width": self.width, "height": self.height},
            "element_count": len(self.elements),
            "elements": [e.to_dict() for e in self.elements],
            "metadata": self.metadata,
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def interactables(self) -> list[UIElement]:
        return [e for e in self.elements if e.interactable]

    def find_by_text(self, needle: str) -> list[UIElement]:
        """Cheap literal lookup. Semantic grounding is a separate concern."""
        n = needle.strip().lower()
        return [e for e in self.elements if n in e.text.lower()]
