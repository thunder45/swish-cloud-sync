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

Override with `SWISH_LOCAL_DIR` / `SWISH_DATA_DIR`. Use the ledger from one machine at a time
(SQLite on a synced folder must not be open on two machines at once).

## Setup

```
python3.12 -m venv .venv && .venv/bin/pip install -r photos_migration/requirements.txt
brew install exiftool ffmpeg
.venv/bin/python -m photos_migration status
```

- GoPro: copy the browser's `Cookie` header for api.gopro.com into `local/secrets/gopro_cookies.txt`.
- Google: OAuth "Desktop app" client (Photos Library API; scopes appendonly +
  readonly/edit.appcreateddata) saved as `local/secrets/client_secret.json`.

## Commands (`python -m photos_migration …`)

| Command | What it does |
|---|---|
| `inventory` | List GoPro Cloud (multi page-size union: its pagination drops items) and write `same_name_review.csv` |
| `pilot <ids>` / `run` | Download original → dedup by SHA-256 → capture time/place → adaptive HEVC → write metadata → upload |
| `audit` | Inventory ↔ ledger ↔ Google read-back: completeness, exact times, no app duplicates, albums |
| `fix-dates` | Re-upload camera-clock-reset items (2015/2016 dates) with the real time |
| `albums` / `albums --create` | One album per trip (consecutive days away from home) or outing (other days), named by date + place |
| `cleanup --plan` / `cleanup` | Move superseded copies to the Google Photos trash via the web UI, verifying each item |
| `status` | Ledger counts |

## How capture time is decided

1. GPS: GPMF telemetry in MP4s, EXIF GPS in photos. Gives exact UTC; the zone comes from the coordinates (land only).
2. GoPro Cloud `captured_at`. Camera direct uploads store the camera wall clock labelled `Z`; Quik app uploads store real UTC.
3. Zone hints from nearby items, otherwise `config.DEFAULT_TIMEZONE`.

Frame grabs take the capture of their source video. Clock-reset periods are corrected with
the constant camera→UTC offset measured on GPS-bearing items of the same period.

## Safety rules

- The ledger (`ledger.sqlite3`) is the source of truth. A unit that ever had an upload
  token is never re-prepared: retries resend the same SHA-verified bytes, which Google dedups.
- Auth errors stop the run, and network loss pauses it. A unit is FAILED only before its upload starts.
- Items with the same name or recording (`source_gumi`) wait for a decision in `same_name_review.csv`.
- Re-encode only when the file is ≥30% smaller at VMAF ≥95 (5th-percentile frame ≥88). The colour-range
  flags are copied from the source and the GPMF track is kept.
