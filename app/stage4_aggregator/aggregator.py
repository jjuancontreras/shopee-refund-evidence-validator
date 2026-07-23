from app.schemas import AggregatedEvidence, InspectorResult, QualityReport


def aggregate(
    order_id: str,
    return_reason: str,
    quality: QualityReport,
    inspector_results: list[InspectorResult],
) -> AggregatedEvidence:
    return AggregatedEvidence(
        order_id=order_id,
        return_reason=return_reason,
        quality=quality,
        inspector_results=inspector_results,
    )
