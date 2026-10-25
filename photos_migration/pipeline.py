"""Per-unit pipeline: download -> dedup -> capture -> prepare -> upload -> create.

Safety rules (a duplicate in Google Photos cannot be deleted through the API):
- a unit that ever had an upload token is never re-downloaded or re-prepared;
  retries re-send the same SHA-verified file, so Google's byte dedup applies;
- FAILED is only written before an upload started, and never over another status;
- GoPro auth errors abort the run instead of marking items;
- same-name / same-recording items are held in REVIEW until the user decides
  in same_name_review.csv;
- frame grabs wait (DEFERRED) for their source video's capture.
"""
from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import re
import shutil
import zipfile
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, encode, metadata
from .gopro import GoProAuthError, GoProClient, GoProError, Source
from .ledger import TERMINAL, Ledger
from .metadata import Capture

VIDEO_TYPES = ("Video", "TimeLapseVideo", "MultiClipEdit")
SEQUENCE_TYPES = ("TimeLapse", "Burst")
TOKEN_MAX_AGE = timedelta(hours=20)       # upload tokens expire after 1 day
HINT_WINDOW = timedelta(hours=18)
MAX_CREATE_ERRORS = 3
REVIEW_CSV = config.DATA_DIR / "same_name_review.csv"


def is_phone_media(item: dict) -> bool:
    return (item.get("filename") or "").startswith("PXL_") or (item.get("camera_model") or "").startswith("Pixel")


def chapter_filename(name: str, part: int | None) -> str:
    """GoPro chapter naming: GH01xxxx.MP4 -> GH02xxxx.MP4 for chapter 2."""
    if not part:
        return name
    m = re.match(r"^(G[HXP])(\d{2})(\d{4})(\.\w+)$", name, re.I)
    return f"{m[1]}{part:02d}{m[3]}{m[4]}" if m else f"{Path(name).stem}_part{part}{Path(name).suffix}"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _cloud_utc(item: dict) -> datetime | None:
    s = item.get("captured_at")
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if s else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- manual review of same-name / same-recording items ----------
def review_groups(items: list[dict]) -> dict[str, str]:
    """gopro_id -> reason, for items sharing a filename or a recording (source_gumi)."""
    by_name, by_gumi = defaultdict(list), defaultdict(list)
    for x in items:
        if is_phone_media(x):
            continue
        if x.get("filename"):
            by_name[x["filename"]].append(x)
        if x.get("source_gumi"):
            by_gumi[x["source_gumi"]].append(x)
    out = {}
    for grp in by_name.values():
        if len(grp) > 1:
            for x in grp:
                out[x["id"]] = f"same filename as {len(grp) - 1} other item(s)"
    for grp in by_gumi.values():
        if len(grp) > 1:
            for x in grp:
                out[x["id"]] = f"same recording (source_gumi) as {len(grp) - 1} other item(s)"
    # a phone copy stored in GoPro Cloud itself: GH010001_1600000000000.MP4 next to GH010001.MP4
    names = {n.upper() for n in by_name}
    for n, grp in by_name.items():
        m = re.match(r"^(G[A-Z0-9]{6,7})_[A-Z0-9]+(\.\w+)$", n, re.I)
        if m and f"{m[1]}{m[2]}".upper() in names:
            for x in grp:
                out[x["id"]] = f"phone copy of {m[1]}{m[2]} stored in GoPro Cloud"
    return out


def read_review_decisions(path: Path = REVIEW_CSV) -> dict[str, str]:
    """gopro_id -> 'upload' | 'skip' (anything else = undecided)."""
    if not path.exists():
        return {}
    out = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            d = (row.get("decision") or "").strip().lower()
            if d in ("upload", "skip"):
                out[row["gopro_id"]] = d
    return out


