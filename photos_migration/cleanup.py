"""Move superseded copies in Google Photos to the trash (recoverable for 60 days).

Candidates:
- phone copies: old Storage-saver copies backed up from the Android phone (old_copies.csv),
  only for GoPro items whose new original is confirmed in Google, and never for items
  Google merged into an identical original (those copies ARE the originals now);
- superseded: this app's own wrong-date (clock-reset) versions replaced by fix-dates.

Every item is re-checked on its info panel right before deletion:
- phone copy: 'Uploaded from Android device' and a file name starting with the GoPro
  file's stem (GOPR1310_ALTA....JPG for GOPR1310.JPG);
- superseded: our exact file name and a 2015/2016 date.

    python -m photos_migration cleanup --plan            # build cleanup_plan.csv
    python -m photos_migration cleanup --limit 10        # delete the first 10 pending
"""
from __future__ import annotations

import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .gphotos import API, GooglePhotos
from .ledger import Ledger

PLAN = config.DATA_DIR / "cleanup_plan.csv"
OLD_COPIES = config.DATA_DIR / "old_copies.csv"
SCHEMA = """CREATE TABLE IF NOT EXISTS cleanup (
    url TEXT PRIMARY KEY, kind TEXT, gopro_id TEXT, expect TEXT, status TEXT, info TEXT, at TEXT)"""


def _readable(led: Ledger, gp: GooglePhotos) -> set[str]:
    """Media item ids the app can read back (= real, app-created items)."""
    ids = [r["media_item_id"] for r in led.rows("status = 'DONE' AND media_item_id IS NOT NULL")]
    ok = set()
    for i in range(0, len(ids), 50):
        r = gp.session.get(f"{API}/mediaItems:batchGet", params={"mediaItemIds": ids[i:i + 50]}, timeout=120)
        gp._check(r, "batchGet")
        ok |= {res["mediaItem"]["id"] for res in r.json().get("mediaItemResults", []) if "mediaItem" in res}
    return ok


def build_plan() -> int:
    led, gp = Ledger(), GooglePhotos()
    led.db.execute(SCHEMA)
    readable = _readable(led, gp)
    units_by_item: dict[str, list] = {}
    for r in led.rows("status IN ('DONE','DUPLICATE')"):
        units_by_item.setdefault(r["gopro_id"], []).append(r)

    def replaced_ok(gid: str) -> tuple[bool, str]:
        us = units_by_item.get(gid, [])
        if not us:
            return False, "no new upload"
        for u in us:
            if u["status"] == "DUPLICATE":
                continue
            if u["media_item_id"] not in readable:
                return False, "merged into an identical original (this copy IS the original)"
        return True, ""

    rows, skipped = [], {}
    for c in csv.DictReader(open(OLD_COPIES)):
        ok, why = replaced_ok(c["gopro_id"])
        if not ok:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        stem = Path(c["gopro_filename"]).stem
        if c["gopro_type"] in ("TimeLapse", "Burst"):
            stem = stem[:4]                      # frames G0060781... share the G006 group prefix
        rows.append({"kind": "phone_copy", "url": c["gp_url"], "gopro_id": c["gopro_id"],
                     "expect": stem, "gopro_filename": c["gopro_filename"], "gp_label": c["gp_label"],
                     "confidence": c["confidence"]})
    for s in led.db.execute("SELECT unit_id, old_media_item_id FROM superseded"):
        uid, old = s[0], s[1]
        if old not in readable and not _app_item_exists(gp, old):
            # the wrong-date version was Google's merge into a phone copy; that copy is in old_copies
            skipped["superseded item is a phone copy (handled via old_copies)"] = \
                skipped.get("superseded item is a phone copy (handled via old_copies)", 0) + 1
            continue
        mi = gp.get(old)
        rows.append({"kind": "superseded", "url": mi["productUrl"], "gopro_id": led.get(uid)["gopro_id"],
                     "expect": mi.get("filename"), "gopro_filename": led.get(uid)["filename"],
                     "gp_label": mi.get("mediaMetadata", {}).get("creationTime"), "confidence": "exact"})
    seen, unique = set(), []
    for r in rows:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)
    with open(PLAN, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(unique[0]))
        w.writeheader()
        w.writerows(unique)
    PLAN.chmod(0o600)
    for r in unique:
        led.db.execute("INSERT OR IGNORE INTO cleanup (url, kind, gopro_id, expect, status) VALUES (?,?,?,?, 'PENDING')",
                       (r["url"], r["kind"], r["gopro_id"], r["expect"]))
    from collections import Counter
    print(json.dumps({"to_trash": len(unique), "by_kind": Counter(r["kind"] for r in unique),
                      "by_confidence": Counter(r["confidence"] for r in unique), "skipped": skipped}, indent=1))
    return 0


