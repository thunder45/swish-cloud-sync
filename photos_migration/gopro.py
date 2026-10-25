"""GoPro Cloud client (unofficial web API, browser-cookie auth). Read-only."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from . import config

API = "https://api.gopro.com"


class GoProError(RuntimeError):
    pass


class GoProAuthError(GoProError):
    """Cookies expired/blocked: abort the whole run, never mark items."""


@dataclass
class Source:
    """One downloadable original belonging to a GoPro Cloud media item."""
    gopro_id: str
    part: int | None          # chapter number when the item has several sources
    label: str                # 'source', 'baked_source' (Quik edit) or 'zip' (time-lapse/burst frames)
    url: str = field(repr=False)  # signed CDN URL - never log or persist it
    filename: str


class GoProClient:
    def __init__(self, cookies: str | None = None):
        cookies = cookies or config.GOPRO_COOKIES.read_text().strip()
        self.session = requests.Session()
        self.session.headers.update({
            "Cookie": cookies,
            "User-Agent": config.USER_AGENT,
            "Accept": "application/vnd.gopro.jk.media+json; version=2.0.0",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://gopro.com/",
        })

    def _get(self, path: str, **params) -> dict:
        r = None
        for attempt in range(6):
            try:
                r = self.session.get(f"{API}{path}", params=params, timeout=60)
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt == 5:
                    raise config.NetworkDown(f"GoPro API unreachable: {type(e).__name__}") from None
                time.sleep(10 * 2 ** attempt)
                continue
            except requests.RequestException as e:
                raise GoProError(f"GET {path}: {type(e).__name__}") from None
            if r.status_code in (401, 403):
                raise GoProAuthError(f"HTTP {r.status_code}: cookies expired or blocked - refresh {config.GOPRO_COOKIES}")
            if r.status_code == 429 or r.status_code >= 500 or (r.status_code == 404 and attempt < 2):
                time.sleep(2 ** attempt * 5)      # GoPro sometimes answers a transient 404
                continue
            if r.status_code >= 400:
                raise GoProError(f"GET {path}: HTTP {r.status_code}")
            return r.json()
        # a service that keeps failing for one item must not pause the whole run: fail the item
        raise GoProError(f"GET {path} kept failing (last HTTP {r.status_code if r is not None else '?'})")

    def list_all(self, page_sizes=(100, 97, 50)) -> list[dict]:
        """List every media item.

        /media/search pagination is lossy (an item can repeat at a page boundary
        while another is skipped), so list with several page sizes and union by id
        until the count matches _pages.total_items.
        """
        items: dict[str, dict] = {}
        total = None
        for per_page in page_sizes:
            page, pages = 1, 1
            while page <= pages:
                d = self._get("/media/search", page=page, per_page=per_page)
                pages, total = d["_pages"]["total_pages"], d["_pages"]["total_items"]
                for it in d["_embedded"]["media"]:
                    it.pop("token", None)
                    items[it["id"]] = it
                page += 1
                time.sleep(0.5)
            if len(items) == total:
                break
        if total is not None and len(items) != total:
            raise GoProError(f"listed {len(items)} unique items but API reports {total}")
        return list(items.values())

    def sources(self, item: dict) -> list[Source]:
        """Original files for an item. Never returns proxies/transcodes."""
        emb = self._get(f"/media/{item['id']}/download")["_embedded"]
        kind = item.get("type")
        name = item.get("filename") or f"{item['id']}.mp4"
        if kind == "MultiClipEdit":
            # Quik edits have empty or UUID file names
            name = f"QuikEdit_{(item.get('captured_at') or '')[:10]}_{item['id'][:8]}.mp4"
        if kind in ("TimeLapse", "Burst"):
            picks = [s for s in emb.get("sidecar_files", []) if s.get("label") == "zip"]
            label = "zip"
            name = Path(name).stem + ".zip"
        elif kind == "MultiClipEdit":
            picks = [v for v in emb.get("variations", []) if v.get("label") == "baked_source"]
            label = "baked_source"
        else:
            picks = [v for v in emb.get("variations", []) if v.get("label") == "source"]
            label = "source"
        if not picks:
            raise GoProError(f"no original ({label}) listed for {item['id']} {name}")
        missing = [p for p in picks if not p.get("available")]
        if missing:
            raise GoProError(f"{len(missing)} of {len(picks)} originals ({label}) not available for {item['id']} {name}")
        if label == "zip" and len(picks) > 1:
            raise GoProError(f"{len(picks)} zip sidecars for {item['id']} - unexpected, not handled")
        picks.sort(key=lambda p: p.get("item_number") or 0)
        # chaptered uploads list one 'source' per chapter with item_number; older uploads
        # hold a single merged source (item_number None) even when item_count > 1
        multi = len(picks) > 1
        if multi and any(p.get("item_number") is None for p in picks):
            raise GoProError(f"ambiguous chapters for {item['id']} {name}")
        if multi and kind in ("Video", "TimeLapseVideo") and len(picks) != (item.get("item_count") or 1):
            raise GoProError(f"{len(picks)} chapters but item_count {item.get('item_count')} for {item['id']} {name}")
        out = []
        for p in picks:
            part = p.get("item_number") if multi else None
            fname = name
            if multi:
                stem, ext = Path(name).stem, Path(name).suffix
                fname = f"{stem}_part{part}{ext}"
            out.append(Source(item["id"], part, label, p["url"], fname))
        return out

    def fresh_source(self, item: dict, part: int | None, label: str) -> Source:
        """Signed URLs expire (~1 h): fetch a new one right before each download."""
        for s in self.sources(item):
            if s.part == part and s.label == label:
                return s
        raise GoProError(f"source part {part} ({label}) disappeared for {item['id']}")

    def download(self, src: Source, dest: Path, expected_size: int | None = None) -> tuple[int, str]:
        """Stream to dest (atomically via .part). Returns (bytes, sha256). Never leaks the signed URL."""
        tmp = dest.with_suffix(dest.suffix + ".part")
        h, n = hashlib.sha256(), 0
        try:
            with requests.get(src.url, headers={"User-Agent": config.USER_AGENT}, stream=True, timeout=120) as r:
                if r.status_code >= 400:
                    raise GoProError(f"download {src.filename} failed: HTTP {r.status_code}")
                declared = int(r.headers.get("Content-Length") or 0) or None
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(8 << 20):
                        f.write(chunk)
                        h.update(chunk)
                        n += len(chunk)
        except requests.RequestException as e:
            tmp.unlink(missing_ok=True)
            # connection lost mid-download: pause the run instead of failing the unit
            raise config.NetworkDown(f"download {src.filename} interrupted: {type(e).__name__}") from None
        if declared is not None and n != declared:
            tmp.unlink(missing_ok=True)
            raise GoProError(f"truncated download {src.filename}: {n} of {declared} bytes")
        if expected_size is not None and n != expected_size and abs(n - expected_size) > 64 * 1024:
            tmp.unlink(missing_ok=True)
            raise GoProError(f"size mismatch {src.filename}: got {n}, GoPro lists {expected_size}")
        # a few KB off the listing (seen on some Quik uploads) is accepted; the caller verifies the media
        tmp.rename(dest)
        return n, h.hexdigest()


def load_inventory() -> list[dict]:
    return json.loads(config.INVENTORY.read_text())
