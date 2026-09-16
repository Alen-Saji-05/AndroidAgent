# History

How the project got to where it is. Milestones and turning points, not a day-by-day
account — that is [log.md](log.md). Reasoning behind specific technical choices lives in
[decisions.md](decisions.md).

---

## Phase 0 — Problem framing

The starting observation: AOSP smartphones handle everything — communication, banking,
shopping, travel — yet every multi-step task is still manually coordinated by the user,
screen by screen. Booking a flight is `search → compare → fill details → confirm` across
several screens, each step a tap.

Two existing categories of solution were examined and neither closed the gap:

- **Voice assistants** (Google Assistant) can open an app or run a search, but cannot read
  the screen or complete a form.
- **Automation frameworks** replay pre-scripted actions, which break as soon as an
  interface changes.

The gap was named precisely: AOSP has no native on-device agent layer that understands
natural-language intent and acts on it. Without one, apps stay isolated and the user
remains the sole coordinator between intent, interface and completion.

## Phase 1 — Architecture and scope

The design settled on a **three-agent closed loop** — planner decomposes intent, executor
issues UI actions via ADB, verifier checks the resulting screen and triggers replanning on
mismatch — with a shared state/memory module carrying context and screen history between
them.

Screen understanding was designed as **accessibility-tree-first with vision as fallback**
(see [D1](decisions.md)), a choice that also fixed the no-OS-modification constraint: both
paths use public APIs.

Scope boundaries were drawn deliberately tight. Out: direct API integrations, fully
autonomous high-risk actions, multi-device sync, background execution. The exclusions
matter as much as the inclusions — *no per-app integrations* is what forces the perception
layer to be general rather than a pile of special cases, and *human confirmation for
payments and OTPs* is a safety property, not a feature that can be traded away for
convenience.

Prior art was surveyed: AutoDroid, AppAgent (v1 and v2), Mobile-Agent, UI-TARS.

## Phase 2 — First implementation: screen perception

**2026-09-16.** Implementation began with the **vision** perception path, despite it being
the designed *fallback*. The reason was tractability, not priority: the vision path takes
a PNG and returns JSON, so it can be built and tested on a laptop with no device, no ADB
and no emulator ([D2](decisions.md)).

The model choice went through one full reversal:

1. **Vision LLM with a forced JSON schema** was planned first. Workable, but it bills per
   screen — and the control loop captures a screen after *every* action.
2. **SeeClick** was proposed as a lightweight alternative, and rejected on investigation
   for two independent reasons: at ≈9.6B parameters it is not lightweight, and it performs
   referring-expression grounding (instruction → one point) rather than enumeration, so it
   structurally cannot produce a full element list.
3. **OmniParser v2's icon detector** was adopted: ~3.0M params, MIT-licensed, and
   producing exactly the needed output. ([D3](decisions.md))

The detector turned out to be class-agnostic — it finds interactable regions without
naming them — which pushed element typing into a separate rule-based stage
([D4](decisions.md)) and made the fusion step, where detector boxes meet OCR text, the
place where most output quality is decided ([D5](decisions.md), [D6](decisions.md)).

The module shipped with 28 tests, all runnable without weights or torch via stub
detectors, and a debug overlay renderer.

## Phase 3 — Bring-up, and the first silent bug

**2026-09-16.** The module was moved into a virtualenv and run against the real detector
and real OCR for the first time — until then only stub detectors had been exercised.

The detector worked. On a 1080x2400 cab-booking screen it found the back arrow, title,
both input fields, all three ride cards and the confirm button.

The OCR did not, and the way it failed is the lesson of this phase. PaddleOCR *recognised*
every string correctly, but returned box coordinates displaced by tens of pixels — enough
that `Confirm Booking` landed below the button it belonged to. Fusion then attached it to
nothing: the button came back as a blank `icon`, the label as free-floating text.

**No exception was raised. The JSON was well-formed and wrong.** An executor consuming it
would have tapped a button it believed was unlabelled. This is the characteristic failure
mode of a perception pipeline — not a crash, but a confident and incorrect picture of the
screen — and it was caught only by rendering the boxes back onto the image and looking.
The debug overlay stopped being a convenience at that point.

Diagnosis narrowed it to PaddleOCR rather than the parsing code via a controlled
single-word test: `(-67, -59)`px error, growing with image size. RapidOCR on the identical
test was off by `(-3, -14)`, and was ~17x faster end-to-end. The backend was swapped
([D12](decisions.md)) — a one-file change, which was the entire point of having made the
OCR layer pluggable ([D9](decisions.md)) before there was any second backend to justify it.

Testing was extended with integration tests that run the real detector and assert
coordinate sanity — tap centres inside their own boxes, labels fused rather than orphaned
— with explicit regressions for both bugs found. **39 tests**, of which 28 still run
anywhere without weights.

## Where things stand

One of seven components is built and verified end-to-end. Perception emits a stable
`UIElement` schema that the accessibility path is expected to match, so the two can later
be reconciled rather than compete.

What Phase 3 changed about the risk picture: the schema and the fusion logic held up under
real data, and the pluggable-backend bet paid off immediately. What it did not resolve is
**tuning confidence** — the only fixture is synthetic, so every threshold is calibrated
against a screen drawn with OpenCV rather than captured from a device
([D7](decisions.md)). Real screenshots are the next meaningful step, and they will likely
move several constants.

Also outstanding: on-device export remains deferred ([D10](decisions.md)), and cold-run
latency of ~9s on CPU is far too slow for a control loop that perceives after every
action. Neither blocks building the planner; both block a usable product.
