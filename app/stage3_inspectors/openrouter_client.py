"""Shared OpenRouter client for the Parcel/Unboxing and Return Reason
Validation inspectors -- both call the same free-tier vision model endpoint
(configurable via OPENROUTER_MODEL; the build plan's original pick,
qwen/qwen-2.5-vl-7b-instruct:free, was retired from OpenRouter's free tier,
so this defaults to nvidia/nemotron-nano-12b-v2-vl:free instead), just with
different prompts."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.stage3_inspectors.base import image_to_data_url

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _inconclusive(message: str) -> dict[str, Any]:
    return {
        "status": "inconclusive",
        "confidence": 0.0,
        "evidence": f"OpenRouter call failed: {message}",
        "suggested_reason": None,
    }


async def call_vlm(
    prompt: str, image_paths: list[Path], max_images: int = 6, retries: int = 3, backoff_seconds: float = 3.0
) -> dict[str, Any]:
    """Sends a text prompt + up to `max_images` frames to the configured VLM
    and expects a JSON object back (the prompt instructs the model to reply
    in strict JSON). Returns a dict with at least {status, confidence,
    evidence}, falling back to an inconclusive result -- rather than raising
    and killing the whole pipeline -- if the model reply isn't valid JSON or
    OpenRouter's free-tier endpoint is flaky (502/503/timeouts are common).
    """
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for path in image_paths[:max_images]:
        content.append({"type": "image_url", "image_url": {"url": image_to_data_url(path)}})

    payload = {
        "model": settings.openrouter_model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }

    last_error: str = "unknown error"
    for attempt in range(1, retries + 1):
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                resp = await client.post(_OPENROUTER_URL, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_error = str(exc)
            logger.warning("OpenRouter call attempt %d/%d failed: %s", attempt, retries, exc)
            if attempt < retries:
                await asyncio.sleep(backoff_seconds * attempt)
            continue

        if "error" in data or "choices" not in data:
            last_error = data.get("error", {}).get("message", "Unknown OpenRouter response shape")
            logger.warning("OpenRouter call attempt %d/%d returned no usable choices: %s", attempt, retries, last_error)
            if attempt < retries:
                await asyncio.sleep(backoff_seconds * attempt)
            continue

        raw_text = data["choices"][0]["message"]["content"]
        return _parse_json_reply(raw_text)

    return _inconclusive(last_error)


_VALID_STATUSES = {"pass", "fail", "flag", "inconclusive"}


def _parse_json_reply(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError:
        return {
            "status": "inconclusive",
            "confidence": 0.3,
            "evidence": f"Model reply was not valid JSON: {raw_text[:300]}",
            "suggested_reason": None,
        }

    status = str(parsed.get("status", "")).strip().lower()
    if status not in _VALID_STATUSES:
        parsed["evidence"] = f"(status '{status}' unrecognized, treated as inconclusive) " + str(
            parsed.get("evidence", "")
        )
        status = "inconclusive"
    parsed["status"] = status

    try:
        confidence = float(parsed.get("confidence", 0.3))
    except (TypeError, ValueError):
        confidence = 0.3
    if confidence > 1.0:  # model sometimes replies with a 0-100 scale
        confidence = confidence / 100.0
    parsed["confidence"] = max(0.0, min(1.0, confidence))

    return parsed
