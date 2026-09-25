"""Tests for the Phase 0 executor: actions, ADB mapping, grounding, control loop.

All run on FakeADB + NullPlanner + stub perceive — no device, no network, no
torch — the same stub-driven discipline as perception and planner.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from perception.schema import BoundingBox, ElementType, ScreenState, UIElement
from planner import Planner, NullPlanner, PlanStatus
from executor import (
    AgentLoop, Executor, FakeADB, KeyPress, LaunchApp, Swipe, Tap, TypeText, Wait, KEY_BACK,
)
from executor import grounding
from planner.schema import Subgoal

_PKGS = ["com.android.chrome", "com.google.android.youtube",
         "com.google.android.apps.maps", "com.whatsapp", "com.android.vending"]


# ----- fixtures -------------------------------------------------------------
def el(id, type, text="", interactable=True, x1=0, y1=0, x2=200, y2=80):
    return UIElement(id, type, BoundingBox(x1, y1, x2, y2), text, interactable=interactable)


def screen(*els):
    return ScreenState(1080, 2400, list(els))


# ----- actions -> ADB -------------------------------------------------------
class TestActionMapping:
    def test_tap(self):
        assert Tap(540, 2115).adb_args() == ["input", "tap", "540", "2115"]

    def test_type_escapes_spaces(self):
        assert TypeText("hello world").adb_args() == ["input", "text", "hello%sworld"]

    def test_swipe(self):
        assert Swipe(1, 2, 3, 4, 250).adb_args() == ["input", "swipe", "1", "2", "3", "4", "250"]

    def test_key_back(self):
        assert KeyPress(KEY_BACK).adb_args() == ["input", "keyevent", "4"]

    def test_wait_sends_nothing(self):
        assert Wait(0).adb_args() == []


class TestExecutor:
    def test_execute_sends_the_command(self):
        adb = FakeADB()
        Executor(adb, settle_seconds=0).execute(Tap(10, 20))
        assert adb.commands == [("input", "tap", "10", "20")]

    def test_wait_sends_no_command(self):
        adb = FakeADB()
        Executor(adb, settle_seconds=0).execute(Wait(0))
        assert adb.commands == []


# ----- grounding ------------------------------------------------------------
class TestGrounding:
    def test_matches_button_by_label(self):
        s = screen(el("e0", ElementType.BUTTON, "Confirm Booking", x1=48, y1=2050, x2=1032, y2=2180))
        action = grounding.ground(Subgoal("s0", "Confirm the booking"), s)
        assert isinstance(action, Tap)
        assert action.x == 540 and action.y == 2115

    def test_back_hint(self):
        action = grounding.ground(Subgoal("s0", "Go back to the previous screen"), screen())
        assert action == KeyPress(KEY_BACK)

    def test_no_match_returns_none(self):
        s = screen(el("e0", ElementType.BUTTON, "Settings"))
        assert grounding.ground(Subgoal("s0", "Play the next song"), s) is None

    def test_read_only_text_is_not_a_target(self):
        s = screen(el("e0", ElementType.TEXT, "Confirm Booking", interactable=False))
        assert grounding.ground(Subgoal("s0", "Confirm booking"), s) is None

    def test_typing_taps_the_field(self):
        s = screen(el("e0", ElementType.TEXT_FIELD, "Search", x1=50, y1=300, x2=1030, y2=410))
        action = grounding.ground(Subgoal("s0", "Search for pizza"), s)
        assert isinstance(action, Tap)          # focuses the field first
        assert grounding.type_text_for(Subgoal("s0", "Search for pizza")) == TypeText("pizza")

    def test_typing_targets_top_search_bar_not_bottom_nav(self):
        # Spotify case: search box comes back as a full-width button up top, and a
        # "Search" nav item sits at the bottom. Typing must target the box.
        from perception.schema import ElementType as T
        s = ScreenState(1080, 2400, [
            el("e0", T.BUTTON, "What do you want to listen to?", x1=36, y1=300, x2=1044, y2=460),
            el("e1", T.NAV_ITEM, "Search", x1=360, y1=2260, x2=560, y2=2360),
        ])
        action = grounding.ground(Subgoal("s0", "Tap the search bar and type pizza"), s)
        assert isinstance(action, Tap)
        assert action.y < 700          # the top box, not the bottom nav

    def test_type_target_excludes_the_field_phrase(self):
        # regression: "type X into the search bar" must yield X, not the whole clause
        assert grounding.type_text_for(
            Subgoal("s0", "Type pizza into the search bar")) == TypeText("pizza")
        assert grounding.type_text_for(
            Subgoal("s0", "enter lofi music into the search box")) == TypeText("lofi music")

    def test_scroll_when_asked_and_unmatched(self):
        action = grounding.ground(Subgoal("s0", "Scroll down to find more"), screen())
        assert isinstance(action, Swipe)
        assert action.y1 > action.y2          # downward = swipe up


class TestLaunchResolution:
    def test_resolves_app_name_to_package(self):
        assert grounding.resolve_launch("Open Chrome app", _PKGS) == LaunchApp("com.android.chrome")
        assert grounding.resolve_launch("launch the maps app", _PKGS) == LaunchApp("com.google.android.apps.maps")

    def test_resolves_app_name_buried_in_a_longer_phrase(self):
        # the real subgoal that first broke it: extra words after the app name
        assert grounding.resolve_launch(
            "Open the Chrome app on the device", _PKGS) == LaunchApp("com.android.chrome")
        assert grounding.resolve_launch(
            "open the chrome browser", _PKGS) == LaunchApp("com.android.chrome")

    def test_non_open_subgoal_is_not_a_launch(self):
        assert grounding.resolve_launch("Tap the confirm button", _PKGS) is None

    def test_unknown_app_returns_none(self):
        # Play Store's package (com.android.vending) does not contain "playstore"
        assert grounding.resolve_launch("Open the Play Store", _PKGS) is None

    def test_launch_action_maps_to_monkey(self):
        assert LaunchApp("com.android.chrome").adb_args() == [
            "monkey", "-p", "com.android.chrome", "-c",
            "android.intent.category.LAUNCHER", "1"]

    def test_loop_launches_app_without_grounding(self):
        # subgoal "Open Chrome" should fire a LaunchApp via the loop, not a tap
        d = {"subgoals": [{"id": "s0", "description": "Open Chrome", "success_hint": "chrome open",
                           "requires_confirmation": False}]}
        loop, adb = _loop(d, [{"kind": "advance", "reason": ""}], lambda: screen())
        result = loop.run("open chrome")
        assert ("monkey", "-p", "com.android.chrome", "-c",
                "android.intent.category.LAUNCHER", "1") in adb.commands
        assert result.stopped == "done"


# ----- the loop -------------------------------------------------------------
def _loop(decompose, reassess, perceive, *, confirm_cb=None, max_steps=10):
    adb = FakeADB()
    planner = Planner(backend=NullPlanner(decompose_responses=[decompose],
                                          reassess_responses=reassess))
    kwargs = {"confirm_cb": confirm_cb} if confirm_cb else {}
    loop = AgentLoop(planner, Executor(adb, settle_seconds=0), perceive,
                     max_steps=max_steps, **kwargs)
    return loop, adb



_WAKE = ("input", "keyevent", "KEYCODE_WAKEUP")


def _action_cmds(adb):
    """ADB commands excluding the loop's startup wake keyevent."""
    return [c for c in adb.commands if c != _WAKE]


