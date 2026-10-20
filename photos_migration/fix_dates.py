"""Re-upload items whose camera clock had been reset (2015/2016 dates) with the real time.

Sources of the real time, in order:
1. the photo's own EXIF GPS time;
2. frame grabs: their source video's (GPS-corrected) capture;
3. no GPS: the reset period's constant offset camera->UTC, measured on the photos of the
   same period that do have GPS (they agree to the second), with their time zone.

The old Google items cannot be re-dated through the API; they are recorded in the
'superseded' table and go to the cleanup list.

    python -m photos_migration fix-dates [--dry-run]
"""
from __future__ import annotations

import json
import shutil
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, encode, metadata
from .gopro import load_inventory
from .metadata import Capture
from .pipeline import VIDEO_TYPES, Migrator, _sha256, chapter_filename

MAX_SPREAD = timedelta(seconds=5)
YEAR = timedelta(days=365)


def _number(filename: str) -> int | None:
    """Camera file counter shared by photos and videos: GOPR1106, GH011106, G0061106."""
    import re
    m = re.match(r"^(?:GOPR|G[HX]\d\d|G\d{3})(\d{4})", filename or "", re.I)
    return int(m[1]) if m else None


def _reset_state(led) -> tuple[set[int], set[int]]:
    """File numbers taken while the clock was reset vs with a good clock.
    GPS-corrected videos still count as 'reset' when their camera clock was years off."""
    reset, good = set(), set()
    for r in led.rows("status IN ('DONE','DUPLICATE') AND capture_json IS NOT NULL"):
        n = _number(r["filename"])
        if n is None:
            continue
        c = Capture.from_json(r["capture_json"])
        off = c.camera_clock_error_s is not None and abs(c.camera_clock_error_s) > YEAR.total_seconds()
        (reset if metadata.is_clock_reset(c.utc.replace(tzinfo=None)) or off else good).add(n)
    return reset, good - reset

SCHEMA = """CREATE TABLE IF NOT EXISTS superseded (
    unit_id TEXT, old_media_item_id TEXT, old_capture_json TEXT, reason TEXT, at TEXT)"""


def _camera_clock(path: Path, is_video: bool) -> datetime | None:
    tag = "-QuickTime:CreateDate" if is_video else "-EXIF:DateTimeOriginal"
    v = metadata.exiftool(path, tag).get("Main:CreateDate" if is_video else "Main:DateTimeOriginal")
    return metadata._parse_exif_dt(v)


def main(dry_run: bool = False) -> int:
    inv = {x["id"]: x for x in load_inventory()}
    m = Migrator(list(inv.values()), upload=not dry_run, reencode=True)
    led = m.ledger
    led.db.execute(SCHEMA)
    targets = [r for r in led.rows("status = 'DONE' AND capture_json IS NOT NULL")
               if metadata.is_clock_reset(Capture.from_json(r["capture_json"]).utc.replace(tzinfo=None))]
    print(f"{len(targets)} units with a clock-reset date")
    work = config.WORK_DIR / "fix"
    work.mkdir(parents=True, exist_ok=True)

    # 1) fetch originals and find the real time where the file or its source video has it
    plan = []
    for r in targets:
        item = inv[r["gopro_id"]]
        is_video = item["type"] in VIDEO_TYPES
        d = work / r["unit_id"].replace("#", "_")
        d.mkdir(exist_ok=True)
        orig = d / f"orig{Path(r['filename']).suffix.lower()}"
        if not orig.exists():
            part = int(r["part"]) if r["part"] and r["part"].isdigit() else None
            src = m.gopro.fresh_source(item, part, "source")
            n, sha = m.gopro.download(src, orig, None)
            if sha != r["orig_sha256"]:
                raise SystemExit(f"{r['unit_id']}: original changed at GoPro since upload - stopping")
        cam = _camera_clock(orig, is_video)
        cap, how = None, None
        if not is_video:
            c, _ = metadata.capture_for_photo(item, orig)
            if c is not None and c.source.startswith("gps-time"):
                cap, how = c, "photo GPS time"
            else:
                v = m.source_video_capture(r["filename"])
                if v is not None and not metadata.is_clock_reset(v.utc.replace(tzinfo=None)):
                    cap, how = v, "source video"
        plan.append({"row": r, "item": item, "orig": orig, "is_video": is_video, "cam": cam, "cap": cap, "how": how})

    # 2) reset-period offsets measured on items with GPS
    _, good = _reset_state(led)
    anchors = [(_number(p["row"]["filename"]), p["cap"].utc - p["cam"].replace(tzinfo=timezone.utc), p["cap"].tz)
               for p in plan if p["how"] == "photo GPS time" and p["cam"]]
    for p in plan:
        if p["cap"] is not None or p["cam"] is None:
            continue
        n = _number(p["row"]["filename"])
        # same reset period = no item with a good clock between target and anchor in counter order
        near = [(d, tz) for a, d, tz in anchors
                if a is not None and n is not None and not any(min(a, n) < g < max(a, n) for g in good)]
        if not near:
            continue
        deltas = [d for d, _ in near]
        if max(deltas) - min(deltas) > MAX_SPREAD:
            continue
        delta = timedelta(seconds=statistics.median(d.total_seconds() for d in deltas))
        tz = max(set(t for _, t in near), key=[t for _, t in near].count)
        utc = (p["cam"].replace(tzinfo=timezone.utc) + delta).replace(microsecond=0)
        p["cap"] = Capture(utc, metadata.offset_minutes(tz, utc), tz, source="clock-reset offset of the period",
                           model=p["item"].get("camera_model"))
        p["how"] = f"period offset {delta} from {len(near)} GPS photos"

    for p in plan:
        c = p["cap"]
        print(f"{p['row']['filename']:14s} {p['cam']} -> {c.local if c else '??'} {c.offset_str if c else ''} "
              f"{(c.tz or '') if c else ''} [{p['how'] or 'NO SOURCE - kept as is'}]")
    if dry_run:
        return 0

    # 3) write the real time into a fresh copy and upload it as a new item
    for p in plan:
        r, cap = p["row"], p["cap"]
        if cap is None:
            continue
        uid = r["unit_id"]
        if p["is_video"]:
            upload = p["orig"]
            enc = encode.adaptive_encode(p["orig"], p["orig"].parent / "enc")
            if enc.use_hevc:
                upload = p["orig"].parent / "upload.mp4"
                enc.path.rename(upload)
            metadata.write_video(upload, cap)
        else:
            upload = p["orig"]
            if cap.model is None:
                cap.model = p["item"].get("camera_model")
            metadata.write_photo(upload, cap)
        led.db.execute("INSERT INTO superseded VALUES (?,?,?,?,?)",
                       (uid, r["media_item_id"], r["capture_json"], f"clock reset; fixed via {p['how']}",
                        datetime.now(timezone.utc).isoformat()))
        led.upsert(uid, capture_json=cap.to_json(), upload_file=str(upload), upload_bytes=upload.stat().st_size,
                   upload_sha256=_sha256(upload), upload_token=None, token_at=None, media_item_id=None,
                   create_errors=0, status="PREPARED", note=f"date fixed: {p['how']}")
        name = r["filename"]
        m._upload_and_create(uid, name, upload, "video/mp4" if p["is_video"] else "image/jpeg", r["album_title"])
        if led.get(uid)["status"] == "DONE":
            shutil.rmtree(p["orig"].parent, ignore_errors=True)
    print(led.counts())
    return 0
