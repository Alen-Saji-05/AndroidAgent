"""Eyeball a decomposition from the command line.

    python -m planner.cli "book a cab to the airport"

Uses Gemini when GEMINI_API_KEY is set, otherwise NullPlanner (a one-step echo
plan) so the command still runs offline. This only shows the INITIAL plan;
reassess needs a live ScreenState from the perception module and the loop.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .backend import GeminiPlanner, NullPlanner, PlannerError, default_backend
from .planner import Planner


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="planner", description="Decompose an instruction into subgoals.")
    ap.add_argument("goal", help="natural-language instruction, e.g. 'book a cab to the airport'")
    ap.add_argument("--context", help="optional context (installed apps, home screen digest)")
    ap.add_argument("--null", action="store_true", help="force the offline NullPlanner")
    ap.add_argument("--model", help="override the Gemini model id")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.null:
        backend = NullPlanner()
    elif args.model:
        backend = GeminiPlanner(model=args.model)
    else:
        backend = default_backend()

    try:
        plan = Planner(backend=backend).decompose(args.goal, args.context)
    except (PlannerError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(plan.to_json())
    conf = [s.id for s in plan.subgoals if s.requires_confirmation]
    if conf:
        print(f"\n# subgoals needing user confirmation: {', '.join(conf)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
