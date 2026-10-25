"""Re-upload GPS-less items whose camera clock was off, using the error measured by GPS.

A video with a GPS fix tells how far the camera clock was from the real local time
(camera_clock_error_s = camera clock - real local time, e.g. -59 min when the camera skipped a
DST change, +3 h when it still had another country's time). GPS-less items shot around it by
the same camera carry the same error. For each GPS-less unit, among the GPS videos of the same
camera model on the same local day (nearest in camera-counter order on each side):
- anchors on both sides that agree within 60 s -> corrected;
- only a later anchor -> corrected: syncing the clock (the Quik app does it on connect) brings
  the error back to ~0 and drift adds only seconds a day, so an error still there later the same
  day was already there before. An earlier anchor alone is not enough (a sync may follow it);
- the error must be between 2 min and 14 h and no DST change may fall between item and anchor;
  anything else is listed for the user.
Real local time = the file's own camera clock - error, zone from the anchor. The run checks that
the resulting shift equals the error before uploading. Frame grabs follow their corrected video.
GPS-tagged items, time-lapse/burst frames and units corrected before are never touched.

    python -m photos_migration fix-drift --dry-run      # list corrections
    python -m photos_migration fix-drift                # re-upload (old items go to cleanup)
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta, timezone
from pathlib import Path

from . import metadata
from .fix_dates import number
from .gopro import GoProError, load_inventory
from .metadata import Capture
from .pipeline import SEQUENCE_TYPES, VIDEO_TYPES, Migrator
from .redate import camera_clock, fetch_verified_original, redate, work_dir

AGREE = timedelta(seconds=60)
MIN_ERR, MAX_ERR = timedelta(minutes=2), timedelta(hours=14)
SOURCE = "clock drift corrected"


def _skip(c: Capture, item: dict | None) -> bool:
    """Never re-derive a time that came from GPS, a previous correction, or a sequence frame."""
    return (c.lat is not None or "gps" in c.source or c.source.startswith(SOURCE) or "clock-reset" in c.source
            or "estimated" in c.source or (item or {}).get("type") in SEQUENCE_TYPES)


def _same_zone_rules(tz: str, a, b) -> bool:
    """No DST change between two UTC instants in zone tz."""
    return metadata.offset_minutes(tz, a) == metadata.offset_minutes(tz, b)


def plan(led, inv: dict) -> tuple[list[dict], list[dict]]:
    rows = led.rows("status = 'DONE' AND capture_json IS NOT NULL")
    caps = {r["unit_id"]: Capture.from_json(r["capture_json"]) for r in rows}
    anchors = defaultdict(list)                     # model -> [(counter, err_s, tz, local date, utc)]
    for r in rows:
        c = caps[r["unit_id"]]
        n = number(r["filename"])
        if (c.source == "gps" and c.camera_clock_error_s is not None and n is not None and c.tz
                and inv.get(r["gopro_id"], {}).get("type") not in SEQUENCE_TYPES
                and abs(c.camera_clock_error_s) < 86400 and (c.clock_ref or "local") == "local"):
            anchors[c.model].append((n, c.camera_clock_error_s, c.tz, c.local.date(), c.utc))
    auto, manual = [], []
    for r in rows:
        c = caps[r["unit_id"]]
        item = inv.get(r["gopro_id"])
        n = number(r["filename"])
        if n is None or _skip(c, item):
            continue
        model = c.model or (item or {}).get("camera_model")
        day = [a for a in anchors.get(model, []) if a[3] == c.local.date()]
        if not day:
            continue
        lo = max((a for a in day if a[0] <= n), default=None)
        hi = min((a for a in day if a[0] >= n), default=None)
        sides = [s for s in (lo, hi) if s is not None]
        errs = [timedelta(seconds=s[1]) for s in sides]
        err = timedelta(seconds=round((sum(errs, timedelta(0)) / len(errs)).total_seconds()))
        if abs(err) < MIN_ERR:
            continue
        if lo and hi:
            ok, why = abs(errs[0] - errs[-1]) <= AGREE, "anchors on both sides agree"
        elif hi:
            ok, why = True, "later anchor the same day (a clock sync would have cleared the error)"
        else:
            ok, why = False, "earlier anchor only (the clock may have been synced after it)"
        tz = (hi or lo)[2]
        new_local = c.local - err
        new_utc = (new_local - timedelta(minutes=metadata.zone_offset(tz, wall=new_local)[0])).replace(tzinfo=timezone.utc)
        if ok and not all(_same_zone_rules(tz, new_utc, s[4]) for s in sides):
            ok, why = False, "a DST change falls between the item and its anchor"
        if ok and abs(err) > MAX_ERR:
            ok, why = False, "error above 14 h"
        entry = {"row": r, "cap": c, "err": err, "tz": tz, "anchors": [s[0] for s in sides], "why": why}
        (auto if ok else manual).append(entry)
    return auto, manual


def _predicted(e: dict) -> str:
    local = e["cap"].local - e["err"]
    off = metadata.zone_offset(e["tz"], wall=local)[0]
    return f"{local} {'+' if off >= 0 else '-'}{abs(off) // 60:02d}:{abs(off) % 60:02d} {e['tz']}"


def main(dry_run: bool = False, takeover: bool = False) -> int:
    inv = {x["id"]: x for x in load_inventory()}
    m = Migrator(list(inv.values()), upload=not dry_run, reencode=True, takeover=takeover)
    led = m.ledger
    try:
        auto, manual = plan(led, inv)
        fmt = lambda e: (f"{e['row']['filename']:28s} {e['cap'].local} -> {_predicted(e)}  "   # noqa: E731
                         f"(camera {int(e['err'].total_seconds() // 60):+d} min; anchors #{', #'.join(map(str, e['anchors']))}: "
                         f"{e['why']})")
        print(f"{len(auto)} to correct:")
        for e in auto:
            print("  " + fmt(e))
        print(f"{len(manual)} left as they are (need a decision):")
        for e in manual:
            print("  " + fmt(e))
        if dry_run:
            return 0
        corrected: dict[str, tuple[Capture, timedelta]] = {}
        videos_first = sorted(auto, key=lambda e: inv[e["row"]["gopro_id"]]["type"] not in VIDEO_TYPES)
        for e in videos_first:
            r, item = e["row"], inv[e["row"]["gopro_id"]]
            uid = r["unit_id"]
            is_video = item["type"] in VIDEO_TYPES
            try:
                orig = fetch_verified_original(m, r, item, work_dir("drift", uid))
            except (GoProError, RuntimeError) as ex:
                print(f"[{uid}] skipped: {ex}", flush=True)
                continue
            grab = not is_video and re.match(r"^G[HX]\d{6}\.jpe?g$", r["filename"] or "", re.I)
            cam = None if grab else camera_clock(orig, is_video)
            if cam is None:                                     # frame grab: follow its corrected video
                stem = Path(r["filename"]).stem.upper()
                src = next((c for f, (c, _) in corrected.items() if Path(f).stem.upper() == stem), None)
                if src is None:
                    print(f"[{uid}] skipped: no camera clock and no corrected source video", flush=True)
                    continue
                cap = Capture(src.utc, src.utc_offset_min, src.tz, src.lat, src.lon, src.alt,
                              source=f"{SOURCE} (source video)", model=src.model)
            else:
                local = cam - e["err"]
                off = metadata.zone_offset(e["tz"], wall=local)[0]
                utc = (local - timedelta(minutes=off)).replace(tzinfo=timezone.utc)
                cap = Capture(utc, off, e["tz"], source=f"{SOURCE} by GPS anchors",
                              model=e["cap"].model or item.get("camera_model"))
            # the file's own clock must give the shift the anchors predict (e.g. a phone copy's
            # CreateDate is not the camera clock): otherwise leave the item alone
            shift = cap.local - e["cap"].local
            if abs(shift + e["err"]) > AGREE:
                print(f"[{uid}] skipped: shift {shift} does not match the camera error {e['err']}", flush=True)
                continue
            if is_video:
                corrected[r["filename"]] = (cap, e["err"])
            reason = (f"clock drift fixed: camera clock {int(e['err'].total_seconds()):+d} s "
                      f"(GPS anchors #{', #'.join(map(str, e['anchors']))})")
            status = redate(m, r, item, orig, cap, reason)
            print(f"[{uid}] {status}: {e['cap'].local} -> {cap.local} {cap.offset_str}", flush=True)
            led.heartbeat()
        print(led.counts())
        return 0
    finally:
        led.release_lease()
