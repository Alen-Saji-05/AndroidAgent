"""Regenerate the synthetic cab-booking fixture.

    python tests/fixtures/make_sample_cab.py

`sample_cab.png` is drawn rather than captured so `test_fixtures.py` has a
fixture with known ground-truth geometry. Keep this script in sync with it: if
the image is lost, the 11 integration tests skip silently rather than fail.
"""

from pathlib import Path

import cv2
import numpy as np

W, H = 1080, 2400
OUT = Path(__file__).parent / "sample_cab.png"


def build() -> np.ndarray:
    img = np.full((H, W, 3), 250, np.uint8)

    # status bar
    cv2.rectangle(img, (0, 0), (W, 80), (240, 240, 240), -1)
    cv2.putText(img, "9:41", (40, 55), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (30, 30, 30), 2)

    # app bar with back arrow
    cv2.rectangle(img, (0, 80), (W, 220), (255, 255, 255), -1)
    cv2.arrowedLine(img, (100, 150), (45, 150), (40, 40, 40), 4, tipLength=0.4)
    cv2.putText(img, "Book a Cab", (160, 168), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (20, 20, 20), 3)

    # input fields
    for label, y in [("Pickup location", 320), ("Enter destination", 470)]:
        cv2.rectangle(img, (50, y), (1030, y + 110), (255, 255, 255), -1)
        cv2.rectangle(img, (50, y), (1030, y + 110), (200, 200, 200), 3)
        cv2.putText(img, label, (85, y + 72), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (130, 130, 130), 2)

    # ride cards -- 180px tall, the case that pinned LIST_ROW_MIN_HEIGHT
    for name, price, y in [("Mini", "250", 680), ("Sedan", "390", 900), ("SUV", "520", 1120)]:
        cv2.rectangle(img, (50, y), (1030, y + 180), (255, 255, 255), -1)
        cv2.rectangle(img, (50, y), (1030, y + 180), (220, 220, 220), 2)
        cv2.circle(img, (150, y + 90), 50, (90, 160, 230), -1)
        cv2.putText(img, name, (240, y + 80), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (20, 20, 20), 3)
        cv2.putText(img, "5 min away", (240, y + 140), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (140, 140, 140), 2)
        cv2.putText(img, price, (880, y + 105), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (20, 20, 20), 3)

    cv2.putText(img, "Total: 250", (60, 1420), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (60, 60, 60), 2)

    # primary action -- ground truth y 2050-2180, used by the OCR regression test
    cv2.rectangle(img, (48, 2050), (1032, 2180), (60, 170, 60), -1)
    cv2.putText(img, "Confirm Booking", (340, 2135), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)

    # bottom nav -- flat rectangles the detector currently misses (see D7)
    cv2.rectangle(img, (0, 2280), (W, H), (245, 245, 245), -1)
    for x in [130, 400, 670, 940]:
        cv2.rectangle(img, (x - 45, 2320), (x + 45, 2400), (150, 150, 150), -1)

    return img


if __name__ == "__main__":
    cv2.imwrite(str(OUT), build())
    print(f"wrote {OUT}")
