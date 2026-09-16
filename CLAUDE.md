# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

**Autonomous AI Agent Layer for Android (AOSP)** — an on-device agent that turns a
natural-language command ("Book a cab to the airport") into real UI actions on a phone,
driving existing apps the way a human would. No app-specific API integrations.

See [context.md](context.md) for the full project description, scope, and references.

## Current State

Only the **screen perception** module exists so far (`perception/`) — the vision-based
grounding path. No planner, executor, verifier, or Android app yet.

The project uses a virtualenv at `.venv/`. **Always invoke it explicitly** — a bare
`python` hits the system interpreter, which has none of the dependencies.

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt -r requirements-ocr.txt
```

```bash
.venv/Scripts/python.exe -m perception.download_weights
```

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

```bash
.venv/Scripts/python.exe -m perception.cli screen.png -o out.json --overlay out.png
```

On macOS/Linux the interpreter is `.venv/bin/python`. Requirements are split three ways:
`requirements.txt` (core), `requirements-ocr.txt` (optional OCR), `requirements-dev.txt`
(core + pytest).

See [perception/README.md](perception/README.md) for details.

## Architecture (as designed)

Three agents in a closed loop, coordinated through a shared state/memory module:

- **Planner** — decomposes natural-language intent into ordered sub-tasks / screen-level goals.
- **Executor** — translates each sub-task into concrete UI actions (tap, long-press, swipe,
  type, back, scroll), issued as ADB shell commands.
- **Verifier** — compares the post-action screen state against the expected outcome; on
  mismatch, triggers re-planning rather than failing.

Loop: `execute → capture new state → verify → replan on deviation`.
Terminates on task-success detection, a max-step limit, or user-intervention fallback.

### Screen perception
Two paths, same `UIElement` output shape so they can be reconciled later.

Primary path is the **accessibility tree** (ADB `uiautomator` / AccessibilityService) —
a structured, text-based view of on-screen elements. Vision-based grounding (screenshot +
coordinate prediction) is the **fallback** for apps with poor accessibility labeling, not
the default — implemented in `perception/` using the OmniParser v2 YOLOv8 icon detector
(~3M params) plus OCR. State passed to the planner combines tree hierarchy + bounding
boxes + text.

Note the detector is class-agnostic: it finds *interactable regions*, and element typing
is rule-based in `perception/classify.py`. Keep it rule-based — a learned classifier there
would add an opaque failure mode to a pipeline that already has two.

## Working Constraints

These are project-defining decisions, not preferences — don't design around them:

- **No OS modification.** Everything runs through public ADB / accessibility APIs. Do not
  propose AOSP framework patches or system-image changes.
- **No per-app API integrations.** The agent must stay app-agnostic; no hardcoded selectors
  or app-specific automation scripts. If a task needs an app-specific hack to work, that's
  a signal the grounding layer needs improving.
- **Human-in-the-loop for sensitive actions.** Payments, OTPs, and other high-risk steps
  always require explicit user confirmation. Fully autonomous high-risk execution is
  explicitly out of scope.
- **On-device operation.** No cloud-dependent OS-level changes. (Remote LLM inference via
  the Gemini API is in the stack; OS-level cloud dependency is not.)
- **Foreground only.** The agent acts in response to a user command — no background
  execution.

Also out of scope: multi-device / cloud sync.

## Tech Stack

- **Languages:** Kotlin, Java (Android side), Python (agent / ML side)
- **Android:** Android SDK, ADB, Gradle, AccessibilityService, UIAutomator
- **ML/CV:** PyTorch / TensorFlow, OpenCV, ML Kit (OCR)
- **LLM:** Gemini API / Llama
- **Storage:** SQLite / Room
- **Dev env:** Ubuntu 22.04 or Windows 11; Android Studio, VS Code

## Conventions

- Every action the executor takes should be logged in a form the replay module can consume
  — debugging multi-app workflows depends on it.
- Prefer accessibility-tree grounding; reach for vision only when the tree is inadequate,
  and say so explicitly in code comments when you do.
- **Verify perception changes visually.** Run with `--overlay` and look at the image.
  Coordinate bugs in this pipeline fail silently — a mislocated OCR box produces
  well-formed JSON that is simply wrong, and no test-free eyeballing of the JSON will
  catch it. This is how the PaddleOCR defect was found (decisions.md D12).
- OCR backends are pluggable and interchangeable; do not hardcode one. RapidOCR is the
  default on measured box accuracy.
- Target apps for now: Maps, cab booking, shopping, and generic forms.
