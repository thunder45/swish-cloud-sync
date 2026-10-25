"""CLI: python -m photos_migration <command>"""
from __future__ import annotations

import argparse
import json
import sys

from . import config
from .gopro import GoProAuthError, GoProClient, load_inventory

ORDER = {"Video": 0, "TimeLapseVideo": 0, "MultiClipEdit": 1, "Photo": 2, "TimeLapse": 3, "Burst": 3}
MAX_IDLE_RESUMES = 3


def _wait_for_network(max_hours: float = 12) -> bool:
    import time
    import requests
    deadline = time.time() + max_hours * 3600
    while time.time() < deadline:
        try:
            requests.get("https://api.gopro.com", timeout=15)
            requests.get("https://photoslibrary.googleapis.com", timeout=15)
            return True
        except (requests.ConnectionError, requests.Timeout):
            time.sleep(60)
    return False


def _run(items: list[dict], upload: bool, reencode: bool, first_run: bool, takeover: bool) -> int:
    import time
    import traceback
    from .gphotos import PhotosAuthError
    from .pipeline import Migrator
    try:
        m = Migrator(load_inventory(), upload=upload, reencode=reencode, first_run=first_run, takeover=takeover)
    except PhotosAuthError as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        return 2
    # GPS units first: they carry zone hints and clock errors; frame grabs take their capture from videos
    items = sorted(items, key=lambda x: (ORDER.get(x["type"], 9), x.get("captured_at") or ""))
    restarts, idle_resumes = 0, 0
    try:
        while True:
            done_before = m.ledger.counts().get("DONE", 0)
            try:
                for it in items:
                    m.process_item(it)            # finished items are skipped from the ledger alone
                deferred = {r["gopro_id"] for r in m.ledger.rows("status = 'DEFERRED'")}
                for it in items:                   # second pass: frame grabs whose video is now done
                    if it["id"] in deferred:
                        m.process_item(it)
                break
            except (GoProAuthError, PhotosAuthError) as e:
                print(f"STOPPED: {e}", file=sys.stderr, flush=True)
                return 2
            except config.NetworkDown as e:
                progressed = m.ledger.counts().get("DONE", 0) > done_before
                idle_resumes = 0 if progressed else idle_resumes + 1
                if idle_resumes >= MAX_IDLE_RESUMES:
                    print(f"STOPPED: {MAX_IDLE_RESUMES} pauses in a row without progress ({e})", file=sys.stderr, flush=True)
                    return 3
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
    finally:
        m.ledger.release_lease()
    print(m.ledger.counts(), flush=True)
    return 0


