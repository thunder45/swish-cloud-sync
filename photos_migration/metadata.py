"""True capture time/location and writing them where Google Photos reads them.

Ground truth, in order of preference:
1. GPS (GPMF telemetry in GoPro MP4s, EXIF GPS in photos): exact UTC + place,
   time zone derived from the coordinates. Fixes wrong camera clocks.
2. GoPro Cloud captured_at, interpreted by upload path:
   - camera direct upload ('gp-cah-controller/'): camera local wall clock labelled Z
   - Quik app upload: real UTC (+ captured_at_timezone on newer uploads)
3. Time zone from a same-day neighbour with GPS, else config.DEFAULT_TIMEZONE.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config

MIN_FIX_MODE = 2          # GPSMeasureMode: 2 = 2D fix, 3 = 3D fix
MAX_FIX_ERROR = 10.0      # GPSHPositioningError (99.99 means no fix)
QUARTER_HOUR = timedelta(minutes=15)
CLOCK_RESET_YEAR = 2016   # HERO cameras restart at 2016-01-01 (or 2015-12-31) after losing power


def is_clock_reset(dt: datetime | None) -> bool:
    return dt is not None and dt.year <= CLOCK_RESET_YEAR


@dataclass
class Capture:
    utc: datetime                  # aware, UTC
    utc_offset_min: int            # local offset at capture time
    tz: str | None                 # IANA name when known
    lat: float | None = None
    lon: float | None = None
    alt: float | None = None
    source: str = ""               # how utc/offset were determined
    camera_clock_error_s: float | None = None
    model: str | None = None

    @property
    def local(self) -> datetime:
        return (self.utc + timedelta(minutes=self.utc_offset_min)).replace(tzinfo=None)

    @property
    def offset_str(self) -> str:
        m = self.utc_offset_min
        sign = "+" if m >= 0 else "-"
        return f"{sign}{abs(m) // 60:02d}:{abs(m) % 60:02d}"

    def to_json(self) -> str:
        d = asdict(self)
        d["utc"] = self.utc.isoformat()
        return json.dumps(d)

    @staticmethod
    def from_json(s: str) -> "Capture":
        d = json.loads(s)
        d["utc"] = datetime.fromisoformat(d["utc"])
        return Capture(**d)


def exiftool(path: Path, *args: str) -> dict:
    out = subprocess.run(["exiftool", "-j", "-n", "-G3", "-api", "LargeFileSupport=1", *args, str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)[0]


@lru_cache(maxsize=1)
def _tzfinder():
    from timezonefinder import TimezoneFinder
    return TimezoneFinder()


def tz_at(lat: float, lon: float) -> str | None:
    """IANA zone on land; None offshore (ocean 'Etc/GMT±n' zones have no DST and wrong offsets)."""
    tz = _tzfinder().timezone_at_land(lat=lat, lng=lon)
    return None if not tz or tz.startswith("Etc/") else tz


def offset_minutes(tz: str, utc: datetime) -> int:
    return int(utc.astimezone(ZoneInfo(tz)).utcoffset().total_seconds() // 60)


def parse_offset(s: str | None) -> int | None:
    """'+01:00' -> 60. None for anything that is not a fixed offset."""
    if not s or s[0] not in "+-" or ":" not in s:
        return None
    hh, mm = s[1:].split(":")
    return (1 if s[0] == "+" else -1) * (int(hh) * 60 + int(mm))


def zone_offset(hint: str | None, *, utc: datetime | None = None, wall: datetime | None = None) -> tuple[int, str | None]:
    """Offset (minutes) for a hint that is an IANA zone or a fixed '+HH:MM'; default zone if no hint."""
    hint = hint or config.DEFAULT_TIMEZONE
    fixed = parse_offset(hint)
    if fixed is not None:
        return fixed, None
    z = ZoneInfo(hint)
    if utc is not None:
        return int(utc.astimezone(z).utcoffset().total_seconds() // 60), hint
    return int(wall.replace(tzinfo=z).utcoffset().total_seconds() // 60), hint


def item_tz(item: dict) -> str | None:
    """captured_at_timezone when trustworthy. Quik edits rendered in the cloud carry a fake '+00:00'."""
    tz = item.get("captured_at_timezone")
    if not tz or (item.get("content_source") is None and tz == "+00:00"):
        return None
    return tz


def _parse_exif_dt(s: str | None) -> datetime | None:
    if not s or s.startswith("0000"):
        return None
    s = s.strip().rstrip("Z").split(".")[0].split("+")[0]
    try:
        return datetime.strptime(s, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _parse_cloud(s: str | None) -> datetime | None:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ") if s else None


def _round_quarter(delta: timedelta) -> timedelta | None:
    """delta rounded to a whole 15 min if it is within 2 min of one, else None."""
    q = round(delta / QUARTER_HOUR)
    return q * QUARTER_HOUR if abs(delta - q * QUARTER_HOUR) <= timedelta(minutes=2) else None


def _gps_time(doc: dict) -> datetime | None:
    t = _parse_exif_dt(doc.get("GPSDateTime"))
    if t is None:
        return None
    frac = str(doc["GPSDateTime"]).split(".")
    ms = int((frac[1] + "000")[:3]) if len(frac) > 1 else 0
    return (t + timedelta(milliseconds=ms)).replace(tzinfo=timezone.utc)


def video_gps_start(path: Path) -> tuple[datetime, float, float, float | None] | None:
    """UTC at t=0 plus position, from GPMF samples with a real fix.

    Time-lapse/TimeWarp videos compress time: GPS time advances k seconds per second
    of video, so scale the first fix's SampleTime by k measured across all fixes.
    """
    d = exiftool(path, "-ee", "-SampleTime", "-GPSDateTime", "-GPSMeasureMode",
                 "-GPSHPositioningError", "-GPSLatitude", "-GPSLongitude", "-GPSAltitude")
    docs: dict[str, dict] = {}
    for k, v in d.items():
        if ":" in k and k.startswith("Doc"):
            doc, tag = k.split(":", 1)
            docs.setdefault(doc, {})[tag] = v
    fixes = []
    for doc in docs.values():
        if doc.get("GPSMeasureMode", 0) < MIN_FIX_MODE or doc.get("GPSHPositioningError", 99.99) > MAX_FIX_ERROR:
            continue
        t = _gps_time(doc)
        if t is None or "GPSLatitude" not in doc:
            continue
        fixes.append((float(doc.get("SampleTime") or 0), t, doc))
    if not fixes:
        return None
    fixes.sort(key=lambda f: f[0])
    st0, t0, doc0 = fixes[0]
    stn, tn, _ = fixes[-1]
    k = (tn - t0).total_seconds() / (stn - st0) if stn - st0 > 2 else 1.0
    if k < 1.5:
        k = 1.0
    utc0 = t0 - timedelta(seconds=st0 * k)
    return utc0.replace(microsecond=0), doc0["GPSLatitude"], doc0["GPSLongitude"], doc0.get("GPSAltitude")


def capture_for_video(item: dict, path: Path, hint: str | None = None) -> Capture:
    tags = exiftool(path, "-QuickTime:CreateDate", "-GoPro:Model")
    camera_clock = _parse_exif_dt(tags.get("Main:CreateDate"))
    model = tags.get("Main:Model") or item.get("camera_model")
    gps = video_gps_start(path)
    if gps:
        utc, lat, lon, alt = gps
        land = tz_at(lat, lon)
        off, tz = (offset_minutes(land, utc), land) if land else zone_offset(hint, utc=utc)
        cap = Capture(utc, off, tz, lat, lon, alt, "gps" if land else "gps+hint-tz", model=model)
        if camera_clock:
            # camera clock vs true local (or vs UTC when the file stores UTC)
            err_local = (camera_clock - cap.local).total_seconds()
            err_utc = (camera_clock - utc.replace(tzinfo=None)).total_seconds()
            cap.camera_clock_error_s = err_local if abs(err_local) <= abs(err_utc) else err_utc
        return cap
    return capture_from_cloud(item, camera_clock, hint, model)


def capture_from_cloud(item: dict, camera_clock: datetime | None, hint: str | None,
                       model: str | None = None) -> Capture:
    """No GPS: GoPro Cloud captured_at, interpreted by upload path."""
    cloud = _parse_cloud(item.get("captured_at"))
    if cloud is None:
        raise ValueError(f"no capture time for {item.get('id')}")
    hint_src = "hint" if hint else "default"
    if (item.get("content_source") or "").startswith("gp-cah"):
        # camera wall clock stored as if it were UTC; a chapter's own CreateDate is its real start
        wall = camera_clock or cloud
        off, tz = zone_offset(item_tz(item) or hint, wall=wall)
        utc = (wall - timedelta(minutes=off)).replace(tzinfo=timezone.utc)
        return Capture(utc, off, tz, source=f"cloud-local+{hint_src}-tz", model=model)
    utc = cloud.replace(tzinfo=timezone.utc)
    if camera_clock is not None:
        q = _round_quarter(camera_clock - cloud)
        if q is not None and timedelta(0) < abs(q) <= timedelta(hours=14):
            return Capture(utc, int(q.total_seconds() // 60), None, source="cloud-utc+camera-offset", model=model)
    itz = item_tz(item)
    if itz:
        return Capture(utc, parse_offset(itz), None, source="cloud-utc+cloud-tz", model=model)
    off, tz = zone_offset(hint, utc=utc)
    return Capture(utc, off, tz, source=f"cloud-utc+{hint_src}-tz", model=model)


def capture_for_photo(item: dict, path: Path, hint: str | None = None) -> tuple[Capture | None, bool]:
    """(capture, needs_write).

    - EXIF date + GPS: left untouched (the place gives Google the zone).
    - EXIF date, no GPS: only the UTC offset tags get written.
    - no EXIF date (frame grabs): (None, True) - caller supplies the capture.
    """
    t = exiftool(path, "-EXIF:DateTimeOriginal", "-Composite:GPSLatitude", "-Composite:GPSLongitude",
                 "-GPS:GPSAltitude", "-Composite:GPSDateTime")
    dto = _parse_exif_dt(t.get("Main:DateTimeOriginal"))
    lat, lon = t.get("Main:GPSLatitude"), t.get("Main:GPSLongitude")
    gps_utc = _parse_exif_dt(str(t.get("Main:GPSDateTime") or "")) if t.get("Main:GPSDateTime") else None
    if is_clock_reset(dto) and gps_utc and not is_clock_reset(gps_utc) and lat is not None:
        # camera clock was reset: the GPS fix carries the real time
        utc = gps_utc.replace(tzinfo=timezone.utc)
        land = tz_at(lat, lon)
        off, tz = (offset_minutes(land, utc), land) if land else zone_offset(hint, utc=utc)
        return Capture(utc, off, tz, lat, lon, t.get("Main:GPSAltitude"), "gps-time (camera clock reset)"), True
    if is_clock_reset(dto):
        return None, True          # caller must find the real time (source video, segment offset)
    if dto and lat is not None and lon is not None:
        land = tz_at(lat, lon)
        off, tz = zone_offset(land or hint, wall=dto)
        utc = (dto - timedelta(minutes=off)).replace(tzinfo=timezone.utc)
        return Capture(utc, off, tz, lat, lon, t.get("Main:GPSAltitude"), "exif+gps"), False
    if dto:
        source, off, tz = None, None, None
        itz = item_tz(item)
        cloud = _parse_cloud(item.get("captured_at"))
        if itz:
            off, source = parse_offset(itz), "exif+cloud-tz"
        elif cloud and not (item.get("content_source") or "").startswith("gp-cah"):
            q = _round_quarter(dto - cloud)        # Quik uploads: captured_at is real UTC
            if q is not None and timedelta(0) < abs(q) <= timedelta(hours=14):
                off, source = int(q.total_seconds() // 60), "exif+cloud-offset"
        if off is None:
            off, tz = zone_offset(hint, wall=dto)
            source = f"exif+{'hint' if hint else 'default'}-tz"
        utc = (dto - timedelta(minutes=off)).replace(tzinfo=timezone.utc)
        return Capture(utc, off, tz, source=source), True
    return None, True


def write_video(path: Path, cap: Capture) -> None:
    stamp = cap.local.strftime("%Y:%m:%d %H:%M:%S") + cap.offset_str
    args = ["exiftool", "-q", "-overwrite_original", "-api", "QuickTimeUTC=1", "-api", "LargeFileSupport=1"]
    for tag in ("QuickTime:CreateDate", "QuickTime:ModifyDate", "QuickTime:TrackCreateDate",
                "QuickTime:TrackModifyDate", "QuickTime:MediaCreateDate", "QuickTime:MediaModifyDate"):
        args.append(f"-{tag}={stamp}")
    args.append(f"-Keys:CreationDate={stamp}")
    if cap.lat is not None and cap.lon is not None:
        # Google Photos reportedly ignores ISO 6709 strings with more than 5 decimals
        coords = f"{cap.lat:.5f}, {cap.lon:.5f}" + (f", {cap.alt:.1f}" if cap.alt is not None else "")
        args += [f"-Keys:GPSCoordinates={coords}", f"-UserData:GPSCoordinates={coords}",
                 f"-ItemList:GPSCoordinates={coords}",
                 "-UserData:LocationInformation="]     # ffmpeg-copied 'loci' would be a second, older location
    if cap.model:
        args += ["-Keys:Make=GoPro", f"-Keys:Model={cap.model}"]
    subprocess.run(args + [str(path)], check=True)


def write_photo(path: Path, cap: Capture) -> None:
    local = cap.local.strftime("%Y:%m:%d %H:%M:%S")
    args = ["exiftool", "-q", "-overwrite_original",
            f"-EXIF:DateTimeOriginal={local}", f"-EXIF:CreateDate={local}",
            f"-EXIF:OffsetTimeOriginal={cap.offset_str}", f"-EXIF:OffsetTimeDigitized={cap.offset_str}",
            f"-EXIF:OffsetTime={cap.offset_str}",
            "-ExifIFD:ExifVersion=0232"]        # OffsetTime* tags are EXIF 2.31+; GoPro writes 2.21
    if cap.lat is not None and cap.lon is not None:
        args.append(f"-GPSPosition={cap.lat:.6f}, {cap.lon:.6f}")
        if cap.alt is not None:
            args += [f"-GPSAltitude={abs(cap.alt):.1f}", f"-GPSAltitudeRef#={'0' if cap.alt >= 0 else '1'}"]
    if cap.model:
        args += ["-EXIF:Make=GoPro", f"-EXIF:Model={cap.model}"]
    subprocess.run(args + [str(path)], check=True)
