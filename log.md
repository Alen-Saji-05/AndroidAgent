# Work Log

Dated, append-only. Newest first. Milestones are summarised in
[history.md](history.md); rationale lives in [decisions.md](decisions.md).

---

## 2026-09-16 — First real screenshot; published to GitHub

Ran the pipeline against a real capture (Spotify onboarding, 500x1080) rather than the
synthetic fixture.

**Results were good.** All four CTA buttons boxed tightly and fused with correct labels
(`Sign up free`, `Continue with Google/Facebook/Apple`), tap centres accurate. Status bar,
back link and the wifi/battery cluster all found.

**One real bug — a false negative on interactability.** The `Log in` link at the bottom
was not boxed by the detector, so OCR picked it up as an orphan and [D6](decisions.md)'s
rule ("text outside any box is a static label") typed it `text`, `interactable: false`.
An executor reading that JSON would conclude the screen offers no way to log in.

This is worse than a missed decoration: it silently removes an action from the planner's
option set. Fix direction is narrow — `_ACTION_WORDS` in `classify.py` already holds the
vocabulary, it is simply not consulted for `source == "ocr"` elements. **Open.**

Also noted: album art and the circular play control undetected; detection took 7.5s on a
500x1080 image versus 4.8s on the larger 1080x2400 fixture, which is backwards and
probably the `imgsz=1280` upscale.

**Fixture near-miss.** The synthetic `sample_cab.png` had been overwritten, so the 11
integration tests were *skipping silently* — the suite reported 28 passed with no failure
while the docs claimed 39. Caught during the pre-push review. Regenerated the fixture and
added `tests/fixtures/make_sample_cab.py` so it is reproducible rather than an
unreproducible binary.

**Published** to `github.com/Alen-Saji-05/AndroidAgent` (public). `.venv/`, `weights/`
and `out/` excluded.

---

## 2026-09-16 — venv, real end-to-end run, OCR backend replaced

Moved the project into a virtualenv at `.venv/` and ran the pipeline against the real
detector for the first time. Three things came out of it.

**1. Environment**

- `.venv` created (Python 3.10.5); requirements split three ways ([D13](decisions.md)):
  `requirements.txt` (core), `requirements-ocr.txt` (optional), `requirements-dev.txt`.
- Installed torch 2.14, ultralytics 8.4.153, opencv 5.0.0.
- Downloaded OmniParser v2 detector weights → `weights/icon_detect.pt` (**40MB** on disk;
  the ~12MB figure quoted earlier was the parameter size, not the checkpoint).

**2. Detector verified**

First real run on a synthetic 1080x2400 cab-booking screen. Found 8 regions: back arrow,
title, both input fields, all three ride cards, confirm button. **Missed the four
bottom-nav items** — flat grey rectangles with no internal structure, plausibly an
artifact of the synthetic fixture. Detection ≈4.8s cold on CPU.

**3. PaddleOCR replaced with RapidOCR** ([D12](decisions.md))

PaddleOCR resolved to 3.7.0, whose API differs from the 2.x my backend was written
against (`use_angle_cls`/`show_log` gone, `predict()` instead of `ocr()`, dict-of-arrays
instead of per-line tuples). Rewrote the backend to handle both majors. Then:

- **It crashed** — `ConvertPirAttribute2RuntimeAttribute not support`, oneDNN executor,
  paddlepaddle 3.3.1 on Windows. Worked around with `enable_mkldnn=False`.
- **Its coordinates were wrong** — on a controlled single-word test at 1080x2400 the box
  was off by `(-67, -59)`px, error growing with image size.

The second failed *silently*: `Confirm Booking` was read correctly but placed at y2214–2258
when the button is at 2050–2180, so it fused with nothing. The button became a blank
`icon`, the label became orphan text, and the JSON came out well-formed and wrong.

Switched to RapidOCR (ONNX): off by `(-3, -14)` on the same test, every label inside its
widget on the fixture, and OCR time down **77.5s → 4.4s**. PaddleOCR retained as a
selectable backend, commented out of requirements.

