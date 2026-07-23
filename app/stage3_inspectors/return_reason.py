from __future__ import annotations

from pathlib import Path

from app.schemas import InspectorResult, InspectorStatus
from app.stage3_inspectors.openrouter_client import call_vlm

_PROMPT_TEMPLATE = """You are validating a customer's refund proof against their stated return reason.

Stated return reason: "{return_reason}"

Look at the attached frames/images and judge whether the visual evidence is
CONSISTENT with this specific return reason. For example, if the reason is
about the item being damaged, check for visible damage; if about the wrong
item being sent, check whether the item matches the expected product; if
about a suspicious/tampered parcel, check the parcel's condition.

Reply with ONLY a JSON object, no other text, in this exact shape:
{{"status": "pass" | "fail" | "flag" | "inconclusive",
 "confidence": <float 0.0-1.0>,
 "evidence": "<one or two sentences citing what you saw>",
 "suggested_reason": "<short label or null>"}}

Use "pass" if the evidence supports the stated reason, "fail" if it contradicts
it, "flag" if it's ambiguous but suspicious, "inconclusive" if the frames don't
show enough to judge.
"""


async def run_return_reason_inspector(return_reason: str, image_paths: list[Path]) -> InspectorResult:
    if not image_paths:
        return InspectorResult(
            inspector_name="return_reason_validation",
            status=InspectorStatus.INCONCLUSIVE,
            confidence=0.0,
            evidence="No images/frames available to inspect.",
        )

    prompt = _PROMPT_TEMPLATE.format(return_reason=return_reason)
    result = await call_vlm(prompt, image_paths)
    return InspectorResult(
        inspector_name="return_reason_validation",
        status=InspectorStatus(result.get("status", "inconclusive")),
        confidence=float(result.get("confidence", 0.3)),
        evidence=result.get("evidence", ""),
        suggested_reason=result.get("suggested_reason"),
    )
