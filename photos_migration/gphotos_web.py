"""Read-only Google Photos *web* search, driving the user's own Chrome via Playwright.

The Library API cannot see items it did not create, so finding GoPro media that is
already in the library (e.g. phone backups) needs the web UI. The user signs in
manually in a dedicated Chrome profile; this module only opens search pages,
reads result labels and takes screenshots. It never clicks, edits or deletes.

    python -m photos_migration.gphotos_web "GH011054.MP4" "GoPro" ...
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

from . import config

PROFILE = config.CONFIG_DIR / "chrome-profile"
OUT_DIR = config.DATA_DIR / "gp_search"


def _open(p):
    PROFILE.mkdir(parents=True, exist_ok=True)
    PROFILE.chmod(0o700)
    return p.chromium.launch_persistent_context(
        str(PROFILE), channel="chrome", headless=False,
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1400, "height": 900})


def _logged_in_page(ctx):
    """Any tab showing the signed-in Photos app (sign-in may finish in a new tab)."""
    for pg in ctx.pages:
        url = pg.url
        if url.startswith("https://photos.google.com/") and "/about" not in url and "accounts.google" not in url:
            return pg
    return None


def wait_for_login(ctx, timeout_s: int = 900):
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://photos.google.com/")
    time.sleep(3)
    t0 = time.time()
    print("Waiting for you to sign in to Google Photos in the Chrome window...", flush=True)
    while time.time() - t0 < timeout_s:
        try:
            pg = _logged_in_page(ctx)
            if pg is not None:
                return pg
            print("  tabs: " + " | ".join(p.url[:80] for p in ctx.pages), flush=True)
        except Exception:  # noqa: BLE001 - page navigating
            pass
        time.sleep(5)
    return None


def search(page, query: str, max_scrolls: int = 0) -> dict:
    page.goto("https://photos.google.com/search/" + urllib.parse.quote(query))
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(4)
    seen: dict[str, str | None] = {}
    for i in range(max_scrolls + 1):
        links = page.locator('a[href*="photo/"]')
        for j in range(links.count()):
            a = links.nth(j)
            href = a.get_attribute("href") or ""
            if href and href not in seen:
                seen[href] = a.get_attribute("aria-label")
        if i < max_scrolls:
            page.mouse.wheel(0, 4000)
            time.sleep(2)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shot = OUT_DIR / (re.sub(r"[^A-Za-z0-9._-]+", "_", query) + ".png")
    page.screenshot(path=str(shot))
    return {"query": query, "count": len(seen), "labels": list(seen.values())[:200], "screenshot": str(shot)}


def main(queries: list[str]) -> int:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = _open(p)
        page = wait_for_login(ctx)
        if page is None:
            print("Timed out waiting for sign-in.", flush=True)
            ctx.close()
            return 1
        print("Signed in. Searching...", flush=True)
        results = []
        for q in queries:
            r = search(page, q, max_scrolls=8 if not re.search(r"\.\w{3,4}$", q.strip("\"")) else 0)
            results.append(r)
            print(f"{q!r}: {r['count']} result(s)", flush=True)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
        ctx.close()
    return 0




LABEL_RE = re.compile(r"^(Photo|Video)\b.*?([A-Z][a-z]{2} \d{1,2}, \d{4}), (\d{1,2}:\d{2}:\d{2})\s?([AP]M)")


def parse_label(label: str | None):
    """'Video - Landscape - Aug 13, 2025, 12:26:12 PM' -> ('Video', datetime(2025,8,13,12,26,12))."""
    from datetime import datetime
    m = LABEL_RE.search((label or "").replace(" ", " "))
    if not m:
        return None
    return m[1], datetime.strptime(f"{m[2]} {m[3]} {m[4]}", "%b %d, %Y %I:%M:%S %p")


def index_dates(page, days: list[str], out_path: Path) -> dict:
    """Search each day and scroll until nothing new appears. Resumable via out_path."""
    index = json.loads(out_path.read_text()) if out_path.exists() else {}
    for day in days:
        if day in index:
            continue
        page.goto("https://photos.google.com/search/" + urllib.parse.quote(day))
        time.sleep(5)
        seen: dict[str, str | None] = {}
        dry = 0
        for _ in range(60):
            before = len(seen)
            links = page.locator('a[href*="photo/"]')
            for j in range(links.count()):
                a = links.nth(j)
                href = a.get_attribute("href") or ""
                if href and href not in seen:
                    seen[href] = a.get_attribute("aria-label")
            dry = dry + 1 if len(seen) == before else 0
            if dry >= 3:
                break
            page.mouse.wheel(0, 5000)
            time.sleep(1.5)
        index[day] = [{"href": h, "label": l} for h, l in seen.items()]
        out_path.write_text(json.dumps(index, indent=1, ensure_ascii=False))
        print(f"{day}: {len(seen)} items", flush=True)
    return index


def main_index() -> int:
    """Index every candidate capture date of the GoPro inventory."""
    from collections import Counter
    from datetime import datetime, timedelta
    from playwright.sync_api import sync_playwright
    inv = json.loads(config.INVENTORY.read_text())
    days = Counter()
    for x in inv:
        if (x.get("filename") or "").startswith("PXL_") or (x.get("camera_model") or "").startswith("Pixel"):
            continue
        t = datetime.strptime(x["captured_at"], "%Y-%m-%dT%H:%M:%SZ")
        for h in (-3, 0, 3):
            days[(t + timedelta(hours=h)).strftime("%b %-d, %Y")] += 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = _open(p)
        page = wait_for_login(ctx)
        if page is None:
            return 1
        index_dates(page, sorted(days), OUT_DIR / "date_index.json")
        ctx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main_index() if sys.argv[1:] == ["--index-dates"] else main(sys.argv[1:]))
