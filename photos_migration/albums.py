"""Group the migrated GoPro items into one album per trip or outing.

- a day is "away" when its GPS centre is more than AWAY_KM from home (the most common
  GPS area of the whole library); consecutive away days in the same region form a trip;
- every other day is one outing ("passeio") album;
- days without GPS join a trip when they fall inside it, otherwise they stand alone.

Only app-created items can be added to albums through the API; items Google merged into
originals that already existed are listed for manual adding.

    python -m photos_migration albums --plan     # write albums_plan.csv and print it
    python -m photos_migration albums --create   # create the albums and add the items
"""
from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from datetime import date, timedelta

from . import config
from .cleanup import _readable
from .gphotos import API, GooglePhotos
from .ledger import Ledger
from .metadata import Capture

AWAY_KM = 60
TRIP_GAP_DAYS = 2
TRIP_REGION_KM = 400
PLAN = config.DATA_DIR / "albums_plan.csv"
# trip names chosen by the user, keyed by the trip's first day (kept outside the repo: personal data)
TRIP_NAMES_FILE = config.TRIP_NAMES
COUNTRY = {"LU": "Luxemburgo", "FR": "França", "DE": "Alemanha", "BE": "Bélgica", "NL": "Holanda", "ES": "Espanha",
           "PT": "Portugal", "IT": "Itália", "TR": "Turquia", "TN": "Tunísia", "BR": "Brasil", "CH": "Suíça",
           "AT": "Áustria", "GR": "Grécia", "HR": "Croácia", "GB": "Reino Unido", "MA": "Marrocos", "US": "EUA"}


def _km(a, b) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p = math.pi / 180
    h = (math.sin((la2 - la1) * p / 2) ** 2
         + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(h))


def _median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2]


