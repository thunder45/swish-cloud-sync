# REVIEW.md

What failure looks like in this repo. Anything that can create an item the Google Photos API
cannot delete is **Important**.

1. **Duplicate in Google Photos.** A second upload of the same capture, for example after a crash, an expired
   token, a missing or stale ledger, a re-encode or a metadata rewrite.
2. **Media lost.** An original marked DONE without a Google item, or the only copy trashed by `cleanup`
   (the trash keeps items for 30 days only).
3. **Wrong capture time, zone or place written into an uploaded file.** Watch for camera clock resets
   (2015/2016), clock drift measured by GPS, DST, time-lapse time scaling and frame grabs.
4. **UI automation acting on the wrong thing.** `cleanup` must verify each item right before clicking and
   must never reach "Delete permanently".
5. **Personal data leaking into the public repo.** Cookies, tokens, OAuth files, account ids,
   e-mails, trip names/dates, item ids.
6. **Docs or config that lie or misroute.** Anything that sends someone to the retired AWS stack, or puts the
   ledger somewhere a new machine will not find it.

Rules for reviewers:
- Evidence is a file and line (or a reproduced command), not a guess from a name.
- Drop findings that are only about style, naming, typing or cleanup. Report a bug class once, with all its instances.
- Tag each finding Important, Nit or Pre-existing.
- Each incident that has happened gets a regression test in `photos_migration/tests/` before its fix
  counts as done.
