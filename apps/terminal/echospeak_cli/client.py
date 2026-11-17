"""HTTP + NDJSON transport to the EchoSpeak backend. Standard library only."""
from __future__ import annotations

import http.client
import json
import time
import uuid
from typing import Any, Callable, Iterator, Optional
from urllib.parse import quote, urlencode, urlsplit

AUTH_HEADER = "x-echospeak-key"


class ApiError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class AuthError(ApiError):
    pass


class ConnectError(Exception):
    """The backend could not be reached (refused, reset, timed out)."""


def new_request_id() -> str:
    # Matches QueryRequest.client_request_id: ^[A-Za-z0-9._:-]+$, 8..128 chars.
    return "term-" + uuid.uuid4().hex


class Client:
    def __init__(self, base_url: str, key: str = "", timeout: float = 15.0, stream_idle: float = 60.0) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError(f"Not an http(s) URL: {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self.scheme = parts.scheme
        self.host = parts.hostname
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.prefix = parts.path.rstrip("/")
        self.key = key
        self.timeout = timeout
        self.stream_idle = stream_idle

    def retarget(self, base_url: str, key: str) -> None:
        """Point at a backend that came back on another port (a restart publishes a new endpoint file)."""
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError(f"Not an http(s) URL: {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self.scheme, self.host = parts.scheme, parts.hostname
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.prefix = parts.path.rstrip("/")
        self.key = key

    # ── plumbing ──────────────────────────────────────────────────────
    def _conn(self, timeout: float) -> http.client.HTTPConnection:
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        return cls(self.host, self.port, timeout=timeout)

    def _open(self, method: str, path: str, body: Any = None, params: Optional[dict] = None,
              timeout: Optional[float] = None) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        url = self.prefix + path
        if params:
            url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
        headers = {"Accept": "application/json, application/x-ndjson", "User-Agent": "echospeak-terminal"}
        if self.key:
            headers[AUTH_HEADER] = self.key
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn = self._conn(timeout or self.timeout)
        try:
            conn.request(method, url, body=data, headers=headers)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise ConnectError(f"{type(exc).__name__}: {exc}") from exc
        if resp.status >= 400:
            raw = resp.read(65536)
            conn.close()
            detail = raw.decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
                detail = parsed.get("detail", detail) if isinstance(parsed, dict) else detail
                if isinstance(detail, dict):
                    detail = str(detail.get("message") or detail)
            except ValueError:
                pass
            err = AuthError if resp.status == 401 else ApiError
            raise err(resp.status, str(detail))
        return conn, resp

    def request(self, method: str, path: str, body: Any = None, params: Optional[dict] = None) -> Any:
        conn, resp = self._open(method, path, body, params)
        try:
            raw = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            raise ConnectError(str(exc)) from exc
        finally:
            conn.close()
        return json.loads(raw) if raw.strip() else None

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params or None)

    def post(self, path: str, body: Any = None, **params: Any) -> Any:
        return self.request("POST", path, body=body if body is not None else {}, params=params or None)

    def stream(self, method: str, path: str, body: Any = None, params: Optional[dict] = None) -> Iterator[dict]:
        """Open now (so HTTP errors raise here), then yield NDJSON objects lazily.

        Iteration raises ConnectError if the connection drops or stays idle longer than stream_idle.
        """
        conn, resp = self._open(method, path, body, params, timeout=self.stream_idle)
        return self._lines(conn, resp)

    @staticmethod
    def _lines(conn: http.client.HTTPConnection, resp: http.client.HTTPResponse) -> Iterator[dict]:
        try:
            while True:
                try:
                    line = resp.readline(4 * 1024 * 1024)
                except (OSError, http.client.HTTPException) as exc:
                    raise ConnectError(f"stream interrupted: {type(exc).__name__}") from exc
                if not line:
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    yield item
        finally:
            conn.close()

    # ── endpoints ─────────────────────────────────────────────────────
    def health(self) -> dict:
        return self.get("/health")

    def threads(self, limit: int = 50) -> list[dict]:
        return self.get("/threads", limit=limit) or []

    def thread(self, thread_id: str) -> dict:
        return self.get("/threads/" + quote(thread_id, safe=""))

    def create_thread(self, title: str = "", project_id: str = "", idempotency_key: str = "") -> dict:
        return self.post("/threads", {"title": title, "source": "web", "project_id": project_id,
                                      "idempotency_key": idempotency_key})

    def runs(self, thread_id: str) -> list[dict]:
        return (self.get("/query/runs", thread_id=thread_id) or {}).get("items", [])

    def approvals(self, session_id: str = "") -> list[dict]:
        return (self.get("/lean/approvals", session_id=session_id) or {}).get("items", [])

    def decide(self, approval_id: str, decision: str) -> dict:
        return self.post("/lean/approvals/" + quote(approval_id, safe=""), {"decision": decision})

    def cancel(self, request_id: str, thread_id: str) -> dict:
        return self.post("/query/cancel", {"request_id": request_id, "thread_id": thread_id})

    def steer(self, thread_id: str, message: str) -> dict:
        return self.post("/query/steer", {"thread_id": thread_id, "message": message, "client_request_id": new_request_id()})

    def queue(self, thread_id: str, message: str) -> dict:
        return self.post("/query/queue", {"thread_id": thread_id, "message": message, "client_request_id": new_request_id()})


def follow(client: Client, thread_id: str, request_id: str, *, after: int = 0, first: Optional[Iterator[dict]] = None,
           on_event: Callable[[dict], None], retries: int = 6, sleep: Callable[[float], None] = time.sleep,
           notice: Callable[[str], None] = lambda _m: None) -> tuple[str, int]:
    """Follow one run until ``journal_done``; reconnect with ``after=<cursor>`` when the stream drops.

    ``first`` is an already-open stream (the POST /query/stream response). Events at or below the
    cursor are skipped, so a reconnect never renders anything twice. Returns (status, cursor).
    """
    cursor = after
    attempts = 0
    stream = first
    path = f"/query/runs/{quote(request_id, safe='')}/events"
    while True:
        try:
            if stream is None:
                stream = client.stream("GET", path, params={"thread_id": thread_id, "after": cursor})
            for event in stream:
                if event.get("type") == "journal_done":
                    return str(event.get("status") or "completed"), cursor
                seq = int(event.get("_replay_seq") or 0)
                if seq and seq <= cursor:
                    continue
                cursor = max(cursor, seq)
                attempts = 0
                on_event(event)
            raise ConnectError("stream ended before the run finished")
        except ConnectError as exc:
            stream = None
            attempts += 1
            if attempts > retries:
                raise
            notice(f"connection lost ({exc}); reconnecting from event {cursor}")
            sleep(min(8.0, 0.25 * 2 ** attempts))


def submit(client: Client, thread_id: str, message: str, request_id: str, agent_id: str = "",
           extra: Optional[dict] = None) -> Iterator[dict]:
    """POST /query/stream. The body is identical on retry, so the journal's claim treats it as the same run."""
    body = {"message": message, "thread_id": thread_id, "client_request_id": request_id}
    if agent_id:
        body["agent_id"] = agent_id
    body.update(extra or {})
    try:
        return client.stream("POST", "/query/stream", body)
    except ConnectError:
        # Ambiguous: the run may have been claimed. Reattach if it exists; else retry the same POST.
        try:
            return client.stream("GET", f"/query/runs/{quote(request_id, safe='')}/events",
                                 params={"thread_id": thread_id, "after": 0})
        except ApiError as exc:
            if exc.status != 404:
                raise
        return client.stream("POST", "/query/stream", body)
