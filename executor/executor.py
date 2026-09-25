"""Executor: perform an Action on the device via ADB.

Thin on purpose — grounding (subgoal -> Action) is grounding.py's job, planning
is the planner's. This just issues the command and pauses for the UI to react.
"""

from __future__ import annotations

import logging
import time

from .actions import Action, Wait
from .adb import ADBClient

log = logging.getLogger(__name__)


class Executor:
    def __init__(self, adb: ADBClient, *, settle_seconds: float = 0.8) -> None:
        self.adb = adb
        self.settle_seconds = settle_seconds

    def execute(self, action: Action) -> None:
        if isinstance(action, Wait):
            time.sleep(action.seconds)
            return
        args = action.adb_args()
        log.info("execute: %s", action.describe())
        if args:
            self.adb.shell(*args)
        # Give the UI a moment to animate/load before the next perceive.
        time.sleep(self.settle_seconds)
