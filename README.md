# Autonomous AI Agent Layer for Android (AOSP)

An on-device agent that turns a plain-language command — *"Book a cab to the airport"* —
into real actions on a phone, driving existing apps the way a human would.

No app-specific API integrations. No OS modification. The agent reads the screen, plans
the steps, taps and types, checks the result, and asks before it does anything
irreversible.

**Group 17** · Alan Shaji · Alen Saji Skariah · Allen Issac Issac · Athul K K
**Guide:** Ms. Janani K

---

## Why

Booking a flight today means `search → compare → fill details → confirm`, across several
screens, all of it manual. Voice assistants can open an app or run a search but can't read
the screen or finish a form. Automation frameworks use pre-scripted steps that break the
moment a UI changes.

The gap is an agent layer that understands intent and acts on it — so the user states a
goal instead of coordinating every screen.

## How

Three agents in a closed loop:

```
  "Book a cab to the airport"
             │
             ▼
     ┌───────────────┐
     │    PLANNER    │  intent → ordered sub-tasks
     └───────┬───────┘
             ▼
     ┌───────────────┐
     │   EXECUTOR    │  sub-task → tap / type / swipe (ADB)
     └───────┬───────┘
             ▼
     ┌───────────────┐
     │   VERIFIER    │  screen matches expectation?
     └───────┬───────┘
             │ no → replan
             ▼
           done
```

Screen state comes from the **accessibility tree** where apps expose one, and from a
**vision** path where they don't.

## Status

Early. The vision perception path is built and tested; the agents are not.

| Component | Status |
|---|---|
| Screen perception — vision | **Built & verified end-to-end** — [perception/](perception/README.md) |
| Screen perception — a11y tree | Not started |
| Planner | **Built** (decompose + reassess) — [planner/](planner/README.md) |
| Executor / Verifier | Not started |
| Shared state & memory | Not started |
| Android app | Not started |

## Quick start

Perception module only — runs on a dev machine, no phone required.

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt -r requirements-ocr.txt
```

On macOS/Linux use `.venv/bin/python` throughout.

```bash
.venv/Scripts/python.exe -m perception.download_weights
```

```bash
.venv/Scripts/python.exe -m perception.cli screen.png -o out.json --overlay out.png
```

`out.json` lists every detected element with its type, text, bounds and tap centre.
`out.png` draws them on the screenshot — look at it, it is far faster than reading
coordinates.

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

39 perception tests — 28 run anywhere, 11 need the detector weights and skip without them.

### Planner

Turns an instruction into an ordered list of subgoals. Uses Gemini Flash when
`GEMINI_API_KEY` is set, otherwise a no-network fallback so it still runs.

```bash
.venv/Scripts/python.exe -m pip install -r requirements-planner.txt
```

```bash
.venv/Scripts/python.exe -m planner.cli "book a cab to the airport"
```

The full suite is 65 tests (39 perception + 26 planner); 3 more integration
tests run only with an API key.

## Constraints

Project-defining, not preferences:

- **No OS modification** — public ADB and accessibility APIs only.
- **No per-app integrations** — app-agnostic, no hardcoded selectors.
- **Human confirmation for sensitive actions** — payments, OTPs. Always.
- **Foreground only** — the agent acts on a command, never in the background.

## Docs

| | |
|---|---|
| [context.md](context.md) | Project scope, features, requirements |
| [history.md](history.md) | How the project got here |
| [decisions.md](decisions.md) | Technical decisions and their rationale |
| [log.md](log.md) | Dated work log |
| [CLAUDE.md](CLAUDE.md) | Guidance for AI coding assistants |
| [perception/README.md](perception/README.md) | Perception module reference |
| [planner/README.md](planner/README.md) | Planner module reference |

## References

AutoDroid (MobiCom '24) · AppAgent (arXiv:2312.13771) · Mobile-Agent (arXiv:2401.16158) ·
AppAgent v2 (arXiv:2408.11824) · UI-TARS (arXiv:2501.12326) — full citations in
[context.md](context.md).
