# photos_migration: GoPro Cloud → Google Photos

Local tool (no AWS) that copies every GoPro Cloud original to Google Photos once, with
the real capture date/time/location, and cleans up older reduced copies.

## Where things live

| Place | Contents | On a new machine |
|---|---|---|
| `local/secrets/` (in this project, git-ignored) | `client_secret.json` (Google OAuth client), `google_token.json`, `gopro_cookies.txt` | copy the folder, or recreate: download the client again from Google Cloud, sign in again, paste fresh GoPro cookies |
| `local/chrome-profile/` | Chrome profile signed in to Google Photos (web search/cleanup) | sign in again when the browser opens |
| `local/work/` | temporary downloads/encodes (multi-GB during a run) | nothing to keep |
| `.venv/` | Python environment | recreate (see Setup) |
| **Google Drive** `My Drive/swish-cloud-sync-data/` | **`ledger.sqlite3`** (what was uploaded — needed to avoid duplicates on future runs), inventory, review/cleanup/album sheets, `trip_names.json`, logs | comes with Google Drive for desktop; nothing to do |

The data folder is found under `~/Library/CloudStorage/GoogleDrive-*/My Drive/` ("Meu Drive" is only
the Finder name). Override with `SWISH_LOCAL_DIR` / `SWISH_DATA_DIR`.

The ledger fails closed: it is never created implicitly (an unsynced Drive must not look like
"nothing uploaded yet"), and a lease in the ledger stops a second machine from running at the
same time (`--takeover` only when the other machine is gone).

## Setup

```
brew install python@3.12 exiftool ffmpeg          # ffmpeg must have libx265 and libvmaf
python3.12 -m venv .venv && .venv/bin/pip install -r photos_migration/requirements.txt
.venv/bin/python -m photos_migration doctor        # tools, folders, credentials
.venv/bin/python -m photos_migration login         # Google sign-in (browser)
.venv/bin/python -m photos_migration status
```

Google Chrome is needed for the web search/cleanup steps (driven by Playwright).

- GoPro: copy the browser's `Cookie` header for api.gopro.com into `local/secrets/gopro_cookies.txt`.
- Google: OAuth "Desktop app" client (Photos Library API; scopes appendonly +
  readonly/edit.appcreateddata) saved as `local/secrets/client_secret.json`. Keep the **same**
  client: the API only shows items created by that client, so a new one cannot see (or dedup
  against) the existing uploads. The run checks the client id against the ledger.
- A new migration from scratch: `init` creates the empty ledger and the first run needs `--first-run`.

## Commands (`python -m photos_migration …`)

| Command | What it does |
|---|---|
| `init` / `login` / `doctor` | New empty ledger (brand-new migration only) / Google sign-in / machine check |
| `inventory` | List GoPro Cloud (multi page-size union: its pagination drops items) and write `same_name_review.csv` |
| `pilot <ids>` / `run` | Download original → dedup by SHA-256 → capture time/place → adaptive HEVC → write metadata → upload. `--no-upload`, `--no-reencode`, `--first-run`, `--takeover` |
| `audit` | Inventory ↔ ledger ↔ Google read-back: completeness, exact times, no app duplicates, albums |
| `fix-dates` | Re-upload camera-clock-reset items (2015/2016 dates) with the real time |
| `fix-drift [--dry-run]` | Re-upload GPS-less items with the camera clock error measured by same-day GPS videos (both sides agreeing, or a later one: a clock sync would have cleared it) |
| `albums` / `albums --create` | One album per trip (consecutive days away from home) or outing (other days), named by date + place |
| `cleanup --plan` / `cleanup [--dry-run]` | Move older reduced copies and our own wrong-time versions to the Google Photos trash via the web UI, verifying each item |
| `status` | Ledger path, counts, last update, lease (read-only) |
| `verify [ids]` | Read back uploaded items and compare Google's capture time with ours |

## How capture time is decided

1. GPS: GPMF telemetry in MP4s, EXIF GPS in photos. Gives exact UTC; the zone comes from the coordinates (land only).
2. GoPro Cloud `captured_at`. Camera direct uploads store the camera wall clock labelled `Z`; Quik app
   uploads store UTC derived from the camera clock, so they carry the camera clock error too.
3. Zone hints from nearby items, otherwise `config.DEFAULT_TIMEZONE`.

Frame grabs take the capture of their source video. Clock-reset periods (2015/2016 dates) are
corrected with the constant camera→UTC offset measured on GPS-bearing items of the same period;
without GPS, a source video or such an offset the unit is held for review and is never dated
from the cloud. Clock drift (camera skipped a DST change, kept another country's time) is
measured on GPS videos and applied by `fix-drift` to GPS-less neighbours.

## Safety rules

- The ledger (`ledger.sqlite3`) is the source of truth. A unit that ever had an upload
  token is never re-prepared: retries resend the same SHA-verified bytes, which Google dedups.
- Auth errors stop the run, and network loss pauses it. A unit is FAILED only before its upload starts.
- Items with the same name or recording (`source_gumi`) wait for a decision in `same_name_review.csv`.
- Re-encode only when the file is ≥30% smaller at VMAF ≥95 (5th/1st-percentile frame ≥88/80, worst
  2-second window ≥90). The colour-range flags are copied from the source and the GPMF track is
  kept; GoPro's own `udta` boxes are not (the original stays in GoPro Cloud).
- Cleanup trashes an item only when its replacement is complete and readable in Google
  (every chapter/frame), after checking the item's info panel, and only counts it as trashed
  when Google shows the "Undo" toast.

## Tests

```
.venv/bin/python -m pytest photos_migration/tests -q
.venv/bin/ruff check photos_migration --select F,B,E9
```

`tests/test_incidents.py` has one regression test per incident this project already hit; add one
for each new incident. The tests never touch the network, the real ledger or Google Drive.
