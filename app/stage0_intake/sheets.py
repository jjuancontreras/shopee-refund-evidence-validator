"""Stage 0 - read refund request rows from the intake Google Sheet.

The sheet only needs to be shared as "Anyone with the link can view" for this
to work: we hit the public gviz CSV export endpoint, so no OAuth/service
account setup is required for read access.
"""

from __future__ import annotations

import csv
import io
from urllib.parse import quote

import httpx

from app.config import settings
from app.schemas import RefundRequest

# Fuzzy header matching: the exact column names in the sheet may vary
# slightly (extra spaces, casing), so match on substrings instead.
_FIELD_HINTS = {
    "order_id": ["order id", "order_id"],
    "shop_id": ["shop id", "shop_id"],
    "item_id": ["item id", "item_id"],
    "return_reason": ["return reason"],
    "proof": ["image/video link", "image / video link", "proof"],
}


def _csv_export_url(sheet_id: str, tab_name: str) -> str:
    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/gviz/tq?tqx=out:csv&sheet={quote(tab_name)}"
    )


def _find_header_row(raw_rows: list[list[str]]) -> int | None:
    """The sheet has decorative banner/title rows above the real column
    headers (a merged "Starter Data Pack" title, a blank row, a "Training
    Data" label). Google's gviz CSV export has its own unreliable
    "auto-detect one header per column" heuristic that picks whichever cell
    happens to be first non-blank in each column -- when the sheet's layout
    shifts, that heuristic silently starts producing garbage instead of the
    real headers. So: read the raw grid (no implicit-header assumption) and
    find the actual header row ourselves by requiring BOTH an "order id"
    and a "shop id" hint to appear in the same row -- the banner text only
    weakly matches "order id" (it happens to end with those words) and
    never matches "shop id", so this reliably picks the real header row."""
    for i, row in enumerate(raw_rows):
        lowered = [str(c).strip().lower() for c in row]
        has_order_id = any(any(hint in c for hint in _FIELD_HINTS["order_id"]) for c in lowered)
        has_shop_id = any(any(hint in c for hint in _FIELD_HINTS["shop_id"]) for c in lowered)
        if has_order_id and has_shop_id:
            return i
    return None


def _match_all_columns(headers: list[str]) -> dict[str, str | None]:
    """Matches each known field to a header. Prefers an exact (case-insensitive)
    match over a substring match, and never lets two fields claim the same
    header -- necessary here because the sheet's Order ID column header is a
    giant merged title blob that happens to *contain* "return reason" and
    "image/video link" as substrings, which would otherwise hijack those
    fields' columns."""
    unused = list(headers)
    result: dict[str, str | None] = {}

    for field, hints in _FIELD_HINTS.items():
        exact = next((h for h in unused if h.strip().lower() in hints), None)
        if exact:
            result[field] = exact
            unused.remove(exact)
        else:
            result[field] = None

    for field, hints in _FIELD_HINTS.items():
        if result[field] is not None:
            continue
        match = next((h for h in unused if any(hint in h.strip().lower() for hint in hints)), None)
        if match:
            result[field] = match
            unused.remove(match)

    return result


def fetch_sheet_rows(sheet_id: str | None = None, tab_name: str | None = None) -> list[dict]:
    sheet_id = sheet_id or settings.google_sheet_id
    tab_name = tab_name or settings.google_sheet_tab
    url = _csv_export_url(sheet_id, tab_name)

    resp = httpx.get(url, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    raw_rows = list(csv.reader(io.StringIO(resp.text)))

    header_row_index = _find_header_row(raw_rows)
    if header_row_index is None:
        return []

    headers = raw_rows[header_row_index]
    return [
        dict(zip(headers, row))
        for row in raw_rows[header_row_index + 1 :]
        if any(v.strip() for v in row if v)
    ]


def row_to_refund_request(row: dict) -> RefundRequest:
    headers = list(row.keys())
    col = _match_all_columns(headers)

    def get(field: str, default: str = "") -> str:
        c = col[field]
        return (row.get(c) or default).strip() if c else default

    proof_raw = get("proof")
    proof_filenames = [f.strip() for f in proof_raw.replace(";", ",").split(",") if f.strip()]

    item_id_raw = get("item_id")
    listing_item_ids = [i.strip() for i in item_id_raw.replace(";", ",").split(",") if i.strip()]

    return RefundRequest(
        order_id=get("order_id"),
        shop_id=get("shop_id"),
        item_id=item_id_raw,
        return_reason=get("return_reason"),
        proof_filenames=proof_filenames,
        listing_item_ids=listing_item_ids or [item_id_raw],
    )


def fetch_refund_request_by_order_id(order_id: str) -> RefundRequest | None:
    rows = fetch_sheet_rows()
    for row in rows:
        req = row_to_refund_request(row)
        if req.order_id == str(order_id):
            return req
    return None
