"""Render a perception ScreenState into compact text for the Planner prompt.

Feeding raw ScreenState JSON to the LLM is wasteful and noisy: pixel bounds are
irrelevant to a planner (the Executor keeps those) and burn tokens. The digest
keeps id + type + text — enough to reason about *what is on screen* — and drops
geometry.

SECURITY: the text in a digest comes from whatever app is on screen and is
therefore attacker-controllable. It is DATA, never instructions. The prompt
layer wraps it as such; nothing here should be treated as a command.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid importing perception at module load
    from perception.schema import ScreenState, UIElement

# Elements with neither a label nor a role worth mentioning add only noise.
_SKIP_BLANK_TYPES = {"container", "image"}


def _element_line(el: "UIElement") -> str | None:
    text = (el.text or "").strip().replace("\n", " ")
    etype = el.type.value if hasattr(el.type, "value") else str(el.type)
    if not text and etype in _SKIP_BLANK_TYPES:
        return None
    label = f' "{text}"' if text else ""
    flag = "" if el.interactable else " (read-only)"
    # checkbox/toggle state is decision-relevant when known
    checked = getattr(el.state, "checked", None)
    state = "" if checked is None else (" [checked]" if checked else " [unchecked]")
    return f"[{el.id}] {etype}{label}{flag}{state}"


def digest(state: "ScreenState", *, max_elements: int = 60) -> str:
    """Compact, ordered, human-readable list of on-screen elements.

    Ordered as perception ordered them (reading order), so ids referenced by the
    Planner map straight back to the Executor's coordinate table.
    """
    lines: list[str] = []
    for el in state.elements:
        line = _element_line(el)
        if line:
            lines.append(line)
        if len(lines) >= max_elements:
            lines.append(f"... ({len(state.elements) - max_elements}+ more elements)")
            break
    if not lines:
        return "(screen appears empty or unreadable)"
    header = f"Screen {state.width}x{state.height}, {len(state.elements)} elements:"
    return header + "\n" + "\n".join(lines)