def write_review_csv(items: list[dict], path: Path = REVIEW_CSV) -> int:
    """Rewrite the review sheet with a suggestion per item, keeping decisions already made."""
    old = read_review_decisions(path)
    reasons = review_groups(items)
    inv = {x["id"]: x for x in items}
    best_of_gumi = {}
    for gid in reasons:
        x = inv[gid]
        key = ((x.get("item_count") or 1), x.get("file_size") or 0)
        g = x.get("source_gumi")
        if g not in best_of_gumi or key > best_of_gumi[g][0]:
            best_of_gumi[g] = (key, gid)
    gumi_count = defaultdict(int)
    for gid in reasons:
        gumi_count[inv[gid].get("source_gumi")] += 1
    seen = {}
    rows = []
    for gid in sorted(reasons, key=lambda i: (inv[i].get("filename") or "", inv[i].get("created_at") or "")):
        x = inv[gid]
        g = x.get("source_gumi")
        if gumi_count[g] > 1:
            best = best_of_gumi[g][1]
            sugg = "upload" if best == gid else f"skip: chapter-1 copy of {best}, which has all chapters"
        else:
            k = (x.get("filename"), x.get("file_size"), x.get("type"))
            if k in seen:
                sugg = f"skip: same name, size and type as {seen[k]}"
            else:
                seen[k] = gid
                sugg = "upload: distinct file (different content id)"
        rows.append({
            "decision": old.get(gid, ""), "suggestion": sugg, "filename": x.get("filename"), "gopro_id": gid,
            "type": x["type"], "file_size": x.get("file_size"), "duration_ms": x.get("source_duration"),
            "item_count": x.get("item_count"), "captured_at": x.get("captured_at"),
            "captured_at_timezone": x.get("captured_at_timezone"), "upload_path": x.get("content_source"),
            "uploaded_at": x.get("created_at"), "reason": reasons[gid],
        })
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    path.chmod(0o600)
    return len(rows)


class TzHints:
    """Zone evidence for captures without GPS: ledger GPS captures (IANA zones) and
    GoPro Cloud captured_at_timezone of nearby items (Pixel ones included)."""

    def __init__(self, items: list[dict], ledger: Ledger):
        self.ledger = ledger
        ev = []
        for x in items:
            tz, utc = metadata.item_tz(x), _cloud_utc(x)
            if tz and utc and not (x.get("content_source") or "").startswith("gp-cah"):
                ev.append((utc, tz))
        ev.sort()
        self.cloud = ev
        self.cloud_t = [e[0] for e in ev]

    def __call__(self, utc_guess: datetime | None) -> str | None:
        if utc_guess is None:
            return None
        best = None
        for row in self.ledger.rows("capture_json LIKE '%\"source\": \"gps\"%' OR capture_json LIKE '%\"source\": \"exif+gps\"%'"):
            cap = Capture.from_json(row["capture_json"])
            d = abs(cap.utc - utc_guess)
            if cap.tz and not cap.tz.startswith("Etc/") and d <= HINT_WINDOW and (best is None or d < best[0]):
                best = (d, cap.tz)
        if best:
            return best[1]
        i = bisect_left(self.cloud_t, utc_guess)
        near = [(abs(self.cloud[j][0] - utc_guess), self.cloud[j][1]) for j in (i - 1, i) if 0 <= j < len(self.cloud)]
        near = [n for n in near if n[0] <= HINT_WINDOW]
        return min(near)[1] if near else None


