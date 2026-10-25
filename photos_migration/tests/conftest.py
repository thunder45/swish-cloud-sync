"""Shared fixtures: every test runs against throw-away folders, never the real ledger/Drive."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from photos_migration import config


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    data, local = tmp_path / "data", tmp_path / "local"
    data.mkdir()
    (local / "secrets").mkdir(parents=True)
    for name, value in {"DATA_DIR": data, "LOCAL_DIR": local, "SECRETS_DIR": local / "secrets",
                        "BROWSER_PROFILE": local / "chrome-profile", "WORK_DIR": local / "work",
                        "LOCK_FILE": local / "migration.lock", "LEDGER": data / "ledger.sqlite3",
                        "INVENTORY": data / "gopro_inventory.json", "TRIP_NAMES": data / "trip_names.json",
                        "GOPRO_COOKIES": local / "secrets" / "gopro_cookies.txt",
                        "GOOGLE_CLIENT_SECRET": local / "secrets" / "client_secret.json",
                        "GOOGLE_TOKEN": local / "secrets" / "google_token.json"}.items():
        monkeypatch.setattr(config, name, value)
    # paths some modules compute at import time
    from photos_migration import albums, audit, cleanup, gphotos_web, inventory_match, pipeline
    for mod, name, value in ((cleanup, "PLAN", data / "cleanup_plan.csv"), (cleanup, "OLD_COPIES", data / "old_copies.csv"),
                             (audit, "OUT", data / "audit_problems.csv"), (albums, "PLAN", data / "albums_plan.csv"),
                             (albums, "TRIP_NAMES_FILE", data / "trip_names.json"),
                             (pipeline, "REVIEW_CSV", data / "same_name_review.csv"),
                             (inventory_match, "OUT_CSV", data / "old_copies.csv"),
                             (gphotos_web, "PROFILE", local / "chrome-profile"), (gphotos_web, "OUT_DIR", data / "gp_search")):
        monkeypatch.setattr(mod, name, value)
    (local / "secrets" / "gopro_cookies.txt").write_text("gp_access_token=x; gp_user_id=y")
    return tmp_path


@pytest.fixture
def ledger():
    from photos_migration.ledger import Ledger
    return Ledger(create=True)


def capture_json(utc: str, offset_min: int = 120, tz: str | None = "Europe/Paris", **kw) -> str:
    from photos_migration.metadata import Capture
    return Capture(datetime.fromisoformat(utc).replace(tzinfo=timezone.utc), offset_min, tz, **kw).to_json()


def write_inventory(items: list[dict]) -> None:
    config.INVENTORY.write_text(json.dumps(items))
