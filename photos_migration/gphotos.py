"""Google Photos Library API client (append-only + read of app-created items)."""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import config

API = "https://photoslibrary.googleapis.com/v1"
SCOPES = [
    "https://www.googleapis.com/auth/photoslibrary.appendonly",
    "https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata",
    "https://www.googleapis.com/auth/photoslibrary.edit.appcreateddata",
]
CHUNK = 64 << 20


class PhotosError(RuntimeError):
    pass


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if config.GOOGLE_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(config.GOOGLE_TOKEN), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None          # Testing-mode refresh tokens die after 7 days
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(config.GOOGLE_CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
    config.GOOGLE_TOKEN.write_text(creds.to_json())
    config.GOOGLE_TOKEN.chmod(0o600)
    return creds


class GooglePhotos:
    def __init__(self):
        from google.auth.transport.requests import AuthorizedSession
        self.session = AuthorizedSession(_credentials())

    def _check(self, r, what: str):
        if r.status_code >= 300:
            raise PhotosError(f"{what}: HTTP {r.status_code} {r.text[:300]}")
        return r

    def _post_retry(self, url: str, what: str, attempts: int = 6, **kw):
        """POST with backoff on network errors, 429 and 5xx. 4xx (except 429) raises immediately."""
        timeout = kw.pop("timeout", 300)
        network_only = True
        for attempt in range(attempts):
            try:
                r = self.session.post(url, timeout=timeout, **kw)
            except Exception as e:  # noqa: BLE001 - network errors are retried
                err = f"{type(e).__name__}"
            else:
                if r.status_code < 300:
                    return r
                if r.status_code != 429 and r.status_code < 500:
                    raise PhotosError(f"{what}: HTTP {r.status_code} {r.text[:300]}")
                err = f"HTTP {r.status_code}"
                network_only = False
            time.sleep(min(300, 30 * 2 ** attempt))
        if network_only:
            raise config.NetworkDown(f"{what}: Google unreachable ({err})")
        raise PhotosError(f"{what}: kept failing ({err})")

    def upload(self, path: Path, mime: str) -> str:
        """Resumable upload of the exact file bytes. Returns the upload token (valid 1 day)."""
        size = path.stat().st_size
        for session_try in range(3):
            r = self._post_retry(f"{API}/uploads", "start upload", headers={
                "Content-Length": "0",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Content-Type": mime,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Raw-Size": str(size),
            })
            url = r.headers["X-Goog-Upload-URL"]
            gran = int(r.headers.get("X-Goog-Upload-Chunk-Granularity", 262144))
            chunk = max(gran, CHUNK // gran * gran)
            token = self._send(path, size, url, chunk)
            if token:
                return token
        raise PhotosError(f"upload of {path.name} failed after 3 sessions")

    def _send(self, path: Path, size: int, url: str, chunk: int) -> str | None:
        """Send chunks; resume via 'query' after errors. None means: start a new session."""
        offset, failures = 0, 0
        with open(path, "rb") as f:
            while True:
                f.seek(offset)
                data = f.read(chunk)
                last = offset + len(data) >= size
                try:
                    r = self.session.post(url, data=data, timeout=900, headers={
                        "X-Goog-Upload-Command": "upload, finalize" if last else "upload",
                        "X-Goog-Upload-Offset": str(offset),
                    })
                    ok = r.status_code < 300
                except Exception:  # noqa: BLE001
                    ok = False
                if ok:
                    failures = 0
                    if last:
                        return r.text.strip() or None
                    offset += len(data)
                    continue
                failures += 1
                if failures > 6:
                    raise config.NetworkDown(f"upload of {path.name} kept failing")
                time.sleep(min(300, 10 * 2 ** failures))
                q = self._post_retry(url, "query upload", headers={"X-Goog-Upload-Command": "query"})
                state = q.headers.get("X-Goog-Upload-Status")
                if state == "final":
                    return q.text.strip() or None
                if state != "active":
                    return None
                offset = int(q.headers.get("X-Goog-Upload-Size-Received", offset))

    def batch_create(self, items: list[tuple[str, str]], album_id: str | None = None) -> list[dict]:
        """items: (upload_token, file_name). Max 50; never call in parallel for one user."""
        body = {"newMediaItems": [{"simpleMediaItem": {"uploadToken": t, "fileName": n}} for t, n in items]}
        if album_id:
            body["albumId"] = album_id
        r = self._post_retry(f"{API}/mediaItems:batchCreate", "batchCreate", json=body)
        return r.json().get("newMediaItemResults", [])

    def app_albums(self) -> dict[str, str]:
        """title -> id for albums this app created (all the API can list since 2025-03-31)."""
        out, token = {}, None
        while True:
            params = {"pageSize": 50, "excludeNonAppCreatedData": "true"}
            if token:
                params["pageToken"] = token
            r = self.session.get(f"{API}/albums", params=params, timeout=120)
            self._check(r, "list albums")
            d = r.json()
            for a in d.get("albums", []):
                out.setdefault(a.get("title", ""), a["id"])
            token = d.get("nextPageToken")
            if not token:
                return out

    def create_album(self, title: str) -> str:
        """Idempotent by title: reuse an app-created album with the same title."""
        existing = self.app_albums().get(title)
        if existing:
            return existing
        r = self._post_retry(f"{API}/albums", "create album", attempts=1, json={"album": {"title": title}})
        return r.json()["id"]

    def get(self, media_item_id: str) -> dict:
        return self._check(self.session.get(f"{API}/mediaItems/{media_item_id}", timeout=120), "get media item").json()
