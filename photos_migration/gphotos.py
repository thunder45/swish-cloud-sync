"""Google Photos Library API client (append-only + read of app-created items)."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

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


class PhotosAuthError(PhotosError):
    """Login expired or revoked: stop the run and ask for a new sign-in (never treated as a network outage)."""


def client_id() -> str:
    """OAuth client of this machine. Items created by another client are invisible to this one."""
    d = json.loads(config.GOOGLE_CLIENT_SECRET.read_text())
    return next(iter(d.values()))["client_id"]


def _classify(e: Exception, what: str) -> Exception:
    """Network trouble -> NetworkDown (retry/pause); auth trouble -> PhotosAuthError (stop); else PhotosError."""
    from google.auth import exceptions as gexc
    if isinstance(e, (gexc.TransportError, requests.ConnectionError, requests.Timeout)):
        return config.NetworkDown(f"{what}: Google unreachable ({type(e).__name__})")
    if isinstance(e, gexc.GoogleAuthError):
        return PhotosAuthError(f"{what}: Google login expired or revoked ({type(e).__name__}) - "
                               "run 'python -m photos_migration login' and start again")
    return PhotosError(f"{what}: {type(e).__name__}: {re.sub(r'https?://\S+', '<url>', str(e))[:200]}")


def _credentials(interactive: bool = False):
    from google.auth import exceptions as gexc
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if config.GOOGLE_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(config.GOOGLE_TOKEN), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except gexc.RefreshError:
            creds = None          # Testing-mode refresh tokens die after 7 days
        except Exception as e:    # noqa: BLE001 - network blip: do not throw the login away
            raise _classify(e, "token refresh") from None
    if not creds or not creds.valid:
        if not interactive:
            raise PhotosAuthError("Google sign-in needed: run 'python -m photos_migration login'")
        flow = InstalledAppFlow.from_client_secrets_file(str(config.GOOGLE_CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=600)
        if not creds:
            raise PhotosAuthError("sign-in not completed within 10 minutes")
    config.GOOGLE_TOKEN.write_text(creds.to_json())
    config.GOOGLE_TOKEN.chmod(0o600)
    return creds


class GooglePhotos:
    def __init__(self, interactive: bool = False):
        from google.auth.transport.requests import AuthorizedSession
        self.session = AuthorizedSession(_credentials(interactive))

    def _check(self, r, what: str):
        if r.status_code == 401:
            raise PhotosAuthError(f"{what}: HTTP 401 - run 'python -m photos_migration login'")
        if r.status_code >= 300:
            raise PhotosError(f"{what}: HTTP {r.status_code} {r.text[:300]}")
        return r

    def _get(self, url: str, what: str, **kw):
        try:
            r = self.session.get(url, timeout=kw.pop("timeout", 120), **kw)
        except Exception as e:  # noqa: BLE001
            raise _classify(e, what) from None
        return self._check(r, what)

    def _post_retry(self, url: str, what: str, attempts: int = 6, **kw):
        """POST with backoff on network errors, 429 and 5xx. 4xx (except 429) raises immediately."""
        timeout = kw.pop("timeout", 300)
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                r = self.session.post(url, timeout=timeout, **kw)
            except Exception as e:  # noqa: BLE001 - only network errors are retried
                last = _classify(e, what)
                if not isinstance(last, config.NetworkDown):
                    raise last from None
            else:
                if r.status_code < 300:
                    return r
                if r.status_code != 429 and r.status_code < 500:
                    self._check(r, what)
                last = PhotosError(f"{what}: kept failing (HTTP {r.status_code})")
            if attempt < attempts - 1:
                time.sleep(min(300, 30 * 2 ** attempt))
        raise last

    def upload(self, path: Path, mime: str) -> str:
        """Resumable upload of the exact file bytes. Returns the upload token (valid 1 day)."""
        size = path.stat().st_size
        for _ in range(3):
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
                except Exception as e:  # noqa: BLE001
                    err = _classify(e, f"upload {path.name}")
                    if not isinstance(err, config.NetworkDown):
                        raise err from None
                    ok = False
                else:
                    if r.status_code == 401:
                        self._check(r, f"upload {path.name}")
                    if 400 <= r.status_code < 500 and r.status_code != 429:
                        return None           # session rejected the chunk: start a new session
                    ok = r.status_code < 300
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
            r = self._get(f"{API}/albums", "list albums", params=params)
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
        return self._get(f"{API}/mediaItems/{media_item_id}", "get media item").json()

    def batch_get(self, ids: list[str]) -> list[dict]:
        """mediaItemResults for up to 50 ids (app-created items only are readable)."""
        return self._get(f"{API}/mediaItems:batchGet", "batchGet", params={"mediaItemIds": ids}).json() \
            .get("mediaItemResults", [])

    def list_app_items(self) -> dict[str, dict]:
        out, token = {}, None
        while True:
            params = {"pageSize": 100}
            if token:
                params["pageToken"] = token
            d = self._get(f"{API}/mediaItems", "list app items", params=params).json()
            for mi in d.get("mediaItems", []):
                out[mi["id"]] = mi
            token = d.get("nextPageToken")
            if not token:
                return out
