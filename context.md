# Project Context

## Project
**Autonomous AI Agent Layer for Android (AOSP)** — Group 17

An on-device AI agent that turns natural-language commands (voice or text) into real
actions on an Android phone, operating existing apps the way a human would, without
app-specific API integrations.

**Team:** Alan Shaji, Alen Saji Skariah, Allen Issac Issac, Athul K K
**Guide:** Ms. Janani K

## Problem
AOSP has no native on-device agent layer that understands natural-language intent and
acts on it. Every app operates in isolation, leaving the user as the sole coordinator
between intent, interface, and task completion. Multi-step tasks (e.g. booking a flight:
search → compare → fill details → confirm) stay slow, repetitive, and error-prone.

Existing options fall short:
- **Voice assistants** (Google Assistant) can open apps or run a search, but can't read
  the screen or complete multi-step forms.
- **Automation frameworks** rely on rigid pre-scripted actions that break when a UI changes.

## What It Does
Takes a command like *"Book a cab to the airport"* and:
1. Understands the intent
2. Plans the steps
3. Executes them by reading the screen and performing taps / swipes / typing
4. Verifies each step, asking for user approval on sensitive actions (payments, OTPs)

## Architecture — Multi-Agent Pipeline
- **Planner Agent** — decomposes natural-language intent into a sequence of sub-tasks /
  screen-level goals.
- **Executor Agent** — translates each sub-task into concrete UI actions (tap, scroll,
  type, swipe) issued via ADB.
- **Verifier Agent** — checks post-action screen state against the expected outcome and
  triggers re-planning on failure.
- **Shared state/memory module** — passes task context and screen history between agents.

### Screen Perception & Grounding
- Accessibility-tree extraction (ADB `uiautomator` / AccessibilityService) for a
  structured, text-based view of on-screen elements — **no OS modification required**.
- Element grounding: LLM-based matching of natural-language references ("the confirm
  button") to accessibility-tree nodes.
- Fallback vision-based grounding (screenshot + coordinate prediction) for apps with poor
  accessibility labeling.
- State representation = tree hierarchy + bounding boxes + text content.

### Action Execution & Control Loop
- Action space: tap, long-press, swipe, type, back, scroll — issued as ADB shell commands.
- Closed loop: execute → capture new state → verify → replan on deviation.
- Termination: task-success detection, max-step limit, user-intervention fallback.
- Logging/replay module for debugging and evaluating multi-app workflows.

## System Features
**Core**
- Natural-language task execution across apps from a single command
- Screen perception via accessibility tree (no OS modification)
- Autonomous multi-step navigation (opens apps, taps, scrolls, fills forms)

**Intelligence & reliability**
- Multi-agent reasoning pipeline (planner / executor / verifier)
- Self-verification and error recovery via re-planning
- App-agnostic — no pre-scripted, app-specific rules

**User experience**
- No manual screen-by-screen input
- On-device operation on AOSP, without cloud-dependent OS-level changes

## Scope
**In scope**
- Voice/text command understanding
- Task planning and step-by-step execution
- Screen perception (Accessibility Service + OCR/UI parsing)
- Action execution: tap, type, scroll, swipe
- Verification, retry, and user confirmation for sensitive actions
- Support for select apps (Maps, cab booking, shopping, forms)

**Out of scope**
- Direct API integrations with apps
- Fully autonomous high-risk actions (no human check)
- Multi-device / cloud sync
- Background execution without a user command

## Tech Stack
| Area | Tools |
|---|---|
| OS / dev env | Ubuntu 22.04, Windows 11 |
| IDEs | Android Studio, VS Code |
| Languages | Kotlin, Java, Python |
| Android | Android SDK, ADB, Gradle, Accessibility Service, UIAutomator |
| ML | PyTorch, OpenCV, ultralytics (YOLOv8) |
| Perception | OmniParser v2 icon detector, RapidOCR + ONNX (dev) / ML Kit (on-device) |
| LLM | Gemini API / Llama |
| Storage | SQLite / Room |
| VCS | Git & GitHub |

Refined from the abstract during implementation:

- **TensorFlow is unused** — PyTorch only, via ultralytics.
- **OCR is RapidOCR (ONNX)**, not PaddleOCR. PaddleOCR was the assumed choice until it was
  measured: its box coordinates were off by tens of pixels on mobile-resolution captures,
  enough to detach a label from its own button. ([D12](decisions.md))
- **ML Kit** remains the intended on-device OCR; the backend is pluggable so the swap is
  one file. ([D9](decisions.md))

## Implementation Status

| Component | Status |
|---|---|
| Screen perception — vision path | **Built, verified end-to-end** ([perception/](perception/README.md)) |
| Screen perception — accessibility tree | Not started |
| Planner agent | **Built** (decompose + reassess); no control loop yet ([planner/](planner/README.md)) |
| Executor agent | Not started |
| Verifier agent | Not started |
| Shared state / memory | Not started |
| Android app | Not started |

The vision path takes a screenshot and emits JSON describing every detected UI element
(type, text, bounds, tap centre, interactability). It runs standalone on a dev machine —
no device or ADB required — which is what makes the rest testable before hardware is in
the loop.

Verified against a 1080x2400 capture: all major widgets detected, every label correctly
fused to its control, 39 tests passing.

**Caveats worth carrying forward.** The only fixture so far is *synthetic* (drawn with
OpenCV), so every threshold is tuned against an artificial screen. Cold-run latency is
~9s on CPU — fine for development, far too slow for a loop that perceives after every
action. Neither blocks the next component; both block a usable product.

### Running it

```bash
.venv/Scripts/python.exe -m perception.cli screen.png -o out.json --overlay out.png
```

Setup is in the [README](README.md#quick-start); the module reference is
[perception/README.md](perception/README.md).

See [log.md](log.md) for the work log, [history.md](history.md) for how the project got
here, and [decisions.md](decisions.md) for why things are the way they are.

## References
1. H. Wen et al., "AutoDroid: LLM-powered task automation in Android," *MobiCom '24*, pp. 543–557.
2. C. Zhang et al., "AppAgent: Multimodal agents as smartphone users," arXiv:2312.13771, 2023.
3. J. Wang et al., "Mobile-Agent: Autonomous multi-modal mobile device agent with visual perception," arXiv:2401.16158, 2024.
4. Y. Li et al., "AppAgent v2: Advanced agent for flexible mobile interactions," arXiv:2408.11824, 2024.
5. Y. Qin et al., "UI-TARS: Pioneering automated GUI interaction with native agents," arXiv:2501.12326, 2025.

---
*Source: `Abstract Presentation Group 17_20260702_091626_0000.pdf`*
