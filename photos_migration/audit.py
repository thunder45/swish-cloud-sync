"""End-to-end audit: GoPro inventory <-> ledger <-> Google Photos.

1. Completeness: every expected unit (photo, video, chapter, time-lapse frame) of every
   GoPro item is DONE, or DUPLICATE of a DONE unit with the same original SHA-256, or
   EXCLUDED by an explicit rule. Anything else is listed.
2. Google read-back (batchGet): each DONE media item exists, has the expected file
   name and creation time, and videos finished processing.
3. No duplicates created by this app: list every app-created item and compare with the
   ledger (extra items = duplicates/orphans), and check albums.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from . import config
from .completeness import item_fully_in_google
from .gphotos import API, GooglePhotos
from .ledger import Ledger
from .metadata import Capture
from .pipeline import is_phone_media, read_review_decisions

TINY_VIDEO_MS = 1000      # Google Photos silently drops clips this short

OUT = config.DATA_DIR / "audit_problems.csv"


def completeness(inv: list[dict], led: Ledger) -> list[dict]:
    rows_by_item = defaultdict(list)
    for r in led.rows():
        rows_by_item[r["gopro_id"]].append(r)
    by_unit = {r["unit_id"]: r for rs in rows_by_item.values() for r in rs}
    decisions = read_review_decisions()
    problems = []
    for x in inv:
        rs = rows_by_item.get(x["id"], [])
        if is_phone_media(x) or decisions.get(x["id"]) == "skip" or (rs and all(r["status"] == "EXCLUDED" for r in rs)):
            if not any(r["status"] == "EXCLUDED" for r in rs):
                problems.append({"gopro_id": x["id"], "filename": x.get("filename"), "type": x["type"],
                                 "problem": "should be EXCLUDED", "detail": [r["status"] for r in rs]})
            continue
        ok, why = item_fully_in_google(x, rs, by_unit, readable=None)   # read-back is checked separately
        if not ok:
            detail = why + " | " + "; ".join(f"{r['unit_id']}={r['status']} {(r['note'] or '')[:80]}" for r in rs[:3])
            problems.append({"gopro_id": x["id"], "filename": x.get("filename"), "type": x["type"],
                             "problem": "not fully uploaded", "detail": detail})
    return problems


def _tiny_unreencoded_video(row, inv: dict) -> bool:
    enc = json.loads(row["encode_json"] or "{}")
    dur = int((inv.get(row["gopro_id"]) or {}).get("source_duration") or 0)
    return row["kind"] in ("Video", "TimeLapseVideo") and not enc.get("hevc") and 0 < dur < TINY_VIDEO_MS


def google_readback(led: Ledger, gp: GooglePhotos, inv: dict) -> tuple[list[dict], Counter, list[str]]:
    done = [r for r in led.rows("status = 'DONE'")]
    problems, stats, tiny = [], Counter(), []
    for i in range(0, len(done), 50):
        chunk = done[i:i + 50]
        results = gp.batch_get([c["media_item_id"] for c in chunk])
        for row, res in zip(chunk, results, strict=True):
            mi = res.get("mediaItem")
            if not mi:
                if row["upload_sha256"] == row["orig_sha256"]:
                    # untouched original: Google matched identical bytes already in the library
                    stats["merged_into_existing_identical"] += 1
                    continue
                if _tiny_unreencoded_video(row, inv):
                    stats["dropped_by_google_tiny_video"] += 1
                    tiny.append(row["filename"])
                    continue
                problems.append({"gopro_id": row["gopro_id"], "filename": row["filename"], "type": row["kind"],
                                 "problem": "missing in Google", "detail": res.get("status")})
                continue
            stats["found"] += 1
            if mi.get("filename") != row["filename"]:
                problems.append({"gopro_id": row["gopro_id"], "filename": row["filename"], "type": row["kind"],
                                 "problem": "filename differs", "detail": mi.get("filename")})
            meta = mi.get("mediaMetadata", {})
            if row["capture_json"]:
                cap = Capture.from_json(row["capture_json"])
                g = datetime.fromisoformat(meta.get("creationTime", "").replace("Z", "+00:00"))
                if abs(g - cap.utc) > timedelta(seconds=1):
                    problems.append({"gopro_id": row["gopro_id"], "filename": row["filename"], "type": row["kind"],
                                     "problem": "creation time differs",
                                     "detail": f"google {g.isoformat()} vs ours {cap.utc.isoformat()}"})
                else:
                    stats["time_ok"] += 1
            if "video" in meta:
                st = meta["video"].get("status", "?")
                stats[f"video_{st}"] += 1
                if st != "READY":
                    problems.append({"gopro_id": row["gopro_id"], "filename": row["filename"], "type": row["kind"],
                                     "problem": f"video status {st}", "detail": ""})
    return problems, stats, tiny


def app_items(led: Ledger, gp: GooglePhotos, inv: dict) -> tuple[list[dict], dict]:
    ids = gp.list_app_items()
    ours = {r["media_item_id"]: r for r in led.rows("media_item_id IS NOT NULL")}
    try:
        superseded = {r[0] for r in led.db.execute("SELECT old_media_item_id FROM superseded")}
    except Exception:  # noqa: BLE001 - table only exists after fix-dates
        superseded = set()
    problems = []
    for mid, mi in ids.items():
        if mid in superseded:
            continue                      # wrong-date version replaced by fix-dates: goes to cleanup
        if mid not in ours:
            problems.append({"gopro_id": "", "filename": mi.get("filename"), "type": "",
                             "problem": "app-created item not in ledger (possible duplicate)",
                             "detail": f"{mi.get('mediaMetadata', {}).get('creationTime')} {mi.get('productUrl')}"})
    for mid, r in ours.items():
        if mid not in ids and r["upload_sha256"] != r["orig_sha256"] and not _tiny_unreencoded_video(r, inv):
            problems.append({"gopro_id": r["gopro_id"], "filename": r["filename"], "type": r["kind"],
                             "problem": "ledger item not listed by Google", "detail": mid})
    keys = Counter((mi.get("filename"), mi.get("mediaMetadata", {}).get("creationTime"))
                   for mid, mi in ids.items() if mid not in superseded)
    for (fn, ct), n in keys.items():
        if n > 1:
            same = [r for r in led.rows("filename = ? AND status = 'DONE'", fn)]
            if len(same) == n and len({r["orig_sha256"] for r in same}) == n:
                continue                  # distinct frame grabs of one video (approved in manual review)
            problems.append({"gopro_id": "", "filename": fn, "type": "", "problem": f"{n} app items with same name+time",
                             "detail": ct})
    # albums
    albums = gp.app_albums()
    album_counts = {}
    for title, aid in albums.items():
        album_counts[title] = int(gp._get(f"{API}/albums/{aid}", "get album").json().get("mediaItemsCount", 0))
    expected = Counter(r["album_title"] for r in led.rows("status = 'DONE' AND album_title IS NOT NULL"))
    for title, n in expected.items():
        if album_counts.get(title) != n:
            problems.append({"gopro_id": "", "filename": "", "type": "album", "problem": f"album '{title}' count",
                             "detail": f"google {album_counts.get(title)} vs ledger {n}"})
    return problems, {"app_items_in_google": len(ids), "ledger_media_items": len(ours),
                      "superseded_wrong_date_items": len(superseded & set(ids)), "albums": album_counts}


def main() -> int:
    inv_list = json.loads(config.INVENTORY.read_text())
    inv = {x["id"]: x for x in inv_list}
    led, gp = Ledger(readonly=True), GooglePhotos()
    p1 = completeness(inv_list, led)
    p2, stats, tiny = google_readback(led, gp, inv)
    p3, info = app_items(led, gp, inv)
    problems = p1 + p2 + p3
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["gopro_id", "filename", "type", "problem", "detail"])
        w.writeheader()
        for p in problems:
            w.writerow({**p, "detail": str(p["detail"])})
    OUT.chmod(0o600)
    print(json.dumps({"ledger": led.counts(), "ledger_path": config.display(led.path), "completeness_problems": len(p1),
                      "google_readback": dict(stats), "tiny_videos_dropped_by_google": tiny,
                      "readback_problems": len(p2), **info, "app_side_problems": len(p3)}, indent=1, default=str))
    for p in problems:
        print(f"- {p['problem']}: {p['filename']} ({p['gopro_id']}) {str(p['detail'])[:160]}")
    return 0 if not problems else 1
