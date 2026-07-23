"""Stage 3 - AI-Editing / Forensics inspector.

VLMs are weak at detecting AI-generated/edited imagery specifically, so this
uses a purpose-built open-source HF classifier instead (CPU-friendly,
no paid API -- per project decision to avoid the only paid dependency in
the pipeline).

Only run this against native image proofs (see pipeline._gather_proof_frames)
-- the classifier was trained to separate SDXL renders from native photos,
and produces a systematic false-positive bias (85-99.8% "artificial") on
ffmpeg-extracted video frames, confirmed against the demo's known-genuine
video. The training pack's "Edited using AI" ground truth is always a
.jpg/.png proof, never a video, which matches this scoping.
"""

from __future__ import annotations

from pathlib import Path

from transformers import pipeline

from app.schemas import InspectorResult, InspectorStatus

_MODEL_NAME = "Organika/sdxl-detector"
_AI_LABEL_HINTS = ["ai", "artificial", "fake", "generated", "synthetic"]

FAIL_THRESHOLD = 0.75   # high confidence AI-generated/edited -> fail
FLAG_THRESHOLD = 0.5    # moderate confidence -> flag for human review

_classifier = None


def _get_classifier():
    global _classifier
    if _classifier is None:
        _classifier = pipeline("image-classification", model=_MODEL_NAME)
    return _classifier


def _ai_score(predictions: list[dict]) -> float:
    for pred in predictions:
        label = pred["label"].lower()
        if any(hint in label for hint in _AI_LABEL_HINTS):
            return float(pred["score"])
    return 0.0


def run_forensics_inspector(image_paths: list[Path]) -> InspectorResult:
    if not image_paths:
        return InspectorResult(
            inspector_name="ai_editing_forensics",
            status=InspectorStatus.INCONCLUSIVE,
            confidence=0.0,
            evidence="No native image proofs to inspect (video-only proof -- this "
            "classifier is scoped to native images, see module docstring).",
        )

    classifier = _get_classifier()
    scores = []
    for path in image_paths:
        predictions = classifier(str(path))
        scores.append(_ai_score(predictions))

    max_score = max(scores)

    if max_score >= FAIL_THRESHOLD:
        status = InspectorStatus.FAIL
        evidence = f"High AI-generated/edited likelihood ({max_score:.2f}) in at least one frame."
        suggested_reason = "Edited using AI"
    elif max_score >= FLAG_THRESHOLD:
        status = InspectorStatus.FLAG
        evidence = f"Moderate AI-generated/edited likelihood ({max_score:.2f}) -- needs human review."
        suggested_reason = None
    else:
        status = InspectorStatus.PASS
        evidence = f"Low AI-generated/edited likelihood ({max_score:.2f})."
        suggested_reason = None

    return InspectorResult(
        inspector_name="ai_editing_forensics",
        status=status,
        confidence=max_score,
        evidence=evidence,
        suggested_reason=suggested_reason,
    )
