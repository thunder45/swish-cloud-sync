"""Paths and constants.

Everything lives in two visible places (see README "Where things live"):
- the project's own `local/` folder (git-ignored): secrets, browser profile, temp work;
- a data folder on Google Drive: ledger, inventory, review sheets, logs (backed up, portable).
Override with SWISH_LOCAL_DIR / SWISH_DATA_DIR.
"""
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOCAL_DIR = Path(os.environ.get("SWISH_LOCAL_DIR", PROJECT_DIR / "local"))
DATA_DIR = Path(os.environ.get("SWISH_DATA_DIR", Path.home() / "Google Drive" / "My Drive" / "swish-cloud-sync-data"))

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
MIN_VMAF_P5 = 88.0      # 5th-percentile frame VMAF: no sustained visibly-bad stretch


class NetworkDown(RuntimeError):
    """Internet (or a remote service) unreachable after retries: pause the run, do not mark items."""


def ensure_dirs() -> None:
    if not DATA_DIR.parent.exists():
        raise SystemExit(f"data folder's parent not found: {DATA_DIR.parent}\n"
                         "Is Google Drive for desktop running? Or set SWISH_DATA_DIR.")
    for d in (SECRETS_DIR, BROWSER_PROFILE, WORK_DIR, DATA_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for d in (LOCAL_DIR, SECRETS_DIR, BROWSER_PROFILE):
        d.chmod(0o700)
