"""Single sequential FIFO worker backing the ops dashboard.

Replaces the old per-request `BackgroundTasks` pattern (where every incoming
order fired its own independent background task, with no shared queue and
no way to say "this one order is currently processing"). This worker
processes exactly one order at a time, so the dashboard's "currently
processing" concept is unambiguous, and records every run attempt (success
or failure) to `PipelineRunModel` for the history panel.

Lives entirely in the FastAPI process (in-memory queue + current-job
state) -- restarting the server drops pending/current state, which is
acceptable for this project's existing "skip Redis/Celery for hackathon
scope" posture. History survives restarts because it's a real table.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.db import SessionLocal
from app.models import PipelineRunModel
from app.pipeline import run_pipeline_for_order
from app.schemas import RefundRequest

logger = logging.getLogger(__name__)


@dataclass
class QueueItem:
    order_id: str
    refund_request: Optional[RefundRequest] = None
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CurrentJob:
    order_id: str
    stage: str
    started_at: datetime
    shop_id: Optional[str] = None
    return_reason: Optional[str] = None


class PipelineQueueWorker:
    def __init__(self) -> None:
        self._queue: "asyncio.Queue[QueueItem]" = asyncio.Queue()
        self._pending: list[QueueItem] = []
        self._current: Optional[CurrentJob] = None
        self._task: Optional[asyncio.Task] = None

    def enqueue(self, order_id: str, refund_request: Optional[RefundRequest] = None) -> None:
        item = QueueItem(order_id=order_id, refund_request=refund_request)
        self._pending.append(item)
        self._queue.put_nowait(item)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_forever(self) -> None:
        while True:
            item = await self._queue.get()
            self._pending = [i for i in self._pending if i is not item]
            await self._process(item)

    async def _process(self, item: QueueItem) -> None:
        started_at = datetime.now(timezone.utc)
        self._current = CurrentJob(
            order_id=item.order_id,
            stage="Starting",
            started_at=started_at,
            shop_id=item.refund_request.shop_id if item.refund_request else None,
            return_reason=item.refund_request.return_reason if item.refund_request else None,
        )

        def on_stage(stage: str) -> None:
            if self._current is not None:
                self._current.stage = stage

        status, classification, confidence, error_message = "completed", None, None, None
        try:
            report = await run_pipeline_for_order(
                item.order_id, refund_request=item.refund_request, on_stage=on_stage
            )
            classification = report.decision.classification.value
            confidence = report.decision.confidence
        except Exception as exc:  # noqa: BLE001 - any inspector/intake failure must not kill the worker loop
            logger.exception("Pipeline failed for order_id=%s", item.order_id)
            status, error_message = "failed", str(exc)[:2000]
        finally:
            self._persist_run(
                item.order_id, status, classification, confidence,
                error_message, started_at, datetime.now(timezone.utc),
            )
            self._current = None

    def _persist_run(
        self, order_id, status, classification, confidence, error_message, started_at, finished_at
    ) -> None:
        db = SessionLocal()
        try:
            db.add(
                PipelineRunModel(
                    order_id=order_id,
                    status=status,
                    classification=classification,
                    confidence=confidence,
                    error_message=error_message,
                    started_at=started_at,
                    finished_at=finished_at,
                )
            )
            db.commit()
        finally:
            db.close()

    def snapshot(self) -> dict:
        current = None
        if self._current is not None:
            current = {
                "order_id": self._current.order_id,
                "stage": self._current.stage,
                "shop_id": self._current.shop_id,
                "return_reason": self._current.return_reason,
                "started_at": self._current.started_at.isoformat(),
            }
        queue = [
            {"order_id": q.order_id, "position": i + 1, "enqueued_at": q.enqueued_at.isoformat()}
            for i, q in enumerate(self._pending)
        ]
        db = SessionLocal()
        try:
            rows = (
                db.query(PipelineRunModel)
                .order_by(PipelineRunModel.finished_at.desc())
                .limit(20)
                .all()
            )
            history = [
                {
                    "order_id": r.order_id,
                    "status": r.status,
                    "classification": r.classification,
                    "confidence": r.confidence,
                    "error_message": r.error_message,
                    # SQLite drops tzinfo on the round trip through the DB column
                    # (we always write UTC via datetime.now(timezone.utc)), so
                    # reattach it here -- otherwise the frontend's `new
                    # Date(isoString)` parses the naive string as local time.
                    "finished_at": (
                        r.finished_at.replace(tzinfo=timezone.utc).isoformat() if r.finished_at else None
                    ),
                }
                for r in rows
            ]
        finally:
            db.close()
        return {"current": current, "queue": queue, "history": history}


worker = PipelineQueueWorker()
