"""Assign element types from geometry, text, and screen position.

Rule-based on purpose. The detector is class-agnostic, and a learned classifier
here would be one more opaque failure mode in a pipeline that already has two.
Rules are debuggable: when a button is mistyped you can read why.

Every rule below is a heuristic over mobile UI convention, not a guarantee.
"""

from __future__ import annotations

import re

from .schema import ElementType, ScreenState, UIElement

# Text that reliably indicates an actionable control.
_ACTION_WORDS = {
    "ok", "cancel", "submit", "confirm", "continue", "next", "back", "done",
    "save", "send", "search", "login", "log in", "sign in", "sign up", "book",
    "buy", "pay", "order", "add", "remove", "delete", "apply", "proceed",
    "checkout", "retry", "allow", "deny", "accept", "decline", "skip", "start",
}
# Placeholder/label text typical of input fields.
_FIELD_HINTS = re.compile(
    r"^(enter|type|search|add)\b|\b(email|password|username|phone|address|"
    r"pincode|otp|name)\b|\.{3}$",
    re.IGNORECASE,
)

# --- geometry thresholds, as fractions of screen dimensions ---
BOTTOM_BAR_START = 0.90   # below this y-fraction -> bottom navigation
TOP_BAR_END = 0.10        # above this -> app bar / tabs
WIDE_ELEMENT = 0.60       # width fraction that suggests a full-width control
SMALL_ICON = 0.12         # width fraction below which a box is icon-sized
SQUARISH = (0.7, 1.4)     # aspect ratio band for icons/checkboxes
LIST_ROW_MIN_HEIGHT = 0.06  # height fraction above which a wide box is a list row


def _is_squarish(ratio: float) -> bool:
    return SQUARISH[0] <= ratio <= SQUARISH[1]


def classify_element(element: UIElement, screen_w: int, screen_h: int) -> ElementType:
    """Best-guess type for one element."""
    if element.source == "ocr":
        return ElementType.TEXT  # never had a detection box

    box = element.bounds
    text = element.text.strip()
    lowered = text.lower()

    w_frac = box.width / screen_w
    h_frac = box.height / screen_h
    y_frac = box.y1 / screen_h

    # --- position-driven rules (strongest signal in mobile UIs) ---
    if y_frac >= BOTTOM_BAR_START and w_frac < 0.35:
        return ElementType.NAV_ITEM
    if y_frac <= TOP_BAR_END and text and w_frac < 0.35:
        return ElementType.TAB

    # --- text-driven rules ---
    if text:
        if lowered in _ACTION_WORDS or any(
            lowered.startswith(w + " ") for w in _ACTION_WORDS
        ):
            return ElementType.BUTTON
        if _FIELD_HINTS.search(text):
            return ElementType.TEXT_FIELD

    # --- geometry-driven rules ---
    if not text:
        if _is_squarish(box.aspect_ratio) and w_frac <= SMALL_ICON:
            return ElementType.ICON
        if w_frac > WIDE_ELEMENT and h_frac > 0.25:
            return ElementType.IMAGE
        return ElementType.ICON

    if w_frac >= WIDE_ELEMENT:
        # Full-width and short: a button or a field. Tall: a list row.
        # 0.06 measured against a cab-booking capture where 180px cards
        # (0.075 of 2400) were being typed as buttons at the old 0.09 cut.
        if h_frac > LIST_ROW_MIN_HEIGHT:
            return ElementType.LIST_ITEM
        return ElementType.BUTTON

    if box.aspect_ratio > 2.0:
        return ElementType.BUTTON

    return ElementType.UNKNOWN


def classify_all(state: ScreenState) -> ScreenState:
    """Type every element in place and fix up interactability."""
    for element in state.elements:
        element.type = classify_element(element, state.width, state.height)
        # Static text is not a tap target; everything with a detection box is.
        element.interactable = element.type is not ElementType.TEXT
    return state
