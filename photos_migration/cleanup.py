"""Move superseded copies in Google Photos to the trash (recoverable for 30 days).

Candidates:
- phone copies: old copies of GoPro files that the Quik app saved on the phone and the
  phone backed up (old_copies.csv). Only for GoPro items that are COMPLETELY in Google
  (every chapter/frame DONE and readable), and never for items Google merged into an
  identical original (those copies ARE the originals now);
- superseded: this app's own wrong-date versions replaced by fix-dates/fix-drift, only once
  the replacement is DONE and readable.

The same ledger rules are checked again right before each click, and the item's info
panel must match:
- phone copy: 'Uploaded from Android device' and a file name starting with the GoPro
  file's stem (GOPR1310_ALTA....JPG for GOPR1310.JPG);
- superseded: our exact file name and the old version's date.

    python -m photos_migration cleanup --plan            # build cleanup_plan.csv
    python -m photos_migration cleanup --limit 10        # trash the first 10 pending
"""
from __future__ import annotations

import csv
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .completeness import item_fully_in_google
from .gphotos import GooglePhotos
from .ledger import Ledger
from .metadata import Capture

PLAN = config.DATA_DIR / "cleanup_plan.csv"
OLD_COPIES = config.DATA_DIR / "old_copies.csv"
SCHEMA = """CREATE TABLE IF NOT EXISTS cleanup (
    url TEXT PRIMARY KEY, kind TEXT, gopro_id TEXT, expect TEXT, status TEXT, info TEXT, at TEXT)"""


def _readable(led: Ledger, gp: GooglePhotos) -> set[str]:
    """Media item ids the app can read back (= real, app-created items)."""
    ids = [r["media_item_id"] for r in led.rows("status = 'DONE' AND media_item_id IS NOT NULL")]
    ok = set()
    for i in range(0, len(ids), 50):
        ok |= {res["mediaItem"]["id"] for res in gp.batch_get(ids[i:i + 50]) if "mediaItem" in res}
    return ok


