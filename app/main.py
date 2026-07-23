from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from app.db import Base, engine, SessionLocal
from app.models import FinalReportModel
from app.queue_worker import worker
from app.schemas import ExplainableReport, RefundRequest

logging.basicConfig(level=logging.INFO)

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    worker.start()
    yield
    await worker.stop()


app = FastAPI(title="Refund Evidence Validation System", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/refund-requests")
async def receive_refund_request(request: RefundRequest):
    """Webhook target for the Apps Script trigger: fires when a sheet row's
    status flips to 'Ready for Processing'. Enqueues onto the single
    sequential worker so the webhook call returns immediately."""
    worker.enqueue(request.order_id, refund_request=request)
    return {"accepted": True, "order_id": request.order_id}


@app.post("/refund-requests/{order_id}/run")
async def run_for_order_id(order_id: str):
    """Convenience endpoint: enqueues by order_id, the worker pulls the row
    straight from the Google Sheet instead of requiring a full payload."""
    worker.enqueue(order_id)
    return {"accepted": True, "order_id": order_id}


@app.get("/reports/{order_id}", response_model=ExplainableReport)
def get_report(order_id: str):
    db = SessionLocal()
    try:
        row = db.get(FinalReportModel, order_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Report not found")
        return row.report_json
    finally:
        db.close()


@app.get("/api/queue-status")
def queue_status():
    return worker.snapshot()


# Registered last: StaticFiles is a greedy "/" mount, so explicit routes
# above (matched first, in registration order) still win for their exact
# paths, and everything else falls through to serving the dashboard.
app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="dashboard")