def build_plan(led: Ledger, gp: GooglePhotos) -> tuple[list[dict], list[dict]]:
    import reverse_geocoder as rg
    readable = _readable(led, gp)
    items, not_addable = [], []
    for r in led.rows("status = 'DONE' AND capture_json IS NOT NULL"):
        cap = Capture.from_json(r["capture_json"])
        it = {"media_item_id": r["media_item_id"], "filename": r["filename"], "kind": r["kind"],
              "local": cap.local, "gps": (cap.lat, cap.lon) if cap.lat is not None else None}
        (items if r["media_item_id"] in readable else not_addable).append(it)
    gps = [i["gps"] for i in items if i["gps"]]
    home = Counter((round(a, 1), round(b, 1)) for a, b in gps).most_common(1)[0][0]

    days = defaultdict(list)
    for i in items:
        days[i["local"].date()].append(i)
    info = {}
    for d, its in days.items():
        pts = [i["gps"] for i in its if i["gps"]]
        rep = (_median([p[0] for p in pts]), _median([p[1] for p in pts])) if pts else None
        info[d] = {"rep": rep, "away": rep is not None and _km(rep, home) > AWAY_KM, "pts": pts}

    # trips: runs of away days, close in time and region; GPS-less days inside a run join it
    ordered = sorted(days)
    groups, cur = [], None
    for d in ordered:
        inf = info[d]
        if cur and cur["away"]:
            gap = (d - cur["end"]).days
            same_region = inf["rep"] is None or _km(inf["rep"], cur["rep"]) <= TRIP_REGION_KM
            if gap <= TRIP_GAP_DAYS and (inf["away"] or inf["rep"] is None) and same_region:
                cur["days"].append(d)
                cur["end"] = d
                continue
        cur = {"days": [d], "start": d, "end": d, "away": inf["away"], "rep": inf["rep"]}
        groups.append(cur)

    # GPS-less days right next to a trip (travel days) join it
    for gi, g in enumerate(groups):
        if not g["away"]:
            continue
        for nb in (gi - 1, gi + 1):
            if 0 <= nb < len(groups):
                o = groups[nb]
                if not o["away"] and all(info[d]["rep"] is None for d in o["days"]) and len(o["days"]) == 1:
                    d = o["days"][0]
                    if abs((d - g["start"]).days) <= 1 or abs((d - g["end"]).days) <= 1:
                        g["days"] = sorted(set(g["days"]) | {d})
                        g["start"], g["end"] = g["days"][0], g["days"][-1]
                        o["days"] = []
    groups = [g for g in groups if g["days"]]

    geo_cache = {}

    def place(points, away: bool) -> str:
        if not points:
            return ""
        key = tuple(points[:200])
        if key not in geo_cache:
            res = rg.search(list(points[:200]), mode=1, verbose=False)
            cities = Counter(x["name"] for x in res).most_common(1)[0][0]
            region = Counter(x["admin1"] or x["name"] for x in res).most_common(1)[0][0]
            cc = Counter(x["cc"] for x in res).most_common(1)[0][0]
            country = COUNTRY.get(cc, cc)
            geo_cache[key] = f"{region}, {country}" if away else (cities if cc == "LU" else f"{cities}, {country}")
        return geo_cache[key]

    plan = []
    for g in groups:
        its = [i for d in g["days"] for i in days[d]]
        pts = [p for d in g["days"] for p in info[d]["pts"]]
        where = place(pts, g["away"])
        span = (g["start"].isoformat() if g["start"] == g["end"]
                else f"{g['start'].isoformat()} a {g['end'].strftime('%d/%m') if g['start'].year == g['end'].year else g['end'].isoformat()}")
        title = f"GoPro {span}" + (f" · {where}" if where else "")
        kinds = Counter("vídeo" if i["kind"] in ("Video", "TimeLapseVideo", "MultiClipEdit") else "foto" for i in its)
        gps_ids = [i["media_item_id"] for i in its if i["gps"]]
        plan.append({"title": title, "sample": gps_ids[len(gps_ids) // 2] if gps_ids else None, "where": where, "kind": "viagem" if g["away"] and len(g["days"]) > 1 else ("viagem (1 dia)" if g["away"] else "passeio"),
                     "start": g["start"].isoformat(), "end": g["end"].isoformat(), "items": len(its),
                     "videos": kinds.get("vídeo", 0), "photos": kinds.get("foto", 0),
                     "ids": [i["media_item_id"] for i in sorted(its, key=lambda x: x["local"])]})
    return plan, not_addable


def _google_place_names(plan: list[dict], gp: GooglePhotos) -> None:
    """Trips: use the place name Google Photos itself shows for a GPS item of the trip."""
    import time
    from playwright.sync_api import sync_playwright
    from .cleanup import _details
    from .gphotos_web import _open, wait_for_login
    trip_names = json.loads(TRIP_NAMES_FILE.read_text()) if TRIP_NAMES_FILE.exists() else {}
    for p in plan:
        name = trip_names.get(p["start"]) if p["kind"].startswith("viagem") else None
        if name:
            p["title"] = p["title"].replace(f" · {p['where']}", "") + f" · {name}" if p["where"] else f"{p['title']} · {name}"
            p["named"] = True
    trips = [p for p in plan if p["kind"].startswith("viagem") and p["sample"] and not p.get("named")]
    if not trips:
        return
    with sync_playwright() as pw:
        ctx = _open(pw)
        page = wait_for_login(ctx)
        for p in trips:
            page.goto(gp.get(p["sample"])["productUrl"], wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)
            lines = [l.strip() for l in _details(page).splitlines() if l.strip()]
            stop = ("Map data", "Terms", "Learn more", "Add a location", "Estimated location")
            tail = [l for l in lines if not l.startswith(stop)]
            # the place is the last non-boilerplate line after the backup/quality lines
            idx = max((i for i, l in enumerate(tail) if l.startswith(("Original quality", "Storage saver", "Backed up"))), default=None)
            name = tail[idx + 1] if idx is not None and idx + 1 < len(tail) else None
            if name and len(name) < 60:
                p["title"] = p["title"].replace(p["where"], name) if p["where"] else f"{p['title']} · {name}"
        ctx.close()


def main(create: bool = False) -> int:
    led, gp = Ledger(), GooglePhotos()
    plan, not_addable = build_plan(led, gp)
    _google_place_names(plan, gp)
    with open(PLAN, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["title", "kind", "start", "end", "items", "videos", "photos"])
        w.writeheader()
        for p in plan:
            w.writerow({k: p[k] for k in w.fieldnames})
    PLAN.chmod(0o600)
    for p in plan:
        print(f"{p['title']:60s} {p['kind']:15s} {p['videos']:3d} vídeos {p['photos']:4d} fotos")
    print(f"\n{len(plan)} albums, {sum(p['items'] for p in plan)} items; "
          f"{len(not_addable)} items not addable via API (Google merged them into existing originals)")
    if not create:
        return 0
    existing = gp.app_albums()
    for p in plan:
        aid = existing.get(p["title"]) or gp.create_album(p["title"])
        for i in range(0, len(p["ids"]), 50):
            gp._post_retry(f"{API}/albums/{aid}:batchAddMediaItems", "add to album", json={"mediaItemIds": p["ids"][i:i + 50]})
        print(f"created {p['title']} ({p['items']})", flush=True)
    with open(config.DATA_DIR / "albums_not_addable.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["filename", "local_time"])
        for i in sorted(not_addable, key=lambda x: x["local"]):
            w.writerow([i["filename"], i["local"].isoformat()])
    return 0
