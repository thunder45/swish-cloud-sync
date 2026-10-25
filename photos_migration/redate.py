"""Re-upload units with a corrected capture (shared by fix-dates and fix-drift).

The Library API cannot change the date of an existing item, so a corrected copy is
uploaded and the old item is recorded in `superseded` for `cleanup`:
- the original is downloaded again and must have the SHA-256 recorded at upload time;
- metadata is written into a copy, never into the verified original;
- the superseded row is 'pending' until the replacement is DONE, then 'active'
  (cleanup only trashes active rows; the ledger never loses the old item id).
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import config, encode, metadata
from .metadata import Capture
from .pipeline import VIDEO_TYPES, Migrator, _sha256

SCHEMA = """CREATE TABLE IF NOT EXISTS superseded (
    unit_id TEXT, old_media_item_id TEXT, old_capture_json TEXT, reason TEXT, at TEXT, state TEXT DEFAULT 'active')"""


def ensure_schema(led) -> None:
    led.db.execute(SCHEMA)
    cols = {r["name"] for r in led.db.execute("PRAGMA table_info(superseded)")}
    if "state" not in cols:
        led.db.execute("ALTER TABLE superseded ADD COLUMN state TEXT DEFAULT 'active'")


def fetch_verified_original(m: Migrator, row, item: dict, workdir: Path) -> Path:
    """The unit's original from GoPro Cloud, byte-identical to what was hashed at upload."""
    workdir.mkdir(parents=True, exist_ok=True)
    orig = workdir / f"orig{Path(row['filename']).suffix.lower()}"
    if orig.exists() and _sha256(orig) != row["orig_sha256"]:
        orig.unlink()
    if not orig.exists():
        part = int(row["part"]) if row["part"] and str(row["part"]).isdigit() else None
        label = "baked_source" if item["type"] == "MultiClipEdit" else "source"
        tmp = workdir / f"download{orig.suffix}"
        _, sha = m.gopro.download(m.gopro.fresh_source(item, part, label), tmp, None)
        if sha != row["orig_sha256"]:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"{row['unit_id']}: original at GoPro differs from the uploaded one")
        tmp.rename(orig)
    return orig


def camera_clock(path: Path, is_video: bool):
    tag = "-QuickTime:CreateDate" if is_video else "-EXIF:DateTimeOriginal"
    return metadata._parse_exif_dt(metadata.exiftool(path, tag).get("Main:CreateDate" if is_video else "Main:DateTimeOriginal"))


def redate(m: Migrator, row, item: dict, orig: Path, cap: Capture, reason: str) -> str:
    """Upload a corrected copy of `orig` for unit `row`. Returns the final unit status."""
    led = m.ledger
    ensure_schema(led)
    uid = row["unit_id"]
    is_video = item["type"] in VIDEO_TYPES
    if cap.model is None:
        cap.model = item.get("camera_model")
    upload = orig.parent / f"upload{orig.suffix}"
    upload.unlink(missing_ok=True)
    if is_video:
        enc = encode.adaptive_encode(orig, orig.parent / "enc")
        if enc.use_hevc:
            enc.path.rename(upload)
        else:
            shutil.copy2(orig, upload)
        metadata.write_video(upload, cap)
    else:
        shutil.copy2(orig, upload)
        metadata.write_photo(upload, cap)
    old = row["media_item_id"]
    if old:
        led.db.execute("INSERT INTO superseded (unit_id, old_media_item_id, old_capture_json, reason, at, state) "
                       "VALUES (?,?,?,?,?, 'pending')",
                       (uid, old, row["capture_json"], reason, datetime.now(timezone.utc).isoformat()))
    led.upsert(uid, capture_json=cap.to_json(), upload_file=str(upload), upload_bytes=upload.stat().st_size,
               upload_sha256=_sha256(upload), upload_token=None, token_at=None, media_item_id=None,
               create_errors=0, status="PREPARED", note=reason[:300])
    m._upload_and_create(uid, row["filename"], upload, "video/mp4" if is_video else "image/jpeg", row["album_title"])
    status = led.get(uid)["status"]
    if old and status == "DONE":
        led.db.execute("UPDATE superseded SET state='active' WHERE unit_id=? AND old_media_item_id=?", (uid, old))
    if status == "DONE":
        shutil.rmtree(orig.parent, ignore_errors=True)
    return status


def work_dir(kind: str, uid: str) -> Path:
    return config.WORK_DIR / kind / uid.replace("#", "_").replace(":", "_")