def _app_item_exists(gp: GooglePhotos, mid: str) -> bool:
    r = gp.session.get(f"{API}/mediaItems/{mid}", timeout=60)
    return r.status_code == 200


def trash_ids(page) -> set[str]:
    """IDs currently in the trash (trash grid links are ./trash/<id>, same id as ./photo/<id>)."""
    page.goto("https://photos.google.com/trash")
    time.sleep(6)
    ids, dry = set(), 0
    for _ in range(200):
        before = len(ids)
        hrefs = page.eval_on_selector_all("a[href*='trash/']", "els => els.map(e => e.getAttribute('href'))")
        ids |= {h.rstrip("/").split("/")[-1] for h in hrefs if h}
        dry = dry + 1 if len(ids) == before else 0
        if dry >= 6:
            break
        page.mouse.wheel(0, 5000)
        time.sleep(1.2)
    return ids


def _pid(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def _details(page) -> str:
    txt = page.inner_text("body")
    for _ in range(5):
        if "Details" in txt:
            break
        page.keyboard.press("i")
        time.sleep(3)
        txt = page.inner_text("body")
    i = txt.find("Details")
    return txt[i:i + 600] if i >= 0 else ""


def _verify(kind: str, expect: str, panel: str) -> tuple[bool, str]:
    lines = [l.strip() for l in panel.splitlines() if l.strip()]
    names = [l for l in lines if re.search(r"\.(jpe?g|mp4|mov|heic)$", l, re.I)]
    if not names:
        return False, "no file name in info panel"
    name = names[0]
    if kind == "phone_copy":
        if "Uploaded from Android device" not in panel:
            return False, f"not a phone backup ({name})"
        if not name.upper().startswith(expect.upper()):
            return False, f"file name {name} does not start with {expect}"
        return True, name
    if name != expect:
        return False, f"file name {name} != {expect}"
    if kind == "own_duplicate":
        return True, name
    if not re.search(r"\b(2015|2016)\b", " ".join(lines[:3])):
        return False, "date is not 2015/2016"
    return True, name


def _is_gone(page, url: str) -> bool:
    """A trashed item no longer opens: Google shows "Can't access photo"."""
    page.goto("https://photos.google.com/")
    time.sleep(2)
    page.goto(url)
    time.sleep(5)
    body = page.inner_text("body")
    return "Can\u2019t access photo" in body or "Can't access photo" in body or "doesn\u2019t exist" in body


def recheck_uncertain() -> None:
    from playwright.sync_api import sync_playwright
    from .gphotos_web import _open, wait_for_login
    led = Ledger()
    rows = led.db.execute("SELECT url, kind, info FROM cleanup WHERE status = 'UNCERTAIN'").fetchall()
    with sync_playwright() as p:
        ctx = _open(p)
        page = wait_for_login(ctx)
        for url, kind, info in rows:
            gone = _is_gone(page, url)
            led.db.execute("UPDATE cleanup SET status=? WHERE url=?", ("TRASHED" if gone else "PENDING", url))
            print(f"{'TRASHED' if gone else 'still there -> PENDING'} {kind} {info}", flush=True)
        ctx.close()


def execute(limit: int, dry_run: bool = False, kind: str | None = None) -> int:
    from playwright.sync_api import sync_playwright
    from .gphotos_web import _open, wait_for_login
    led = Ledger()
    led.db.execute(SCHEMA)
    todo = led.db.execute("SELECT url, kind, gopro_id, expect FROM cleanup WHERE status = 'PENDING' "
                          "AND (? IS NULL OR kind = ?) ORDER BY kind DESC, url LIMIT ?", (kind, kind, limit)).fetchall()
    done = 0
    with sync_playwright() as p:
        ctx = _open(p)
        page = wait_for_login(ctx)
        in_trash = trash_ids(page)
        print(f"{len(in_trash)} items already in trash", flush=True)
        errors = 0
        for url, kind, gid, expect in todo:
            if kind == "phone_copy" and _pid(url) in in_trash:
                led.db.execute("UPDATE cleanup SET status='TRASHED', info='found in trash' WHERE url=?", (url,))
                continue
            try:
                done += _one(page, led, url, kind, expect, in_trash, dry_run)
                errors = 0
            except Exception as e:  # noqa: BLE001 - slow page / network: leave PENDING, move on
                if "no Undo toast" in str(e):
                    print(f"STOPPED: {e}", flush=True)
                    break
                errors += 1
                print(f"ERROR {kind} {expect}: {type(e).__name__} (left PENDING)", flush=True)
                if errors >= 5:
                    print("STOPPED: 5 consecutive errors", flush=True)
                    break
                time.sleep(20)
        # resolve UNCERTAIN (no Undo toast seen) against the trash itself; never click them again
        unc = led.db.execute("SELECT url FROM cleanup WHERE status = 'UNCERTAIN'").fetchall()
        if unc:
            in_trash = trash_ids(page)
            for (u,) in unc:
                if _pid(u) in in_trash:
                    led.db.execute("UPDATE cleanup SET status='TRASHED', info='confirmed in trash' WHERE url=?", (u,))
        ctx.close()
    print({r[0]: r[1] for r in led.db.execute("SELECT status, COUNT(*) FROM cleanup GROUP BY status")})
    return 0


def _one(page, led, url, kind, expect, in_trash, dry_run) -> int:
    """Verify one candidate and move it to the trash. Returns 1 if trashed."""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    panel = _details(page)
    ok, info = _verify(kind, expect, panel)
    now = datetime.now(timezone.utc).isoformat()
    if not ok:
        led.db.execute("UPDATE cleanup SET status='SKIPPED', info=?, at=? WHERE url=?", (info, now, url))
        print(f"SKIP {kind} {expect}: {info}", flush=True)
        return 0
    if dry_run:
        print(f"WOULD TRASH {kind} {info}", flush=True)
        return 0
    toolbar = page.get_by_role("button", name="Move to trash")
    if toolbar.count() == 0 or page.get_by_role("button", name=re.compile("Restore|Delete permanently", re.I)).count():
        led.db.execute("UPDATE cleanup SET status='SKIPPED', info=?, at=? WHERE url=?",
                       ("no 'Move to trash' button (already in trash?)", now, url))
        print(f"SKIP {kind} {info}: no Move to trash button", flush=True)
        return 0
    toolbar.first.click()
    time.sleep(1.5)
    # one-time notices ("Got it, continue") and/or a confirmation dialog
    for _ in range(3):
        dlg = page.get_by_role("dialog")
        if dlg.count() == 0:
            break
        btn = dlg.get_by_role("button", name=re.compile(r"^(Got it, continue|Move to trash)$", re.I))
        if btn.count() == 0:
            break
        btn.first.click()
        time.sleep(2)
    undo = False
    for _ in range(12):                      # the toast goes "Moving to trash..." -> "Moved to trash  Undo"
        time.sleep(1)
        if page.get_by_role("button", name="Undo").count() > 0:
            undo = True
            break
    gone = undo or (kind in ("superseded", "own_duplicate") and _is_gone(page, url))
    status = "TRASHED" if gone else "UNCERTAIN"
    if gone:
        in_trash.add(_pid(url))
    led.db.execute("UPDATE cleanup SET status=?, info=?, at=? WHERE url=?", (status, info, now, url))
    print(f"{status} {kind} {info}", flush=True)
    return 1 if gone else 0


def retarget_skipped() -> int:
    """Second pass for SKIPPED phone copies whose panel name shows the real GoPro file
    (neighbour mismatches such as GOPR0083_... matched to GOPR0082): re-point them to
    that item, only if its new original is confirmed readable in Google."""
    led, gp = Ledger(), GooglePhotos()
    readable = _readable(led, gp)
    inv = json.loads(config.INVENTORY.read_text())
    by_stem: dict[str, list[dict]] = {}
    for x in inv:
        if x.get("filename"):
            by_stem.setdefault(Path(x["filename"]).stem.upper(), []).append(x)
    n = 0
    for url, info in led.db.execute("SELECT url, info FROM cleanup WHERE status='SKIPPED' AND kind='phone_copy'").fetchall():
        m = re.match(r"file name (G[A-Z0-9]+?)_\S+ does not start", info or "", re.I)
        if not m:
            continue                                   # e.g. DSC_6858.JPG: not a GoPro file, never touch
        stem = m[1].upper()
        items = by_stem.get(stem, [])
        ok = False
        for x in items:
            units = led.rows("gopro_id = ? AND status IN ('DONE','DUPLICATE')", x["id"])
            if units and all(u["status"] == "DUPLICATE" or u["media_item_id"] in readable for u in units):
                ok = True
        if ok:
            led.db.execute("UPDATE cleanup SET status='PENDING', expect=?, info='retargeted' WHERE url=?", (stem, url))
            n += 1
            print(f"retargeted -> {stem}: {url[-20:]}")
    print(f"{n} skipped copies re-pointed to their real GoPro item")
    return n


def add_own_duplicate(gopro_id: str, reason: str) -> None:
    """Queue one of this app's uploads (a duplicate that came from GoPro Cloud itself)."""
    led, gp = Ledger(), GooglePhotos()
    for u in led.rows("gopro_id = ? AND status = 'DONE'", gopro_id):
        mi = gp.get(u["media_item_id"])
        led.db.execute("INSERT OR IGNORE INTO cleanup (url, kind, gopro_id, expect, status, info) "
                       "VALUES (?, 'own_duplicate', ?, ?, 'PENDING', ?)", (mi["productUrl"], gopro_id, mi["filename"], reason))
        led.upsert(u["unit_id"], status="EXCLUDED", note=reason)
        print(f"queued own duplicate {mi['filename']}")
