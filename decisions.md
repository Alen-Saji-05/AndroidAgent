# Decisions

Technical decisions and why they were made. Each entry records what was chosen, what was
rejected, and the reasoning — so a future reader can tell whether a decision still holds
when its assumptions change.

Status: **Accepted** (in force) · **Superseded** · **Open** (not yet settled).

---

## D1 — Accessibility tree is the primary perception path; vision is the fallback

**Accepted** · from project design

The accessibility tree gives a structured, text-based view of on-screen elements with
element roles, labels and states already attached, via public APIs and with no OS
modification. Vision has to infer all of that and gets it wrong sometimes.

Vision exists because a real fraction of apps ship poor or absent accessibility labels,
and an app-agnostic agent cannot simply skip them.

**Implication:** vision output must be *reconcilable* with tree output, which is why both
emit the same `UIElement` shape.

---

## D2 — Vision perception built first, despite being the fallback

**Accepted** · 2026-09-16

Ordering choice, not a priority claim. The vision path takes a PNG and returns JSON, so it
is testable on a laptop with no device, no ADB and no emulator. The accessibility path
needs a connected device before it does anything at all.

Building the harder-to-verify path second, against a schema already proven by the first,
is cheaper than the reverse.

---

## D3 — OmniParser v2 icon detector over SeeClick

**Accepted** · 2026-09-16 · supersedes an earlier plan to use a general vision LLM

SeeClick was proposed for being lightweight. Two problems, either one disqualifying:

1. **It is not lightweight.** SeeClick is a LoRA fine-tune of Qwen-VL — ≈9.6B parameters.
   A server model, not an on-device one.
2. **It does not enumerate.** SeeClick does referring-expression grounding: screenshot
   *plus an instruction* → one click point. Getting a full element list out of it would
   require already knowing what to ask for — which is what the list was meant to tell us.

OmniParser v2's icon detector is ~3.0M params (40MB checkpoint), MIT-licensed, and produces exactly
the required output: a set of interactable regions.

**Not discarded:** SeeClick-class grounding solves the *other* half — "where is the confirm
button?" → (x, y). That is the executor's problem, and worth revisiting there (see **D10**).

**Also rejected:** a general vision LLM (Gemini) with a forced JSON schema. It works and
was the original plan, but it bills per screen, and the control loop captures a screen
after *every* action.

---

## D4 — Detector is class-agnostic; typing is rule-based

**Accepted** · 2026-09-16

The OmniParser detector answers *"is this region interactable?"*, not *"what widget is
this?"*. Element types are assigned in `perception/classify.py` from geometry, screen
position and text keywords.

Rules over a learned classifier, deliberately. The pipeline already contains two opaque
components (detector, OCR); a third would make "why was this typed as an icon?"
unanswerable. A rule is readable, and fixable in one line.

**Revisit if:** rule count outgrows maintainability, or accuracy plateaus below what the
planner needs.

---

## D5 — Fusion assigns text by containment, not IoU

**Accepted** · 2026-09-16

A label inside a button has IoU ≈ 0.08 with it — the text is far smaller than the box, so
their union is dominated by the box. An IoU threshold high enough to be meaningful would
have attached no text to anything, silently.

Containment (fraction of the text run inside the box) is 1.0 for the same pair.

Text goes to the **smallest** box that contains it, because UI boxes nest — a button
inside a card inside a list row — and the tightest container is the one the label belongs
to.

Both properties are pinned by tests.

---

## D6 — OCR runs independently of the detector, and unmatched text is kept

**Accepted** · 2026-09-16

OCR is not run *inside* detected boxes; it runs over the whole screenshot. Text that falls
outside every box — headings, prices, status lines, error messages — becomes a static
`text` element marked `interactable: false`.

That text is not a tap target but it is exactly what the planner and verifier need to read
to decide whether a step succeeded. Discarding it would make the screen state look
complete while omitting the part that carries the meaning.

---

## D7 — Detector thresholds lowered from stock values

**Accepted** · 2026-09-16 · **provisional**

`conf` 0.25 → 0.05, NMS `iou` 0.7 → 0.10, `imgsz` 640 → 1280.

OmniParser ships tuned for desktop captures. Mobile UIs are denser: a 0.25 confidence
floor drops small icons, loose NMS merges adjacent list rows into a single box, and at
640px small icons fall below the detector's minimum feature size.

