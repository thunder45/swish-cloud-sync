"""Paths and constants.

Everything lives in two visible places (see README "Where things live"):
- the project's own `local/` folder (git-ignored): secrets, browser profile, temp work;
- a data folder on Google Drive: ledger, inventory, review sheets, logs (backed up, portable).
Override with SWISH_LOCAL_DIR / SWISH_DATA_DIR.
"""
import os
import re
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOCAL_DIR = Path(os.environ.get("SWISH_LOCAL_DIR", PROJECT_DIR / "local"))
DATA_FOLDER = "swish-cloud-sync-data"


def _default_data_dir() -> Path:
    """Google Drive for desktop mounts under ~/Library/CloudStorage/GoogleDrive-<account>/My Drive
    (the on-disk name; Finder shows a localized one). The ~/Google Drive link is optional."""
    mounts = sorted((Path.home() / "Library" / "CloudStorage").glob("GoogleDrive-*/My Drive"))
    for m in mounts:
        if (m / DATA_FOLDER).exists():
            return m / DATA_FOLDER
    base = mounts[0] if mounts else Path.home() / "Google Drive" / "My Drive"
    return base / DATA_FOLDER


DATA_DIR = Path(os.environ.get("SWISH_DATA_DIR") or _default_data_dir())

SECRETS_DIR = LOCAL_DIR / "secrets"
CONFIG_DIR = SECRETS_DIR                      # kept for older imports
BROWSER_PROFILE = LOCAL_DIR / "chrome-profile"
WORK_DIR = LOCAL_DIR / "work"                 # multi-GB temp files: never on Drive
LOCK_FILE = LOCAL_DIR / "migration.lock"      # file locks are unreliable on synced drives

GOPRO_COOKIES = SECRETS_DIR / "gopro_cookies.txt"
GOOGLE_CLIENT_SECRET = SECRETS_DIR / "client_secret.json"
GOOGLE_TOKEN = SECRETS_DIR / "google_token.json"

INVENTORY = DATA_DIR / "gopro_inventory.json"
LEDGER = DATA_DIR / "ledger.sqlite3"
TRIP_NAMES = DATA_DIR / "trip_names.json"

# Chrome on macOS; GoPro's DataDome bot protection expects a browser UA.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)

# Used only when a capture has no GPS fix and no same-day neighbour with one.
DEFAULT_TIMEZONE = "Europe/Luxembourg"

# Adaptive re-encode: keep the HEVC version only if it is at least this much
# smaller and its VMAF (harmonic mean) is at least this high.
MIN_SAVING = 0.30
MIN_VMAF = 95.0
MIN_VMAF_P5 = 88.0      # 5th-percentile frame VMAF
MIN_VMAF_P1 = 80.0      # 1st-percentile frame VMAF
MIN_VMAF_WINDOW = 90.0  # worst rolling 2-second mean: no sustained visibly-bad stretch
VMAF_WINDOW_S = 2.0


def display(p) -> str:
    """A path for printing: home as ~, the Google Drive account (an e-mail) masked."""
    s = str(p)
    home = str(Path.home())
    if s.startswith(home):
        s = "~" + s[len(home):]
    return re.sub(r"GoogleDrive-[^/]+", "GoogleDrive-<account>", s)


class NetworkDown(RuntimeError):
    """Internet (or a remote service) unreachable after retries: pause the run, do not mark items."""


def ensure_dirs(create_data: bool = False) -> None:
    """Create the local folders. The data folder must already exist (it holds the ledger):
    a missing one means Drive is not mounted/synced yet, never "start from scratch"."""
    if not DATA_DIR.exists():
        if not create_data:
            raise SystemExit(f"data folder not found: {display(DATA_DIR)}\n"
                             "Is Google Drive for desktop running and synced? Or set SWISH_DATA_DIR. "
                             "Only 'python -m photos_migration init' creates a new one.")
        DATA_DIR.mkdir(parents=True)
    for d in (SECRETS_DIR, BROWSER_PROFILE, WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for d in (LOCAL_DIR, SECRETS_DIR, BROWSER_PROFILE):
        d.chmod(0o700)
