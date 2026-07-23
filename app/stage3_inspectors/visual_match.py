"""Stage 3 - Visual Match inspector.

Compares listing images (scraped from Shopee) against the refund proof
images/frames using CLIP embeddings + cosine similarity. Runs fine on CPU
for single images, per the build plan.
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import open_clip
import torch
from PIL import Image

from app.schemas import InspectorResult, InspectorStatus

_MODEL_NAME = "ViT-B-32"
_PRETRAINED = "openai"

_model = None
_preprocess = None

# Calibrated against the demo order: comparing a clean catalog photo against a
# handheld unboxing-video frame (hands, packaging, motion blur, different
# framing) scores meaningfully lower via CLIP than two clean same-item photos
# would -- a confirmed same-item comparison here landed at 0.58. Thresholds
# are set so that range reads as "uncertain" rather than a confident (wrong)
# mismatch call.
PASS_THRESHOLD = 0.75
FLAG_THRESHOLD = 0.45


def _get_model():
    global _model, _preprocess
    if _model is None:
        _model, _, _preprocess = open_clip.create_model_and_transforms(_MODEL_NAME, pretrained=_PRETRAINED)
        _model.eval()
    return _model, _preprocess


def _load_image(source: str | Path) -> Image.Image | None:
    try:
        if isinstance(source, Path) or (isinstance(source, str) and Path(source).exists()):
            return Image.open(source).convert("RGB")
        resp = httpx.get(str(source), timeout=15, follow_redirects=True)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert("RGB")
    except Exception:
        return None


def _embed(images: list[Image.Image]) -> torch.Tensor:
    model, preprocess = _get_model()
    tensors = torch.stack([preprocess(img) for img in images])
    with torch.no_grad():
        features = model.encode_image(tensors)
        features /= features.norm(dim=-1, keepdim=True)
    return features


def run_visual_match_inspector(
    listing_image_sources: list[str], proof_image_paths: list[Path]
) -> InspectorResult:
    listing_images = [img for img in (_load_image(s) for s in listing_image_sources) if img]
    proof_images = [img for img in (_load_image(p) for p in proof_image_paths) if img]

    if not listing_images or not proof_images:
        return InspectorResult(
            inspector_name="visual_match",
            status=InspectorStatus.INCONCLUSIVE,
            confidence=0.0,
            evidence="Missing listing images or proof images/frames to compare.",
        )

    listing_features = _embed(listing_images)
    proof_features = _embed(proof_images)
    similarity = (listing_features @ proof_features.T)
    best_score = float(similarity.max())

    if best_score >= PASS_THRESHOLD:
        status = InspectorStatus.PASS
        evidence = f"Best listing/proof cosine similarity {best_score:.2f} -- strong visual match."
    elif best_score >= FLAG_THRESHOLD:
        status = InspectorStatus.FLAG
        evidence = f"Best listing/proof cosine similarity {best_score:.2f} -- uncertain match."
    else:
        status = InspectorStatus.FAIL
        evidence = f"Best listing/proof cosine similarity {best_score:.2f} -- likely item mismatch."

    return InspectorResult(
        inspector_name="visual_match",
        status=status,
        confidence=best_score,
        evidence=evidence,
        suggested_reason="Item mismatch" if status == InspectorStatus.FAIL else None,
    )