**4. Tuning and tests**

- `LIST_ROW_MIN_HEIGHT` 0.09 → 0.06; 180px cards on a 2400px screen were typing as buttons.
- Added `tests/test_fixtures.py`: 11 integration tests against real weights, auto-skipped
  when weights are absent. Cover coordinate sanity, tap-centre containment, and explicit
  regressions for the two bugs above.

**Tests: 39 passing** (28 model-free + 11 integration).

**Still open:** detector thresholds remain reasoned rather than measured on *real*
screenshots ([D7](decisions.md)); bottom-nav recall gap unexplained.

---

## 2026-09-16 — Project documentation

Wrote [README.md](README.md), [history.md](history.md), [decisions.md](decisions.md) and
this log. Updated [context.md](context.md) with an implementation-status table and a
corrected tech stack.

**Stack corrections** from what the abstract listed:
- TensorFlow dropped — PyTorch only, via ultralytics.
- OCR named concretely: PaddleOCR (dev) / ML Kit (on-device).
- OmniParser v2 + YOLOv8 added.

---

## 2026-09-16 — Screen perception module (vision path)

Built `perception/` — screenshot in, JSON element list out. Runs standalone; no device,
ADB or emulator needed.

**Added**

| File | Role |
|---|---|
| `schema.py` | `BoundingBox`, `UIElement`, `ScreenState`; closed `ElementType` enum |
| `detector.py` | OmniParser v2 YOLOv8 wrapper, lazily loaded |
| `ocr.py` | `OCRBackend` protocol; PaddleOCR + `NullOCR` |
| `fusion.py` | Box↔text assignment, dedup, reading order |
| `classify.py` | Rule-based element typing |
| `pipeline.py` | `ScreenParser` orchestration |
| `overlay.py` | Annotated debug image |
| `cli.py` | `python -m perception.cli` |
| `download_weights.py` | Pulls detector weights from Hugging Face |

**Tests:** 28, all passing. Cover geometry, dedup, fusion, classification, serialisation
and pipeline wiring. All run without weights or torch via stub detectors, so the
model-free logic — where the bugs actually live — stays fast to iterate on.

**Notable during implementation**

- Text-to-box assignment by IoU failed as designed: a label inside a button scores IoU
  ≈ 0.08, so any meaningful threshold would have attached no text at all, silently.
  Switched to containment (1.0 for the same pair), assigning to the *smallest* containing
  box since UI boxes nest. Both pinned by tests. ([D5](decisions.md))
- Detector thresholds lowered from stock — `conf` 0.25→0.05, NMS `iou` 0.7→0.10, `imgsz`
  640→1280 — reasoned from mobile UI density. **Not yet measured.** ([D7](decisions.md))
- Florence-2 icon captioning omitted: 0.23B params for descriptions the planner mostly
  does not need. ([D8](decisions.md))

**Not done**

- `ultralytics`/torch not installed, so **the real detector is unverified end-to-end** —
  only the stubbed pipeline is exercised.
- No real-screenshot fixtures in `tests/fixtures/`.

---

## 2026-09-16 — Model selection reversed

Planned the perception module around a vision LLM with a forced JSON schema, then reversed
twice before settling.

- **Vision LLM** — works, but bills per screen, and the loop captures a screen after every
  action.
- **SeeClick** — proposed as a lightweight option; rejected. ≈9.6B params (not
  lightweight), and it grounds an *instruction* to one point rather than enumerating
  elements, so it cannot produce the required list. Still a candidate for the executor's
  grounding step.
- **OmniParser v2 icon detector** — adopted. ~3.0M params, MIT. ([D3](decisions.md))
  *(Size was quoted as ~12MB here; the checkpoint is actually 40MB — corrected below.)*

---

## 2026-09-16 — Repository initialised

Created [context.md](context.md) from the Group 17 abstract presentation, and
[CLAUDE.md](CLAUDE.md) with architecture notes and the project's hard constraints
(no OS modification, no per-app integrations, human confirmation for sensitive actions,
foreground-only execution).
