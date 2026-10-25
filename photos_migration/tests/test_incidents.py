"""Regression tests: one per incident this project already hit ("incidents are now an eval")."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from photos_migration import config, metadata
from photos_migration.tests.conftest import capture_json


# 1. GoPro /media/search pagination repeated one item and silently dropped another.
def test_gopro_listing_unions_page_sizes_until_count_matches():
    from photos_migration.gopro import GoProClient
    allitems = [{"id": f"m{i}"} for i in range(5)]

    def fake_get(self, path, page, per_page):
        lossy = allitems[:2] + [allitems[1]] + allitems[3:]          # m2 dropped, m1 repeated
        chunk = (lossy if per_page == 100 else allitems)[(page - 1) * per_page: page * per_page]
        return {"_pages": {"total_pages": -(-5 // per_page), "total_items": 5}, "_embedded": {"media": [dict(x) for x in chunk]}}

    client = GoProClient.__new__(GoProClient)
    GoProClient._get = fake_get
    try:
        got = {x["id"] for x in client.list_all(page_sizes=(100, 2))}
    finally:
        del GoProClient._get
    assert got == {x["id"] for x in allitems}


# 2. An expired GoPro cookie on a re-run turned DONE rows into FAILED (and they were re-uploaded).
def _bare_migrator(ledger, gopro):
    from photos_migration.pipeline import Migrator
    m = Migrator.__new__(Migrator)
    m.ledger, m.gopro, m.photos, m.reencode = ledger, gopro, None, False
    m.review, m.decisions, m.video_stems, m.items = {}, {}, set(), {}
    m.hint = lambda utc: None
    return m


def test_cookie_expiry_never_downgrades_done(ledger):
    from photos_migration.gopro import GoProAuthError, GoProError
    ledger.upsert("A", gopro_id="A", kind="Photo", filename="GOPR0001.JPG", status="DONE", media_item_id="MI")

    class Dead:
        def sources(self, item):
            raise GoProAuthError("HTTP 401")

    class Broken:
        def sources(self, item):
            raise GoProError("HTTP 500")

    item = {"id": "A", "type": "Photo", "filename": "GOPR0001.JPG"}
    with pytest.raises(GoProAuthError):
        ledger.upsert("A", status="DONE")
        _bare_migrator(ledger, Dead()).process_item({**item, "id": "B"})   # unknown item: auth error stops the run
    _bare_migrator(ledger, Broken()).process_item(item)                    # finished item: never touched
    assert ledger.get("A")["status"] == "DONE" and ledger.get("A")["media_item_id"] == "MI"


# 3. GoPro Cloud held a phone copy (GH010001_1600000000000.MP4) next to the original GH010001.MP4.
def test_phone_copy_stored_in_gopro_goes_to_review():
    from photos_migration.pipeline import review_groups
    items = [{"id": "orig", "type": "Video", "filename": "GH010001.MP4", "source_gumi": "a"},
             {"id": "copy", "type": "Video", "filename": "GH010001_1600000000000.MP4", "source_gumi": "b"}]
    assert "copy" in review_groups(items)


# 4. Camera clock reset (2016-01-01) uploaded items with wrong dates.
def test_reset_photo_with_gps_uses_gps_time(monkeypatch):
    monkeypatch.setattr(metadata, "exiftool", lambda path, *a: {
        "Main:DateTimeOriginal": "2016:01:01 03:25:56", "Main:GPSLatitude": 48.85, "Main:GPSLongitude": 2.35,
        "Main:GPSDateTime": "2020:06:01 11:15:26Z"})
    cap, needs_write = metadata.capture_for_photo({}, Path("x.jpg"))
    assert cap.utc == datetime(2020, 6, 1, 11, 15, 26, tzinfo=timezone.utc) and needs_write


def test_reset_photo_without_gps_is_never_dated_from_the_cloud(monkeypatch):
    monkeypatch.setattr(metadata, "exiftool", lambda path, *a: {"Main:DateTimeOriginal": "2016:01:01 03:21:57"})
    with pytest.raises(metadata.ClockReset):
        metadata.capture_for_photo({"captured_at": "2020-06-01T10:00:00Z"}, Path("x.jpg"))


def test_reset_video_without_gps_raises(monkeypatch):
    monkeypatch.setattr(metadata, "exiftool", lambda path, *a: {"Main:CreateDate": "2016:01:12 13:39:18"})
    monkeypatch.setattr(metadata, "video_gps_start", lambda path: None)
    with pytest.raises(metadata.ClockReset):
        metadata.capture_for_video({"captured_at": "2016-01-12T13:39:18Z", "content_source": "gp-cah-controller/"},
                                   Path("x.mp4"))


# 5. Colour-range signalling: GoPro flags full range in the bitstream but 'Limited' in the colr atom.
@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs ffmpeg")
def test_hevc_mirrors_source_colour_range(tmp_path, monkeypatch):
    import subprocess
    from photos_migration import encode
    raw, src = tmp_path / "raw.mp4", tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=2",
                    "-pix_fmt", "yuvj420p", "-c:v", "libx264", str(raw)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(raw), "-c", "copy", "-color_range", "tv", "-movflags", "+write_colr", str(src)], check=True)
    assert encode.color_range(src) == (True, False)
    monkeypatch.setattr(encode, "_search", lambda refs: (20, {20: (99.0, 0.1)}))
    monkeypatch.setattr(config, "MIN_SAVING", -100.0)            # tiny synthetic clip: ignore the size gate
    monkeypatch.setattr(encode, "vmaf", lambda d, r, subsample=1: encode.VmafStats(99, 99, 99, 99, 0))
    res = encode.adaptive_encode(src, tmp_path / "enc")
    assert res.use_hevc, res.reason
    assert encode.color_range(res.path) == (True, False)


# 6. Cleanup: the "Undo" toast takes a few seconds; and never click when the viewer moved.
class FakeLoc:
    def __init__(self, n, on_click=None, text=""):
        self.n, self.on_click, self.text = n, on_click, text

    def count(self):
        return self.n

    @property
    def first(self):
        return self

    def click(self):
        if self.on_click:
            self.on_click()

    def inner_text(self):
        return self.text

    def get_by_role(self, role, name=None):
        return FakeLoc(self.n, self.on_click, "Move to trash")


class FakePage:
    def __init__(self, url, toast_after=3, moved=False):
        self.url, self.toast_after, self.moved, self.clicked, self.t = url, toast_after, moved, 0, 0
        self.keyboard = self

    def goto(self, url, **kw):
        self.url = url if not self.moved else "https://photos.google.com/photo/OTHER"

    def press(self, key):
        pass

    def inner_text(self, sel):
        return "Details\nGOPR0001_ALTA123.JPG\nUploaded from Android device\n"

    def get_by_role(self, role, name=None):
        name = getattr(name, "pattern", name) or ""
        if role == "dialog":
            return FakeLoc(1 if self.clicked == 1 else 0, self._confirm)
        if name == "Undo":
            self.t += 1
            return FakeLoc(1 if self.t >= self.toast_after else 0)
        if "Restore" in name:
            return FakeLoc(0)
        return FakeLoc(1, self._click)

    def _click(self):
        self.clicked += 1

    def _confirm(self):
        self.clicked += 1


def test_cleanup_waits_for_slow_undo_toast(ledger, monkeypatch):
    from photos_migration import cleanup
    monkeypatch.setattr(cleanup.time, "sleep", lambda s: None)
    ledger.db.execute(cleanup.SCHEMA)
    url = "https://photos.google.com/photo/PID1"
    ledger.db.execute("INSERT INTO cleanup (url, kind, gopro_id, expect, status) VALUES (?, 'phone_copy', 'g', 'GOPR0001', 'PENDING')", (url,))
    page = FakePage(url, toast_after=4)
    assert cleanup._one(page, ledger, url, "phone_copy", "GOPR0001", set(), False) == 1
    assert ledger.db.execute("SELECT status FROM cleanup").fetchone()[0] == "TRASHED"


def test_cleanup_never_clicks_when_viewer_shows_another_item(ledger, monkeypatch):
    from photos_migration import cleanup
    monkeypatch.setattr(cleanup.time, "sleep", lambda s: None)
    ledger.db.execute(cleanup.SCHEMA)
    url = "https://photos.google.com/photo/PID1"
    ledger.db.execute("INSERT INTO cleanup (url, kind, gopro_id, expect, status) VALUES (?, 'phone_copy', 'g', 'GOPR0001', 'PENDING')", (url,))
    page = FakePage(url, moved=True)
    assert cleanup._one(page, ledger, url, "phone_copy", "GOPR0001", set(), False) == 0
    assert page.clicked == 0


# 7. A missing/unsynced data folder must never become an empty ledger.
def test_ledger_is_never_created_implicitly(isolated_paths):
    from photos_migration.ledger import Ledger, LedgerError
    with pytest.raises(LedgerError):
        Ledger()
    assert not config.LEDGER.exists()


def test_run_refuses_empty_ledger_with_inventory(ledger):
    from photos_migration.pipeline import Migrator
    with pytest.raises(SystemExit, match="no uploaded items"):
        Migrator([{"id": "x", "type": "Photo", "filename": "GOPR0001.JPG"}], upload=False)


def test_lease_of_another_machine_is_respected(ledger):
    from photos_migration.ledger import LedgerError
    ledger.set_meta("lease_holder", "other-mac:123")
    ledger.set_meta("lease_heartbeat", datetime.now(timezone.utc).isoformat())
    with pytest.raises(LedgerError):
        ledger.acquire_lease()
    ledger.acquire_lease(takeover=True)


# 8. Cleanup must not trash the only copy of a partly migrated item.
def test_item_complete_needs_every_chapter_and_frame(ledger):
    from photos_migration.completeness import item_fully_in_google
    ledger.upsert("V#1", gopro_id="V", status="DONE", media_item_id="a", orig_sha256="1")
    ledger.upsert("V#2", gopro_id="V", status="REVIEW", orig_sha256="2")
    rows = ledger.rows("gopro_id = 'V'")
    by_unit = {r["unit_id"]: r for r in rows}
    ok, _ = item_fully_in_google({"id": "V", "type": "Video", "item_count": 2}, rows, by_unit, {"a"})
    assert not ok
    ledger.upsert("V#2", status="DONE", media_item_id="b")
    rows = ledger.rows("gopro_id = 'V'")
    by_unit = {r["unit_id"]: r for r in rows}
    assert not item_fully_in_google({"id": "V", "type": "Video", "item_count": 2}, rows, by_unit, {"a"})[0]  # b unreadable
    assert item_fully_in_google({"id": "V", "type": "Video", "item_count": 2}, rows, by_unit, {"a", "b"})[0]


def test_duplicate_targets_include_holds_that_will_upload_but_not_manual_review(ledger):
    ledger.upsert("held", gopro_id="h", status="REVIEW", orig_sha256="S", note="manual review: same name")
    assert ledger.live_duplicate_of("new", "S") is None          # the user may still skip it
    ledger.upsert("reset", gopro_id="r", status="REVIEW", orig_sha256="R",
                  note="camera clock reset, no GPS/source video (x); run fix-dates")
    assert ledger.live_duplicate_of("new", "R") == "reset"       # fix-dates will upload it
    ledger.upsert("up", gopro_id="u", status="UPLOADED", orig_sha256="T", upload_token="tok")
    assert ledger.live_duplicate_of("new", "T") == "up"


# 9. An expired Google login was treated as a network outage (endless pause/resume).
def test_google_auth_errors_stop_the_run():
    import requests
    from google.auth.exceptions import RefreshError
    from photos_migration.gphotos import PhotosAuthError, _classify
    assert isinstance(_classify(RefreshError("invalid_grant"), "x"), PhotosAuthError)
    assert isinstance(_classify(requests.ConnectionError(), "x"), config.NetworkDown)


# 10. Quality gate: a long degraded stretch must fail even when the average is high.
def test_vmaf_window_catches_degraded_stretch():
    from photos_migration.encode import window_stats
    scores = [98.0] * 900 + [65.0] * 60 + [98.0] * 900           # 2 s at 65 inside 62 s of 98
    worst, at = window_stats(scores, fps=30, subsample=1)
    assert worst < 70 and 29 <= at <= 31


# 11. The audit's tiny-video exemption used the size after HEVC.
def test_tiny_video_exemption_uses_duration_and_original():
    from photos_migration.audit import _tiny_unreencoded_video
    row = {"kind": "Video", "gopro_id": "g", "encode_json": '{"hevc": true}'}
    assert not _tiny_unreencoded_video(row, {"g": {"source_duration": "3900"}})
    assert not _tiny_unreencoded_video({**row, "encode_json": None}, {"g": {"source_duration": "3900"}})
    assert _tiny_unreencoded_video({**row, "encode_json": None}, {"g": {"source_duration": "200"}})


# 12. GPS-less items kept the camera clock error measured by their GPS neighbours.
def _drift_ledger(ledger):
    err = -59 * 60
    for n, utc in ((100, "2020-06-01T15:50:00"), (109, "2020-06-01T16:50:00")):
        ledger.upsert(f"gps{n}", gopro_id=f"g{n}", filename=f"GH01{n:04d}.MP4", status="DONE",
                      capture_json=capture_json(utc, source="gps", camera_clock_error_s=err, model="HERO7 Black",
                                                clock_ref="local"))
    def unit(uid, n, utc, source, **kw):
        ledger.upsert(uid, gopro_id=uid, filename=f"GH01{n:04d}.MP4", status="DONE",
                      capture_json=capture_json(utc, source=source, model="HERO7 Black", **kw))
    unit("t095", 95, "2020-06-01T15:00:00", "cloud-local+default-tz")      # only a later anchor
    unit("t102", 102, "2020-06-01T16:05:00", "cloud-utc+camera-offset")    # anchors on both sides
    unit("t112", 112, "2020-06-01T17:30:00", "cloud-local+hint-tz")        # only an earlier anchor
    unit("fixed", 103, "2020-06-01T17:10:00", "clock drift corrected by GPS anchors")
    unit("offshore", 104, "2020-06-01T16:20:00", "gps+hint-tz", lat=43.0, lon=7.5)
    return timedelta(seconds=err)


def test_drift_correction_rules(ledger):
    from photos_migration.fix_drift import plan
    err = _drift_ledger(ledger)
    auto, manual = plan(ledger, {})
    assert sorted(e["row"]["unit_id"] for e in auto) == ["t095", "t102"]
    assert [e["row"]["unit_id"] for e in manual] == ["t112"]      # a clock sync may follow an earlier anchor
    assert all(e["err"] == err for e in auto)                      # never GPS-tagged or already corrected units


def test_drift_ignores_time_lapse_frames(ledger):
    from photos_migration.fix_drift import plan
    _drift_ledger(ledger)
    auto, _ = plan(ledger, {"t102": {"type": "TimeLapse"}})
    assert "t102" not in [e["row"]["unit_id"] for e in auto]


def test_fix_dates_skips_sequence_frames_without_downloading(ledger, monkeypatch):
    from photos_migration import fix_dates
    ledger.upsert("S#G0010001.JPG", gopro_id="S", filename="G0010001.JPG", status="REVIEW",
                  note="camera clock reset, no GPS/source video (x); run fix-dates")
    monkeypatch.setattr(fix_dates, "fetch_verified_original", lambda *a: pytest.fail("frames cannot be re-downloaded"))
    m = type("M", (), {"ledger": ledger})()
    assert fix_dates._main(m, {"S": {"type": "TimeLapse"}}, dry_run=True) == 0


# 14. A frame grab of a reset-clock video stayed DEFERRED: its cloud time is the reset camera clock.
def test_frame_grab_of_reset_clock_video_finds_its_video(ledger):
    m = _bare_migrator(ledger, None)
    m.items = {"V": {"id": "V", "captured_at": "2016-01-01T10:00:00Z"}}
    ledger.upsert("V", gopro_id="V", kind="Video", filename="GH010005.MP4", status="DONE",
                  capture_json=capture_json("2020-06-01T16:00:00", source="gps"))
    cap = m.source_video_capture("GH010005.jpg", {"id": "G", "captured_at": "2016-01-01T09:00:00Z"})
    assert cap is not None and cap.utc.year == 2020


# 15. Lease: one item can take longer than the TTL; the heartbeat must keep beating meanwhile.
def test_lease_heartbeat_runs_in_background(ledger, monkeypatch):
    import time
    from photos_migration import ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "HEARTBEAT_EVERY_S", 0.05)
    ledger.acquire_lease()
    ledger.set_meta("lease_heartbeat", "2000-01-01T00:00:00+00:00")
    time.sleep(0.5)
    assert ledger.meta("lease_heartbeat") != "2000-01-01T00:00:00+00:00"
    ledger.release_lease()
    assert ledger.meta("lease_holder") is None


# 16. Cleanup of superseded versions: verify the old version's own date; finish interrupted redates.
def test_superseded_check_uses_the_old_versions_date():
    from photos_migration.cleanup import _verify
    old = datetime(2020, 6, 1, 18, 5)
    panel = "Details\nJun 1, 2020\nMon, 6:05 PM GMT+02:00\nGH010005.MP4\n"
    assert _verify("superseded", "GH010005.MP4", panel, old)[0]
    assert not _verify("superseded", "GH010005.MP4", panel.replace("Jun 1", "Jun 2"), old)[0]
    assert not _verify("superseded", "GH010005.MP4", panel, None)[0]


def test_interrupted_redate_still_queues_the_old_item(ledger):
    from photos_migration.cleanup import _promote_finished
    from photos_migration.redate import ensure_schema
    ensure_schema(ledger)
    ledger.db.execute("INSERT INTO superseded (unit_id, old_media_item_id, state) VALUES ('a', 'OLD', 'pending'), "
                      "('b', 'OLDB', 'pending')")
    ledger.upsert("a", gopro_id="a", status="DONE", media_item_id="NEW")
    ledger.upsert("b", gopro_id="b", status="UPLOADED", upload_token="t")
    _promote_finished(ledger)
    states = dict(ledger.db.execute("SELECT unit_id, state FROM superseded").fetchall())
    assert states == {"a": "active", "b": "pending"}


# 13. Output pasted into reports must not carry the Drive account (an e-mail) or signed URLs.
def test_printed_paths_and_errors_hide_personal_data():
    import requests
    from photos_migration.gopro import Source
    from photos_migration.gphotos import _classify
    p = Path.home() / "Library/CloudStorage/GoogleDrive-someone@example.com/My Drive/swish-cloud-sync-data/ledger.sqlite3"
    assert config.display(p) == "~/Library/CloudStorage/GoogleDrive-<account>/My Drive/swish-cloud-sync-data/ledger.sqlite3"
    err = _classify(requests.exceptions.InvalidURL("bad https://photoslibrary.googleapis.com/upload?upload_id=SECRET"), "upload")
    assert "SECRET" not in str(err)
    assert "SIGNED" not in repr(Source("g", None, "source", "https://cdn/x?Signature=SIGNED", "a.mp4"))
