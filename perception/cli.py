"""Command line entry point.

    python -m perception.cli screen.png -o out.json --overlay out.png
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import overlay as overlay_mod
from .detector import DetectorConfig, IconDetector
from .ocr import NullOCR, default_backend
from .pipeline import PerceptionError, ScreenParser, load_image


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="perception", description="Parse a mobile UI screenshot into JSON elements."
    )
    p.add_argument("image", type=Path, help="screenshot (png/jpg)")
    p.add_argument("-o", "--output", type=Path, help="write JSON here (default: stdout)")
    p.add_argument("--overlay", type=Path, help="write an annotated image here")
    p.add_argument("--weights", type=Path, default=Path("weights/icon_detect.pt"))
    p.add_argument("--conf", type=float, default=0.05, help="detector confidence floor")
    p.add_argument("--iou", type=float, default=0.10, help="detector NMS IoU")
    p.add_argument("--imgsz", type=int, default=1280, help="inference long edge")
    p.add_argument("--device", default="cpu", help="cpu | cuda:0 | mps")
    p.add_argument("--no-ocr", action="store_true", help="skip text extraction")
    p.add_argument("--no-static-text", action="store_true",
                   help="drop text that sits outside any detected box")
    p.add_argument("--interactable-only", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    parser = ScreenParser(
        detector=IconDetector(
            weights=args.weights,
            config=DetectorConfig(
                conf_threshold=args.conf,
                iou_threshold=args.iou,
                imgsz=args.imgsz,
                device=args.device,
            ),
        ),
        ocr=NullOCR() if args.no_ocr else default_backend(),
        include_static_text=not args.no_static_text,
    )

    try:
        image = load_image(args.image)
        state = parser.parse(image)
    except PerceptionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.interactable_only:
        state.elements = state.interactables()

    payload = state.to_json()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"wrote {len(state.elements)} elements -> {args.output}", file=sys.stderr)
    else:
        print(payload)

    if args.overlay:
        print(f"overlay -> {overlay_mod.save(image, state, args.overlay)}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
