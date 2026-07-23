"""Stage 6 - reviewer UI. Streamlit is the *review* UI; the Google Sheet
stays the *intake* UI per the brief."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import FinalReportModel  # noqa: E402
from app.pipeline import run_pipeline_for_order  # noqa: E402

Base.metadata.create_all(bind=engine)

st.set_page_config(page_title="Refund Evidence Review", layout="wide")
st.title("Refund Evidence Validation - Reviewer")

DEMO_ORDER_ID = "DEMO-ORDER-001"

order_id = st.text_input("Order ID", value=st.query_params.get("order_id", DEMO_ORDER_ID))

col_run, col_refresh = st.columns([1, 1])
with col_run:
    if st.button("Run pipeline for this order", type="primary"):
        with st.spinner(f"Running pipeline for order {order_id}..."):
            try:
                asyncio.run(run_pipeline_for_order(order_id))
                st.success("Pipeline finished.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Pipeline failed: {exc}")
with col_refresh:
    if st.button("Refresh"):
        st.rerun()

db = SessionLocal()
row = db.get(FinalReportModel, order_id)
db.close()

if row is None:
    st.info("No report yet for this order. Click 'Run pipeline for this order' above.")
    st.stop()

report = row.report_json

st.subheader(f"Order {report['order_id']}")
st.caption(f"Stated return reason: {report['return_reason']}")

st.markdown("### Evidence Quality")
q = report["quality"]
qc1, qc2, qc3 = st.columns(3)
qc1.metric("Blur score (Laplacian var)", f"{q['blur_score']:.1f}")
qc2.metric("Brightness", f"{q['brightness_score']:.1f}")
qc3.metric("Resolution", f"{q['resolution'][0]}x{q['resolution'][1]}")
if not q["is_sufficient_quality"]:
    st.warning(q["notes"])
else:
    st.success(q["notes"])

st.markdown("### Inspector Breakdown")

_STATUS_COLOR = {
    "pass": "🟢",
    "fail": "🔴",
    "flag": "🟠",
    "inconclusive": "⚪",
}

for result in report["inspector_results"]:
    with st.container(border=True):
        c1, c2 = st.columns([3, 1])
        c1.markdown(
            f"**{_STATUS_COLOR.get(result['status'], '')} {result['inspector_name']}** "
            f"— `{result['status']}`"
        )
        c2.progress(result["confidence"], text=f"{result['confidence']:.0%} confidence")
        st.write(result["evidence"])
        if result.get("suggested_reason"):
            st.caption(f"Suggested reason: {result['suggested_reason']}")

st.markdown("### Final Decision")
decision = report["decision"]
classification = decision["classification"]
badge = {"Valid": "🟢", "Invalid": "🔴", "Needs Human Review": "🟠"}.get(classification, "")
st.markdown(f"## {badge} {classification}")
st.write(f"**Reason:** {decision['reason']}")
st.write(f"**Confidence:** {decision['confidence']:.0%}")
st.caption(f"Triggered rule: `{decision['triggered_rule']}`")

st.markdown("### Human Review")
notes = st.text_area("Reviewer notes", value=report.get("human_notes") or "")

b1, b2, b3 = st.columns(3)
override_choice = None
if b1.button("✅ Approve as Valid"):
    override_choice = "Valid"
if b2.button("❌ Approve as Invalid"):
    override_choice = "Invalid"
if b3.button("🤝 Keep as Needs Human Review"):
    override_choice = "Needs Human Review"

if override_choice:
    db = SessionLocal()
    row = db.get(FinalReportModel, order_id)
    row.human_override = override_choice
    row.human_notes = notes
    report["human_override"] = override_choice
    report["human_notes"] = notes
    row.report_json = report
    db.commit()
    db.close()
    st.success(f"Recorded human decision: {override_choice}")
    st.rerun()

if report.get("human_override"):
    st.info(f"Human override on record: **{report['human_override']}** -- {report.get('human_notes', '')}")
