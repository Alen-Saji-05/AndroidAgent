"""Ground a subgoal into a concrete Action against the current screen.

Phase 0 uses transparent keyword heuristics, not an LLM. This is deliberate: it
makes the control loop runnable and debuggable end to end before spending a
second Gemini call per step on grounding. The real Executor will ground with the
model (the way the Planner reasons), and slot in behind the same `ground()`
signature — grounding.py becomes one more pluggable backend.

Returns an Action, or None when nothing on screen matches (the loop treats that
as "cannot proceed here" and asks the planner to revise, or scrolls).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Optional

from .actions import Action, KeyPress, LaunchApp, Swipe, Tap, TypeText, KEY_BACK

if TYPE_CHECKING:
    from perception.schema import ScreenState, UIElement
    from planner.schema import Subgoal

log = logging.getLogger(__name__)

# Pure filler: stripped from both the subgoal and element labels.
_FILLER = {
    "the", "a", "an", "to", "and", "or", "of", "on", "in", "for", "with", "your",
    "is", "are", "be", "then", "it", "this", "that", "into", "screen", "app",
}
# Action verbs: noise in a subgoal ("tap the Confirm button"), but meaningful in
# a label ("Go", "Open"). Stripped from the subgoal's intent only — and not even
# then if doing so would leave nothing to match on (e.g. "Tap Go").
_VERBS = {
    "set", "tap", "click", "select", "choose", "open", "go", "press", "button",
    "hit", "toggle", "view", "show",
}
_BACK_HINT = re.compile(r"\bgo back\b|\bnavigate back\b|\bpress back\b|\bdismiss\b", re.I)
_SCROLL_HINT = re.compile(r"\bscroll\b|\bswipe\b|\bfind\b.*\blist\b", re.I)
# "type/enter X", "set ... to X", "search for X"
_TYPE_HINT = re.compile(r"\b(?:type|enter|input|search for|set .*? to)\b\s+(.+)", re.I)


def _tokens(text: str) -> set[str]:
    """Content tokens of a label: filler removed, verbs kept ("Go" stays)."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _FILLER and len(w) > 1}


def _intent_tokens(desc: str) -> set[str]:
    """Target words of a subgoal: also drop action verbs — unless that empties it."""
    toks = _tokens(desc)
    content = toks - _VERBS
    return content or toks


def _score(subgoal_tokens: set[str], el: "UIElement") -> float:
    et = _tokens(el.text)
    if not et or not subgoal_tokens:
        return 0.0
    overlap = subgoal_tokens & et
    if not overlap:
        return 0.0
    # fraction of the element's label that the subgoal covers, biased by hit count
    return len(overlap) / len(et) + 0.15 * len(overlap)


def best_match(subgoal_desc: str, screen: "ScreenState", *, threshold: float = 0.34
               ) -> Optional["UIElement"]:
    toks = _intent_tokens(subgoal_desc)
    ranked = sorted(
        ((el, _score(toks, el)) for el in screen.interactables()),
        key=lambda p: p[1], reverse=True,
    )
    if ranked and ranked[0][1] >= threshold:
        log.debug("grounded %r -> %s (score %.2f)", subgoal_desc,
                  ranked[0][0].text, ranked[0][1])
        return ranked[0][0]
    return None


_OPEN_HINT = re.compile(r"^\s*(?:open|launch|start|go to)\s+(.+)", re.I)
# Words to ignore when picking the app name out of "open the X app on my phone".
_LAUNCH_STOP = {
    "the", "a", "an", "app", "application", "on", "my", "device", "phone",
    "screen", "please", "up", "to", "for", "and", "then", "in", "using", "with",
}


def resolve_launch(desc: str, packages: list[str]) -> Optional[LaunchApp]:
    """If the subgoal is 'open/launch <app>', map <app> to an installed package.

    Substring-matches the app word against package names, which works for apps
    whose package contains their name (chrome, youtube, maps, whatsapp) and not
    for those where it does not (Play Store = com.android.vending). On a miss it
    returns None and the loop falls back to on-screen grounding. See D20.
    """
    m = _OPEN_HINT.search(desc)
    if not m:
        return None
    tail = re.split(r"\b(?:and|then)\b", m.group(1), maxsplit=1)[0]
    words = [w for w in re.findall(r"[a-z0-9]+", tail.lower()) if w not in _LAUNCH_STOP]
    if not words:
        return None

    norm = [re.sub(r"[^a-z0-9]", "", p.lower()) for p in packages]
    # Candidate keys: each word, and the whole phrase joined ("play store"->"playstore").
    candidates = [w for w in words if len(w) >= 3]
    if len(words) > 1:
        candidates.insert(0, "".join(words))  # try the specific multi-word name first

    for key in candidates:
        matches = [packages[i] for i, p in enumerate(norm) if key in p]
        if matches:
            best = min(matches, key=len)  # shortest package = least likely a variant
            log.debug("launch %r -> %s (key %r)", tail.strip(), best, key)
            return LaunchApp(best)
    return None


