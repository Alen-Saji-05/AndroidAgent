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

## Phase 4 — The Planner, and the first LLM in the loop

**2026-09-17.** With perception producing a stable element list, work moved to the
**Planner** — the component that turns "book a cab to the airport" into an ordered list of
subgoals. It is the project's first LLM-dependent piece, which changed the shape of the
work: nondeterministic output, an API dependency, and prompt engineering, none of which
perception had.

The central design question was answered before any code: **do you plan the whole task
upfront, or decide each step reactively?** Both fail. A static script breaks when the real
UI differs from the guess; pure reactivity has no spine and loops. The Planner does neither
— a coarse decomposition once, then reassessment against the live screen after every step
([D14](decisions.md)). A scripted end-to-end walk exercised the hard case: a login prompt
appears mid-task, the plan revises its tail while keeping already-satisfied subgoals
immutable, and execution continues.

Two decisions carried the project's constraints into the new component. The
**confirmation gate for sensitive actions is enforced in code, not trusted to the model**
([D15](decisions.md)) — the model may flag a payment step, but only the loop's own check
lets it proceed, so the human-in-the-loop rule is structural. And the **screen text fed to
the planner is treated as untrusted data** ([D16](decisions.md)): an app can render a
sentence aimed at hijacking the agent, so this is the project's first prompt-injection
surface, and the confirmation gate doubles as its backstop.

Two patterns were lifted wholesale from perception because they had already proven out: a
**pluggable backend** with a no-network stub for tests (`NullPlanner`, the analogue of
`NullOCR`), and **forced-JSON output**. Gemini Flash was chosen over Pro because the
reassess loop calls the model every step, so speed and cost dominate ([D17](decisions.md)).
The result shipped with 26 model-free tests driven by scripted responses — the logic tested
without a key or a network, exactly as perception's stubs allowed.

## Phase 5 — The loop closes: Phase 0 executor over ADB

**2026-09-26.** Perception and the Planner existed but nothing connected them. Phase 0
built `executor/` — a control loop that captures the screen, plans, grounds a subgoal into
a concrete gesture, executes it, and reassesses — driving a real device over **ADB**.

Building it surfaced a conflation the design docs had carried from the start. They say
actions are "issued as ADB shell commands," which quietly assumes a computer driving a
phone. An app cannot ADB itself; on-device execution without root or OS modification is the
**AccessibilityService** API. So there are two execution surfaces, not one: ADB is the dev
harness (fast, reuses all the Python, not shippable), and AccessibilityService is the real
on-device path — and conveniently the same service is the primary perception path too. The
`Action` model spans both, so the planner, grounding, and loop are indifferent to which
issues the gesture ([D18](decisions.md)). Choosing to prove the loop over ADB first was the
Phase 0 recommendation, and it paid off: a full run works end to end on stubs, with the
confirmation gate firing on the sensitive step alone before anything executes.

Grounding — subgoal to gesture — is a keyword heuristic for now, not an LLM, to keep a
second model call out of the loop while it is proven ([D19](decisions.md)). Building it
turned up a small but real bug: a button labelled "Go" could never be matched because the
word was a stopword stripped from both sides. The fix — filler words versus action verbs —
is the kind of thing only a running loop reveals.

## Phase 6 — The LLM Executor, and a working agent

**2026-09-26.** Phase 0's keyword grounder proved the loop but hit its ceiling fast: each
real task on a real phone exposed a new phrasing or a new screen it mis-read. The fix,
always the plan, was the **LLM Executor** — ground by understanding the screen rather than
matching words ([D21](decisions.md)).

`GroqGrounder` hands the model the subgoal and a labelled element list and gets back a
structured action. It runs on Groq (`openai/gpt-oss-120b`, ~0.8s a call — the low latency
matters when you call it every step) and slots behind the same `Grounder` interface, with
the heuristic kept as the offline fallback. The user asked for "grok"; the key was a Groq
key — a different service, and a good one for this.

But the LLM grounder alone did not make the hard task work. Getting "play the song TV Off"
to complete took four control-loop fixes ([D22](decisions.md)), and every one came from
watching a real run fail: the phone was **asleep** (the agent was driving a lock screen);
"open Spotify" **relaunched** the app every retry, resetting it; launching never
**advanced** the subgoal; and a multi-step subgoal was **blocked** after four steps even
while it was visibly progressing. None of these were visible to the stub tests — they only
appear against real hardware, which is the running theme of this project.

With those in place, the agent opened Spotify, navigated to search, typed "TV Off", and
tapped the correct song — the model picking the real search box that perception had
mislabelled as a button, and the right row among many. The song played.

## Where things stand

The agent now runs real tasks end to end. Perception emits a stable `UIElement` schema; the
Planner consumes it (via a compact screen digest) and produces a revisable subgoal plan.
Both were built the same way — pluggable backend, stub-driven tests, JSON contracts — and
that discipline is now the house style rather than a one-off.

The gap between "built" and "working" is now explicit: there is **no control loop**. The
Planner's `decompose` and `reassess` exist and are unit-tested, but nothing yet drives
perceive → reassess → execute → verify, because the Executor and Verifier do not exist. The
Planner's `reassess` is therefore unverified against a real model end-to-end.

Carried forward from earlier phases: perception thresholds are still tuned against one
synthetic screen ([D7](decisions.md)); on-device inference is deferred for both the detector
and the LLM ([D10](decisions.md)); and cold-run perception latency of ~9s on CPU is far too
slow for a loop that perceives after every action. None block building the next component;
all block a usable product.
