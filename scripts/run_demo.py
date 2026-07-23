"""CLI entrypoint: run the full pipeline for the single demo order
(row 7 of the Training Data tab, Order ID DEMO-ORDER-001) without needing
FastAPI or the Apps Script webhook running."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline import run_pipeline_for_order  # noqa: E402

DEMO_ORDER_ID = "DEMO-ORDER-001"


async def main(order_id: str) -> None:
    report = await run_pipeline_for_order(order_id)
    print(json.dumps(json.loads(report.model_dump_json()), indent=2))


if __name__ == "__main__":
    order_id = sys.argv[1] if len(sys.argv) > 1 else DEMO_ORDER_ID
    asyncio.run(main(order_id))
