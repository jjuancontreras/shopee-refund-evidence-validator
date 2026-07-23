from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RefundRequest(BaseModel):
    order_id: str
    shop_id: str
    item_id: str
    return_reason: str
    proof_filenames: list[str] = Field(default_factory=list)
    listing_item_ids: list[str] = Field(default_factory=list)


class InspectorStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    FLAG = "flag"
    INCONCLUSIVE = "inconclusive"


class InspectorResult(BaseModel):
    inspector_name: str
    status: InspectorStatus
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str
    suggested_reason: Optional[str] = None


class QualityReport(BaseModel):
    blur_score: float
    brightness_score: float
    resolution: tuple[int, int]
    scene_cut_count: Optional[int] = None
    is_sufficient_quality: bool
    notes: str = ""


class AggregatedEvidence(BaseModel):
    order_id: str
    return_reason: str
    quality: QualityReport
    inspector_results: list[InspectorResult]


class Classification(str, Enum):
    VALID = "Valid"
    INVALID = "Invalid"
    NEEDS_HUMAN_REVIEW = "Needs Human Review"


class FinalDecision(BaseModel):
    classification: Classification
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    triggered_rule: str


class ExplainableReport(BaseModel):
    order_id: str
    return_reason: str
    quality: QualityReport
    inspector_results: list[InspectorResult]
    decision: FinalDecision
    human_override: Optional[Classification] = None
    human_notes: Optional[str] = None
