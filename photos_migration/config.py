"""Paths and constants. Secrets and data live outside the repository."""
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "swish-cloud-sync"
DATA_DIR = Path.home() / ".local" / "share" / "swish-cloud-sync"

GOPRO_COOKIES = CONFIG_DIR / "gopro_cookies.txt"
GOOGLE_CLIENT_SECRET = CONFIG_DIR / "client_secret.json"
GOOGLE_TOKEN = CONFIG_DIR / "google_token.json"

INVENTORY = DATA_DIR / "gopro_inventory.json"
LEDGER = DATA_DIR / "ledger.sqlite3"
WORK_DIR = DATA_DIR / "work"

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
    for d in (CONFIG_DIR, DATA_DIR, WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
