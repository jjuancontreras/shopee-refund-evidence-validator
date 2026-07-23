from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, func

from app.db import Base


class RefundRequestModel(Base):
    __tablename__ = "refund_requests"

    order_id = Column(String, primary_key=True)
    shop_id = Column(String)
    item_id = Column(String)
    return_reason = Column(String)
    proof_filenames = Column(JSON, default=list)
    listing_item_ids = Column(JSON, default=list)
    created_at = Column(DateTime, server_default=func.now())


class InspectorResultModel(Base):
    __tablename__ = "inspector_results"

    id = Column(String, primary_key=True)  # f"{order_id}:{inspector_name}"
    order_id = Column(String, index=True)
    inspector_name = Column(String)
    status = Column(String)
    confidence = Column(Float)
    evidence = Column(String)
    suggested_reason = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class FinalReportModel(Base):
    __tablename__ = "final_reports"

    order_id = Column(String, primary_key=True)
    classification = Column(String)
    reason = Column(String)
    confidence = Column(Float)
    triggered_rule = Column(String)
    report_json = Column(JSON)
    human_override = Column(String, nullable=True)
    human_notes = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class PipelineRunModel(Base):
    """Append-only history of processing attempts (one row per run, unlike
    FinalReportModel which is keyed by order_id and gets overwritten on
    reprocessing) -- backs the ops dashboard's history panel, including
    failed runs."""

    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String, index=True)
    status = Column(String)  # "completed" | "failed"
    classification = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    error_message = Column(String, nullable=True)
    started_at = Column(DateTime)
    finished_at = Column(DateTime, index=True)