def test_loop_runs_to_completion_and_taps():
    home = screen(el("e0", ElementType.BUTTON, "Search", x1=48, y1=500, x2=1032, y2=620))
    perceive = lambda: home
    d = {"subgoals": [{"id": "s0", "description": "Tap Search", "success_hint": "results",
                       "requires_confirmation": False}]}
    loop, adb = _loop(d, [{"kind": "advance", "reason": ""}], perceive)
    result = loop.run("search")
    assert result.stopped == "done"
    assert ("input", "tap", "540", "560") in adb.commands
    assert result.plan.status is PlanStatus.DONE


def test_loop_gate_blocks_until_confirmed():
    scr = screen(el("e0", ElementType.BUTTON, "Pay Now", x1=48, y1=2050, x2=1032, y2=2180))
    d = {"subgoals": [{"id": "s0", "description": "Pay now", "success_hint": "paid",
                       "requires_confirmation": True}]}
    # deny confirmation -> the tap must NOT be sent
    loop, adb = _loop(d, [{"kind": "advance", "reason": ""}], lambda: scr,
                      confirm_cb=lambda sg: False)
    result = loop.run("pay")
    assert result.stopped == "denied"
    assert _action_cmds(adb) == []            # nothing executed (wake aside)
    assert result.plan.active.id == "s0"


