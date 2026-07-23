from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import ffmpeg
from scenedetect import SceneManager, open_video
from scenedetect.detectors import ContentDetector


@dataclass
class VideoPreprocessResult:
    frame_paths: list[Path]
    scene_cut_count: int
    duration_seconds: float


def _probe_duration(path: Path) -> float:
    probe = ffmpeg.probe(str(path))
    return float(probe["format"]["duration"])


def extract_evenly_spaced_frames(path: Path, out_dir: Path, n_frames: int = 8) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = _probe_duration(path)

    frame_paths = []
    for i in range(n_frames):
        timestamp = duration * (i + 0.5) / n_frames
        out_path = out_dir / f"frame_{i:03d}.jpg"
        (
            ffmpeg.input(str(path), ss=timestamp)
            .output(str(out_path), vframes=1, loglevel="error")
            .overwrite_output()
            .run()
        )
        if out_path.exists():
            frame_paths.append(out_path)
    return frame_paths


def detect_scene_cuts(path: Path) -> int:
    video = open_video(str(path))
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector())
    scene_manager.detect_scenes(video)
    return len(scene_manager.get_scene_list())


def preprocess_video(path: Path, out_dir: Path, n_frames: int = 8) -> VideoPreprocessResult:
    duration = _probe_duration(path)
    frames = extract_evenly_spaced_frames(path, out_dir, n_frames)
    cuts = detect_scene_cuts(path)
    return VideoPreprocessResult(frame_paths=frames, scene_cut_count=cuts, duration_seconds=duration)
