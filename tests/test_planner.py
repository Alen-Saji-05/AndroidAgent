"""Model-free tests for the Planner state machine.

Everything here runs through NullPlanner with scripted responses — no API key,
no network — so the control logic (advance, revise, retry/give-up, confirmation
gating, termination, malformed-output tolerance) is pinned deterministically.
This is where the real bugs live, exactly as with perception's stub detectors.
Integration against the real Gemini backend is test_planner_integration.py.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from perception.schema import BoundingBox, ElementType, ScreenState, UIElement
from planner import Planner, NullPlanner, PlanStatus, SubgoalStatus, UpdateKind
from planner.schema import Subgoal


def screen(*labels: str) -> ScreenState:
    els = [
        UIElement(f"e{i}", ElementType.BUTTON, BoundingBox(0, i * 100, 200, i * 100 + 80), t)
        for i, t in enumerate(labels)
    ]
    return ScreenState(1080, 2400, els or [UIElement("e0", ElementType.TEXT,
                       BoundingBox(0, 0, 100, 40), "x", interactable=False)])


def four_step():
    return {"subgoals": [
        {"id": "s0", "description": "open app", "success_hint": "home", "requires_confirmation": False},
        {"id": "s1", "description": "set destination", "success_hint": "dest set", "requires_confirmation": False},
        {"id": "s2", "description": "choose ride", "success_hint": "ride picked", "requires_confirmation": False},
        {"id": "s3", "description": "confirm booking", "success_hint": "confirmed", "requires_confirmation": True},
    ]}


def planner(decompose=None, reassess=None, **kw):
    return Planner(backend=NullPlanner(
        decompose_responses=[decompose] if decompose else None,
        reassess_responses=reassess,
    ), **kw)


class TestDecompose:
    def test_produces_ordered_active_plan(self):
        plan = planner(four_step()).decompose("book a cab")
        assert [s.id for s in plan.subgoals] == ["s0", "s1", "s2", "s3"]
        assert plan.status is PlanStatus.IN_PROGRESS
        assert plan.active.status is SubgoalStatus.ACTIVE

    def test_empty_goal_rejected(self):
        with pytest.raises(ValueError):
            planner().decompose("   ")

    def test_empty_backend_still_yields_runnable_plan(self):
        # NullPlanner with no script echoes the goal as one subgoal.
        plan = planner().decompose("do the thing")
        assert len(plan.subgoals) == 1
        assert plan.active is not None

    def test_sensitive_first_subgoal_gates_immediately(self):
        d = {"subgoals": [{"id": "s0", "description": "pay now",
                           "success_hint": "paid", "requires_confirmation": True}]}
        plan = planner(d).decompose("pay the bill")
        assert plan.status is PlanStatus.AWAITING_CONFIRMATION


class TestAdvance:
    def test_advance_marks_satisfied_and_moves_cursor(self):
        p = planner(four_step(), [{"kind": "advance", "reason": ""}])
        plan = p.decompose("book a cab")
        p.reassess(plan, screen())
        assert plan.subgoals[0].status is SubgoalStatus.SATISFIED
        assert plan.cursor == 1
        assert plan.active.id == "s1"

    def test_advancing_off_the_end_completes(self):
        d = {"subgoals": [{"id": "s0", "description": "tap ok", "success_hint": "done",
                           "requires_confirmation": False}]}
        p = planner(d, [{"kind": "advance", "reason": ""}])
        plan = p.decompose("tap ok")
        p.reassess(plan, screen())
        assert plan.status is PlanStatus.DONE


class TestRetryAndGiveUp:
    def test_retry_increments_attempts_without_moving(self):
        p = planner(four_step(), [{"kind": "retry", "reason": "not yet"}])
        plan = p.decompose("book a cab")
        p.reassess(plan, screen())
        assert plan.cursor == 0
        assert plan.active.attempts == 1
        assert plan.status is PlanStatus.IN_PROGRESS

    def test_gives_up_after_max_attempts(self):
        retries = [{"kind": "retry", "reason": "stuck"}] * 4
        p = planner(four_step(), retries, max_attempts=3)
        plan = p.decompose("book a cab")
        for _ in range(3):
            p.reassess(plan, screen())
        assert plan.subgoals[0].status is SubgoalStatus.FAILED
        assert plan.status is PlanStatus.BLOCKED


class TestRevise:
    def test_revise_keeps_satisfied_history_and_rewrites_tail(self):
        reassess = [
            {"kind": "advance", "reason": "opened"},
            {"kind": "revise", "reason": "login wall", "revised_subgoals": [
                {"description": "dismiss login", "success_hint": "gone", "requires_confirmation": False},
                {"description": "set destination", "success_hint": "set", "requires_confirmation": False},
            ]},
        ]
        p = planner(four_step(), reassess)
        plan = p.decompose("book a cab")
        p.reassess(plan, screen())          # advance past s0
        p.reassess(plan, screen())          # revise the tail
        assert plan.subgoals[0].status is SubgoalStatus.SATISFIED   # history kept
        assert plan.subgoals[0].description == "open app"
        # tail replaced starting at the cursor, renumbered after the kept prefix
        assert plan.active.description == "dismiss login"
        assert plan.active.id == "s1"

    def test_revise_with_no_subgoals_degrades_to_retry(self):
        p = planner(four_step(), [{"kind": "revise", "reason": "oops", "revised_subgoals": []}])
        plan = p.decompose("book a cab")
        upd = p.reassess(plan, screen())
        assert upd.kind is UpdateKind.RETRY
        assert plan.cursor == 0


class TestConfirmationGate:
    def test_reaching_sensitive_subgoal_awaits_confirmation(self):
        # advance three times to land on the sensitive s3
        p = planner(four_step(), [{"kind": "advance", "reason": ""}] * 3)
        plan = p.decompose("book a cab")
        for _ in range(3):
            p.reassess(plan, screen())
        assert plan.active.id == "s3"
        assert plan.status is PlanStatus.AWAITING_CONFIRMATION
        assert p.needs_user_confirmation(plan) is True

    def test_confirm_clears_the_gate(self):
        p = planner(four_step(), [{"kind": "advance", "reason": ""}] * 3)
        plan = p.decompose("book a cab")
        for _ in range(3):
            p.reassess(plan, screen())
        p.confirm(plan)
        assert p.needs_user_confirmation(plan) is False
        assert plan.status is PlanStatus.IN_PROGRESS

    def test_model_can_escalate_to_confirmation_midstep(self):
        p = planner(four_step(), [{"kind": "needs_confirmation", "reason": "this pays money"}])
        plan = p.decompose("book a cab")
        p.reassess(plan, screen())
        assert plan.status is PlanStatus.AWAITING_CONFIRMATION
        assert plan.active.requires_confirmation is True


class TestTerminal:
    def test_done_short_circuits(self):
        p = planner(four_step(), [{"kind": "done", "reason": "already booked"}])
        plan = p.decompose("book a cab")
        p.reassess(plan, screen())
        assert plan.status is PlanStatus.DONE

    def test_blocked_stops(self):
        p = planner(four_step(), [{"kind": "blocked", "reason": "captcha"}])
        plan = p.decompose("book a cab")
        p.reassess(plan, screen())
        assert plan.status is PlanStatus.BLOCKED


class TestMalformedOutput:
    def test_unknown_kind_falls_back_to_retry(self):
        p = planner(four_step(), [{"kind": "teleport", "reason": "???"}])
        plan = p.decompose("book a cab")
        upd = p.reassess(plan, screen())
        assert upd.kind is UpdateKind.RETRY

    def test_junk_subgoal_fields_are_coerced_or_dropped(self):
        d = {"subgoals": [
            {"description": "ok", "requires_confirmation": 1, "junk": 9},   # 1 -> True, junk dropped
            "not a dict",                                                    # skipped
            {"description": "second", "requires_confirmation": False},
        ]}
        plan = planner(d).decompose("x")
        assert [s.description for s in plan.subgoals] == ["ok", "second"]
        assert plan.subgoals[0].requires_confirmation is True

    def test_backend_returning_no_subgoals_is_survivable(self):
        plan = planner({"subgoals": []}).decompose("do it")
        assert len(plan.subgoals) == 1          # synthesised one-step fallback
        assert plan.status is PlanStatus.IN_PROGRESS


class TestSerialisation:
    def test_plan_json_roundtrips_shape(self):
        import json
        plan = planner(four_step()).decompose("book a cab")
        payload = json.loads(plan.to_json())
        assert payload["goal"] == "book a cab"
        assert payload["subgoal_count"] == 4
        assert {s["status"] for s in payload["subgoals"]} <= {
            "pending", "active", "satisfied", "failed", "skipped"
        }
        # the sensitive subgoal survives serialisation
        assert any(s["requires_confirmation"] for s in payload["subgoals"])
