"""Authorization gate for pi-chrome.

Chrome control is **locked by default**. The agent can never grant itself access —
authorization is a human action (CLI: ``/chrome authorize``; web-ui: an explicit
UI button/confirm that calls this module). ``ChromeAuth`` is a pure state holder:
it records *until when* control is granted; the responsibility for obtaining human
consent belongs to the caller.

Two-layer gate (mirrors the original pi-chrome design):
  * visibility layer — ``is_authorized`` is used as each chrome_* tool's ``check_fn``
    so the tools do not even appear in the agent's context while locked.
  * runtime layer — ``require_authorized`` is called inside every tool handler
    before talking to the bridge (defense in depth).

Expiry is lazy: there is no timer; ``is_authorized`` compares the stored deadline
against the current time on every call.
"""

from __future__ import annotations

import threading
import time

_INDEFINITE = "indefinite"


class ChromeAuthError(RuntimeError):
    """Raised by require_authorized when Chrome control is locked."""


class ChromeAuth:
    def __init__(self, default_timeout_minutes: int = 15) -> None:
        # None = locked; float = epoch seconds deadline; "indefinite" = until revoked.
        self._authorized_until: float | str | None = None
        self._default_timeout_minutes = default_timeout_minutes
        self._lock = threading.Lock()

    # -- queries -----------------------------------------------------------

    def is_authorized(self) -> bool:
        """True while a grant is active. Lazily clears an expired grant.

        Used as the ``check_fn`` for every chrome_* tool, so it must be cheap and
        side-effect-light.
        """
        with self._lock:
            until = self._authorized_until
            if until == _INDEFINITE:
                return True
            if isinstance(until, (int, float)) and until > time.time():
                return True
            if until is not None:
                # Expired — clear it so status reflects reality.
                self._authorized_until = None
            return False

    def require_authorized(self) -> None:
        if not self.is_authorized():
            raise ChromeAuthError(
                "Chrome control locked. Ask the user to run /chrome authorize "
                "(or authorize via the UI) before using chrome_* tools."
            )

    def summary(self) -> str:
        with self._lock:
            until = self._authorized_until
        if until == _INDEFINITE:
            return "authorized indefinitely"
        if isinstance(until, (int, float)):
            remaining = until - time.time()
            if remaining > 0:
                return f"authorized for ~{max(1, round(remaining / 60))}m"
        return "locked"

    # -- mutations (caller is responsible for human consent) ---------------

    def authorize(self, minutes: int | str | None = None) -> str:
        """Grant Chrome control. Pure state setter — does NOT prompt for consent.

        ``minutes`` accepts an int, a string like ``"30m"`` / ``"45"`` /
        ``"indefinite"`` / ``"forever"``, or None (uses the default window).
        Returns a human-readable status message.
        """
        label, until = self._parse_duration(minutes)
        if until is None:
            return (
                "Unknown authorize duration. Use minutes (15m, 30m, 45) or "
                "'indefinite'."
            )
        with self._lock:
            self._authorized_until = until
        return f"Chrome control authorized for {label}."

    def revoke(self) -> str:
        with self._lock:
            self._authorized_until = None
        return "Chrome control locked. Run /chrome authorize to allow chrome_* tools again."

    # -- helpers -----------------------------------------------------------

    def _parse_duration(
        self, arg: int | str | None
    ) -> tuple[str, float | str | None]:
        """Returns (label, until) where until is an epoch float, "indefinite", or None on parse failure."""
        if arg is None or (isinstance(arg, str) and not arg.strip()):
            minutes = self._default_timeout_minutes
            return f"{minutes} minutes", time.time() + minutes * 60

        if isinstance(arg, str):
            normalized = arg.strip().lower()
            if normalized in ("indefinite", "forever"):
                return "indefinitely", _INDEFINITE
            raw = normalized[:-1] if normalized.endswith("m") else normalized
            try:
                minutes = float(raw)
            except ValueError:
                return "", None
        else:
            minutes = float(arg)

        if minutes <= 0:
            return "", None
        minutes_label = int(minutes) if minutes == int(minutes) else minutes
        return f"{minutes_label} minutes", time.time() + minutes * 60
