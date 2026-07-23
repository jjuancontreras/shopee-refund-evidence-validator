from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import exifread
import numpy as np
from PIL import Image

STANDARD_SIZE = (512, 512)


def load_and_normalize(path: Path, size: tuple[int, int] = STANDARD_SIZE) -> np.ndarray:
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f"Could not read image: {path}")
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA)


def extract_metadata(path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    with Image.open(path) as im:
        metadata["format"] = im.format
        metadata["size"] = im.size
        metadata["mode"] = im.mode

    with open(path, "rb") as f:
        tags = exifread.process_file(f, details=False)
    metadata["exif"] = {tag: str(val) for tag, val in tags.items()}
    return metadata
