"""Run a full task against a connected device over ADB.

    .venv/Scripts/python.exe -m executor.cli "open settings and turn on wifi"

Ties the whole system together: ADB screencap -> perception -> planner (Gemini)
-> grounding -> ADB input -> verify. Needs a device with USB debugging on, adb
on PATH (or ANDROID_HOME set), and the perception weights + a Gemini key for the
real thing. Sensitive subgoals prompt for confirmation on the terminal.

This is the dev harness. It is intentionally cautious: it prints each grounded
action and, by default, pauses for confirmation on sensitive steps.
"""

from __future__ import annotations

import argparse
import logging
import sys

# Load .env (GEMINI_API_KEY, GEMINI_MODEL, ADB_PATH) so the CLI works with no flags.
try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True), override=False)
except ImportError:
    pass

from .adb import ADBError, SubprocessADB, find_adb


def _confirm(subgoal) -> bool:
    print(f"\n  [CONFIRM] sensitive step {subgoal.id}: {subgoal.description}")
    try:
        return input("  proceed? [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="executor", description="Run a task on a device via ADB.")
    ap.add_argument("goal", help="natural-language task, e.g. 'open settings and turn on wifi'")
    ap.add_argument("--serial", help="target device serial (adb -s)")
    ap.add_argument("--adb-path", help="full path to adb.exe if it is not on PATH")
    ap.add_argument("--weights", default="weights/icon_detect.pt")
    ap.add_argument("--max-steps", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true",
                    help="ground and print actions but do NOT send them to the device")
    ap.add_argument("--yes", action="store_true",
                    help="auto-approve sensitive steps (DANGEROUS; off by default)")
    ap.add_argument("--save-screens", metavar="DIR", help="save each captured screenshot here")
    ap.add_argument("--grounder", choices=["auto", "llm", "heuristic"], default="auto",
                    help="how to pick actions: 'llm' (Grok), 'heuristic', or 'auto' "
                         "(Grok if XAI_API_KEY is set, else heuristic)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    adb_path = args.adb_path or find_adb()
    if not adb_path:
        print("error: adb not found. Pass --adb-path, install platform-tools, or set ANDROID_HOME.",
              file=sys.stderr)
        return 2
    try:
        adb = SubprocessADB(serial=args.serial, adb_path=adb_path)
        devices = adb.devices()
    except ADBError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not devices:
        print("error: no device. Connect one with USB debugging enabled (`adb devices`).",
              file=sys.stderr)
        return 2
    print(f"device(s): {', '.join(devices)}")

    # Heavy imports deferred until we know a device is present.
    from perception import ScreenParser
    from planner import Planner
    from .executor import Executor
    from .loop import AgentLoop, deny
    from . import adb_perceive

    if args.dry_run:
        class _NoSend(Executor):
            def execute(self, action):
                from .actions import Wait
                if not isinstance(action, Wait):
                    print(f"  [DRY-RUN] would execute: {action.describe()}")
        executor = _NoSend(adb, settle_seconds=0)
    else:
        executor = Executor(adb)

    perceive = adb_perceive(adb, ScreenParser(detector=_detector(args.weights)),
                            save_dir=args.save_screens)
    confirm = (lambda _s: True) if args.yes else _confirm

    from .grounder import GroqGrounder, HeuristicGrounder, default_grounder
    if args.grounder == "llm":
        grounder = GroqGrounder()
    elif args.grounder == "heuristic":
        grounder = HeuristicGrounder()
    else:
        grounder = default_grounder()
    print(f"grounder: {type(grounder).__name__}")

    loop = AgentLoop(Planner(), executor, perceive, grounder=grounder,
                     confirm_cb=confirm, max_steps=args.max_steps)
    result = loop.run(args.goal)

    print("\n--- transcript ---")
    for r in result.steps:
        print(f"  {r.step:>2}. [{r.subgoal_id}] {r.subgoal}")
        print(f"      action: {r.action or '(none)'}   -> {r.update}")
        if r.note:
            print(f"      note:   {r.note}")
    print(f"\nended: {result.stopped} | plan: {result.plan.status.value} | steps: {len(result.steps)}")
    return 0 if result.stopped in ("done",) else 1


def _detector(weights):
    from perception.detector import DetectorConfig, IconDetector
    return IconDetector(weights=weights, config=DetectorConfig())


if __name__ == "__main__":
    raise SystemExit(main())
