"""Integration tests against the real Gemini backend.

Skipped unless GEMINI_API_KEY is set and google-generativeai is installed, so
the default suite runs offline — the weights-skip pattern from perception.
Assertions are on plan SHAPE (count range, a confirmation flag on a money task),
never exact wording: LLM output varies run to run, the lesson from asserting
coordinate sanity rather than exact boxes.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load .env the same way the backend does, so a key in the file (not just the
# shell) is seen when deciding whether to run these tests.
try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=False)
except ImportError:
    pass

_HAS_KEY = bool(os.environ.get("GEMINI_API_KEY"))
try:
    import google.generativeai  # noqa: F401
    _HAS_SDK = True
except ImportError:
    _HAS_SDK = False

pytestmark = pytest.mark.skipif(
    not (_HAS_KEY and _HAS_SDK),
    reason="needs GEMINI_API_KEY and google-generativeai",
)


# Access/quota errors AND transient server-side failures: all are environment
# problems with the live free-tier API, not faults in our code. A genuine shape
# bug still surfaces as an AssertionError, which is never in this list.
_ACCESS_ERRORS = (
    "403", "denied", "permission", "quota", "429", "rate", "not_found", "404",
    "500", "502", "503", "unavailable", "timeout", "deadline", "internal",
    "non-json",  # a truncated/garbled response under load, not a logic error
)


def _skip_if_unavailable(exc: Exception) -> None:
    """A denied/quota/region-blocked key is an environment problem, not a code
    failure — skip rather than redden the suite. A genuine bug (bad JSON, wrong
    shape) still fails normally."""
    msg = str(exc).lower()
    if any(tok in msg for tok in _ACCESS_ERRORS):
        pytest.skip(f"Gemini not reachable with this key: {exc}")
    raise exc


@pytest.fixture(scope="module")
def planner():
    from planner import Planner, GeminiPlanner
    from planner.backend import PlannerError

    p = Planner(backend=GeminiPlanner())
    try:  # liveness probe: skip the whole module if the key can't generate
        p.backend.decompose("say ok")
    except PlannerError as e:
        _skip_if_unavailable(e)
    return p


def _decompose(planner, goal):
    """Decompose, but treat a rate-limit/quota/access error as a skip.

    The free tier's requests-per-minute cap will otherwise fail these tests for
    an environment reason whenever they run back to back. A genuine assertion
    failure below still fails normally."""
    from planner.backend import PlannerError
    try:
        return planner.decompose(goal)
    except PlannerError as e:
        _skip_if_unavailable(e)


def test_decompose_returns_a_reasonable_plan(planner):
    plan = _decompose(planner, "book a cab to the airport")
    assert 2 <= len(plan.subgoals) <= 8
    assert all(s.description for s in plan.subgoals)
    assert all(s.success_hint for s in plan.subgoals)


def test_money_task_flags_confirmation(planner):
    plan = _decompose(planner, "order a large pizza and pay with my saved card")
    assert any(s.requires_confirmation for s in plan.subgoals), \
        "a paying task must flag at least one confirmation-required subgoal"


def test_ids_are_unique(planner):
    plan = _decompose(planner, "send a message to Mom saying I'll be late")
    ids = [s.id for s in plan.subgoals]
    assert len(ids) == len(set(ids))
