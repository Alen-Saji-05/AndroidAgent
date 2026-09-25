"""The action space, and how each maps to an ADB `input` command.

Deliberately the same small vocabulary the design lists: tap, long-press, swipe,
type, back, scroll — plus wait. When the on-device app replaces ADB, only the
mapping changes (dispatchGesture / performAction); these Action types stay, so
the planner and grounding layers never learn they moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

# Android keyevent codes used here.
KEY_BACK = 4
KEY_HOME = 3
KEY_ENTER = 66


@dataclass(frozen=True)
class Tap:
    x: int
    y: int
    def adb_args(self) -> list[str]:
        return ["input", "tap", str(self.x), str(self.y)]
    def describe(self) -> str:
        return f"tap ({self.x},{self.y})"


@dataclass(frozen=True)
class LongPress:
    x: int
    y: int
    duration_ms: int = 700
    def adb_args(self) -> list[str]:
        # long-press = zero-length swipe with a duration
        return ["input", "swipe", str(self.x), str(self.y), str(self.x), str(self.y),
                str(self.duration_ms)]
    def describe(self) -> str:
        return f"long-press ({self.x},{self.y}) {self.duration_ms}ms"


@dataclass(frozen=True)
class Swipe:
    x1: int
    y1: int
    x2: int
    y2: int
    duration_ms: int = 300
    def adb_args(self) -> list[str]:
        return ["input", "swipe", str(self.x1), str(self.y1), str(self.x2), str(self.y2),
                str(self.duration_ms)]
    def describe(self) -> str:
        return f"swipe ({self.x1},{self.y1})->({self.x2},{self.y2})"


@dataclass(frozen=True)
class TypeText:
    text: str
    def adb_args(self) -> list[str]:
        # `input text` needs spaces as %s and cannot carry newlines; the executor
        # sends an ENTER key separately when needed.
        return ["input", "text", self.text.replace(" ", "%s")]
    def describe(self) -> str:
        return f"type {self.text!r}"


@dataclass(frozen=True)
class KeyPress:
    keycode: int
    def adb_args(self) -> list[str]:
        return ["input", "keyevent", str(self.keycode)]
    def describe(self) -> str:
        names = {KEY_BACK: "BACK", KEY_HOME: "HOME", KEY_ENTER: "ENTER"}
        return f"key {names.get(self.keycode, self.keycode)}"


@dataclass(frozen=True)
class LaunchApp:
    """Open an app by package via its launcher intent.

    This is an OS action — the same thing the launcher does when you tap an icon
    — not an integration with the app's internals. It exists because bare home
    -screen icons carry no text label, so keyword grounding cannot find them
    (D20). Reliable regardless of what is on screen.
    """
    package: str
    def adb_args(self) -> list[str]:
        return ["monkey", "-p", self.package, "-c",
                "android.intent.category.LAUNCHER", "1"]
    def describe(self) -> str:
        return f"launch app {self.package}"


@dataclass(frozen=True)
class Wait:
    """No ADB call — just pause for the screen to settle."""
    seconds: float = 1.0
    def adb_args(self) -> list[str]:
        return []
    def describe(self) -> str:
        return f"wait {self.seconds}s"


Action = Union[Tap, LongPress, Swipe, TypeText, KeyPress, LaunchApp, Wait]