def _doctor() -> int:
    """Check a machine before a run: tools, folders, credentials."""
    import shutil
    import subprocess
    checks = []

    def check(name, ok, hint=""):
        checks.append(ok)
        print(f"{'OK  ' if ok else 'FAIL'} {name}{'' if ok else '  -> ' + hint}")

    check(f"Python {sys.version.split()[0]}", sys.version_info >= (3, 12), "use Python 3.12+ (e.g. brew install python@3.12)")
    check("exiftool", bool(shutil.which("exiftool")), "brew install exiftool")
    ff = shutil.which("ffmpeg")
    enc = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout if ff else ""
    flt = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True, text=True).stdout if ff else ""
    check("ffmpeg with libx265 and libvmaf", "libx265" in enc and "libvmaf" in flt, "brew install ffmpeg")
    check("Google Chrome", any(p.exists() for p in [__import__("pathlib").Path("/Applications/Google Chrome.app")]),
          "install Chrome (web search and cleanup drive it)")
    try:
        import playwright  # noqa: F401
        check("playwright", True)
    except ImportError:
        check("playwright", False, ".venv/bin/pip install -r photos_migration/requirements.txt")
    check(f"data folder {config.display(config.DATA_DIR)}", config.DATA_DIR.exists(), "start Google Drive for desktop and let it sync")
    check(f"ledger {config.display(config.LEDGER)}", config.LEDGER.exists(), "wait for Drive to sync it (never 'init' on a second machine)")
    check("Google OAuth client (local/secrets/client_secret.json)", config.GOOGLE_CLIENT_SECRET.exists(),
          "copy it from the old machine; a different client cannot see the existing uploads")
    check("Google sign-in (local/secrets/google_token.json)", config.GOOGLE_TOKEN.exists(), "python -m photos_migration login")
    check("GoPro cookies (local/secrets/gopro_cookies.txt)", config.GOPRO_COOKIES.exists(), "paste the browser Cookie header")
    return 0 if all(checks) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="photos_migration")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create a NEW empty ledger (only for a brand-new migration)")
    sub.add_parser("login", help="sign in to Google Photos (opens the browser)")
    sub.add_parser("doctor", help="check tools, folders and credentials on this machine")
    sub.add_parser("inventory", help="list GoPro Cloud, save the inventory and the review sheet")
    for name in ("pilot", "run"):
        p = sub.add_parser(name, help="process given GoPro ids" if name == "pilot" else "process the whole inventory")
        if name == "pilot":
            p.add_argument("ids", nargs="+")
        p.add_argument("--no-upload", action="store_true")
        p.add_argument("--no-reencode", action="store_true")
        p.add_argument("--first-run", action="store_true", help="allow an empty ledger (brand-new migration)")
        p.add_argument("--takeover", action="store_true", help="ignore another machine's lease")
    sub.add_parser("review-csv", help="(re)write same_name_review.csv, keeping decisions")
    sub.add_parser("status", help="ledger summary")
    sub.add_parser("audit", help="inventory <-> ledger <-> Google Photos audit")
    al = sub.add_parser("albums", help="one album per trip/outing")
    al.add_argument("--create", action="store_true")
    al.add_argument("--takeover", action="store_true", help="ignore another machine's lease")
    cl = sub.add_parser("cleanup", help="trash superseded copies in Google Photos (via the web UI)")
    cl.add_argument("--plan", action="store_true")
    cl.add_argument("--limit", type=int, default=10)
    cl.add_argument("--dry-run", action="store_true")
    cl.add_argument("--kind", choices=["phone_copy", "superseded"])
    cl.add_argument("--recheck", action="store_true")
    cl.add_argument("--takeover", action="store_true", help="ignore another machine's lease")
    for name, hlp in (("fix-dates", "re-upload camera-clock-reset items with their real time"),
                      ("fix-drift", "re-upload GPS-less items with the camera clock error measured by GPS neighbours")):
        fx = sub.add_parser(name, help=hlp)
        fx.add_argument("--dry-run", action="store_true")
        fx.add_argument("--takeover", action="store_true")
    v = sub.add_parser("verify", help="read back uploaded items and compare capture times")
    v.add_argument("ids", nargs="*")
    a = ap.parse_args(argv)

    if a.cmd == "doctor":
        return _doctor()
    config.ensure_dirs(create_data=a.cmd == "init")
    if a.cmd == "init":
        from .ledger import Ledger
        if config.LEDGER.exists():
            raise SystemExit(f"ledger already exists: {config.display(config.LEDGER)}")
        Ledger(create=True)
        print(f"new empty ledger: {config.display(config.LEDGER)}")
    elif a.cmd == "login":
        from .gphotos import GooglePhotos
        GooglePhotos(interactive=True)
        print("signed in")
    elif a.cmd == "inventory":
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
        return _run(items, upload=not a.no_upload, reencode=not a.no_reencode,
                    first_run=a.first_run, takeover=a.takeover)
    elif a.cmd == "albums":
        from .albums import main as albums_main
        return albums_main(create=a.create, takeover=a.takeover)
    elif a.cmd == "cleanup":
        from . import cleanup
        if a.recheck:
            cleanup.recheck_uncertain(a.takeover)
            return 0
        if a.plan:
            return cleanup.build_plan(a.takeover)
        return cleanup.execute(a.limit, dry_run=a.dry_run, kind=a.kind, takeover=a.takeover)
    elif a.cmd == "fix-dates":
        from .fix_dates import main as fix_main
        return fix_main(dry_run=a.dry_run, takeover=a.takeover)
    elif a.cmd == "fix-drift":
        from .fix_drift import main as drift_main
        return drift_main(dry_run=a.dry_run, takeover=a.takeover)
    elif a.cmd == "audit":
        from .audit import main as audit_main
        return audit_main()
    elif a.cmd == "status":
        from .ledger import Ledger
        import socket
        led = Ledger(readonly=True)
        holder = led.meta("lease_holder")
        lease = holder and ("this machine" if holder.split(":")[0] == socket.gethostname() else "another machine")
        print(json.dumps({"ledger": config.display(led.path), "counts": led.counts(), "last_update": led.last_update(),
                          "lease": lease, "lease_heartbeat": led.meta("lease_heartbeat")}, indent=1))
    elif a.cmd == "verify":
        from .gphotos import GooglePhotos
        from .ledger import Ledger
        from .metadata import Capture
        led, gp = Ledger(readonly=True), GooglePhotos()
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
