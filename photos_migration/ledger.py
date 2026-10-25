"""SQLite ledger: the single source of truth for what was uploaded.

A unit is one uploadable original (a video chapter, a photo, a time-lapse frame).
Dedup key: SHA-256 of the ORIGINAL bytes, so re-encoding or metadata rewriting
(both change the uploaded bytes) can never cause a second upload of the same file.

Status flow: NEW -> DOWNLOADED -> PREPARED -> UPLOADED (token saved) -> DONE (media item id saved)
Side states: DUPLICATE, EXCLUDED, REVIEW (held for the user), DEFERRED (waits for another unit),
FAILED (only ever before an upload started).
A unit that has had an upload token is never re-prepared: retries re-send the same hashed file.

The ledger fails closed: it is never created implicitly (a missing or unsynced data folder
must not look like "nothing uploaded yet"), and a lease stops two machines sharing it via
Google Drive from running at the same time.
"""
from __future__ import annotations

import os
import socket
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
    unit_id        TEXT PRIMARY KEY,
    gopro_id       TEXT NOT NULL,
    part           TEXT,
    kind           TEXT,
    filename       TEXT,
    orig_bytes     INTEGER,
    orig_sha256    TEXT,
    capture_json   TEXT,
    encode_json    TEXT,
    upload_file    TEXT,
    upload_bytes   INTEGER,
    upload_sha256  TEXT,
    upload_token   TEXT,
    token_at       TEXT,
    create_errors  INTEGER DEFAULT 0,
    media_item_id  TEXT,
    album_title    TEXT,
    status         TEXT NOT NULL,
    note           TEXT,
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_units_sha ON units(orig_sha256);
CREATE INDEX IF NOT EXISTS idx_units_status ON units(status);
CREATE TABLE IF NOT EXISTS albums (title TEXT PRIMARY KEY, album_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

# a unit that may already be (or become) a Google item: a new unit with the same original bytes is
# its duplicate. REVIEW holds count too (clock reset -> fix-dates uploads them; failed creates keep their
# file), except manual-review holds: the user may still decide to skip those.
LIVE = ("PREPARED", "UPLOADED", "DONE", "DEFERRED")
TERMINAL = ("DONE", "DUPLICATE", "EXCLUDED", "REVIEW")
LEASE_TTL = timedelta(minutes=30)
HEARTBEAT_EVERY_S = 300.0     # from a background thread: one item (4K encode + VMAF + upload) can exceed the TTL


class LedgerError(SystemExit):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Ledger:
    def __init__(self, path: Path | None = None, readonly: bool = False, create: bool = False):
        path = Path(path or config.LEDGER)
        self.path = path
        if not path.exists() and not create:
            raise LedgerError(f"ledger not found: {config.display(path)}\n"
                              "If Google Drive is still syncing, wait and retry. Create a new, empty ledger only with "
                              "'python -m photos_migration init' (never on a machine that should see the existing one).")
        mode = "ro" if readonly else ("rwc" if create else "rw")
        self.db = sqlite3.connect(f"file:{path}?mode={mode}", uri=True, isolation_level=None)  # autocommit
        self.db.row_factory = sqlite3.Row
        if not readonly:
            self.db.executescript(SCHEMA)
            cols = {r["name"] for r in self.db.execute("PRAGMA table_info(units)")}
            if "create_errors" not in cols:
                self.db.execute("ALTER TABLE units ADD COLUMN create_errors INTEGER DEFAULT 0")
            path.chmod(0o600)

    # ---------- units ----------
    def get(self, unit_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM units WHERE unit_id = ?", (unit_id,)).fetchone()

    def upsert(self, unit_id: str, **fields) -> None:
        fields["updated_at"] = _now().isoformat(timespec="seconds")
        if self.get(unit_id) is None:
            fields.setdefault("status", "NEW")
            cols = ["unit_id", *fields]
            self.db.execute(f"INSERT INTO units ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                            (unit_id, *fields.values()))
        else:
            sets = ", ".join(f"{k} = ?" for k in fields)
            self.db.execute(f"UPDATE units SET {sets} WHERE unit_id = ?", (*fields.values(), unit_id))

    def live_duplicate_of(self, unit_id: str, sha: str) -> str | None:
        row = self.db.execute(
            f"SELECT unit_id FROM units WHERE orig_sha256 = ? AND unit_id != ? AND (status IN ({','.join('?' * len(LIVE))}) "
            "OR upload_token IS NOT NULL OR media_item_id IS NOT NULL OR (status = 'REVIEW' AND "
            "COALESCE(note, '') NOT LIKE 'manual review%' AND COALESCE(note, '') NOT LIKE 'skipped in manual review%')) "
            "ORDER BY updated_at LIMIT 1",
            (sha, unit_id, *LIVE)).fetchone()
        return row["unit_id"] if row else None

    def counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self.db.execute("SELECT status, COUNT(*) n FROM units GROUP BY status")}

    def last_update(self) -> str | None:
        return self.db.execute("SELECT MAX(updated_at) FROM units").fetchone()[0]

    def rows(self, where: str = "1=1", *args) -> list[sqlite3.Row]:
        return self.db.execute(f"SELECT * FROM units WHERE {where} ORDER BY unit_id", args).fetchall()

    # ---------- albums ----------
    def album_id(self, title: str) -> str | None:
        row = self.db.execute("SELECT album_id FROM albums WHERE title = ?", (title,)).fetchone()
        return row["album_id"] if row else None

    def save_album(self, title: str, album_id: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO albums (title, album_id) VALUES (?, ?)", (title, album_id))

    # ---------- meta / lease ----------
    def meta(self, key: str) -> str | None:
        try:
            row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        except sqlite3.OperationalError:      # read-only open of a ledger created before the meta table
            return None
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def acquire_lease(self, takeover: bool = False) -> None:
        """Refuse to run while another machine's lease is fresh (the ledger is shared via Drive)."""
        me = f"{socket.gethostname()}:{os.getpid()}"
        holder, beat = self.meta("lease_holder"), self.meta("lease_heartbeat")
        if holder and beat and not takeover:
            other_host = holder.split(":")[0] != socket.gethostname()
            fresh = _now() - datetime.fromisoformat(beat) < LEASE_TTL
            if other_host and fresh:
                raise LedgerError(f"ledger in use by another machine (last heartbeat {beat}); "
                                  "wait for it to finish, or pass --takeover if that machine is gone")
        self.set_meta("lease_holder", me)
        self.heartbeat()
        self._stop_beat = threading.Event()
        threading.Thread(target=self._beat, args=(self._stop_beat,), daemon=True, name="ledger-heartbeat").start()

    def _beat(self, stop: threading.Event) -> None:
        db = sqlite3.connect(f"file:{self.path}?mode=rw", uri=True, isolation_level=None)  # own connection
        try:
            while not stop.wait(HEARTBEAT_EVERY_S):
                try:
                    db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('lease_heartbeat', ?)",
                               (_now().isoformat(timespec="seconds"),))
                except sqlite3.OperationalError:      # busy: try again next beat
                    pass
        finally:
            db.close()

    def heartbeat(self) -> None:
        self.set_meta("lease_heartbeat", _now().isoformat(timespec="seconds"))

    def release_lease(self) -> None:
        if getattr(self, "_stop_beat", None):
            self._stop_beat.set()
        self.db.execute("DELETE FROM meta WHERE key IN ('lease_holder', 'lease_heartbeat')")
