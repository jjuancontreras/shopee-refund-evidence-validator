"""Stage 0 - scrape listing images from Shopee for the Visual Match inspector.

NOT CURRENTLY USED by the pipeline. Confirmed while building this: Shopee's
anti-bot system redirects headless/automated traffic straight to a
verify/traffic/error login-required page before the real listing ever
renders, regardless of user-agent/slow-mo -- there's no realistic fix short
of deliberately evading Shopee's bot detection (stealth plugins, residential
proxies, etc.), which this project won't do. Per project decision, listing
photos are instead manually collected into a shared Drive folder; see
stage0_intake/listing_images.py, which is what app/pipeline.py actually
calls. This module is kept for reference/future use in case Shopee's
anti-bot posture changes or an authenticated scraping path becomes available.

Shopee product pages are JS-rendered and bot-protected, so this uses
Playwright (headless Chromium) rather than requests+BeautifulSoup, with a
realistic user-agent, slow-mo, and retry/backoff. Results are cached on disk
per (shop_id, item_id) so a rate-limit mid-demo doesn't kill the whole run --
we just fall back to whatever was cached last.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from playwright.async_api import async_playwright

from app.config import settings

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _cache_path(shop_id: str, item_id: str) -> Path:
    d = settings.data_path / "scrape_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{shop_id}_{item_id}.json"


def _product_url(shop_id: str, item_id: str) -> str:
    return f"https://shopee.ph/product/{shop_id}/{item_id}"


class ShopeeBlockedError(Exception):
    """Raised when Shopee's anti-bot system redirects us to a
    verification/login-required page instead of the real listing -- no
    amount of retrying gets past this, so callers should treat it as a
    non-retryable failure rather than burning the full timeout budget."""


async def _scrape_once(shop_id: str, item_id: str) -> list[str]:
    url = _product_url(shop_id, item_id)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, slow_mo=150)
        try:
            page = await browser.new_page(user_agent=_USER_AGENT)
            await page.goto(url, wait_until="networkidle", timeout=30000)

            if "/verify/" in page.url or "/login" in page.url:
                raise ShopeeBlockedError(
                    f"Shopee redirected to an anti-bot verification/login page ({page.url}) "
                    "instead of the listing -- scraping is blocked for this session."
                )

            og_image = await page.get_attribute('meta[property="og:image"]', "content")

            images = await page.eval_on_selector_all(
                "img",
                """(imgs) => imgs
                    .map(img => img.src)
                    .filter(src => src && (src.includes('susercontent.com') || src.includes('shopee')))""",
            )

            all_images = [og_image] if og_image else []
            all_images.extend(images)
            # de-dupe, preserve order
            seen = set()
            unique = []
            for img in all_images:
                if img and img not in seen:
                    seen.add(img)
                    unique.append(img)
            return unique
        finally:
            await browser.close()


async def scrape_listing_images(
    shop_id: str, item_id: str, retries: int = 3, backoff_seconds: float = 5.0
) -> list[str]:
    cache_file = _cache_path(shop_id, item_id)
    if cache_file.exists():
        cached = json.loads(cache_file.read_text())
        if cached.get("images"):
            return cached["images"]

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            images = await _scrape_once(shop_id, item_id)
            if images:
                cache_file.write_text(json.dumps({"images": images}))
                return images
        except ShopeeBlockedError as exc:
            # Not transient -- retrying hits the same anti-bot wall every time.
            logger.error("Scrape blocked by Shopee anti-bot check for %s/%s: %s", shop_id, item_id, exc)
            return []
        except Exception as exc:  # noqa: BLE001 - Shopee blocking is expected
            last_error = exc
            logger.warning("Scrape attempt %d/%d failed for %s/%s: %s", attempt, retries, shop_id, item_id, exc)
            if attempt < retries:
                await asyncio.sleep(backoff_seconds * attempt)

    if last_error:
        logger.error("All scrape attempts failed for %s/%s: %s", shop_id, item_id, last_error)
    return []


async def scrape_all_listings(shop_id: str, item_ids: list[str]) -> dict[str, list[str]]:
    """Multi-listing orders (multiple item IDs under one order) need images
    pulled for every listing, not just the first."""
    results = {}
    for item_id in item_ids:
        results[item_id] = await scrape_listing_images(shop_id, item_id)
    return results
