"""Stage 5 - Decision Engine.

Deliberately a plain, editable rules table (not a black-box model) so Ops can
audit and tune it directly. Rules are evaluated in order; the first match wins.
"""

from __future__ import annotations

from app.schemas import AggregatedEvidence, Classification, FinalDecision, InspectorResult, InspectorStatus

HIGH_CONFIDENCE_FAIL_THRESHOLD = 0.75


def _find(results: list[InspectorResult], name: str) -> InspectorResult | None:
    return next((r for r in results if r.inspector_name == name), None)


def _high_confidence_fail(results: list[InspectorResult]) -> InspectorResult | None:
    for r in results:
        if r.status == InspectorStatus.FAIL and r.confidence >= HIGH_CONFIDENCE_FAIL_THRESHOLD:
            return r
    return None


def decide(evidence: AggregatedEvidence) -> FinalDecision:
    results = evidence.inspector_results

    # Rule 1: any inspector fails with high confidence -> clear-cut Invalid.
    fail_hit = _high_confidence_fail(results)
    if fail_hit:
        return FinalDecision(
            classification=Classification.INVALID,
            reason=fail_hit.suggested_reason or fail_hit.evidence,
            confidence=fail_hit.confidence,
            triggered_rule=f"high_confidence_fail:{fail_hit.inspector_name}",
        )

    # Rule 2: evidence quality itself was insufficient -> don't trust any
    # inspector's judgement, kick to a human.
    if not evidence.quality.is_sufficient_quality:
        return FinalDecision(
            classification=Classification.NEEDS_HUMAN_REVIEW,
            reason=f"Evidence quality insufficient for automated determination: {evidence.quality.notes}",
            confidence=0.5,
            triggered_rule="quality_insufficient",
        )

    # Rule 3: any inspector flagged, or failed but below the high-confidence
    # bar -> ambiguous, needs a human.
    flagged = [r for r in results if r.status in (InspectorStatus.FLAG, InspectorStatus.FAIL)]
    if flagged:
        worst = max(flagged, key=lambda r: r.confidence)
        return FinalDecision(
            classification=Classification.NEEDS_HUMAN_REVIEW,
            reason=f"{worst.inspector_name} flagged: {worst.evidence}",
            confidence=worst.confidence,
            triggered_rule=f"flagged:{worst.inspector_name}",
        )

    # Rule 4: too many inconclusive inspectors -> not enough signal either way.
    inconclusive = [r for r in results if r.status == InspectorStatus.INCONCLUSIVE]
    if results and len(inconclusive) > len(results) / 2:
        return FinalDecision(
            classification=Classification.NEEDS_HUMAN_REVIEW,
            reason="Majority of inspectors were inconclusive -- not enough signal for an automated decision.",
            confidence=0.4,
            triggered_rule="majority_inconclusive",
        )

    # Rule 5: everything passed cleanly -> Valid.
    if results and all(r.status == InspectorStatus.PASS for r in results):
        avg_confidence = sum(r.confidence for r in results) / len(results)
        return FinalDecision(
            classification=Classification.VALID,
            reason="All inspectors passed with no flags.",
            confidence=avg_confidence,
            triggered_rule="all_checks_passed",
        )

    # Fallback: shouldn't normally be reached, but default to human review
    # rather than silently approving/rejecting on an unhandled combination.
    return FinalDecision(
        classification=Classification.NEEDS_HUMAN_REVIEW,
        reason="Inspector results did not match a defined rule -- defaulting to human review.",
        confidence=0.3,
        triggered_rule="default_fallback",
    )
