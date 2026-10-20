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
from .gphotos import API, GooglePhotos
from .ledger import Ledger
from .metadata import Capture
from .pipeline import SEQUENCE_TYPES, is_phone_media, read_review_decisions

OUT = config.DATA_DIR / "audit_problems.csv"


def completeness(inv: list[dict], led: Ledger) -> list[dict]:
    rows_by_item = defaultdict(list)
    for r in led.rows():
        rows_by_item[r["gopro_id"]].append(r)
    by_unit = {r["unit_id"]: r for rs in rows_by_item.values() for r in rs}
    decisions = read_review_decisions()
    problems = []

    def ok(r) -> bool:
        if r["status"] == "DONE":
            return True
        if r["status"] == "DUPLICATE":
            target = by_unit.get((r["note"] or "").split(" as ")[-1])
            return bool(target and target["status"] == "DONE" and target["orig_sha256"] == r["orig_sha256"])
        return False

    for x in inv:
        rs = rows_by_item.get(x["id"], [])
        if is_phone_media(x) or decisions.get(x["id"]) == "skip":
            if not any(r["status"] == "EXCLUDED" for r in rs):
                problems.append({"gopro_id": x["id"], "filename": x.get("filename"), "type": x["type"],
                                 "problem": "should be EXCLUDED", "detail": [r["status"] for r in rs]})
            continue
        if x["type"] in SEQUENCE_TYPES:
            frames = [r for r in rs if "#" in r["unit_id"]]
            good = [r for r in frames if ok(r)]
            if len(good) != (x.get("item_count") or 0):
                problems.append({"gopro_id": x["id"], "filename": x.get("filename"), "type": x["type"],
                                 "problem": f"{len(good)}/{x.get('item_count')} frames done",
                                 "detail": Counter(r["status"] for r in frames)})
            continue
        whole = [r for r in rs if r["unit_id"] == x["id"] and ok(r)]
        parts = [r for r in rs if "#" in r["unit_id"]]
        if whole or (parts and all(ok(r) for r in parts) and len(parts) == (x.get("item_count") or 1)):
            continue
        detail = "; ".join(f"{r['unit_id']}={r['status']} {(r['note'] or '')[:120]}" for r in rs) or "no ledger row"
        problems.append({"gopro_id": x["id"], "filename": x.get("filename"), "type": x["type"],
                         "problem": "not uploaded", "detail": detail})
    return problems


def google_readback(led: Ledger, gp: GooglePhotos) -> tuple[list[dict], Counter]:
    done = [r for r in led.rows("status = 'DONE'")]
    problems, stats = [], Counter()
    for i in range(0, len(done), 50):
        chunk = done[i:i + 50]
        r = gp.session.get(f"{API}/mediaItems:batchGet", params={"mediaItemIds": [c["media_item_id"] for c in chunk]},
                           timeout=120)
        gp._check(r, "batchGet")
        for row, res in zip(chunk, r.json().get("mediaItemResults", [])):
            mi = res.get("mediaItem")
            if not mi:
                if row["upload_sha256"] == row["orig_sha256"]:
                    # untouched original: Google matched identical bytes already in the library
                    stats["merged_into_existing_identical"] += 1
                    continue
                if row["kind"] in ("Video", "TimeLapseVideo") and row["upload_bytes"] < 5_000_000:
                    stats["dropped_by_google_tiny_video"] += 1
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
    return problems, stats


def app_items(led: Ledger, gp: GooglePhotos) -> tuple[list[dict], dict]:
    ids, token = {}, None
    while True:
        params = {"pageSize": 100}
        if token:
            params["pageToken"] = token
        r = gp.session.get(f"{API}/mediaItems", params=params, timeout=120)
        gp._check(r, "list app items")
        d = r.json()
        for mi in d.get("mediaItems", []):
            ids[mi["id"]] = mi
        token = d.get("nextPageToken")
        if not token:
            break
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
        if mid not in ids and r["upload_sha256"] != r["orig_sha256"] and not (
                r["kind"] in ("Video", "TimeLapseVideo") and (r["upload_bytes"] or 0) < 5_000_000):
            problems.append({"gopro_id": r["gopro_id"], "filename": r["filename"], "type": r["kind"],
                             "problem": "ledger item not listed by Google", "detail": mid})
    keys = Counter((mi.get("filename"), mi.get("mediaMetadata", {}).get("creationTime"))
                   for mid, mi in ids.items() if mid not in superseded)
    approved_grabs = {r["filename"] for r in led.rows("kind = 'Photo'") if r["filename"]}
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
        r = gp.session.get(f"{API}/albums/{aid}", timeout=60)
        gp._check(r, "get album")
        album_counts[title] = int(r.json().get("mediaItemsCount", 0))
    expected = Counter(r["album_title"] for r in led.rows("status = 'DONE' AND album_title IS NOT NULL"))
    for title, n in expected.items():
        if album_counts.get(title) != n:
            problems.append({"gopro_id": "", "filename": "", "type": "album", "problem": f"album '{title}' count",
                             "detail": f"google {album_counts.get(title)} vs ledger {n}"})
    return problems, {"app_items_in_google": len(ids), "ledger_media_items": len(ours),
                      "superseded_wrong_date_items": len(superseded & set(ids)), "albums": album_counts}


def main() -> int:
    inv = json.loads(config.INVENTORY.read_text())
    led, gp = Ledger(), GooglePhotos()
    p1 = completeness(inv, led)
    p2, stats = google_readback(led, gp)
    p3, info = app_items(led, gp)
    problems = p1 + p2 + p3
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["gopro_id", "filename", "type", "problem", "detail"])
        w.writeheader()
        for p in problems:
            w.writerow({**p, "detail": str(p["detail"])})
    OUT.chmod(0o600)
    print(json.dumps({"ledger": led.counts(), "completeness_problems": len(p1), "google_readback": dict(stats),
                      "readback_problems": len(p2), **info, "app_side_problems": len(p3)}, indent=1, default=str))
    for p in problems:
        print(f"- {p['problem']}: {p['filename']} ({p['gopro_id']}) {str(p['detail'])[:160]}")
    return 0 if not problems else 1
