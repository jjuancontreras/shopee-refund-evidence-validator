"""Orchestrates the full 7-stage pipeline for a single refund request."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Callable, Optional

import cv2

from app.config import settings
from app.db import Base, SessionLocal, engine
from app.models import FinalReportModel, InspectorResultModel, RefundRequestModel
from app.schemas import ExplainableReport, InspectorResult, RefundRequest
from app.stage0_intake.drive import fetch_proof_files
from app.stage0_intake.listing_images import get_all_listing_images
from app.stage0_intake.sheets import fetch_refund_request_by_order_id
from app.stage1_preprocessing.video_prep import preprocess_video
from app.stage2_quality.quality_inspector import assess_quality
from app.stage3_inspectors.forensics import run_forensics_inspector
from app.stage3_inspectors.parcel_unboxing import run_parcel_unboxing_inspector
from app.stage3_inspectors.return_reason import run_return_reason_inspector
from app.stage3_inspectors.visual_match import run_visual_match_inspector
from app.stage4_aggregator.aggregator import aggregate
from app.stage5_decision.rules import decide
from app.stage6_report.report import build_report

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _order_workdir(order_id: str) -> Path:
    d = settings.data_path / order_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _gather_proof_frames(
    proof_files: list[Path], workdir: Path
) -> tuple[list[Path], list[Path], int | None]:
    """Returns (all_frames, native_image_frames, scene_cut_count).
    Handles multi-proof orders (e.g. a jpg and an mp4 under the same order)
    by concatenating frames from every proof file rather than assuming 1:1.

    native_image_frames holds only frames that were originally still images
    (not extracted from video) -- the AI-editing/forensics classifier
    (Organika/sdxl-detector) was trained to tell SDXL renders apart from
    native photos, and produces a systematic false-positive bias on
    ffmpeg-extracted video frames (verified: 85-99.8% "artificial" on every
    frame of a known-genuine demo video). The only "Edited using AI"
    ground-truth rows in the training pack are all .jpg/.png proofs, never
    videos, so the forensics inspector is scoped to native images only."""
    all_frames: list[Path] = []
    native_image_frames: list[Path] = []
    scene_cut_count = None

    for proof_path in proof_files:
        ext = proof_path.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            out_dir = workdir / f"frames_{proof_path.stem}"
            result = preprocess_video(proof_path, out_dir)
            all_frames.extend(result.frame_paths)
            scene_cut_count = (scene_cut_count or 0) + result.scene_cut_count
        elif ext in IMAGE_EXTENSIONS:
            all_frames.append(proof_path)
            native_image_frames.append(proof_path)
        else:
            logger.warning("Unrecognized proof file type, skipping: %s", proof_path)

    return all_frames, native_image_frames, scene_cut_count


async def run_pipeline_for_order(
    order_id: str,
    refund_request: RefundRequest | None = None,
    on_stage: Optional[Callable[[str], None]] = None,
) -> ExplainableReport:
    _on_stage = on_stage or (lambda stage: None)
    Base.metadata.create_all(bind=engine)

    # Stage 0: intake
    _on_stage("Fetching evidence")
    request = refund_request or fetch_refund_request_by_order_id(order_id)
    if request is None:
        raise ValueError(f"No refund request found for order_id={order_id}")

    workdir = _order_workdir(request.order_id)
    proof_files = fetch_proof_files(request.proof_filenames)
    listing_images_by_item = await get_all_listing_images(request.listing_item_ids)
    listing_image_urls = [str(p) for paths in listing_images_by_item.values() for p in paths]

    # Stage 1: preprocessing
    _on_stage("Preprocessing")
    frame_paths, native_image_frames, scene_cut_count = _gather_proof_frames(proof_files, workdir)
    # Unresized reads: quality checks (resolution/blur/brightness) need the
    # original dimensions, not the 512x512 CLIP-normalized version.
    frame_arrays = [cv2.imread(str(p)) for p in frame_paths]
    frame_arrays = [f for f in frame_arrays if f is not None]

    # Stage 2: quality inspector
    _on_stage("Quality check")
    quality = assess_quality(frame_arrays, scene_cut_count)

    # Stage 3: inspectors, run in parallel. Tracked individually (rather than
    # a single asyncio.gather) so on_stage can report live progress as each
    # inspector finishes -- this is the slowest stage (VLM calls + CLIP/HF
    # inference) and would otherwise sit on one static "Running inspectors"
    # message for the whole 30-90+ seconds.
    inspector_tasks = {
        "visual_match": asyncio.create_task(
            asyncio.to_thread(run_visual_match_inspector, listing_image_urls, frame_paths)
        ),
        "parcel_unboxing": asyncio.create_task(run_parcel_unboxing_inspector(frame_paths, scene_cut_count)),
        "ai_editing_forensics": asyncio.create_task(asyncio.to_thread(run_forensics_inspector, native_image_frames)),
        "return_reason_validation": asyncio.create_task(
            run_return_reason_inspector(request.return_reason, frame_paths)
        ),
    }
    total_inspectors = len(inspector_tasks)
    _on_stage(f"Running inspectors (0/{total_inspectors} done)")
    completed = 0
    for finished in asyncio.as_completed(list(inspector_tasks.values())):
        await finished
        completed += 1
        _on_stage(f"Running inspectors ({completed}/{total_inspectors} done)")

    inspector_results: list[InspectorResult] = [
        inspector_tasks["visual_match"].result(),
        inspector_tasks["parcel_unboxing"].result(),
        inspector_tasks["ai_editing_forensics"].result(),
        inspector_tasks["return_reason_validation"].result(),
    ]

    # Stage 4 + 5: aggregation + decision
    _on_stage("Aggregating & deciding")
    evidence = aggregate(request.order_id, request.return_reason, quality, inspector_results)
    decision = decide(evidence)

    # Stage 6: explainable report
    report = build_report(evidence, decision)
    _persist_report(request, report)
    _on_stage("Report ready")
    return report


def _persist_report(request: RefundRequest, report: ExplainableReport) -> None:
    db = SessionLocal()
    try:
        db.merge(
            RefundRequestModel(
                order_id=request.order_id,
                shop_id=request.shop_id,
                item_id=request.item_id,
                return_reason=request.return_reason,
                proof_filenames=request.proof_filenames,
                listing_item_ids=request.listing_item_ids,
            )
        )
        for r in report.inspector_results:
            db.merge(
                InspectorResultModel(
                    id=f"{request.order_id}:{r.inspector_name}",
                    order_id=request.order_id,
                    inspector_name=r.inspector_name,
                    status=r.status.value,
                    confidence=r.confidence,
                    evidence=r.evidence,
                    suggested_reason=r.suggested_reason,
                )
            )
        db.merge(
            FinalReportModel(
                order_id=request.order_id,
                classification=report.decision.classification.value,
                reason=report.decision.reason,
                confidence=report.decision.confidence,
                triggered_rule=report.decision.triggered_rule,
                report_json=json.loads(report.model_dump_json()),
            )
        )
        db.commit()
    finally:
        db.close()
