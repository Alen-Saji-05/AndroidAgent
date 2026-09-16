# Screen Perception

Mobile UI screenshot → JSON list of UI elements. This is the **vision-based**
grounding path; the accessibility-tree path is separate and will emit the same
`UIElement` shape so the two can be reconciled later.

## Pipeline

```
screenshot
   ├─► YOLOv8 icon detector (OmniParser v2)  ─► interactable boxes
   └─► OCR (RapidOCR / ML Kit)               ─► text runs
                    │
                    ▼
              fusion.py     box+text→control, box→icon, text→static label
                    ▼
             classify.py    geometry + position + keyword rules → type
                    ▼
               ScreenState  →  .to_json()
```

The detector is **class-agnostic** — it answers *"is this interactable?"*, not
*"what widget is it?"*. Typing happens in `classify.py`, rule-based so failures
are readable.

## Setup

From the project root, using the venv interpreter (`.venv/bin/python` on
macOS/Linux):

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt -r requirements-ocr.txt
```

```bash
.venv/Scripts/python.exe -m perception.download_weights
```

Pulls `microsoft/OmniParser-v2.0` (`icon_detect/model.pt`, MIT, ~3.0M params,
40MB checkpoint) into `weights/`.

OCR is optional — without it the pipeline still emits elements, just unlabelled.

## Use

```bash
.venv/Scripts/python.exe -m perception.cli screen.png -o out.json --overlay out.png
```

```python
from perception import ScreenParser

state = ScreenParser().parse_file("screen.png")
print(state.to_json())

for el in state.interactables():
    print(el.id, el.type.value, el.text, el.center)
```

Always look at `--overlay` output when tuning. Reading raw coordinate JSON to
work out why a tap missed is miserable.

## Output

```json
{
  "image_size": { "width": 1080, "height": 2400 },
  "element_count": 3,
  "elements": [
    {
      "id": "e2",
      "type": "button",
      "text": "Confirm Booking",
      "bounds": { "x1": 48, "y1": 2050, "x2": 1032, "y2": 2180 },
      "center": { "x": 540, "y": 2115 },
      "interactable": true,
      "confidence": 0.93,
      "source": "fused",
      "state": { "enabled": true, "selected": false, "checked": null }
    }
  ]
}
```

`center` is precomputed because the executor issues `input tap x y` and should
not re-derive it. `id`s are stable **within a single capture only** — they are
reading-order indices, not identities across screens.

`source` records provenance: `vision` (detector only), `ocr` (text only),
`fused` (both), `accessibility` (reserved for the tree path).

## Tuning

Defaults in `DetectorConfig` are lowered from ultralytics' stock values
(`conf` 0.25→0.05, NMS `iou` 0.7→0.10) because mobile UIs are denser than the
desktop captures OmniParser ships tuned for: a high floor drops small icons, and
loose NMS merges adjacent list rows into one box. `imgsz=1280` rather than 640
keeps small icons above the detector's minimum feature size.

Re-tune against your own screenshots — these are a starting point, not settled.

## OCR backends

Selected automatically: RapidOCR → PaddleOCR → `NullOCR`.

**RapidOCR is the default on measured box accuracy.** On a controlled
single-word test at 1080x2400, RapidOCR's box was off by `(-3, -14)`px;
PaddleOCR 3.7's by `(-67, -59)`, worsening with image size. That magnitude puts
a label outside its own button, and fusion then attaches it to nothing — the
button comes back as a blank `icon` and the label as orphan text. **Nothing
errors.** The JSON is well-formed and wrong. See `decisions.md` D12.

PaddleOCR is still supported (both 2.x and 3.x APIs) but commented out of
`requirements-ocr.txt`.

## Tests

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

39 tests:

- **28 model-free** — geometry, dedup, fusion, classification, serialisation,
  pipeline wiring. Run anywhere via stub detectors, no weights or torch needed,
  so the logic where bugs actually live stays fast to iterate on.
- **11 integration** (`test_fixtures.py`) — real detector against a real
  screenshot. Auto-skipped when weights are absent. Assert coordinate sanity and
  tap-centre containment rather than exact boxes, plus explicit regressions for
  the OCR-displacement and list-row-typing bugs.

## Known gaps

- **Thresholds tuned against a synthetic fixture.** `tests/fixtures/sample_cab.png`
  is drawn by `make_sample_cab.py`, not captured from a device. A real capture
  (`spotify_onboarding.jpg`) is committed but not yet asserted on.
- **False negative on interactability.** On the real capture, a `Log in` link
  the detector did not box was classified as static `text` — so an executor
  would conclude the screen offers no way to log in. `_ACTION_WORDS` in
  `classify.py` is not consulted for OCR-sourced elements. Open.
- **Bottom-nav recall.** On the synthetic fixture the detector misses flat grey
  nav rectangles. Possibly an artifact of featureless synthetic shapes, possibly
  a real recall gap. Unresolved.
- **Cold-run latency ≈9s on CPU** (4.8s detector + 4.4s OCR), first call
  including model load. Fine for development, far too slow for a control loop
  that perceives after every action.
- **`state.checked` is always `null`** for vision-sourced elements. Vision often
  cannot tell, and guessing `False` would be a lie the planner might act on.
- **Icon captioning is deliberately omitted.** OmniParser's third stage
  (Florence-2, 0.23B) describes unlabelled icons. It adds real latency for
  descriptions the planner mostly does not need — add only if blank icons prove
  to be a blocker.
- **Not exported for on-device.** Runs under Python/ultralytics on a dev machine.
  ONNX/NCNN export is a later step and does not change the schema.