def test_loop_gate_allows_when_confirmed():
    scr = screen(el("e0", ElementType.BUTTON, "Pay Now", x1=48, y1=2050, x2=1032, y2=2180))
    d = {"subgoals": [{"id": "s0", "description": "Pay now", "success_hint": "paid",
                       "requires_confirmation": True}]}
    seen = []
    loop, adb = _loop(d, [{"kind": "advance", "reason": ""}], lambda: scr,
                      confirm_cb=lambda sg: (seen.append(sg.id), True)[1])
    result = loop.run("pay")
    assert seen == ["s0"]
    assert ("input", "tap", "540", "2115") in adb.commands
    assert result.stopped == "done"


def test_ungroundable_subgoal_asks_planner_not_device():
    empty = screen()
    d = {"subgoals": [{"id": "s0", "description": "Tap the nonexistent widget",
                       "success_hint": "x", "requires_confirmation": False}]}
    # planner keeps saying retry; loop should never send an ADB command, then hit max_steps
    loop, adb = _loop(d, [{"kind": "retry", "reason": "not found"}] * 6, lambda: empty,
                      max_steps=3)
    result = loop.run("do impossible thing")
    assert _action_cmds(adb) == []
    assert result.stopped in ("max_steps", "blocked")


def test_transcript_is_recorded():
    scr = screen(el("e0", ElementType.BUTTON, "Go", x1=48, y1=500, x2=1032, y2=620))
    d = {"subgoals": [{"id": "s0", "description": "Tap Go", "success_hint": "gone",
                       "requires_confirmation": False}]}
    loop, adb = _loop(d, [{"kind": "advance", "reason": ""}], lambda: scr)
    result = loop.run("go")
    assert len(result.steps) == 1
    assert result.steps[0].action.startswith("tap")
    assert result.steps[0].update == "advance"


class TestGroqGrounder:
    """Grok grounder logic with the network call stubbed — no key, no requests."""

    def _screen(self):
        from perception.schema import ElementType as T
        return ScreenState(1080, 2400, [
            el("e0", T.BUTTON, "What do you want to listen to?", x1=36, y1=300, x2=1044, y2=460),
            el("e1", T.NAV_ITEM, "Search", x1=360, y1=2260, x2=560, y2=2360),
            el("e7", T.LIST_ITEM, "TV Off", x1=40, y1=800, x2=1040, y2=940),
        ])

    def _grounder(self, decision):
        from executor.grounder import GroqGrounder
        g = GroqGrounder(api_key="test")
        g._call = lambda system, user: decision       # stub the HTTP call
        return g

    def test_type_decision_becomes_tap_type_enter(self):
        from executor.grounder import GroqGrounder
        r = GroqGrounder._to_result(
            {"action": "type", "element": "e0", "text": "TV Off"}, self._screen())
        assert [type(a).__name__ for a in r.actions] == ["Tap", "TypeText", "KeyPress"]
        assert r.actions[0].x == 540 and r.actions[0].y == 380     # the top search box
        assert r.actions[1] == TypeText("TV Off")

    def test_tap_decision_targets_the_named_element(self):
        from executor.grounder import GroqGrounder
        r = GroqGrounder._to_result({"action": "tap", "element": "e7"}, self._screen())
        assert r.actions == [Tap(540, 870)]                       # the TV Off row, not nav

    def test_back_and_scroll_and_done(self):
        from executor.grounder import GroqGrounder
        s = self._screen()
        assert GroqGrounder._to_result({"action": "back"}, s).actions[0] == KeyPress(KEY_BACK)
        assert isinstance(GroqGrounder._to_result({"action": "scroll", "direction": "down"}, s).actions[0], Swipe)
        assert GroqGrounder._to_result({"action": "done"}, s).done is True
        assert GroqGrounder._to_result({"action": "unavailable"}, s).actions == []

    def test_unknown_element_yields_no_action(self):
        from executor.grounder import GroqGrounder
        assert GroqGrounder._to_result({"action": "tap", "element": "e99"}, self._screen()).actions == []

    def test_ground_calls_model_and_maps(self):
        from planner.schema import Subgoal
        g = self._grounder({"action": "type", "element": "e0", "text": "TV Off"})
        r = g.ground(Subgoal("s0", "Search for the song TV Off"), self._screen())
        assert TypeText("TV Off") in r.actions

    def test_missing_key_raises(self):
        import os
        import pytest as _pytest
        from executor.grounder import GroqGrounder, GrounderError
        saved = {k: os.environ.pop(k, None) for k in ("GROQ_API_KEY", "GROQ_API_KEY_2")}
        try:
            with _pytest.raises(GrounderError):
                GroqGrounder()._call("s", "u")
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
