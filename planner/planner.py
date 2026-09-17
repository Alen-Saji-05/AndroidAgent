"""The Planner: decompose an instruction, then advance/revise a plan as it runs.

Owns the control logic; backends (backend.py) only produce JSON. Key invariants:
- Satisfied subgoals are immutable — revision only rewrites the road ahead.
- A subgoal that keeps failing is abandoned (blocked), never retried forever.
- Every sensitive subgoal is gated by the loop-level check here, independent of
  what the model flagged — the model's flag can only ADD confirmation, the gate
  is what enforces it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from .backend import PlannerBackend, default_backend
from .schema import (
    Plan,
    PlanStatus,
    PlanUpdate,
    Subgoal,
    SubgoalStatus,
    UpdateKind,
)

if TYPE_CHECKING:
    from perception.schema import ScreenState

log = logging.getLogger(__name__)

# Abandon a subgoal after this many failed attempts rather than loop forever.
DEFAULT_MAX_ATTEMPTS = 4
# Hard ceiling on plan size, so a misbehaving model cannot produce 500 subgoals.
MAX_SUBGOALS = 20


class Planner:
    def __init__(
        self,
        backend: Optional[PlannerBackend] = None,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.backend = backend or default_backend()
        self.max_attempts = max_attempts

    # -- initial decomposition ------------------------------------------
    def decompose(self, goal: str, context: Optional[str] = None) -> Plan:
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("goal is empty")
        raw = self.backend.decompose(goal, context)
        subgoals = self._parse_subgoals(raw.get("subgoals", []), start_index=0)
        if not subgoals:
            # A backend that returns nothing still yields a runnable one-step plan.
            subgoals = [Subgoal(id="s0", description=goal,
                                success_hint="the task appears complete")]
        plan = Plan(goal=goal, subgoals=subgoals, cursor=0, status=PlanStatus.IN_PROGRESS)
        self._enter_active(plan)
        log.info("decomposed %r into %d subgoals", goal, len(subgoals))
        return plan

    # -- per-step reassessment ------------------------------------------
    def reassess(
        self,
        plan: Plan,
        screen: "ScreenState",
        last_action: str = "",
        verifier_report: str = "",
    ) -> PlanUpdate:
        """Consult the backend against the live screen and mutate `plan` in place.

        Returns the PlanUpdate for the caller to log; the plan's cursor/status
        are already advanced so the loop can just read plan.status / plan.active.
        """
        from .digest import digest  # local import: perception optional at import time

        active = plan.active
        if active is None:
            plan.status = PlanStatus.DONE
            return PlanUpdate(UpdateKind.DONE, "no active subgoal")

        raw = self.backend.reassess(
            goal=plan.goal,
            plan_text=self._plan_text(plan),
            screen_digest=digest(screen),
            last_action=last_action,
            verifier_report=verifier_report,
        )
        update = self._parse_update(raw, plan)
        self._apply(plan, update)
        return update

    # -- confirmation gate ----------------------------------------------
    def needs_user_confirmation(self, plan: Plan) -> bool:
        """True when the active subgoal is sensitive and not yet approved.

        The loop MUST check this before letting the Executor act. This is the
        enforced backstop; the model's requires_confirmation flag only feeds it.
        """
        active = plan.active
        return bool(active and active.requires_confirmation
                    and plan.status != PlanStatus.DONE)

    def confirm(self, plan: Plan) -> None:
        """Record that the user approved the active sensitive subgoal."""
        active = plan.active
        if active:
            active.requires_confirmation = False
        if plan.status == PlanStatus.AWAITING_CONFIRMATION:
            plan.status = PlanStatus.IN_PROGRESS

    # -- internals ------------------------------------------------------
    def _apply(self, plan: Plan, update: PlanUpdate) -> None:
        active = plan.active
        if update.kind == UpdateKind.ADVANCE:
            if active:
                active.status = SubgoalStatus.SATISFIED
            self._move_cursor(plan)
        elif update.kind == UpdateKind.RETRY:
            if active:
                active.attempts += 1
                if active.attempts >= self.max_attempts:
                    active.status = SubgoalStatus.FAILED
                    plan.status = PlanStatus.BLOCKED
                    log.warning("subgoal %s failed after %d attempts",
                                active.id, active.attempts)
        elif update.kind == UpdateKind.REVISE:
            self._revise_tail(plan, update.revised_subgoals or [])
        elif update.kind == UpdateKind.DONE:
            for s in plan.subgoals[plan.cursor:]:
                if s.status == SubgoalStatus.ACTIVE:
                    s.status = SubgoalStatus.SATISFIED
            plan.status = PlanStatus.DONE
        elif update.kind == UpdateKind.BLOCKED:
            plan.status = PlanStatus.BLOCKED
        elif update.kind == UpdateKind.NEEDS_CONFIRMATION:
            if active:
                active.requires_confirmation = True
            plan.status = PlanStatus.AWAITING_CONFIRMATION

        if plan.is_complete() and plan.status != PlanStatus.BLOCKED:
            plan.status = PlanStatus.DONE

    def _move_cursor(self, plan: Plan) -> None:
        plan.cursor += 1
        if plan.cursor >= len(plan.subgoals):
            plan.status = PlanStatus.DONE
        else:
            self._enter_active(plan)

    def _enter_active(self, plan: Plan) -> None:
        active = plan.active
        if active and active.status == SubgoalStatus.PENDING:
            active.status = SubgoalStatus.ACTIVE
        if active and active.requires_confirmation:
            plan.status = PlanStatus.AWAITING_CONFIRMATION

    def _revise_tail(self, plan: Plan, new_subgoals: list[Subgoal]) -> None:
        """Replace everything from the cursor onward; keep satisfied history."""
        kept = plan.subgoals[:plan.cursor]
        for s in plan.subgoals[plan.cursor:]:
            if s.status not in (SubgoalStatus.SATISFIED,):
                s.status = SubgoalStatus.SKIPPED
        # renumber new subgoals after the kept prefix
        renumbered = self._parse_subgoals(
            [s.to_dict() for s in new_subgoals], start_index=len(kept)
        )
        plan.subgoals = kept + renumbered[: MAX_SUBGOALS - len(kept)]
        if plan.cursor < len(plan.subgoals):
            self._enter_active(plan)
        else:
            plan.status = PlanStatus.DONE

    def _parse_subgoals(self, raw_list, *, start_index: int) -> list[Subgoal]:
        out: list[Subgoal] = []
        if not isinstance(raw_list, list):
            return out
        for i, item in enumerate(raw_list[:MAX_SUBGOALS]):
            if not isinstance(item, dict):
                continue
            out.append(Subgoal.from_dict(item, fallback_id=f"s{start_index + i}"))
        return out

    def _parse_update(self, raw: dict, plan: Plan) -> PlanUpdate:
        try:
            kind = UpdateKind(str(raw.get("kind", "retry")))
        except ValueError:
            kind = UpdateKind.RETRY
        revised = None
        if kind == UpdateKind.REVISE:
            revised = self._parse_subgoals(
                raw.get("revised_subgoals", []), start_index=plan.cursor
            )
            if not revised:  # revise with no content is a no-op retry
                kind = UpdateKind.RETRY
        return PlanUpdate(kind=kind, reason=str(raw.get("reason", "")), revised_subgoals=revised)

    @staticmethod
    def _plan_text(plan: Plan) -> str:
        lines = []
        for i, s in enumerate(plan.subgoals):
            mark = "->" if i == plan.cursor else "  "
            conf = " (needs confirmation)" if s.requires_confirmation else ""
            lines.append(f"{mark} {s.id} [{s.status.value}] {s.description}{conf}")
        return "\n".join(lines)
