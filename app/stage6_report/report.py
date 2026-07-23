from app.schemas import AggregatedEvidence, ExplainableReport, FinalDecision


def build_report(evidence: AggregatedEvidence, decision: FinalDecision) -> ExplainableReport:
    return ExplainableReport(
        order_id=evidence.order_id,
        return_reason=evidence.return_reason,
        quality=evidence.quality,
        inspector_results=evidence.inspector_results,
        decision=decision,
    )
