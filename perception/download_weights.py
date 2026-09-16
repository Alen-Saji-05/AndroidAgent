"""Fetch the OmniParser icon-detection weights. `python -m perception.download_weights`"""

import logging

from .detector import DEFAULT_WEIGHTS, download_weights

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if DEFAULT_WEIGHTS.exists():
        print(f"already present: {DEFAULT_WEIGHTS}")
    else:
        print(f"downloading -> {download_weights()}")
