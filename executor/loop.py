"""The Phase 0 control loop: perceive -> plan -> ground -> (gate) -> execute -> verify.

Everything is injected — planner, executor, and a `perceive` callable — so the
whole loop runs on FakeADB + NullPlanner + a stub perceive with no device and no
network. That is the point of Phase 0: prove the loop deterministically first,
then point it at a real phone by swapping the injected pieces.

The confirmation gate is honoured here before any Action is executed, so a
sensitive subgoal cannot act until `confirm_cb` returns True. This is the
human-in-the-loop constraint made real (decisions.md D15).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

from . import grounding
from .actions import Action, KEY_ENTER, KeyPress, LaunchApp, Wait
from .executor import Executor
from .grounder import Grounder, HeuristicGrounder

if TYPE_CHECKING:
    from perception.schema import ScreenState
    from planner import Planner
    from planner.schema import Plan, Subgoal

log = logging.getLogger(__name__)

Perceive = Callable[[], "ScreenState"]
ConfirmCb = Callable[["Subgoal"], bool]


def deny(_subgoal) -> bool:
    """Default gate: refuse sensitive actions. Safe, and forces an explicit opt-in."""
    return False


@dataclass
class StepRecord:
    """One loop iteration, in a form a replay module can consume."""
    step: int
    subgoal_id: str
    subgoal: str
    action: Optional[str]
    update: str
    note: str = ""


@dataclass
class RunResult:
    plan: "Plan"
    steps: list[StepRecord] = field(default_factory=list)
    stopped: str = ""     # why the loop ended: done | blocked | max_steps | denied | ungroundable


class AgentLoop:
    def __init__(
        self,
        planner: "Planner",
        executor: Executor,
        perceive: Perceive,
        *,
        grounder: Optional["Grounder"] = None,
        confirm_cb: ConfirmCb = deny,
        max_steps: int = 25,
    ) -> None:
        self.planner = planner
        self.executor = executor
        self.perceive = perceive
        self.confirm_cb = confirm_cb
        self.max_steps = max_steps
        self.grounder = grounder or HeuristicGrounder()
        self._packages: Optional[list[str]] = None  # cached installed packages
        self._last_action = ""
        self._launched: set[str] = set()  # subgoal ids we've already launched for

    def _wake_device(self) -> None:
        """Turn the screen on before starting.

        The agent acts on what is visible; a sleeping screen shows only the lock
        screen and everything stalls. WAKEUP dismisses a non-secure lock; a secure
        (PIN/pattern) lock still needs the user, which is correct — we never try
        to bypass it.
        """
        try:
            self.executor.adb.shell("input", "keyevent", "KEYCODE_WAKEUP")
        except Exception as e:  # dry-run executor or no adb: skip quietly
            log.debug("wake skipped: %s", e)

    def _installed_packages(self) -> list[str]:
        if self._packages is None:
            try:
                self._packages = self.executor.adb.list_packages()
            except Exception as e:  # non-fatal: launch resolution just won't fire
                log.warning("could not list packages: %s", e)
                self._packages = []
        return self._packages

    def run(self, goal: str) -> RunResult:
        from planner.schema import PlanStatus

        self._wake_device()
        plan = self.planner.decompose(goal)
        result = RunResult(plan=plan)
        log.info("goal %r -> %d subgoals", goal, len(plan.subgoals))

        for step in range(1, self.max_steps + 1):
            if plan.status is PlanStatus.DONE:
                result.stopped = "done"
                break
            if plan.status is PlanStatus.BLOCKED:
                result.stopped = "blocked"
                break

            active = plan.active
            if active is None:
                result.stopped = "done"
                break

            # --- confirmation gate: must clear BEFORE any action -------------
            if self.planner.needs_user_confirmation(plan):
                if self.confirm_cb(active):
                    self.planner.confirm(plan)
                    log.info("user confirmed sensitive subgoal %s", active.id)
                else:
                    result.steps.append(StepRecord(step, active.id, active.description,
                                                   None, "denied", "user declined confirmation"))
                    result.stopped = "denied"
                    break

            # "Open <app>" resolves to a launcher intent — no icon hunting, no
            # screenshot needed. Launch ONCE per subgoal: relaunching a running
            # app just resets it to a splash screen and the planner never sees it
            # load. After the first launch, fall through to normal grounding.
            launch = grounding.resolve_launch(active.description, self._installed_packages())
            if launch is not None and active.id not in self._launched:
                self._launched.add(active.id)
                self.executor.execute(launch)
                self.executor.execute(Wait(1.8))   # let the app finish loading
                # Issuing the launch intent IS completing "open the app" — advance
                # directly rather than let a conservative planner keep retrying.
                self.planner.advance(plan, reason="app launched")
                self._last_action = launch.describe()
                result.steps.append(StepRecord(step, active.id, active.description,
                                                launch.describe(), "advance", "app launched"))
                continue

            screen = self.perceive()
            ground = self.grounder.ground(active, screen, last_action=self._last_action)

            if ground.done:
                # The grounder judged the subgoal already satisfied — advance.
                self.planner.advance(plan, reason=ground.reason)
                result.steps.append(StepRecord(step, active.id, active.description,
                                                None, "advance", ground.reason or "grounder: done"))
                continue

            if not ground.actions:
                # Grounder found nothing. Let the planner decide (revise/scroll/block).
                note = ground.reason or "no groundable action on screen"
                update = self.planner.reassess(plan, screen, last_action="(none)",
                                                verifier_report=note)
                result.steps.append(StepRecord(step, active.id, active.description,
                                                None, update.kind.value, note))
                continue

            # --- execute the grounded action(s) -----------------------------
            before_sig = _screen_signature(screen)
            for a in ground.actions:
                self.executor.execute(a)
            performed = "; ".join(a.describe() for a in ground.actions)
            self._last_action = performed

            # --- perceive again + verify via the planner --------------------
            new_screen = self.perceive()
            report = _verify_report(active, new_screen)
            update = self.planner.reassess(plan, new_screen, last_action=performed,
                                            verifier_report=report)
            # A changing screen means the action did something — that is progress
            # through a multi-step subgoal, not being stuck. Don't let the retry
            # counter accumulate toward "blocked" while the screen keeps moving.
            from planner.schema import UpdateKind
            if (update.kind is UpdateKind.RETRY and plan.active is active
                    and _screen_signature(new_screen) != before_sig):
                active.attempts = 0
            result.steps.append(StepRecord(step, active.id, active.description,
                                            performed, update.kind.value, report))
        else:
            result.stopped = "max_steps"

        if not result.stopped:
            result.stopped = plan.status.value
        log.info("run ended: %s after %d steps", result.stopped, len(result.steps))
        return result


def _is_tap(action: Action) -> bool:
    from .actions import Tap
    return isinstance(action, Tap)


def _verify_report(subgoal: "Subgoal", screen: "ScreenState") -> str:
    """Cheap post-action signal for the planner: does the success_hint show up?

    Not a real verifier — just a hint fed into reassess so the model/stub has
    something to judge. The real Verifier is a later component.
    """
    hint = grounding._tokens(subgoal.success_hint)
    if not hint:
        return "action performed; no success_hint to check"
    on_screen = set()
    for el in screen.elements:
        on_screen |= grounding._tokens(el.text)
    hit = hint & on_screen
    return f"success_hint tokens present: {len(hit)}/{len(hint)} ({', '.join(sorted(hit)) or 'none'})"


def _screen_signature(screen: "ScreenState") -> tuple:
    """Cheap fingerprint of a screen: which element labels are present.

    Used to tell "the action changed the screen (progress)" from "nothing moved
    (stuck)". Text set, not order, so minor reflows don't read as change.
    """
    return tuple(sorted({e.text.strip() for e in screen.elements if e.text.strip()}))