**Still the least settled decision here.** The values were reasoned from the failure
modes, then sanity-checked against one synthetic 1080x2400 capture — on which they find
every major widget but miss flat, featureless bottom-nav rectangles. That miss may be an
artifact of the synthetic fixture (real nav icons have internal structure) or a real
recall gap. Unresolved until real screenshots exist.

One related threshold *was* measured: `LIST_ROW_MIN_HEIGHT` moved 0.09 → 0.06 after 180px
cards on a 2400px screen were typed as buttons.

---

## D8 — Florence-2 icon captioning omitted

**Accepted** · 2026-09-16

OmniParser's third stage captions unlabelled icons using Florence-2 (0.23B params). It is
~75× the detector's size and runs per icon.

Most planner decisions key off text and position; a described icon is a marginal gain for
a real latency cost in a loop that runs after every action.

**Revisit if:** unlabelled icons turn out to block real tasks.

---

## D9 — OCR backend is pluggable

**Accepted** · 2026-09-16

`perception/ocr.py` defines an `OCRBackend` protocol. PaddleOCR on the dev machine, ML Kit
on-device later, `NullOCR` when nothing is installed.

The on-device backend will differ from the dev one, and that swap should touch one file.
`NullOCR` also keeps the pipeline runnable without an OCR install — elements still come
out, just unlabelled — which keeps contributors unblocked.

---

## D10 — On-device inference deferred

**Open** · 2026-09-16

The perception module runs under Python/ultralytics on a dev machine. ONNX/NCNN export is
not done.

Deferred because export does not change the output schema, so nothing downstream is
blocked by it, and because export decisions are better made against measured on-device
latency than guessed at.

**Open question:** at what point does on-device become a hard requirement rather than a
stated goal? The answer affects whether the executor can assume perception is cheap.

---

## D11 — `state.checked` is `null`, never guessed

**Accepted** · 2026-09-16

Vision frequently cannot tell whether a checkbox is checked. The field is `Optional[bool]`
and stays `None` when undetermined.

Defaulting to `False` would be indistinguishable from a confident reading, and the planner
might act on it — toggling something already in the desired state. An explicit "unknown"
lets the caller decide to look closer or ask.

---

## D12 — RapidOCR replaces PaddleOCR as the default OCR backend

**Accepted** · 2026-09-16 · supersedes the PaddleOCR default in [D9](#d9--ocr-backend-is-pluggable)

PaddleOCR was the assumed backend until it was actually run. Two problems on
Windows + paddlepaddle 3.3.1:

1. **It crashed.** `NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support`
   from the oneDNN executor on an ordinary screenshot. Worked around with
   `enable_mkldnn=False`, at a large speed cost.
2. **Its box coordinates were wrong.** On a controlled single-word test at 1080x2400 the
   returned polygon was off by `(-67, -59)` px, and the error grew with image size. Text
   was *recognised* correctly, but placed wrongly.

The second is the disqualifying one, and it fails quietly. A label displaced by ~100px
falls outside its own button, so fusion attaches it to nothing: the button becomes a blank
`icon` and the label becomes orphan static text. Nothing errors. The JSON looks
well-formed and is simply wrong — the executor would tap a button it believes is
unlabelled.

RapidOCR on the same controlled test: off by `(-3, -14)`. On the fixture, every label
landed inside its widget. End-to-end OCR time also fell from 77.5s to 4.4s.

Secondary benefits: ONNX runtime rather than paddlepaddle (one less heavy native
dependency, and the likelier road to an on-device build per [D10](#d10--on-device-inference-deferred)).

**PaddleOCR is kept as a selectable backend**, since it is what most Android/OCR tutorials
assume — but it is commented out of `requirements-ocr.txt` and not recommended.

**This is the decision [D9](#d9--ocr-backend-is-pluggable) was written for.** The swap
touched one file.

---

## D13 — Dependencies split across three requirements files

**Accepted** · 2026-09-16

`requirements.txt` (core: detector + JSON), `requirements-ocr.txt` (optional OCR),
`requirements-dev.txt` (core + pytest).

The core install pulls torch and is large. OCR is genuinely optional — `NullOCR` keeps the
pipeline producing elements without it — so it should not be mandatory, and the D12 swap
showed the OCR dependency is the one most likely to churn. Keeping it separate means a
backend change does not touch the core install.
