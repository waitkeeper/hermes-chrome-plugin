"""The ``/chrome`` slash command — Python port of the unified command in
``pi-chrome/extensions/chrome-profile-bridge/index.ts``.

A single command registered as ``chrome`` whose handler parses the first token as
a subcommand: ``authorize | revoke | status | doctor | onboard | background``.
Handlers return plain strings (the host renders them); there is no terminal
``ctx.ui.confirm`` — in CLI the act of typing the command is the human action; in
web-ui an explicit UI confirm precedes the programmatic call.
"""

from __future__ import annotations

import os

from .auth import ChromeAuth
from .bridge import ChromeProfileBridge, BridgeError, PI_CHROME_VERSION

_HELP = """\
/chrome — control the pi-chrome bridge

  /chrome authorize [15m|30m|<minutes>|indefinite]  Allow this session to use chrome_* tools.
  /chrome revoke                                     Lock Chrome control.
  /chrome status                                     One-line: connection, auth, background.
  /chrome doctor                                     Full health check.
  /chrome onboard                                    How to install the companion Chrome extension.
  /chrome background [on|off|toggle|status]          Whether chrome_* runs without focusing Chrome."""

_BACKGROUND_DESC = {
    "on": "pi-chrome runs in the background; Chrome won't pop up or steal focus.",
    "off": "Chrome pops to the front and switches tabs so you can watch what pi-chrome is doing.",
}


def _hostname(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _status_summary(bridge: ChromeProfileBridge, auth: ChromeAuth) -> str:
    parts = []
    try:
        version = bridge.send("tab.version", {}, 5_000) or {}
        ext_version = version.get("extensionVersion")
        if ext_version and ext_version != PI_CHROME_VERSION:
            parts.append(f"⚠ Chrome extension v{ext_version} (pi-chrome v{PI_CHROME_VERSION}, reload extension)")
        else:
            parts.append("✓ Chrome connected")
    except BridgeError:
        parts.append("✗ Chrome not responding")
    parts.append(f"auth: {auth.summary()}")
    parts.append(f"background: {'on' if bridge.background_default else 'off'}")
    return " · ".join(parts)


def _doctor(bridge: ChromeProfileBridge) -> str:
    lines = [f"pi-chrome v{PI_CHROME_VERSION}"]
    status = bridge.status()
    role = "sharing another session's connection" if status.get("mode") == "client" else "running the Chrome connection for this machine"
    lines.append(f"• This session is {role}.")

    extension_alive = False
    version_mismatch = False
    try:
        import time as _time

        started = _time.time()
        version = bridge.send("tab.version", {}, 35_000) or {}
        latency_ms = round((_time.time() - started) * 1000)
        extension_alive = True
        ext_version = version.get("extensionVersion")
        if ext_version and ext_version != PI_CHROME_VERSION:
            version_mismatch = True
            lines += [
                f"✗ The Chrome companion extension is on an old version ({ext_version}); this pi-chrome is {PI_CHROME_VERSION}.",
                "  Fix: open chrome://extensions and click the refresh icon on 'Hermes Chrome Connector'.",
            ]
        else:
            lines.append(f"✓ Chrome is connected (companion extension v{ext_version or '?'}, responded in {latency_ms}ms).")
    except BridgeError as exc:
        lines.append(f"✗ Chrome isn't responding: {exc}")
        lines.append("  Fix: run /chrome onboard to install the Chrome companion extension, then keep that Chrome window open.")

    if extension_alive and not version_mismatch:
        try:
            value = bridge.send("page.evaluate", {"expression": "1+1", "awaitPromise": True, "foreground": False}, 10_000)
            if value == 2:
                lines.append("✓ pi-chrome can run code in the active Chrome tab.")
            else:
                lines.append(f"⚠ pi-chrome ran code but got an unexpected result ({value}). The current tab may be a Chrome internal page or a strict site.")
        except BridgeError as exc:
            lines.append(f"✗ pi-chrome can't run code in the active tab: {exc}")
        try:
            probe = bridge.send("page.probe", {"foreground": False}, 10_000) or {}
            if probe.get("arithmetic") == 2:
                lines.append(f"✓ The active tab is {_hostname(str(probe.get('location')))} and accepts pi-chrome's commands.")
            if probe.get("webdriver"):
                lines.append("⚠ Your Chrome is reporting itself as automated to websites. Some sites use this signal to block sign-ins.")
        except BridgeError as exc:
            lines.append(f"⚠ Couldn't inspect the active tab: {exc}")
    elif version_mismatch:
        lines.append("… Skipped the remaining checks until you reload the Chrome extension.")

    return "\n".join(lines)


def _onboard() -> str:
    ext_path = os.path.join(os.path.dirname(__file__), "chrome-extension")
    return (
        "Install the pi-chrome companion extension in your normal Chrome profile:\n"
        "  1. Open chrome://extensions\n"
        "  2. Turn on 'Developer mode' (top-right).\n"
        "  3. Click 'Load unpacked' and choose this folder:\n"
        f"     {ext_path}\n"
        "  4. Keep that Chrome window open, then run /chrome doctor to confirm."
    )


def _background(bridge: ChromeProfileBridge, arg: str) -> str:
    arg = (arg or "").strip().lower()
    current = "on" if bridge.background_default else "off"
    if arg == "status":
        return f"Run in background is {current}. {_BACKGROUND_DESC[current]}"
    if arg in ("on", "true", "1"):
        bridge.background_default = True
    elif arg in ("off", "false", "0"):
        bridge.background_default = False
    elif arg in ("toggle", ""):
        bridge.background_default = not bridge.background_default
    else:
        return f"Unknown background setting '{arg}'. Pick one of: on | off | toggle | status."
    nxt = "on" if bridge.background_default else "off"
    return f"Run in background → {nxt}. {_BACKGROUND_DESC[nxt]}"


def register_all_commands(ctx, bridge: ChromeProfileBridge, auth: ChromeAuth) -> None:
    def handler(raw_args: str) -> str:
        tokens = (raw_args or "").strip().split()
        if not tokens or tokens[0] in ("help", "-h", "--help"):
            return _HELP
        sub, rest = tokens[0], " ".join(tokens[1:])
        try:
            if sub == "authorize":
                return auth.authorize(rest or None)
            if sub == "revoke":
                return auth.revoke()
            if sub == "status":
                return _status_summary(bridge, auth)
            if sub == "doctor":
                return _doctor(bridge)
            if sub == "onboard":
                return _onboard()
            if sub == "background":
                return _background(bridge, rest)
        except Exception as exc:  # noqa: BLE001
            return f"[chrome] {type(exc).__name__}: {exc}"
        return f"Unknown subcommand '{sub}'. Try: /chrome authorize | revoke | status | doctor | onboard | background."

    ctx.register_command(
        "chrome",
        handler=handler,
        description="Control the pi-chrome bridge (authorize/revoke/status/doctor/onboard/background).",
        args_hint="authorize|revoke|status|doctor|onboard|background",
    )
