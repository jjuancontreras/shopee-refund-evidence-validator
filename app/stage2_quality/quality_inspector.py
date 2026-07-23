"""Stage 2 - Evidence Quality Inspector.

Pure rule-based thresholds, no model calls -- this stage stays cheap on
purpose. Video continuity reuses the PySceneDetect scene-cut count computed
in Stage 1 rather than recomputing it here.
"""

from __future__ import annotations

import cv2
import numpy as np

from app.schemas import QualityReport

BLUR_THRESHOLD = 100.0          # Laplacian variance below this = too blurry
BRIGHTNESS_MIN = 40.0            # mean pixel intensity below this = too dark
BRIGHTNESS_MAX = 220.0           # above this = blown out / overexposed
MIN_RESOLUTION = (360, 360)      # (width, height)
MAX_SCENE_CUTS_SUSPICIOUS = 15   # excessive cuts in a short clip = possible splicing


def blur_score(frame: np.ndarray) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def brightness_score(frame: np.ndarray) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(gray.mean())


def assess_quality(frames: list[np.ndarray], scene_cut_count: int | None = None) -> QualityReport:
    if not frames:
        return QualityReport(
            blur_score=0.0,
            brightness_score=0.0,
            resolution=(0, 0),
            scene_cut_count=scene_cut_count,
            is_sufficient_quality=False,
            notes="No frames available to assess.",
        )

    blur_scores = [blur_score(f) for f in frames]
    brightness_scores = [brightness_score(f) for f in frames]
    avg_blur = sum(blur_scores) / len(blur_scores)
    avg_brightness = sum(brightness_scores) / len(brightness_scores)
    height, width = frames[0].shape[:2]

    notes = []
    sufficient = True

    if avg_blur < BLUR_THRESHOLD:
        sufficient = False
        notes.append(f"Blurry (Laplacian variance {avg_blur:.1f} < {BLUR_THRESHOLD}).")
    if avg_brightness < BRIGHTNESS_MIN:
        sufficient = False
        notes.append(f"Too dark (mean intensity {avg_brightness:.1f} < {BRIGHTNESS_MIN}).")
    elif avg_brightness > BRIGHTNESS_MAX:
        sufficient = False
        notes.append(f"Overexposed (mean intensity {avg_brightness:.1f} > {BRIGHTNESS_MAX}).")
    if width < MIN_RESOLUTION[0] or height < MIN_RESOLUTION[1]:
        sufficient = False
        notes.append(f"Resolution too low ({width}x{height}).")
    if scene_cut_count is not None and scene_cut_count > MAX_SCENE_CUTS_SUSPICIOUS:
        notes.append(f"High scene-cut count ({scene_cut_count}) -- possible splicing/editing.")

    return QualityReport(
        blur_score=avg_blur,
        brightness_score=avg_brightness,
        resolution=(width, height),
        scene_cut_count=scene_cut_count,
        is_sufficient_quality=sufficient,
        notes=" ".join(notes) if notes else "Quality within acceptable thresholds.",
    )
