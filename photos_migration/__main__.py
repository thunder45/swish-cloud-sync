"""CLI: python -m photos_migration <command>"""
from __future__ import annotations

import argparse
import json
import sys

from . import config
from .gopro import GoProAuthError, GoProClient, load_inventory

ORDER = {"Video": 0, "TimeLapseVideo": 0, "MultiClipEdit": 1, "Photo": 2, "TimeLapse": 3, "Burst": 3}


def _wait_for_network(max_hours: float = 12) -> bool:
    import time
    import requests
    deadline = time.time() + max_hours * 3600
    while time.time() < deadline:
        try:
            requests.get("https://api.gopro.com", timeout=15)
            requests.get("https://photoslibrary.googleapis.com", timeout=15)
            return True
        except requests.RequestException:
            time.sleep(60)
    return False


def _run(items: list[dict], upload: bool, reencode: bool) -> int:
    import time
    import traceback
    from .pipeline import Migrator
    m = Migrator(load_inventory(), upload=upload, reencode=reencode)
    # videos first: they carry GPS (zone hints) and frame grabs take their capture from them
    items = sorted(items, key=lambda x: (ORDER.get(x["type"], 9), x.get("captured_at") or ""))
    restarts = 0
    while True:
        try:
            for it in items:
                m.process_item(it)            # finished items are skipped from the ledger alone
            deferred = {r["gopro_id"] for r in m.ledger.rows("status = 'DEFERRED'")}
            for it in items:                   # second pass: frame grabs whose video is now done
                if it["id"] in deferred:
                    m.process_item(it)
            break
        except GoProAuthError as e:
            print(f"STOPPED: {e}", file=sys.stderr, flush=True)
            return 2
        except config.NetworkDown as e:
            print(f"PAUSED (network): {e} - waiting for the connection to come back", flush=True)
            if not _wait_for_network():
                print("STOPPED: network down for 12 h", file=sys.stderr, flush=True)
                return 3
            print("RESUMING", flush=True)
        except Exception:  # noqa: BLE001 - unexpected crash: log, wait, resume from the ledger
            restarts += 1
            traceback.print_exc()
            if restarts > 10:
                print("STOPPED: too many unexpected errors", file=sys.stderr, flush=True)
                return 4
            print(f"RESTARTING in 60 s ({restarts}/10)", flush=True)
            time.sleep(60)
    print(m.ledger.counts(), flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="photos_migration")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("inventory", help="list GoPro Cloud, save the inventory and the review sheet")
    for name in ("pilot", "run"):
        p = sub.add_parser(name, help="process given GoPro ids" if name == "pilot" else "process the whole inventory")
        if name == "pilot":
            p.add_argument("ids", nargs="+")
        p.add_argument("--no-upload", action="store_true")
        p.add_argument("--no-reencode", action="store_true")
    sub.add_parser("review-csv", help="(re)write same_name_review.csv, keeping decisions")
    sub.add_parser("status", help="ledger summary")
    sub.add_parser("audit", help="inventory <-> ledger <-> Google Photos audit")
    al = sub.add_parser("albums", help="one album per trip/outing")
    al.add_argument("--create", action="store_true")
    cl = sub.add_parser("cleanup", help="trash superseded copies in Google Photos (via the web UI)")
    cl.add_argument("--plan", action="store_true")
    cl.add_argument("--limit", type=int, default=10)
    cl.add_argument("--dry-run", action="store_true")
    cl.add_argument("--kind", choices=["phone_copy", "superseded"])
    cl.add_argument("--recheck", action="store_true")
    fx = sub.add_parser("fix-dates", help="re-upload camera-clock-reset items with their real time")
    fx.add_argument("--dry-run", action="store_true")
    v = sub.add_parser("verify", help="read back uploaded items and compare capture times")
    v.add_argument("ids", nargs="*")
    a = ap.parse_args(argv)
    config.ensure_dirs()

    if a.cmd == "inventory":
        from .pipeline import write_review_csv
        items = GoProClient().list_all()
        config.INVENTORY.write_text(json.dumps(items, indent=1))
        config.INVENTORY.chmod(0o600)
        print(f"{len(items)} items -> {config.INVENTORY}; review rows: {write_review_csv(items)}")
    elif a.cmd == "review-csv":
        from .pipeline import REVIEW_CSV, write_review_csv
        print(f"{write_review_csv(load_inventory())} rows -> {REVIEW_CSV}")
    elif a.cmd in ("pilot", "run"):
        inv = {x["id"]: x for x in load_inventory()}
        items = [inv[i] for i in a.ids] if a.cmd == "pilot" else list(inv.values())
        return _run(items, upload=not a.no_upload, reencode=not a.no_reencode)
    elif a.cmd == "albums":
        from .albums import main as albums_main
        return albums_main(create=a.create)
    elif a.cmd == "cleanup":
        from . import cleanup
        if a.recheck:
            cleanup.recheck_uncertain()
            return 0
        return cleanup.build_plan() if a.plan else cleanup.execute(a.limit, dry_run=a.dry_run, kind=a.kind)
    elif a.cmd == "fix-dates":
        from .fix_dates import main as fix_main
        return fix_main(dry_run=a.dry_run)
    elif a.cmd == "audit":
        from .audit import main as audit_main
        return audit_main()
    elif a.cmd == "status":
        from .ledger import Ledger
        print(Ledger().counts())
    elif a.cmd == "verify":
        from .gphotos import GooglePhotos
        from .ledger import Ledger
        from .metadata import Capture
        led, gp = Ledger(), GooglePhotos()
        rows = led.rows("status = 'DONE'") if not a.ids else [r for i in a.ids for r in led.rows("gopro_id = ? AND status = 'DONE'", i)]
        for r in rows:
            mi = gp.get(r["media_item_id"])
            cap = Capture.from_json(r["capture_json"]) if r["capture_json"] else None
            google = mi.get("mediaMetadata", {}).get("creationTime")
            ours = cap.utc.isoformat().replace("+00:00", "Z") if cap else None
            ok = google and ours and google.startswith(ours[:19])
            print(f"{r['filename']:18s} google={google} ours={ours} local={cap.local if cap else None} "
                  f"{cap.offset_str if cap else ''} {'OK' if ok else 'CHECK'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
