"""Render perceived elements back onto the screenshot.

Not a nicety: reading raw coordinate JSON to work out why a tap missed is
miserable, and this is the fastest way to see a detection failure.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .schema import ElementType, ScreenState

_COLORS: dict[ElementType, tuple[int, int, int]] = {  # BGR
    ElementType.BUTTON: (0, 200, 0),
    ElementType.TEXT_FIELD: (255, 140, 0),
    ElementType.TEXT: (160, 160, 160),
    ElementType.ICON: (0, 200, 255),
    ElementType.NAV_ITEM: (200, 0, 200),
    ElementType.TAB: (200, 100, 0),
    ElementType.LIST_ITEM: (0, 120, 255),
    ElementType.IMAGE: (120, 120, 255),
    ElementType.UNKNOWN: (0, 0, 220),
}
_DEFAULT_COLOR = (255, 255, 255)


def draw(image: np.ndarray, state: ScreenState, *, show_centers: bool = True) -> np.ndarray:
    canvas = image.copy()
    scale = max(0.4, min(1.0, state.width / 1400))
    thickness = max(1, int(round(scale * 2)))

    for element in state.elements:
        color = _COLORS.get(element.type, _DEFAULT_COLOR)
        b = element.bounds
        cv2.rectangle(canvas, (b.x1, b.y1), (b.x2, b.y2), color, thickness)

        label = f"{element.id}:{element.type.value}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale * 0.5, 1)
        ty = max(th + 2, b.y1)
        cv2.rectangle(canvas, (b.x1, ty - th - 2), (b.x1 + tw + 2, ty + 2), color, -1)
        cv2.putText(
            canvas, label, (b.x1 + 1, ty),
            cv2.FONT_HERSHEY_SIMPLEX, scale * 0.5, (0, 0, 0), 1, cv2.LINE_AA,
        )

        if show_centers and element.interactable:
            cx, cy = element.center
            cv2.circle(canvas, (cx, cy), max(2, thickness), color, -1)

    return canvas


def save(image: np.ndarray, state: ScreenState, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(path.suffix or ".png", draw(image, state))
    if not ok:
        raise RuntimeError(f"failed to encode overlay to {path}")
    buf.tofile(str(path))
    return path
