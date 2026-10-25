"""Match Google Photos web items (old phone-backup copies) to GoPro Cloud items.

Google shows each copy at its local wall-clock time. GoPro captured_at is the camera
wall clock for camera direct uploads and real UTC for Quik uploads, so a copy of a
GoPro item sits at  wall_gp = captured_at + delta,  where delta is 0 (camera upload)
or the local UTC offset (a whole quarter hour within +-14 h).

    python -m photos_migration.inventory_match      -> old_copies.csv + summary
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from . import config, metadata
from .gphotos_web import OUT_DIR, parse_label

TOL = timedelta(seconds=2)
# offsets of places actually visited (Europe CET/CEST, Turkey, Brazil, UTC); anything else is coincidence
PLAUSIBLE = {timedelta(hours=h) for h in (-3, 0, 1, 2, 3)}
QUARTER = timedelta(minutes=15)
MAX_OFFSET = timedelta(hours=14)
OUT_CSV = config.DATA_DIR / "old_copies.csv"


def _kind(item: dict) -> str:
    return "Video" if item["type"] in ("Video", "TimeLapseVideo", "MultiClipEdit") else "Photo"


def _quarter_delta(delta: timedelta) -> timedelta | None:
    q = round(delta / QUARTER) * QUARTER
    return q if abs(delta - q) <= TOL and abs(q) <= MAX_OFFSET else None


def _expected_delta(item: dict) -> timedelta | None:
    if (item.get("content_source") or "").startswith("gp-cah"):
        return timedelta(0)
    off = metadata.parse_offset(metadata.item_tz(item))
    return timedelta(minutes=off) if off is not None else None


def _span(item: dict) -> timedelta:
    """Time-lapse/burst frames: copies may start up to the end of the sequence.
    Everything else (chapters included) matches on its start time only, so phone
    shots taken during a long recording are never mistaken for old copies."""
    if item["type"] in ("TimeLapse", "Burst"):
        return timedelta(seconds=(item.get("item_count") or 1) * 2 + 5)
    return timedelta(0)


def load_gp() -> list[dict]:
    """One entry per photo id (search pages list a photo under 'top' and in the timeline)."""
    index = json.loads((OUT_DIR / "date_index.json").read_text())
    seen, out = set(), []
    for entries in index.values():
        for e in entries:
            pid = e["href"].rstrip("/").split("/")[-1]
            if pid in seen:
                continue
            seen.add(pid)
            p = parse_label(e["label"])
            if p:
                out.append({"href": f"photo/{pid}", "label": e["label"], "kind": p[0], "wall": p[1]})
    return out


def match(items: list[dict], gp: list[dict]) -> tuple[list[dict], dict]:
    gp_by_kind = defaultdict(list)
    for g in gp:
        gp_by_kind[g["kind"]].append(g)
    used: set[str] = set()
    rows: list[dict] = []
    day_offsets: dict[str, Counter] = defaultdict(Counter)

    def candidates(item, delta_ok):
        t = datetime.strptime(item["captured_at"], "%Y-%m-%dT%H:%M:%SZ")
        span = _span(item)
        out = []
        for g in gp_by_kind[_kind(item)]:
            if g["href"] in used:
                continue
            for d in delta_ok(g["wall"] - t, span):
                out.append((g, d))
        return out

    def take(item, g, d, how):
        used.add(g["href"])
        day_offsets[g["wall"].strftime("%Y-%m-%d")][d] += 1
        rows.append({"gopro_id": item["id"], "gopro_filename": item.get("filename") or "", "gopro_type": item["type"],
                     "captured_at": item["captured_at"], "gp_label": g["label"],
                     "gp_url": "https://photos.google.com/" + g["href"].lstrip("./"),
                     "delta_h": round(d.total_seconds() / 3600, 2), "confidence": how})

    def near_multiple(raw, span, allowed):
        """deltas d in allowed with raw - d within [0 - TOL, span + TOL]"""
        return [d for d in allowed if -TOL <= raw - d <= span + TOL]

    # pass 1: items whose offset is known (camera uploads: 0; Quik uploads with captured_at_timezone)
    ordered = sorted(items, key=lambda x: x["captured_at"])
    pending = []
    for item in ordered:
        exp = _expected_delta(item)
        if exp is None:
            pending.append(item)
            continue
        cands = candidates(item, lambda raw, span, exp=exp: near_multiple(raw, span, [exp]))
        cands.sort(key=lambda c: abs((c[0]["wall"] - datetime.strptime(item["captured_at"], "%Y-%m-%dT%H:%M:%SZ")) - c[1]))
        for i, (g, d) in enumerate(cands):
            take(item, g, d, "exact" if i == 0 else "chapter/sequence")
            if not _span(item):
                break

    # pass 2: unknown offset -> the day's most common offset, else any quarter-hour offset
    for item in pending:
        t = datetime.strptime(item["captured_at"], "%Y-%m-%dT%H:%M:%SZ")

        def allowed(raw, span):
            if not span:
                q = _quarter_delta(raw)
                return [q] if q is not None else []
            out = []
            k_lo, k_hi = math.floor((raw - span - TOL) / QUARTER), math.ceil((raw + TOL) / QUARTER)
            for k in range(k_lo, k_hi + 1):
                d = k * QUARTER
                if abs(d) <= MAX_OFFSET and -TOL <= raw - d <= span + TOL:
                    out.append(d)
            return out

        cands = [c for c in candidates(item, allowed) if c[1] in PLAUSIBLE]
        if not cands:
            continue
        day = t.strftime("%Y-%m-%d")
        common = [d for d, _ in day_offsets[day].most_common()]
        if _span(item) and not common:
            continue          # a sequence with no known day offset is too ambiguous
        cands.sort(key=lambda c: (c[1] not in common[:1], abs(c[0]["wall"] - t - c[1])))
        best_d = cands[0][1]
        how = "probable (day offset)" if best_d in common[:1] else "probable"
        for i, (g, d) in enumerate([c for c in cands if c[1] == best_d]):
            take(item, g, d, how if i == 0 else "chapter/sequence")
            if not _span(item):
                break

    matched_ids = {r["gopro_id"] for r in rows}
    unmatched_gp_near = []
    times_by_day = defaultdict(list)
    for item in items:
        times_by_day[item["captured_at"][:10]].append(datetime.strptime(item["captured_at"], "%Y-%m-%dT%H:%M:%SZ"))
    for g in gp:
        if g["href"] in used:
            continue
        ts = times_by_day.get(g["wall"].strftime("%Y-%m-%d"), [])
        if any(abs(g["wall"] - t) <= MAX_OFFSET for t in ts):
            unmatched_gp_near.append(g)
    stats = {"gopro_items": len(items), "gopro_items_with_old_copy": len(matched_ids),
             "old_copies": len(rows), "gp_items_indexed": len(gp),
             "gp_items_same_days_unmatched": len(unmatched_gp_near),
             "by_confidence": dict(Counter(r["confidence"] for r in rows))}
    return rows, stats


def main() -> int:
    inv = json.loads(config.INVENTORY.read_text())
    items = [x for x in inv if not ((x.get("filename") or "").startswith("PXL_")
                                    or (x.get("camera_model") or "").startswith("Pixel"))]
    rows, stats = match(items, load_gp())
    if rows:
        with open(OUT_CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        OUT_CSV.chmod(0o600)
    print(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
