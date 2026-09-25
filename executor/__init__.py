"""Executor + Phase 0 control loop: drive a real device over ADB.

The dev harness that ties perception + planner into a working perceive -> plan
-> ground -> execute -> verify loop, without the Android app. On-device the same
Action model will be issued via AccessibilityService instead of ADB.

    from executor import AgentLoop, Executor, SubprocessADB, adb_perceive
"""

from .actions import (
    Action, KeyPress, LaunchApp, LongPress, Swipe, Tap, TypeText, Wait,
    KEY_BACK, KEY_HOME, KEY_ENTER,
)
from .adb import ADBClient, ADBError, FakeADB, SubprocessADB, find_adb
from .executor import Executor
from .grounding import ground
from .grounder import (
    Grounder, GrounderError, GroqGrounder, HeuristicGrounder, default_grounder,
)
from .loop import AgentLoop, ConfirmCb, Perceive, RunResult, StepRecord, deny

__all__ = [
    "Action", "KeyPress", "LaunchApp", "LongPress", "Swipe", "Tap", "TypeText", "Wait",
    "KEY_BACK", "KEY_HOME", "KEY_ENTER",
    "ADBClient", "ADBError", "FakeADB", "SubprocessADB", "find_adb",
    "Executor", "ground",
    "Grounder", "GrounderError", "GroqGrounder", "HeuristicGrounder", "default_grounder",
    "AgentLoop", "ConfirmCb", "Perceive", "RunResult", "StepRecord", "deny",
    "adb_perceive",
]


def adb_perceive(adb, parser, *, save_dir=None):
    """Build a `perceive` callable: ADB screencap -> perception ScreenState.

    Imported lazily inside so `executor` stays importable without torch/perception
    installed (tests inject their own stub perceive instead).
    """
    import io
    from pathlib import Path
    import numpy as np
    import cv2

    counter = {"n": 0}

    def perceive():
        png = adb.screencap()
        arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            raise RuntimeError("failed to decode device screenshot")
        if save_dir:
            counter["n"] += 1
            p = Path(save_dir) / f"screen_{counter['n']:03d}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(p), arr)
        return parser.parse(arr)

    return perceive
