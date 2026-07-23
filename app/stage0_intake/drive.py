"""Stage 0 - pull proof media bytes from the shared Google Drive folder.

Column G in the sheet only holds a bare filename, not a link, so the actual
approach is: mirror the shared folder locally once (cached), then match the
filename we got from the sheet against what's on disk.

Uses `gdown` for the folder download so no OAuth/service-account credentials
are needed for a folder shared as "Anyone with the link can view".
"""

from __future__ import annotations

from pathlib import Path

import gdown

from app.config import settings

_DRIVE_CACHE_DIRNAME = "drive_cache"


def _cache_dir() -> Path:
    d = settings.data_path / _DRIVE_CACHE_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def sync_drive_folder(folder_id: str | None = None, force: bool = False) -> Path:
    """Mirrors the shared Drive folder locally. Cached across runs so a demo
    doesn't re-download every time; pass force=True to refresh."""
    folder_id = folder_id or settings.google_drive_folder_id
    cache = _cache_dir()

    marker = cache / ".synced"
    if marker.exists() and not force:
        return cache

    url = f"https://drive.google.com/drive/folders/{folder_id}"
    gdown.download_folder(url=url, output=str(cache), quiet=False, use_cookies=False)
    marker.touch()
    return cache


def find_proof_file(filename: str) -> Path | None:
    """Matches a filename from the sheet (column G) to the actual file
    mirrored from Drive. Falls back to stem-only matching in case the sheet
    filename is missing/has a different extension than what's in Drive."""
    cache = sync_drive_folder()

    exact = list(cache.rglob(filename))
    if exact:
        return exact[0]

    stem = Path(filename).stem
    stem_matches = [p for p in cache.rglob(f"{stem}.*") if p.is_file()]
    return stem_matches[0] if stem_matches else None


def fetch_proof_files(filenames: list[str]) -> list[Path]:
    found = []
    for name in filenames:
        path = find_proof_file(name)
        if path:
            found.append(path)
    return found
