"""Re-upload items whose camera clock had been reset (2015/2016 dates) with the real time.

Targets: DONE units with a clock-reset date, and units held in REVIEW by the pipeline
because their clock was reset and nothing could correct it.

Sources of the real time, in order:
1. the photo's own EXIF GPS time;
2. frame grabs: their source video's (GPS-corrected) capture;
3. no GPS: the reset period's constant offset camera->UTC, measured on the GPS photos of the
   same period (no correctly-dated item between them in camera-counter order), zone from
   the nearest anchor.

    python -m photos_migration fix-dates [--dry-run]
"""
from __future__ import annotations

import re
import statistics
from datetime import timedelta, timezone

from . import metadata
from .gopro import GoProError, load_inventory
from .metadata import Capture
from .pipeline import SEQUENCE_TYPES, VIDEO_TYPES, Migrator
from .redate import camera_clock, fetch_verified_original, redate, work_dir

MAX_SPREAD = timedelta(seconds=5)
YEAR = timedelta(days=365)


def number(filename: str) -> int | None:
    """Camera file counter shared by photos and videos: GOPR1106, GH011106, G0061106."""
    m = re.match(r"^(?:GOPR|G[HX]\d\d|G\d{3})(\d{4})", filename or "", re.I)
    return int(m[1]) if m else None


def _good_clock_numbers(led) -> set[int]:
    """Counters of items taken with a good clock (GPS-corrected videos whose camera clock was
    years off still count as 'reset')."""
    reset, good = set(), set()
    for r in led.rows("status IN ('DONE','DUPLICATE') AND capture_json IS NOT NULL"):
        n = number(r["filename"])
        if n is None:
            continue
        c = Capture.from_json(r["capture_json"])
        off = c.camera_clock_error_s is not None and abs(c.camera_clock_error_s) > YEAR.total_seconds()
        (reset if metadata.is_clock_reset(c.utc.replace(tzinfo=None)) or off else good).add(n)
    return good - reset


def targets(led) -> list:
    done = [r for r in led.rows("status = 'DONE' AND capture_json IS NOT NULL")
            if metadata.is_clock_reset(Capture.from_json(r["capture_json"]).utc.replace(tzinfo=None))]
    held = led.rows("status = 'REVIEW' AND note LIKE 'camera clock reset%'")
    return done + held


def main(dry_run: bool = False, takeover: bool = False) -> int:
    inv = {x["id"]: x for x in load_inventory()}
    m = Migrator(list(inv.values()), upload=not dry_run, reencode=True, takeover=takeover)
    try:
        return _main(m, inv, dry_run)
    finally:
        m.ledger.release_lease()


def _main(m: Migrator, inv: dict, dry_run: bool) -> int:
    led = m.ledger
    rows = targets(led)
    print(f"{len(rows)} units with a clock-reset date")

    plan = []
    for r in rows:
        item = inv[r["gopro_id"]]
        if item["type"] in SEQUENCE_TYPES:       # frames come from the zip: not re-downloadable one by one
            print(f"{r['filename']:14s} time-lapse/burst frame: fix by hand", flush=True)
            continue
        is_video = item["type"] in VIDEO_TYPES
        try:
            orig = fetch_verified_original(m, r, item, work_dir("fix", r["unit_id"]))
        except (GoProError, RuntimeError) as e:
            print(f"{r['filename']:14s} skipped: {e}", flush=True)
            continue
        cam = camera_clock(orig, is_video)
        cap, how = None, None
        if not is_video:
            try:
                c, _ = metadata.capture_for_photo(item, orig)
            except metadata.ClockReset:
                c = None
            if c is not None and c.source.startswith("gps-time"):
                cap, how = c, "photo GPS time"
            else:
                v = m.source_video_capture(r["filename"], item)
                if v is not None:
                    cap, how = v, "source video"
        plan.append({"row": r, "item": item, "orig": orig, "cam": cam, "cap": cap, "how": how})

    good = _good_clock_numbers(led)
    anchors = [(number(p["row"]["filename"]), p["cap"].utc - p["cam"].replace(tzinfo=timezone.utc), p["cap"].tz)
               for p in plan if p["how"] == "photo GPS time" and p["cam"] and p["cap"].tz]
    for p in plan:
        n = number(p["row"]["filename"])
        if p["cap"] is not None or p["cam"] is None or n is None:
            continue
        same_period = [(abs(a - n), d, tz) for a, d, tz in anchors
                       if a is not None and not any(min(a, n) < g < max(a, n) for g in good)]
        if not same_period:
            continue
        deltas = [d for _, d, _ in same_period]
        if max(deltas) - min(deltas) > MAX_SPREAD:
            continue
        delta = timedelta(seconds=statistics.median(d.total_seconds() for d in deltas))
        tz = min(same_period)[2]                                   # zone of the nearest anchor
        utc = (p["cam"].replace(tzinfo=timezone.utc) + delta).replace(microsecond=0)
        p["cap"] = Capture(utc, metadata.offset_minutes(tz, utc), tz, source="clock-reset offset of the period",
                           model=p["item"].get("camera_model"))
        p["how"] = f"period offset {delta} from {len(same_period)} GPS photos"

    for p in plan:
        c = p["cap"]
        print(f"{p['row']['filename']:14s} {p['cam']} -> {c.local if c else '??'} {c.offset_str if c else ''} "
              f"{(c.tz or '') if c else ''} [{p['how'] or 'NO SOURCE - kept as is'}]")
    if dry_run:
        return 0
    for p in plan:
        if p["cap"] is not None:
            status = redate(m, p["row"], p["item"], p["orig"], p["cap"], f"date fixed: {p['how']}")
            print(f"[{p['row']['unit_id']}] {status}", flush=True)
            led.heartbeat()
    print(led.counts())
    return 0
