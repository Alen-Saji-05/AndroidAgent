# Executor + Phase 0 control loop

Drives a **real device over ADB** to run a task end to end:
`perceive → plan → ground → (confirm) → execute → verify`. This is the **dev
harness** — the fastest way to prove the whole loop works before building the
Android app. It reuses `perception/` and `planner/` unchanged.

## Why ADB here, AccessibilityService later

The design docs say actions are "issued as ADB shell commands." That works for a
computer driving a phone over USB — it is **not** how the shippable app will
work, because an app cannot `adb shell` itself. On-device execution (no root, no
OS modification) goes through **AccessibilityService** instead.

Phase 0 uses ADB deliberately: it makes the loop runnable today, on the existing
Python code, with no Android project. The [Action](actions.py) model is the same
either way, so when execution moves on-device only the bottom layer changes —
the planner, grounding, and loop never learn they moved. See decisions D18.

## Pieces

| File | Role |
|---|---|
| `adb.py` | ADB wrapper (`SubprocessADB`) + `FakeADB` for tests; `screencap`, `shell` |
| `actions.py` | `Tap/LongPress/Swipe/TypeText/KeyPress/Wait` → ADB `input` args |
| `executor.py` | `Executor.execute(action)` — issue the command, let the UI settle |
| `grounding.py` | subgoal + screen → `Action` (keyword heuristic; LLM later) |
| `loop.py` | `AgentLoop` — the control loop, with the confirmation gate |
| `cli.py` | `python -m executor.cli "…"` against a connected device |

## The loop

```
plan = planner.decompose(goal)
while not done:
    if planner.needs_user_confirmation(plan):   # sensitive step
        if not confirm(): break                 # gate: nothing executes until approved
        planner.confirm(plan)
    screen = perceive()                          # adb screencap -> perception
    action = ground(active_subgoal, screen)      # or None -> ask planner to revise
    executor.execute(action)                     # adb input ...
    planner.reassess(plan, perceive(), ...)      # advance / retry / revise / done
```

Everything is injected (`planner`, `executor`, `perceive`), so the whole loop
runs on `FakeADB` + `NullPlanner` + a stub `perceive` — no device, no network,
no torch. That is how the tests exercise it.

## Grounding: heuristic or LLM

Grounding — subgoal + screen → Action(s) — is a pluggable `Grounder` (grounder.py):

- **`GroqGrounder`** (the LLM Executor): hands the model the subgoal and a labelled
  element list (id, type, text, top/mid/bottom position) and gets back a structured
  action. It grounds by *understanding* the screen — picking the real search box over
  the bottom "Search" nav tab, or the correct song row — which keyword matching
  cannot. Uses Groq (`openai/gpt-oss-120b`, ~0.8s/call). See D21.
- **`HeuristicGrounder`**: the original keyword matcher. No network. Handles
  "open app / search" and is the offline/test fallback.

Selected by `default_grounder()` — Groq when `GROQ_API_KEY` is set, else heuristic —
or forced with `--grounder llm|heuristic`.

App launching is separate and cheap: an "open <app>" subgoal resolves to a launcher
intent (D20) before any screenshot, for either grounder.

## Run it (needs a device)

```bash
.gent.ps1 "open spotify and play the song tv off"
```

The wrapper reads adb path, the Gemini key (planner) and the Groq key (grounder)
from `.env`. Flags: `--grounder llm|heuristic|auto`, `--dry-run` (ground and print,
send nothing), `--serial`, `--save-screens DIR`, `--max-steps N`, `-v`. Sensitive
steps prompt on the terminal; `--yes` auto-approves (dangerous, off by default).

The loop **wakes the device** first; a non-secure lock is dismissed, a PIN lock
still needs you (never bypassed). Without a device the CLI exits cleanly.

## Tests

```bash
.venv/Scripts/python.exe -m pytest tests/test_executor.py -q
```

18 tests: action→ADB mapping, `Executor`, grounding (match/back/scroll/type,
read-only text ignored), and the loop (runs to completion, the gate blocks until
confirmed, ungroundable subgoals go back to the planner not the device, transcript
recorded). All on fakes — no device.

## Verified on hardware

`open chrome and search for pizza`, `open spotify and search for kendrick lamar`,
and `open spotify and play the song tv off` all complete on a real phone with the
LLM grounder.

## Not done

- **Text-digest grounding only** — no vision. Bare unlabelled icons (outside the
  app-launch shortcut) still cannot be grounded; a vision model or the accessibility
  tree is the fix.
- **Verifier is a keyword hint**, not a real check. The real Verifier is a later piece.
- **On-device app** — this is still the ADB harness (D18). The Kotlin
  AccessibilityService app is the next phase.