class Migrator:
    def __init__(self, items: list[dict], upload: bool = True, reencode: bool = True,
                 first_run: bool = False, takeover: bool = False):
        config.ensure_dirs()
        self._lock = open(config.LOCK_FILE, "w")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another migration run is active (migration.lock)") from None
        self.items = {x["id"]: x for x in items}
        self.gopro = GoProClient()
        self.ledger = Ledger()
        done = self.ledger.counts().get("DONE", 0)
        if items and done == 0 and not first_run:
            raise SystemExit(f"the ledger at {config.display(self.ledger.path)} has no uploaded items but the inventory has "
                             f"{len(items)}: is this the right (fully synced) ledger? Pass --first-run only for a "
                             "brand-new migration.")
        self.ledger.acquire_lease(takeover)
        try:
            self._connect_google(upload)
        except BaseException:
            self.ledger.release_lease()        # a failed start must not block other machines
            raise
        self.reencode = reencode
        self.hint = TzHints(items, self.ledger)
        self.review = review_groups(items)
        self.decisions = read_review_decisions()
        self.video_stems = {Path(x["filename"]).stem.upper() for x in items
                            if x["type"] in ("Video", "TimeLapseVideo") and x.get("filename")}

    def _connect_google(self, upload: bool) -> None:
        self.photos = None
        if not upload:
            return
        from .gphotos import GooglePhotos, client_id
        self.photos = GooglePhotos()
        cid = client_id()
        known = self.ledger.meta("oauth_client_id")
        if known and known != cid:
            raise SystemExit("this machine uses a different Google OAuth client than the one that created the "
                             "uploads; items of the other client are invisible here. Use the original "
                             "client_secret.json.")
        self.ledger.set_meta("oauth_client_id", cid)
        self.check_ledger_matches_google()

    def log(self, uid: str, msg: str) -> None:
        print(f"[{uid}] {msg}", flush=True)

    def check_ledger_matches_google(self) -> None:
        """Every item this app created must be known to the ledger; otherwise the ledger is stale,
        forked or wrong and uploading would create duplicates."""
        known = {r["media_item_id"] for r in self.ledger.rows("media_item_id IS NOT NULL")}
        try:
            known |= {r[0] for r in self.ledger.db.execute("SELECT old_media_item_id FROM superseded")}
        except Exception:  # noqa: BLE001 - table exists only after fix-dates
            pass
        unknown = set(self.photos.list_app_items()) - known
        if unknown:
            raise SystemExit(f"{len(unknown)} items created by this app in Google Photos are not in the ledger at "
                             f"{config.display(self.ledger.path)}: the ledger is stale or not the right one. Stopping before any upload.")

    # ---------- item level ----------
    def _set_failed(self, uid: str, item: dict, note: str, **extra) -> None:
        """FAILED only for rows that never got past download/prepare; otherwise just note the error."""
        row = self.ledger.get(uid)
        if row is None or row["status"] in ("NEW", "FAILED", "DOWNLOADED", "DEFERRED"):
            self.ledger.upsert(uid, gopro_id=item["id"], kind=item["type"], status="FAILED", note=note[:300], **extra)
        else:
            self.ledger.upsert(uid, note=note[:300])

    def _hold(self, item: dict, status: str, note: str) -> None:
        row = self.ledger.get(item["id"])
        if row is None or (row["status"] in ("NEW", "FAILED", "REVIEW", "EXCLUDED") and not row["media_item_id"]
                           and not row["upload_token"]):
            self.ledger.upsert(item["id"], gopro_id=item["id"], kind=item["type"], filename=item.get("filename"),
                               status=status, note=note)

    def item_finished(self, item: dict) -> bool:
        rows = self.ledger.rows("gopro_id = ?", item["id"])
        if not rows:
            return False
        if item["type"] in SEQUENCE_TYPES:
            frames = [r for r in rows if "#" in r["unit_id"]]
            return len(frames) >= (item.get("item_count") or 1) and all(r["status"] in TERMINAL for r in frames)
        if any(r["unit_id"] == item["id"] and r["status"] in TERMINAL for r in rows):
            return True
        parts = [r for r in rows if "#" in r["unit_id"]]
        return bool(parts) and len(parts) >= (item.get("item_count") or 1) and all(r["status"] in TERMINAL for r in parts)

    def process_item(self, item: dict) -> None:
        uid = item["id"]
        self.ledger.heartbeat()
        if is_phone_media(item):
            self._hold(item, "EXCLUDED", "phone media (PXL_/Pixel)")
            return
        decision = self.decisions.get(uid)
        if uid in self.review and decision != "upload":
            if decision == "skip":
                self._hold(item, "EXCLUDED", "skipped in manual review")
            else:
                self._hold(item, "REVIEW", f"manual review: {self.review[uid]} (see {REVIEW_CSV.name})")
            return
        row = self.ledger.get(uid)
        if row and row["status"] in ("REVIEW", "EXCLUDED") and (row["note"] or "").startswith(("manual review", "skipped in manual review")):
            self.ledger.upsert(uid, status="NEW", note="approved in manual review")
        if self.item_finished(item):
            return
        try:
            sources = self.gopro.sources(item)
        except (GoProAuthError, config.NetworkDown):
            raise
        except GoProError as e:
            self._set_failed(uid, item, f"no original: {e} (GoPro status {item.get('ready_to_view')})",
                             filename=item.get("filename"))
            self.log(uid, f"FAILED {e}")
            return
        for src in sources:
            uid_s = uid if src.part is None else f"{uid}#{src.part}"
            try:
                if src.label == "zip":
                    self.process_sequence(item, src)
                else:
                    self.process_unit(item, src)
            except (GoProAuthError, config.NetworkDown, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:  # noqa: BLE001 - one bad unit must not stop the run
                self._set_failed(uid_s, item, f"{type(e).__name__}: {e}")
                self.log(uid_s, f"ERROR {type(e).__name__}: {str(e)[:200]}")

    # ---------- single files ----------
    def process_unit(self, item: dict, src: Source) -> None:
        uid = item["id"] if src.part is None else f"{item['id']}#{src.part}"
        base = src.filename if item["type"] == "MultiClipEdit" or not item.get("filename") else item["filename"]
        name = chapter_filename(base, src.part)
        row = self.ledger.get(uid)
        if row and row["media_item_id"]:
            if row["status"] != "DONE":
                self.ledger.upsert(uid, status="DONE")
            return
        if row and row["status"] in TERMINAL:
            return
        work = config.WORK_DIR / uid.replace("#", "_")
        work.mkdir(parents=True, exist_ok=True)
        orig = work / f"orig{Path(name).suffix.lower()}"
        is_video = item["type"] in VIDEO_TYPES
        status = row["status"] if row else "NEW"
        prepared = Path(row["upload_file"]) if row and row["upload_file"] else None
        if status in ("PREPARED", "UPLOADED") and not (prepared and prepared.exists()):
            if status == "UPLOADED" or (row and row["create_errors"]):
                self.ledger.upsert(uid, status="REVIEW", note="prepared file lost after an upload started; check Google Photos")
                self.log(uid, "REVIEW: prepared file lost after an upload started")
                return
            status = "NEW"
        if status in ("NEW", "FAILED", "DOWNLOADED", "DEFERRED"):
            if status == "DEFERRED" and orig.exists() and row["orig_sha256"]:
                dup = self.ledger.live_duplicate_of(uid, row["orig_sha256"])   # a twin may have gone up meanwhile
                if dup:
                    self.ledger.upsert(uid, status="DUPLICATE", note=f"same original bytes as {dup}")
                    shutil.rmtree(work, ignore_errors=True)
                    return
            else:
                fresh = self.gopro.fresh_source(item, src.part, src.label)   # signed URLs expire
                self._download(item, fresh, uid, name, orig)
                if self.ledger.get(uid)["status"] == "DUPLICATE":
                    shutil.rmtree(work, ignore_errors=True)
                    return
            if not self._prepare(item, uid, name, orig, work, is_video):
                return
        if self.photos is None:
            self.log(uid, f"prepared {self.ledger.get(uid)['upload_file']} (upload disabled)")
            return
        row = self.ledger.get(uid)
        self._upload_and_create(uid, name, Path(row["upload_file"]), "video/mp4" if is_video else "image/jpeg", None)
        if self.ledger.get(uid)["status"] == "DONE":
            shutil.rmtree(work, ignore_errors=True)

    def _download(self, item, src, uid, name, orig) -> None:
        self.log(uid, f"downloading {name}")
        expected = item.get("file_size") if src.part is None and src.label == "source" else None
        n, sha = self.gopro.download(src, orig, expected)
        note = None
        if expected is not None and n != expected:
            self._verify_media(orig, item)           # raises if the file is not a complete, decodable video
            note = f"{n - expected:+d} bytes vs GoPro listing; verified by full decode and duration"
            self.log(uid, note)
        self.ledger.upsert(uid, gopro_id=item["id"], part=str(src.part) if src.part else None, kind=item["type"],
                           filename=name, orig_bytes=n, orig_sha256=sha, status="DOWNLOADED", note=note)
        dup = self.ledger.live_duplicate_of(uid, sha)
        if dup:
            self.ledger.upsert(uid, status="DUPLICATE", note=f"same original bytes as {dup}")
            self.log(uid, f"DUPLICATE of {dup} - skipped")

    @staticmethod
    def _verify_media(path: Path, item: dict) -> None:
        import subprocess
        if item["type"] not in VIDEO_TYPES:
            raise GoProError(f"size differs from GoPro listing for {path.name}")
        dur = encode.duration(path)
        listed = int(item.get("source_duration") or 0) / 1000
        if listed and abs(dur - listed) > 1.0:
            path.unlink(missing_ok=True)
            raise GoProError(f"duration {dur:.1f}s != listed {listed:.1f}s for {path.name}")
        err = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
                             capture_output=True, text=True).stderr.strip()
        if err:
            path.unlink(missing_ok=True)
            raise GoProError(f"decode errors in {path.name}: {err[:150]}")

    def _prepare(self, item, uid, name, orig, work, is_video) -> bool:
        """Returns False when the unit was deferred."""
        hint = self.hint(_cloud_utc(item))
        enc_json = None
        if is_video:
            try:
                cap = metadata.capture_for_video(item, orig, hint)
            except metadata.ClockReset as e:
                return self._hold_clock_reset(uid, str(e))
            upload = orig
            if self.reencode:
                self.log(uid, "probing HEVC re-encode")
                enc = encode.adaptive_encode(orig, work / "enc")
                self.log(uid, f"re-encode: {enc.use_hevc} ({enc.reason})")
                if enc.use_hevc:
                    upload = work / f"upload{Path(name).suffix.lower()}"
                    enc.path.rename(upload)
                enc_json = {"hevc": enc.use_hevc, "crf": enc.crf, "vmaf": enc.vmaf, "ratio": enc.ratio,
                            "reason": enc.reason[:200], **(enc.stats or {})}
            metadata.write_video(upload, cap)
        else:
            reset = None
            try:
                cap, needs_write = metadata.capture_for_photo(item, orig, hint)
            except metadata.ClockReset as e:
                cap, needs_write, reset = None, True, str(e)
            if cap is None:
                cap = self.source_video_capture(name, item)
                if cap is None and Path(name).stem.upper() in self.video_stems:
                    self.ledger.upsert(uid, status="DEFERRED", note="waiting for the source video's capture")
                    self.log(uid, "DEFERRED: frame grab waits for its source video")
                    return False
                if cap is None and reset:
                    return self._hold_clock_reset(uid, reset)
                if cap is None:
                    try:
                        cap = metadata.capture_from_cloud(item, None, hint, item.get("camera_model"))
                    except metadata.ClockReset as e:
                        return self._hold_clock_reset(uid, str(e))
                if cap.model is None:
                    cap.model = item.get("camera_model")
            if needs_write:
                metadata.write_photo(orig, cap)
            upload = orig
        self.ledger.upsert(uid, capture_json=cap.to_json(), encode_json=json.dumps(enc_json) if enc_json else None,
                           upload_file=str(upload), upload_bytes=upload.stat().st_size, upload_sha256=_sha256(upload),
                           upload_token=None, token_at=None, status="PREPARED")
        if upload != orig:
            orig.unlink(missing_ok=True)
        self.log(uid, f"prepared: {cap.local} {cap.offset_str} {cap.tz or ''} via {cap.source}")
        return True

    def _hold_clock_reset(self, uid: str, why: str) -> bool:
        self.ledger.upsert(uid, status="REVIEW", note=f"camera clock reset, no GPS/source video ({why}); run fix-dates")
        self.log(uid, "REVIEW: camera clock reset with nothing to correct it from")
        return False

    def source_video_capture(self, photo_name: str, item: dict | None = None) -> Capture | None:
        """Frame grabs (GH010757.jpg) take time/place from their video (GH010757.MP4).
        GoPro file names repeat, so among same-name videos take the one whose cloud timestamp
        is closest to the grab's own (within two days: both carry the same camera clock, even
        a reset one), and never a clock-reset capture."""
        stem = Path(photo_name).stem.upper()
        cands = []
        for r in self.ledger.rows("kind IN ('Video','TimeLapseVideo') AND capture_json IS NOT NULL"):
            if Path(r["filename"] or "").stem.upper() != stem:
                continue
            c = Capture.from_json(r["capture_json"])
            if not metadata.is_clock_reset(c.utc.replace(tzinfo=None)):
                cands.append((_cloud_utc(self.items.get(r["gopro_id"], {})) or c.utc, c))
        if not cands:
            return None
        guess = _cloud_utc(item) if item else None
        if guess is None:
            return cands[0][1] if len(cands) == 1 else None
        when, best = min(cands, key=lambda wc: abs(wc[0] - guess))
        return best if abs(when - guess) <= timedelta(days=2) else None

    # ---------- upload ----------
    def _album(self, title: str | None) -> str | None:
        if not title:
            return None
        album_id = self.ledger.album_id(title)
        if album_id is None:
            album_id = self.photos.create_album(title)      # reuses an app album with this title
            self.ledger.save_album(title, album_id)
        return album_id

    def _upload_and_create(self, uid, name, path: Path, mime: str, album_title: str | None) -> None:
        row = self.ledger.get(uid)
        token, token_at = row["upload_token"], row["token_at"]
        fresh = token and token_at and datetime.now(timezone.utc) - datetime.fromisoformat(token_at) < TOKEN_MAX_AGE
        if not fresh:
            if _sha256(path) != row["upload_sha256"]:
                self.ledger.upsert(uid, status="REVIEW", note="prepared file changed after hashing - not uploaded")
                self.log(uid, "REVIEW: prepared file changed after hashing")
                return
            self.log(uid, f"uploading {path.stat().st_size / 1e6:.1f} MB")
            token = self.photos.upload(path, mime)
            self.ledger.upsert(uid, upload_token=token, token_at=_now(), status="UPLOADED")
        album_id = self._album(album_title)
        res = self.photos.batch_create([(token, name)], album_id)
        res = res[0] if res else {}
        if "mediaItem" not in res:
            errors = (row["create_errors"] or 0) + 1
            status = "REVIEW" if errors >= MAX_CREATE_ERRORS else "PREPARED"
            # keep the same prepared file: a retry re-uploads identical bytes, which Google dedups
            self.ledger.upsert(uid, status=status, upload_token=None, token_at=None, create_errors=errors,
                               note=f"batchCreate failed ({errors}x): {res.get('status')}"[:300])
            self.log(uid, f"batchCreate failed ({errors}x): {res.get('status')}")
            return
        self.ledger.upsert(uid, media_item_id=res["mediaItem"]["id"], album_title=album_title, status="DONE",
                           note=(res.get("status") or {}).get("message"))
        self.log(uid, f"DONE {name}")

    # ---------- time-lapse / burst ----------
    def process_sequence(self, item: dict, src: Source) -> None:
        """Zip of original frames -> one album per sequence."""
        seq_id = item["id"]
        work = config.WORK_DIR / seq_id
        work.mkdir(parents=True, exist_ok=True)
        zpath = work / "frames.zip"
        if not zpath.exists():
            self.log(seq_id, f"downloading {src.filename}")
            self.gopro.download(self.gopro.fresh_source(item, src.part, src.label), zpath)
        title = f"{'Time-lapse' if item['type'] == 'TimeLapse' else 'Burst'} {item['captured_at'][:10]} {Path(item['filename']).stem}"
        try:
            z = zipfile.ZipFile(zpath)
        except zipfile.BadZipFile as e:
            zpath.unlink()
            raise GoProError(f"corrupt zip for {seq_id}; will download again") from e
        with z:
            frames = sorted((n for n in z.namelist() if n.lower().endswith(".jpg")),
                            key=lambda n: (int(Path(n).stem) if Path(n).stem.isdigit() else 10 ** 9, n))
            total = sum(z.getinfo(n).file_size for n in frames)
            if len(frames) != (item.get("item_count") or 0) or total != item.get("file_size"):
                zpath.unlink()
                raise GoProError(f"zip for {seq_id} has {len(frames)} frames/{total} bytes, GoPro lists "
                                 f"{item.get('item_count')}/{item.get('file_size')}; will download again")
            # the zip names frames 1.jpg, 2.jpg...; restore the camera's names (G0020232.JPG, G0020233.JPG...)
            m = re.match(r"^(G\d{3})(\d{4})\.jpe?g$", item.get("filename") or "", re.I)
            names = [f"{m[1]}{int(m[2]) + i:04d}.JPG" if m else f"{Path(item['filename']).stem}_{i + 1:03d}.JPG"
                     for i in range(len(frames))]
            seq_hint = None
            for fname, new_name in zip(frames, names, strict=True):
                uid = f"{seq_id}#{new_name}"
                row = self.ledger.get(uid)
                if row and (row["status"] in TERMINAL or row["media_item_id"]):
                    continue
                path = work / new_name
                needs_prepare = (not row or row["status"] in ("NEW", "FAILED", "DOWNLOADED")
                                 or not row["upload_sha256"] or not path.exists())
                if needs_prepare and row and (row["upload_token"] or row["create_errors"]):
                    self.ledger.upsert(uid, status="REVIEW", note="frame file lost after an upload started")
                    continue
                if needs_prepare:
                    path.unlink(missing_ok=True)          # always start from the pristine zip member
                    with z.open(fname) as s, open(path, "wb") as d:
                        shutil.copyfileobj(s, d)
                    sha = _sha256(path)
                    self.ledger.upsert(uid, gopro_id=seq_id, part=new_name, kind=item["type"], filename=new_name,
                                       orig_bytes=path.stat().st_size, orig_sha256=sha, status="DOWNLOADED", note=None)
                    dup = self.ledger.live_duplicate_of(uid, sha)
                    if dup:
                        self.ledger.upsert(uid, status="DUPLICATE", note=f"same bytes as {dup}")
                        path.unlink(missing_ok=True)
                        continue
                    if seq_hint is None:
                        seq_hint = self._sequence_hint(z, frames, item)
                    try:
                        cap, needs_write = metadata.capture_for_photo(item, path, seq_hint)
                        if cap is None:
                            cap = metadata.capture_from_cloud(item, None, seq_hint, item.get("camera_model"))
                            needs_write = True
                    except metadata.ClockReset as e:
                        self._hold_clock_reset(uid, str(e))
                        continue
                    if needs_write:
                        metadata.write_photo(path, cap)
                    self.ledger.upsert(uid, capture_json=cap.to_json(), upload_file=str(path),
                                       upload_bytes=path.stat().st_size, upload_sha256=_sha256(path),
                                       upload_token=None, token_at=None, status="PREPARED")
                if self.photos is not None:
                    self._upload_and_create(uid, new_name, path, "image/jpeg", title)
                    if self.ledger.get(uid)["status"] == "DONE":
                        path.unlink(missing_ok=True)
        if self.photos is not None and all((self.ledger.get(f"{seq_id}#{n}") or {"status": ""})["status"]
                                           in TERMINAL for n in names):
            shutil.rmtree(work, ignore_errors=True)

    def _sequence_hint(self, z: zipfile.ZipFile, frames: list[str], item: dict) -> str | None:
        """Zone from the sequence's own GPS frames, else the general hint."""
        tmp = config.WORK_DIR / item["id"] / "_probe.jpg"
        try:
            for fname in frames:
                with z.open(fname) as s, open(tmp, "wb") as d:
                    shutil.copyfileobj(s, d)
                try:
                    cap, _ = metadata.capture_for_photo(item, tmp, None)
                except metadata.ClockReset:
                    continue
                if cap and cap.source in ("exif+gps", "gps-time (camera clock reset)") and cap.tz:
                    return cap.tz
        finally:
            tmp.unlink(missing_ok=True)
        return self.hint(_cloud_utc(item))
