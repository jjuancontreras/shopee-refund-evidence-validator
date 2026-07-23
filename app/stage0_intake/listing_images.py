"""Stage 0 - listing images for the Visual Match inspector.

Shopee's anti-bot system redirects headless/automated traffic to a
verify/traffic/error login-required page before the real listing ever loads
(confirmed while testing the Playwright scraper in scraper.py) -- there's no
scraping past that without deliberately evading Shopee's bot detection, which
this project won't do. Per project decision, listing photos were instead
manually collected into a shared Drive folder and are mirrored locally here,
the same way stage0_intake/drive.py mirrors the proof-media folder.

This folder isn't organized per item_id (it's scoped to the single demo
order for now) -- every image in it is treated as a listing photo for
whichever item_id is requested. If/when multiple listings need distinct
photos, organize the Drive folder into per-item_id subfolders or filenames
and update `get_listing_images` to filter accordingly.
"""

from __future__ import annotations

from pathlib import Path

import gdown

from app.config import settings

_CACHE_DIRNAME = "listing_images_cache"
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _cache_dir() -> Path:
    d = settings.data_path / _CACHE_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def sync_listing_images_folder(folder_id: str | None = None, force: bool = False) -> Path:
    """Mirrors the shared Drive folder of manually-collected listing photos.
    Cached across runs; pass force=True to refresh after adding new photos."""
    folder_id = folder_id or settings.google_drive_listing_images_folder_id
    cache = _cache_dir()

    marker = cache / ".synced"
    if marker.exists() and not force:
        return cache

    if not folder_id:
        return cache

    url = f"https://drive.google.com/drive/folders/{folder_id}"
    gdown.download_folder(url=url, output=str(cache), quiet=False, use_cookies=False)
    marker.touch()
    return cache


def get_listing_images(item_id: str) -> list[Path]:
    """Returns local paths to listing photos for the given item_id. See
    module docstring -- currently returns every image in the shared folder
    regardless of item_id, since it's scoped to a single demo listing."""
    cache = sync_listing_images_folder()
    return sorted(p for p in cache.rglob("*") if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS)


async def get_all_listing_images(item_ids: list[str]) -> dict[str, list[Path]]:
    """Async to match the scrape_all_listings signature it replaces in the
    pipeline (multi-listing orders need images keyed per item_id)."""
    return {item_id: get_listing_images(item_id) for item_id in item_ids}
