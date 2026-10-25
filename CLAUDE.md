# CLAUDE.md

Personal project: one-time (plus incremental) migration of a GoPro Cloud library to Google Photos.

- **Stack:** Python 3.12 package `photos_migration/` (CLI: `python -m photos_migration …`), exiftool,
  ffmpeg (libx265 + libvmaf), Playwright driving the user's Chrome for Google Photos web pages.
- **Goals:** every GoPro original in Google Photos exactly once; correct capture date/time/zone/place;
  safe incremental runs for new GoPro media; older reduced copies removed only with an approved plan.
- **Non-goals / never:**
  - never deploy, recreate or reference the old AWS stack (`aws-legacy` tag); it ran in a work account;
  - never put secrets or personal data in the repo (it is public): cookies, OAuth files, account ids,
    e-mails, trip names/dates, Google Photos item ids;
  - never trash or delete in Google Photos without a reviewed plan and per-item verification;
  - never call GoPro, Google or AWS during code review; use copies of the ledger in /tmp.
- **Where things live:** `local/` (git-ignored: secrets, Chrome profile, temp work), `.venv/`, and the
  data folder on Google Drive `My Drive/swish-cloud-sync-data/` (ledger.sqlite3 = source of truth).
- **Verify before saying done:** `.venv/bin/python -m pytest photos_migration/tests -q`,
  `.venv/bin/ruff check photos_migration --select F,B,E9`, `.venv/bin/python -m photos_migration status`.
- Review rules: see `REVIEW.md`.
