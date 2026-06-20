"""Loopback HTTP bridge between Hermes and the Hermes Chrome Connector extension.

This is a Python port of the Node/TypeScript bridge in
``pi-chrome/extensions/chrome-profile-bridge/index.ts`` (the ``ChromeProfileBridge``
class). The wire protocol is unchanged, so the bundled Chrome extension
(``chrome-extension/``) works against it with **zero modifications**.

Design note (deviates from a pure-asyncio sketch on purpose): the server runs in a
daemon thread using ``ThreadingHTTPServer`` and tool handlers call :meth:`send`
synchronously, blocking on a ``concurrent.futures.Future``. This sidesteps any
question about how the Hermes host runs async tools (fresh loop per call vs.
persistent loop) — the bridge owns its own thread and never depends on the
caller's event loop. Tools are therefore registered as sync handlers.

Endpoints:
  GET  /status   any            -> bridge status JSON
  POST /command  local process  -> enqueue + wait (used by client-mode sessions)
  GET  /next     extension only -> long-poll (<=25s) for the next command
  POST /result   extension only -> deliver a command result

Security (mirrors the TS bridge + SECURITY.md):
  * binds 127.0.0.1 only (loopback) — no remote port, no telemetry.
  * /next and /result require a browser origin of ``chrome-extension://`` (or a
    same-origin/no sec-fetch-site request) so ordinary web pages cannot drive
    Chrome via CORS.
  * /command is accepted only from local non-browser processes.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs
from urllib import request as urllib_request, error as urllib_error
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeout

DEFAULT_HOST = os.environ.get("HERMES_CHROME_BRIDGE_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("HERMES_CHROME_BRIDGE_PORT", "17319"))
DEFAULT_TIMEOUT_MS = 30_000
_NEXT_LONG_POLL_S = 25.0

_EXTENSION_DIR = os.path.join(os.path.dirname(__file__), "chrome-extension")


def read_extension_version() -> str:
    """The version reported to the extension via ``x-hermes-chrome-version``.

    Must equal the bundled extension's manifest version: the extension reloads
    itself only when the bridge advertises a *newer* version, so reporting the
    bundled version exactly avoids spurious self-reloads.
    """
    try:
        with open(os.path.join(_EXTENSION_DIR, "manifest.json"), encoding="utf-8") as fh:
            return str(json.load(fh).get("version") or "0.0.0-dev")
    except Exception:
        return "0.0.0-dev"


HERMES_CHROME_VERSION = read_extension_version()


class BridgeError(RuntimeError):
    """Command failed, timed out, or the extension reported an error."""


@dataclass
class BridgeCommand:
    id: str
    action: str
    params: dict


@dataclass
class _Pending:
    command: BridgeCommand
    future: Future
    delivered_at: Optional[float] = None


@dataclass
class ChromeProfileBridge:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    # When False the chrome_* tools focus Chrome so the user can watch; toggled
    # by ``/chrome background``. Default True = silent/background.
    background_default: bool = True

    _httpd: Optional[ThreadingHTTPServer] = field(default=None, repr=False)
    _thread: Optional[threading.Thread] = field(default=None, repr=False)
    _mode: Optional[str] = None  # "server" | "client" | None
    _last_seen_at: Optional[float] = None
    _client_name: Optional[str] = None

    _queue: list = field(default_factory=list, repr=False)
    _pending: dict = field(default_factory=dict, repr=False)
    _cond: threading.Condition = field(default_factory=threading.Condition, repr=False)
    _start_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- lifecycle ---------------------------------------------------------

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def connected(self) -> bool:
        # MV3 service workers pause between polls; treat a recent poll as connected.
        return self._last_seen_at is not None and (time.time() - self._last_seen_at) < 5 * 60

    def ensure_started(self) -> None:
        with self._start_lock:
            if self._httpd is not None or self._mode == "client":
                return
            self._bind_server_or_client()

    def _bind_server_or_client(self) -> None:
        try:
            httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        except OSError as exc:
            # EADDRINUSE (errno 48/98) — another session owns the port; run as client.
            if getattr(exc, "errno", None) in (48, 98, 10048):
                self._mode = "client"
                return
            raise
        httpd.daemon_threads = True
        httpd.bridge = self  # type: ignore[attr-defined]
        self._httpd = httpd
        self._mode = "server"
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="hermes-chrome-bridge", daemon=True
        )
        self._thread.start()

    def _try_promote_to_server(self) -> bool:
        if self._mode != "client":
            return self._mode == "server"
        self._mode = None
        self._bind_server_or_client()
        return self._mode == "server"

    def stop(self) -> None:
        with self._start_lock:
            if self._mode == "client":
                self._mode = None
                return
            with self._cond:
                for pending in list(self._pending.values()):
                    if not pending.future.done():
                        pending.future.set_exception(BridgeError("Chrome profile bridge stopped"))
                self._pending.clear()
                self._queue.clear()
                self._cond.notify_all()
            if self._httpd is not None:
                self._httpd.shutdown()
                self._httpd.server_close()
            self._httpd = None
            self._thread = None
            self._mode = None

    # -- status ------------------------------------------------------------

    def status(self) -> dict:
        return {
            "url": self.url,
            "mode": self._mode or "starting",
            "connected": self.connected,
            "lastSeenAt": self._last_seen_at,
            "clientName": self._client_name,
            "queuedCommands": len(self._queue),
            "pendingCommands": len(self._pending),
        }

    # -- send (Hermes -> Chrome) ------------------------------------------

    def send(
        self,
        action: str,
        params: Optional[dict] = None,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> Any:
        self.ensure_started()
        if self._mode == "client":
            return self._send_via_owner(action, params or {}, timeout_ms)
        return self._send_local(action, params or {}, timeout_ms)

    def _send_local(self, action: str, params: dict, timeout_ms: int) -> Any:
        command = BridgeCommand(id=uuid.uuid4().hex, action=action, params=params)
        future: Future = Future()
        with self._cond:
            self._pending[command.id] = _Pending(command=command, future=future)
            self._queue.append(command)
            self._cond.notify()
        try:
            return future.result(timeout=timeout_ms / 1000)
        except FuturesTimeout:
            with self._cond:
                entry = self._pending.pop(command.id, None)
                self._queue = [c for c in self._queue if c.id != command.id]
            raise BridgeError(self._timeout_message(entry, timeout_ms))

    def _timeout_message(self, entry: Optional[_Pending], timeout_ms: int) -> str:
        poll_age = None if self._last_seen_at is None else (time.time() - self._last_seen_at) * 1000
        if entry is not None and entry.delivered_at:
            return (
                f"Timed out after {timeout_ms}ms: the Chrome extension received the command but "
                "never returned a result. The action may be long-running, or the result post "
                "failed. Run /chrome doctor; if it persists, reload 'Hermes Chrome Connector' at "
                "chrome://extensions."
            )
        if poll_age is None or poll_age > 60_000:
            seen = "never" if poll_age is None else f"{round(poll_age / 1000)}s ago"
            return (
                f"Timed out after {timeout_ms}ms: the Chrome extension is not polling (last seen "
                f"{seen}). Run /chrome onboard, then load the bundled chrome-extension folder in "
                "your normal Chrome profile and keep that Chrome window open."
            )
        return (
            f"Timed out after {timeout_ms}ms: the Chrome extension is polling (last seen "
            f"{round(poll_age / 1000)}s ago) but did not pick up this command in time. Retry; if "
            "it persists, reload 'Hermes Chrome Connector' at chrome://extensions."
        )

    def _send_via_owner(self, action: str, params: dict, timeout_ms: int) -> Any:
        body = json.dumps({"action": action, "params": params, "timeoutMs": timeout_ms}).encode()
        req = urllib_request.Request(
            f"{self.url}/command",
            data=body,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=(timeout_ms + 2_000) / 1000) as resp:
                payload = json.loads(resp.read().decode() or "{}")
        except urllib_error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode() or "{}")
            except Exception:
                payload = {}
            if exc.code == 404:
                raise BridgeError(
                    "A running session owns the Chrome bridge but is on an older hermes-chrome-plugin "
                    "without multi-session support. Restart that session, then retry."
                )
            raise BridgeError(payload.get("error") or f"Chrome bridge owner HTTP {exc.code}")
        except (urllib_error.URLError, ConnectionError, OSError):
            # Owner is gone — try to take over the port and run locally.
            if self._try_promote_to_server():
                return self._send_local(action, params, timeout_ms)
            raise BridgeError(
                "The session that owned the Chrome bridge is unreachable and this session could "
                "not take over the bridge port. Restart this session, or run /chrome doctor."
            )
        if not payload.get("ok"):
            raise BridgeError(payload.get("error") or "Chrome bridge owner error")
        return payload.get("result")

    # -- queue internals (called by the request handler) -------------------

    def _take_next_command(self) -> Optional[BridgeCommand]:
        """Long-poll: return the next queued command or None after the poll window."""
        deadline = time.monotonic() + _NEXT_LONG_POLL_S
        with self._cond:
            while not self._queue:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            command = self._queue.pop(0)
            entry = self._pending.get(command.id)
            if entry is not None:
                entry.delivered_at = time.time()
            return command

    def _deliver_result(self, result: dict) -> bool:
        with self._cond:
            pending = self._pending.pop(result.get("id"), None)
        if pending is None:
            return False
        if pending.future.done():
            return True
        if result.get("ok"):
            pending.future.set_result(result.get("result"))
        else:
            pending.future.set_exception(
                BridgeError(result.get("error") or "Chrome extension command failed")
            )
        return True

    def _mark_seen(self, client_name: Optional[str] = None) -> None:
        self._last_seen_at = time.time()
        if client_name is not None:
            self._client_name = client_name


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------

def _cors_headers_for(headers) -> dict:
    origin = headers.get("origin") or ""
    if not origin.startswith("chrome-extension://"):
        return {}
    return {
        "access-control-allow-origin": origin,
        "access-control-allow-methods": "GET,POST,OPTIONS",
        "access-control-allow-headers": "content-type",
        "access-control-expose-headers": "x-hermes-chrome-version",
        "vary": "origin",
    }


def _is_browser_origin_allowed(headers) -> bool:
    origin = headers.get("origin") or ""
    if origin:
        return origin.startswith("chrome-extension://")
    sec = headers.get("sec-fetch-site") or ""
    return sec in ("", "none", "same-origin")


def _is_local_process_request(headers) -> bool:
    return not headers.get("origin") and not headers.get("sec-fetch-site")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Silence default stderr logging.
    def log_message(self, *args) -> None:  # noqa: D401
        pass

    @property
    def _bridge(self) -> ChromeProfileBridge:
        return self.server.bridge  # type: ignore[attr-defined]

    def _send_json(self, status: int, body: Any, extra_headers: Optional[dict] = None) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(data)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self) -> str:
        length = int(self.headers.get("content-length") or 0)
        return self.rfile.read(length).decode("utf-8") if length else ""

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not _is_browser_origin_allowed(self.headers):
            self._send_json(403, {"ok": False, "error": "browser origin not allowed"})
            return
        self._send_json(200, {"ok": True}, _cors_headers_for(self.headers))

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/status":
            self._send_json(200, self._bridge.status())
            return
        if path == "/next":
            self._handle_next()
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/command":
            self._handle_command()
            return
        if path == "/result":
            self._handle_result()
            return
        self._send_json(404, {"error": "not found"})

    # -- endpoints ---------------------------------------------------------

    def _handle_command(self) -> None:
        if not _is_local_process_request(self.headers):
            self._send_json(403, {"ok": False, "error": "Chrome commands are accepted only from local processes"})
            return
        try:
            body = json.loads(self._read_body() or "{}")
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "Invalid JSON"})
            return
        action = body.get("action")
        if not action:
            self._send_json(400, {"ok": False, "error": "Missing command action"})
            return
        try:
            result = self._bridge._send_local(
                action, body.get("params") or {}, body.get("timeoutMs") or DEFAULT_TIMEOUT_MS
            )
            self._send_json(200, {"ok": True, "result": result})
        except BridgeError as exc:
            self._send_json(504, {"ok": False, "error": str(exc)})

    def _handle_next(self) -> None:
        if not _is_browser_origin_allowed(self.headers):
            self._send_json(403, {"ok": False, "error": "browser origin not allowed"})
            return
        qs = parse_qs(urlparse(self.path).query)
        self._bridge._mark_seen((qs.get("name") or [None])[0])
        command = self._bridge._take_next_command()
        version = HERMES_CHROME_VERSION
        headers = {**_cors_headers_for(self.headers), "x-hermes-chrome-version": version}
        if command is not None:
            payload = {
                "type": "command",
                "command": {"id": command.id, "action": command.action, "params": command.params},
                "expectedExtensionVersion": version,
            }
        else:
            payload = {"type": "none", "expectedExtensionVersion": version}
        self._send_json(200, payload, headers)

    def _handle_result(self) -> None:
        if not _is_browser_origin_allowed(self.headers):
            self._send_json(403, {"ok": False, "error": "browser origin not allowed"})
            return
        self._bridge._mark_seen()
        try:
            result = json.loads(self._read_body() or "{}")
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "Invalid JSON"})
            return
        delivered = self._bridge._deliver_result(result)
        cors = _cors_headers_for(self.headers)
        if not delivered:
            self._send_json(404, {"ok": False, "error": "unknown command id"}, cors)
            return
        self._send_json(200, {"ok": True}, cors)