def _extract_type_target(desc: str) -> Optional[str]:
    m = _TYPE_HINT.search(desc)
    if not m:
        return None
    target = m.group(1).strip()
    # keep it short; drop trailing clauses
    target = target.split(" and ")[0].split(",")[0].strip()
    # cut at the phrase naming WHERE to type: "pizza into the search bar" -> "pizza"
    target = re.split(
        r"\s+(?:into|in|on|to)\s+(?:the\s+)?"
        r"(?:search|address|text|url|input|field|bar|box|omnibox)\b",
        target, maxsplit=1, flags=re.I,
    )[0].strip()
    # drop a leading article: "the airport" -> "airport"
    target = re.sub(r"^(?:the|a|an)\s+", "", target, flags=re.I)
    # drop a leading content-type noun: "song TV Off" -> "TV Off"
    target = re.sub(r"^(?:song|track|video|playlist|album|artist|movie|episode)\s+",
                    "", target, flags=re.I)
    # strip surrounding quotes/punctuation: "'pizza'" -> "pizza"
    target = target.strip("\"'.` ")
    return target or None


def ground(subgoal: "Subgoal", screen: "ScreenState") -> Optional[Action]:
    """Best next Action for this subgoal on this screen, or None."""
    desc = subgoal.description

    if _BACK_HINT.search(desc):
        return KeyPress(KEY_BACK)

    # Typing: if the subgoal asks to enter text and a text field is visible,
    # tap it (so it's focused) — the loop's next iteration types into it.
    type_target = _extract_type_target(desc)
    if type_target:
        field = _best_field(screen)
        if field is not None:
            # If a field already holds no useful label match, tap to focus first.
            return Tap(*field.center)
        # no field to type into yet; fall through to a normal match/scroll

    match = best_match(desc, screen)
    if match is not None:
        return Tap(*match.center)

    if _SCROLL_HINT.search(desc):
        return _scroll_down(screen)

    return None


def type_text_for(subgoal: "Subgoal") -> Optional[TypeText]:
    """The literal text to type for a typing subgoal, if any."""
    t = _extract_type_target(subgoal.description)
    return TypeText(t) if t else None


def _best_field(screen: "ScreenState") -> Optional["UIElement"]:
    """Where to type. Prefer a real text field; otherwise the wide bar near the
    top of the screen — search and address bars look like that — while excluding
    bottom navigation, whose "Search" tab is a classic mis-target.

    Vision perception often classifies a search box as a plain button (Spotify's
    "What do you want to listen to?" comes back as a full-width button), so the
    type-only filter is not enough on its own.
    """
    inter = screen.interactables()
    fields = [e for e in inter if e.type.value == "text_field"]
    if fields:
        return min(fields, key=lambda e: e.bounds.y1)
    wide_top = [
        e for e in inter
        if e.type.value != "nav_item"
        and e.bounds.width >= 0.5 * screen.width
        and 100 < e.center[1] <= 0.5 * screen.height
    ]
    if wide_top:
        return min(wide_top, key=lambda e: e.bounds.y1)
    return None


def _scroll_down(screen: "ScreenState") -> Swipe:
    cx = screen.width // 2
    return Swipe(cx, int(screen.height * 0.7), cx, int(screen.height * 0.3), 400)


def digest_for_grounding(screen: "ScreenState", *, max_elements: int = 80) -> str:
    """Compact element list for the LLM grounder: id, type, label, position.

    Includes tap centre and a coarse vertical zone (top/middle/bottom) so the
    model can tell a top search bar from a bottom nav tab — the exact ambiguity
    that defeated the keyword grounder.
    """
    lines: list[str] = []
    for e in screen.elements:
        if len(lines) >= max_elements:
            break
        cx, cy = e.center
        frac = cy / screen.height if screen.height else 0
        zone = "top" if frac < 0.2 else "bottom" if frac > 0.85 else "mid"
        label = f' "{e.text.strip()}"' if e.text.strip() else ""
        tag = "" if e.interactable else " (read-only)"
        etype = e.type.value if hasattr(e.type, "value") else str(e.type)
        lines.append(f"[{e.id}] {etype}{label}{tag} @({cx},{cy},{zone})")
    if not lines:
        return "(no elements detected)"
    return f"Screen {screen.width}x{screen.height}:\n" + "\n".join(lines)
