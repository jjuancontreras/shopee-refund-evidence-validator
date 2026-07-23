from __future__ import annotations

from pathlib import Path

from app.schemas import InspectorResult, InspectorStatus
from app.stage3_inspectors.openrouter_client import call_vlm

_PROMPT_TEMPLATE = """You are inspecting frames sampled evenly across a customer's refund-proof
unboxing video (frames are in chronological order, but the video was NOT sampled
continuously -- each frame may be several seconds apart, so a jump between frames
does not by itself mean the footage was edited).

Look for TWO separate kinds of evidence that the parcel was already opened before
a genuine, continuous unboxing was recorded:

1. Exterior tampering: cut/torn tape, a box flap already loose, missing seals, or
   a package that appears re-taped on the OUTER courier packaging.
2. Discontinuous unboxing: the INNER product box/packaging is already open or its
   lid/flap is already lifted the very first time it appears in the frames, with
   no frame showing it being opened -- i.e. the video appears to skip past the
   actual moment the sealed product packaging was first opened.
{scene_cut_context}
Reply with ONLY a JSON object, no other text, in this exact shape:
{{"status": "pass" | "fail" | "flag" | "inconclusive",
 "confidence": <float 0.0-1.0>,
 "evidence": "<one or two sentences citing what you saw>",
 "suggested_reason": "<short label or null>"}}

Use "fail" if either kind of evidence above is present.
Use "pass" only if the outer packaging looks intact at the start AND the inner
product packaging is shown being opened on camera (not already open when first shown).
Use "inconclusive" only if the frames genuinely don't show enough of the packaging
to judge either point.
"""


def _scene_cut_context(scene_cut_count: int | None) -> str:
    if scene_cut_count is None:
        return ""
    if scene_cut_count <= 1:
        return f"\nNote: scene-cut detection found {scene_cut_count} cut(s) in the source video -- footage looks continuous.\n"
    return (
        f"\nNote: scene-cut detection found {scene_cut_count} cuts in the source video -- "
        "the footage has multiple edits/jumps, which raises the chance that a cut hides the "
        "actual moment the inner packaging was first opened. Weigh this alongside what you see.\n"
    )


async def run_parcel_unboxing_inspector(
    frame_paths: list[Path], scene_cut_count: int | None = None
) -> InspectorResult:
    if not frame_paths:
        return InspectorResult(
            inspector_name="parcel_unboxing",
            status=InspectorStatus.INCONCLUSIVE,
            confidence=0.0,
            evidence="No frames available to inspect.",
        )

    prompt = _PROMPT_TEMPLATE.format(scene_cut_context=_scene_cut_context(scene_cut_count))
    result = await call_vlm(prompt, frame_paths)
    return InspectorResult(
        inspector_name="parcel_unboxing",
        status=InspectorStatus(result.get("status", "inconclusive")),
        confidence=float(result.get("confidence", 0.3)),
        evidence=result.get("evidence", ""),
        suggested_reason=result.get("suggested_reason"),
    )
