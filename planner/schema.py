"""Data model for the Planner agent.

The Planner turns a natural-language instruction into an ordered list of
screen-level subgoals (a Plan), then revises that Plan as execution proceeds.
It owns *what to accomplish and how far along we are*; the Executor owns *which
pixel to tap*. See decisions D14+ (planner) once written.

This is the contract between the Planner and the rest of the loop:
- the Executor reads the active Subgoal's `description`,
- the Verifier reads its `success_hint`,
- the control loop reads `requires_confirmation` to gate sensitive actions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class SubgoalStatus(str, Enum):
    PENDING = "pending"      # not yet reached
    ACTIVE = "active"        # currently being executed
    SATISFIED = "satisfied"  # success_hint observed
    FAILED = "failed"        # gave up after repeated attempts
    SKIPPED = "skipped"      # revised out / no longer needed


class PlanStatus(str, Enum):
    PLANNING = "planning"                    # decompose not yet run
    IN_PROGRESS = "in_progress"
    AWAITING_CONFIRMATION = "awaiting_confirmation"  # blocked on user approval
    DONE = "done"
    BLOCKED = "blocked"                      # stuck; needs user intervention


class UpdateKind(str, Enum):
    """What `reassess` decided to do with the plan."""

    ADVANCE = "advance"                # active subgoal satisfied; move cursor on
    RETRY = "retry"                    # not satisfied yet; try the same subgoal again
    REVISE = "revise"                  # the plan is wrong; apply subgoal edits
    DONE = "done"                      # whole task complete
    BLOCKED = "blocked"                # cannot proceed
    NEEDS_CONFIRMATION = "needs_confirmation"  # next step is sensitive; pause for user


@dataclass
class Subgoal:
    id: str
    description: str            # imperative, screen-level: "Set the destination to the airport"
    success_hint: str = ""     # observable postcondition the Verifier checks, in words
    requires_confirmation: bool = False  # payment / OTP / send / purchase / irreversible
    status: SubgoalStatus = SubgoalStatus.PENDING
    attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, fallback_id: str) -> "Subgoal":
        """Build from LLM-produced JSON, tolerating missing/extra fields.

        LLM output is untrusted shape: coerce types, default what is absent, and
        never let a stray key raise. `fallback_id` is used when the model omits
        an id so ids stay unique and ordered.
        """
        status = d.get("status", SubgoalStatus.PENDING.value)
        try:
            status = SubgoalStatus(status)
        except ValueError:
            status = SubgoalStatus.PENDING
        return cls(
            id=str(d.get("id") or fallback_id),
            description=str(d.get("description", "")).strip(),
            success_hint=str(d.get("success_hint", "")).strip(),
            requires_confirmation=bool(d.get("requires_confirmation", False)),
            status=status,
            attempts=int(d.get("attempts", 0) or 0),
        )


@dataclass
class Plan:
    goal: str
    subgoals: list[Subgoal] = field(default_factory=list)
    cursor: int = 0
    status: PlanStatus = PlanStatus.PLANNING

    # --- cursor helpers -------------------------------------------------
    @property
    def active(self) -> Optional[Subgoal]:
        if 0 <= self.cursor < len(self.subgoals):
            return self.subgoals[self.cursor]
        return None

    def remaining(self) -> list[Subgoal]:
        return self.subgoals[self.cursor:]

    def is_complete(self) -> bool:
        return all(
            s.status in (SubgoalStatus.SATISFIED, SubgoalStatus.SKIPPED)
            for s in self.subgoals
        ) and len(self.subgoals) > 0

    # --- serialisation --------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "status": self.status.value,
            "cursor": self.cursor,
            "subgoal_count": len(self.subgoals),
            "subgoals": [s.to_dict() for s in self.subgoals],
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass
class PlanUpdate:
    """The result of `reassess`: a decision plus any subgoal edits.

    `revised_subgoals`, when present, replaces the tail of the plan from the
    cursor onward — the Planner never rewrites history (satisfied subgoals are
    immutable), only the road ahead.
    """

    kind: UpdateKind
    reason: str = ""
    revised_subgoals: Optional[list[Subgoal]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "reason": self.reason,
            "revised_subgoals": (
                [s.to_dict() for s in self.revised_subgoals]
                if self.revised_subgoals is not None
                else None
            ),
        }