def _pid(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


class Rules:
    """Ledger-side conditions for trashing, evaluated at plan time and again before each click."""

    def __init__(self, led: Ledger, gp: GooglePhotos):
        self.led = led
        self.readable = _readable(led, gp)
        self.inv = {x["id"]: x for x in json.loads(config.INVENTORY.read_text())}
        self.refresh()

    def refresh(self) -> None:
        self.rows_by_item = defaultdict(list)
        self.by_unit = {}
        for r in self.led.rows():
            self.rows_by_item[r["gopro_id"]].append(r)
            self.by_unit[r["unit_id"]] = r

    def phone_copy_ok(self, gid: str) -> tuple[bool, str]:
        item = self.inv.get(gid)
        if item is None:
            return False, "GoPro item not in inventory"
        return item_fully_in_google(item, self.rows_by_item.get(gid, []), self.by_unit, self.readable)

    def superseded_ok(self, old_id: str) -> tuple[bool, str]:
        row = self.led.db.execute("SELECT unit_id FROM superseded WHERE old_media_item_id = ? AND state = 'active'",
                                  (old_id,)).fetchone()
        if row is None:
            return False, "not a superseded item"
        u = self.led.get(row[0])
        if not (u and u["status"] == "DONE" and u["media_item_id"] and u["media_item_id"] != old_id
                and u["media_item_id"] in self.readable):
            return False, "replacement not DONE and readable yet"
        return True, ""

    def ok(self, kind: str, gid: str, url: str) -> tuple[bool, str]:
        if kind == "phone_copy":
            return self.phone_copy_ok(gid)
        if kind == "superseded":
            return self.superseded_ok(_pid(url))
        return False, f"kind {kind} is not trashed automatically"


def _promote_finished(led: Ledger) -> None:
    """A redate interrupted after its upsert is finished later by `run`: its superseded row must
    still become active once the replacement is DONE (with a different item id)."""
    led.db.execute("UPDATE superseded SET state = 'active' WHERE state = 'pending' AND EXISTS (SELECT 1 FROM units u "
                   "WHERE u.unit_id = superseded.unit_id AND u.status = 'DONE' AND u.media_item_id IS NOT NULL "
                   "AND u.media_item_id != superseded.old_media_item_id)")


def build_plan(takeover: bool = False) -> int:
    led = Ledger()
    led.acquire_lease(takeover)
    try:
        return _build_plan(led, GooglePhotos())
    finally:
        led.release_lease()


def _build_plan(led: Ledger, gp: GooglePhotos) -> int:
    led.db.execute(SCHEMA)
    rules = Rules(led, gp)
    rows, skipped = [], Counter()
    for c in csv.DictReader(open(OLD_COPIES)):
        ok, why = rules.phone_copy_ok(c["gopro_id"])
        if not ok:
            skipped[why.split(":")[0]] += 1
            continue
        stem = Path(c["gopro_filename"]).stem
        if c["gopro_type"] in ("TimeLapse", "Burst"):
            stem = stem[:4]                      # frames G0060781... share the G006 group prefix
        rows.append({"kind": "phone_copy", "url": c["gp_url"], "gopro_id": c["gopro_id"],
                     "expect": stem, "gopro_filename": c["gopro_filename"], "gp_label": c["gp_label"],
                     "confidence": c["confidence"]})
    from .redate import ensure_schema
    ensure_schema(led)
    _promote_finished(led)
    for uid, old in led.db.execute("SELECT unit_id, old_media_item_id FROM superseded WHERE state = 'active'").fetchall():
        try:
            mi = gp.get(old)
        except Exception:  # noqa: BLE001 - not app-created (Google merged it into a phone copy) or gone
            skipped["superseded item not readable (phone copy or already gone)"] += 1
            continue
        ok, why = rules.superseded_ok(old)
        if not ok:
            skipped[why] += 1
            continue
        rows.append({"kind": "superseded", "url": mi["productUrl"], "gopro_id": led.get(uid)["gopro_id"],
                     "expect": mi.get("filename"), "gopro_filename": led.get(uid)["filename"],
                     "gp_label": mi.get("mediaMetadata", {}).get("creationTime"), "confidence": "exact"})
    unique = list({r["url"]: r for r in rows}.values())
    if unique:
        with open(PLAN, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(unique[0]))
            w.writeheader()
            w.writerows(unique)
        PLAN.chmod(0o600)
    for r in unique:      # a superseded item skipped earlier is verified again from scratch before any click
        led.db.execute("INSERT INTO cleanup (url, kind, gopro_id, expect, status) VALUES (?,?,?,?, 'PENDING') "
                       "ON CONFLICT(url) DO UPDATE SET status = 'PENDING' WHERE status = 'SKIPPED' AND kind = 'superseded'",
                       (r["url"], r["kind"], r["gopro_id"], r["expect"]))
    print(json.dumps({"to_trash": len(unique), "by_kind": Counter(r["kind"] for r in unique),
                      "by_confidence": Counter(r["confidence"] for r in unique), "skipped": skipped}, indent=1))
    return 0


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


def _panel_date(d: datetime) -> str:
    """How the info panel writes a date: 'Sep 2, 2020'."""
    return f"{d:%b} {d.day}, {d.year}"


def _old_local(led: Ledger, url: str) -> datetime | None:
    row = led.db.execute("SELECT old_capture_json FROM superseded WHERE old_media_item_id = ?", (_pid(url),)).fetchone()
    return Capture.from_json(row[0]).local if row and row[0] else None


def _verify(kind: str, expect: str, panel: str, old_local: datetime | None = None) -> tuple[bool, str]:
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
    if old_local is None:
        return False, "no recorded date for the old version"
    if _panel_date(old_local) not in lines[:4]:
        return False, f"date is not the old version's {_panel_date(old_local)} ({' | '.join(lines[1:4])[:80]})"
    return True, name


def _is_gone(page, url: str) -> bool:
    """A trashed app item no longer opens: Google shows "Can't access photo"."""
    page.goto("https://photos.google.com/")
    time.sleep(2)
    page.goto(url)
    time.sleep(5)
    body = page.inner_text("body")
    return "Can’t access photo" in body or "Can't access photo" in body or "doesn’t exist" in body


def recheck_uncertain(takeover: bool = False) -> None:
    from playwright.sync_api import sync_playwright
    from .gphotos_web import _open, wait_for_login
    led = Ledger()
    led.acquire_lease(takeover)
    try:
        _recheck(led, sync_playwright, _open, wait_for_login)
    finally:
        led.release_lease()


def _recheck(led, sync_playwright, _open, wait_for_login) -> None:
    rows = led.db.execute("SELECT url, kind, info FROM cleanup WHERE status = 'UNCERTAIN'").fetchall()
    with sync_playwright() as p:
        ctx = _open(p)
        page = wait_for_login(ctx)
        in_trash = trash_ids(page)
        for url, kind, info in rows:
            gone = _pid(url) in in_trash or (kind == "superseded" and _is_gone(page, url))
            if gone:
                led.db.execute("UPDATE cleanup SET status='TRASHED', info='confirmed in trash' WHERE url=?", (url,))
            print(f"{'TRASHED' if gone else 'still UNCERTAIN (check by hand)'} {kind} {info}", flush=True)
        ctx.close()


def execute(limit: int, dry_run: bool = False, kind: str | None = None, takeover: bool = False) -> int:
    led = Ledger()
    led.acquire_lease(takeover)
    try:
        return _execute(led, GooglePhotos(), limit, dry_run, kind)
    finally:
        led.release_lease()


def _execute(led: Ledger, gp: GooglePhotos, limit: int, dry_run: bool, kind: str | None) -> int:
    from playwright.sync_api import sync_playwright
    from .gphotos_web import _open, wait_for_login
    led.db.execute(SCHEMA)
    rules = Rules(led, gp)
    todo = led.db.execute("SELECT url, kind, gopro_id, expect FROM cleanup WHERE status = 'PENDING' "
                          "AND (? IS NULL OR kind = ?) ORDER BY kind DESC, url LIMIT ?", (kind, kind, limit)).fetchall()
    with sync_playwright() as p:
        ctx = _open(p)
        page = wait_for_login(ctx)
        in_trash = trash_ids(page)
        print(f"{len(in_trash)} items already in trash", flush=True)
        errors = 0
        for url, kind_, gid, expect in todo:
            if _pid(url) in in_trash:
                led.db.execute("UPDATE cleanup SET status='TRASHED', info='found in trash' WHERE url=?", (url,))
                continue
            rules.refresh()
            ok, why = rules.ok(kind_, gid, url)
            if not ok:
                print(f"KEEP PENDING {kind_} {expect}: {why}", flush=True)
                continue
            try:
                _one(page, led, url, kind_, expect, in_trash, dry_run)
                errors = 0
            except Exception as e:  # noqa: BLE001 - slow page / network: leave PENDING, move on
                errors += 1
                print(f"ERROR {kind_} {expect}: {type(e).__name__} (left PENDING)", flush=True)
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
    """Verify one candidate on its own page and move it to the trash. Returns 1 if trashed."""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    now = datetime.now(timezone.utc).isoformat()
    if _pid(url) not in page.url:
        led.db.execute("UPDATE cleanup SET status='SKIPPED', info=?, at=? WHERE url=?",
                       (f"page moved to {page.url[-40:]}", now, url))
        print(f"SKIP {kind} {expect}: page is not the planned item", flush=True)
        return 0
    panel = _details(page)
    ok, info = _verify(kind, expect, panel, _old_local(led, url) if kind == "superseded" else None)
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
    if _pid(url) not in page.url:                # the viewer must still show the verified item
        return 0
    toolbar.first.click()
    time.sleep(1.5)
    # one-time notice ("Got it, continue") and then the confirmation; stop after confirming once
    for _ in range(2):
        dlg = page.get_by_role("dialog")
        if dlg.count() == 0:
            break
        btn = dlg.get_by_role("button", name=re.compile(r"^(Got it, continue|Move to trash)$", re.I))
        if btn.count() == 0:
            break
        label = btn.first.inner_text().strip()
        btn.first.click()
        time.sleep(2)
        if label.lower() == "move to trash":
            break
    undo = False
    for _ in range(12):                      # the toast goes "Moving to trash..." -> "Moved to trash  Undo"
        time.sleep(1)
        if page.get_by_role("button", name="Undo").count() > 0:
            undo = True
            break
    gone = undo or (kind == "superseded" and _is_gone(page, url))
    status = "TRASHED" if gone else "UNCERTAIN"
    if gone:
        in_trash.add(_pid(url))
    led.db.execute("UPDATE cleanup SET status=?, info=?, at=? WHERE url=?", (status, info, now, url))
    print(f"{status} {kind} {info}", flush=True)
    return 1 if gone else 0
