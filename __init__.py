"""hermes-chrome-plugin for Hermes — drive your real, signed-in Chrome profile.

Ported from the Pi extension at
``pi-chrome/extensions/chrome-profile-bridge/``. The Chrome companion extension
(``chrome-extension/``) is reused verbatim; only the Hermes-side (bridge, tools,
commands, formatters, auth) is reimplemented in Python here.

Wiring (see plugin.yaml for the manifest):
  * bridge  — loopback HTTP broker, started lazily on first use (its own thread).
  * auth    — locked by default; chrome_* tools gated by check_fn=is_authorized.
  * tools   — 21 chrome_* tools.
  * command — /chrome authorize|revoke|status|doctor|onboard|background.
  * primer  — pre_llm_call injects usage guidance (first turn, only once authorized).
  * cleanup — process exit stops the bridge.

Module-name note: the plugin directory is ``hermes-chrome-plugin`` (hyphenated,
not a valid Python package name), so all intra-plugin imports are relative.
"""

from __future__ import annotations

import atexit

from .auth import ChromeAuth
from .bridge import ChromeProfileBridge
from .commands import register_all_commands
from .tools import register_all_tools

_CHROME_PRIMER = """\
<hermes-chrome-plugin>
Chrome control is available through the chrome_* tools via a companion Chrome extension running in the user's normal, signed-in Chrome profile (real cookies/sessions; no remote-debug port, no throwaway profile).

When to use which:
- Use chrome_* when the task needs the user's existing logins/state (Gmail, GitHub, Linear, internal apps).
- For one-off fetches that don't need a logged-in session, the generic browser_*/web_search tools may be simpler.

Capability notes:
- Interactive controls (click/type/fill/key/hover/drag/scroll/tap) use Chrome's real input layer via CDP; they satisfy normal user-activation gates.
- chrome_evaluate and chrome_snapshot run in MAIN world via CDP Runtime.evaluate and are NOT subject to the page's CSP (work on strict-CSP sites).

Usage rules:
1. chrome_snapshot before clicking/typing; prefer the stable `uid` over `selector`.
2. Pass includeSnapshot=true on click/type/fill/key to verify state in one round trip.
3. chrome_* run in the background by default; pass background=false (or /chrome background off) when the user wants to watch.
4. If a chrome_* tool reports Chrome control is locked, ask the user to authorize (/chrome authorize, or the UI button); the agent cannot authorize itself.
5. Run /chrome doctor when in doubt about connectivity.
</chrome-profile-bridge>"""


def _apply_standing_grant(auth: ChromeAuth) -> None:
    """Honor an opt-in standing authorization from env or profile config."""
    import os

    grant = (os.environ.get("HERMES_CHROME_AUTHORIZE") or "").strip()
    if not grant:
        try:
            from hermes_cli.config import load_config

            cfg = load_config() or {}
            grant = str(((cfg.get("hermes_chrome_plugin") or {}).get("authorize")) or "").strip()
        except Exception:
            grant = ""
    if grant:
        auth.authorize(grant)


def register(ctx) -> None:
    bridge = ChromeProfileBridge()
    auth = ChromeAuth()

    # Standing authorization grant (opt-in). Hosts that don't surface plugin slash
    # commands — notably hermes-web-ui, whose bridge only dispatches goal/subgoal/
    # skill commands, never plugin commands — have no way to run /chrome authorize.
    # For those, the user grants control out-of-band by setting it in their own
    # profile (which IS the human consent): either
    #   * env var  HERMES_CHROME_AUTHORIZE=indefinite|30m|45 , or
    #   * config.yaml:  hermes_chrome_plugin:\n    authorize: indefinite
    # Default (unset) keeps Chrome control LOCKED — the security model is unchanged
    # unless the operator explicitly opts in.
    _apply_standing_grant(auth)

    # Tools (gated by auth via check_fn) and the /chrome command.
    register_all_tools(ctx, bridge, auth)
    register_all_commands(ctx, bridge, auth)

    # Primer: inject usage guidance once per session (first turn), only while
    # authorized — keeps it out of the prompt when chrome_* tools are hidden, and
    # avoids re-paying the tokens every turn. pre_llm_call return {"context": ...}
    # is appended to the user message (ephemeral; preserves the system-prompt cache).
    def _inject_primer(is_first_turn: bool = False, **_kw):
        if not is_first_turn or not auth.is_authorized():
            return None
        return {"context": _CHROME_PRIMER}

    ctx.register_hook("pre_llm_call", _inject_primer)

    # Cleanup: stop the bridge when the owning Python process exits. Do not tie
    # this to on_session_end; Hermes fires that hook after every conversation
    # turn, so one chat session could stop another session's active bridge.
    atexit.register(bridge.stop)
