"""The 21 chrome_* tools — Python port of ``registerChromeTools`` in
``pi-chrome/extensions/chrome-profile-bridge/index.ts``.

Handlers are sync (``handler(args, **kw) -> str``) and registered with
``check_fn=auth.is_authorized`` so the tools stay invisible to the agent until the
user authorizes. Each handler also calls ``auth.require_authorized()`` before
touching the bridge (defense in depth), shapes the wire params (background ->
foreground), sends through the bridge, and formats the result for the model.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any, Callable

from .auth import ChromeAuth, ChromeAuthError
from .bridge import ChromeProfileBridge, BridgeError, DEFAULT_TIMEOUT_MS
from .formatters import (
    MAX_ELEMENTS,
    format_chrome_inspect,
    format_chrome_snapshot,
    format_included_snapshot_text,
    safe_json,
    summarize_action_result,
    truncate_text,
)

_SNAPSHOT_MODES = ["auto", "interactive", "forms", "pageMap", "text", "changes", "full"]
_TAB_ACTIONS = ["list", "new", "activate", "close", "group", "ungroup", "version"]
_IMAGE_FORMATS = ["png", "jpeg"]
_WAIT_KINDS = ["selector", "expression"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _err(msg: str) -> str:
    return f"[chrome] {msg}"


def _target_props() -> dict:
    return {
        "targetId": {"type": "string", "description": "Chrome tab id to target."},
        "urlIncludes": {"type": "string", "description": "Target the tab whose URL contains this substring."},
        "titleIncludes": {"type": "string", "description": "Target the tab whose title contains this substring."},
    }


def _bg_prop() -> dict:
    return {
        "background": {
            "type": "boolean",
            "description": "If true (default), run silently without focusing Chrome; false brings Chrome to the foreground so the user can watch.",
        }
    }


def _snapshot_extra_props() -> dict:
    return {
        "includeSnapshot": {"type": "boolean", "description": "Include a fresh chrome_snapshot of the page after the action."},
        "maxElements": {"type": "number", "description": f"Max elements in the included snapshot (default {MAX_ELEMENTS})."},
    }


def _clean(args: dict) -> dict:
    """Drop None values so omitted optionals don't reach the wire as nulls."""
    return {k: v for k, v in (args or {}).items() if v is not None}


def _wire(bridge: ChromeProfileBridge, args: dict, *, background_aware: bool = True) -> dict:
    """Build wire params: cleaned args + foreground flag (from background or session default)."""
    params = _clean(args)
    if background_aware:
        background = params.pop("background", None)
        if background is None:
            background = bridge.background_default
        params["foreground"] = not background
    return params


def _send(auth: ChromeAuth, bridge: ChromeProfileBridge, action: str, params: dict, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> Any:
    auth.require_authorized()
    # page.* interactions join a per-session tab group, mirroring pi's auto-grouping.
    if action.startswith("page.") and "sessionGroupTitle" not in params:
        params = {**params, "sessionGroupTitle": "Hermes", "joinSessionGroup": True}
    return bridge.send(action, params, timeout_ms)


def _guard(fn: Callable[[dict], str]) -> Callable[..., str]:
    def wrapper(args: dict | None = None, **_kw: Any) -> str:
        try:
            return fn(args or {})
        except ChromeAuthError as exc:
            return _err(str(exc))
        except BridgeError as exc:
            return _err(str(exc))
        except Exception as exc:  # noqa: BLE001 — never raise out of a tool handler
            return _err(f"{type(exc).__name__}: {exc}")
    return wrapper


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_all_tools(ctx, bridge: ChromeProfileBridge, auth: ChromeAuth) -> None:
    def add(name: str, description: str, properties: dict, handler: Callable[[dict], str], *, required: list | None = None, emoji: str = "🌐") -> None:
        ctx.register_tool(
            name=name,
            toolset="hermes-chrome-plugin",
            schema={"type": "object", "properties": properties, "required": required or []},
            handler=_guard(handler),
            check_fn=auth.is_authorized,
            emoji=emoji,
        )

    # -- chrome_launch -----------------------------------------------------
    def h_launch(args: dict) -> str:
        url = args.get("url")
        if url and bridge.connected:
            _send(auth, bridge, "tab.new", _wire(bridge, {"url": url}, background_aware=False))
            return f"Chrome bridge connected; opened {url}"
        ext_path = os.path.join(os.path.dirname(__file__), "chrome-extension")
        status = "connected" if bridge.connected else "waiting for extension"
        return (
            f"Chrome profile bridge is listening at {bridge.url}.\n\n"
            "To connect your existing Chrome profile:\n"
            "1. Open chrome://extensions in the Chrome profile you normally use.\n"
            "2. Enable Developer mode.\n"
            '3. Click "Load unpacked".\n'
            f"4. Select: {ext_path}\n\n"
            f"Status: {status}."
        )

    add(
        "chrome_launch",
        "Start/check the local bridge used by the companion Chrome extension. Does not launch a separate Chrome profile; load the unpacked chrome-extension folder in your existing Chrome to connect.",
        {
            "url": {"type": "string", "description": "Optional URL to open after the extension is connected."},
            "port": {"type": "number", "description": "Ignored. The bundled extension polls 127.0.0.1:16319."},
            "userDataDir": {"type": "string", "description": "Ignored."},
            "useDefaultProfile": {"type": "boolean", "description": "Ignored."},
            "headless": {"type": "boolean", "description": "Ignored."},
        },
        h_launch,
    )

    # -- chrome_tab --------------------------------------------------------
    def h_tab(args: dict) -> str:
        action = args.get("action")
        forwarded = _clean(args)
        if action in ("new", "group") and forwarded.get("groupTitle") is None and args.get("group") is not False:
            forwarded["groupTitle"] = "Hermes"
        result = _send(auth, bridge, f"tab.{action}", forwarded)
        if action == "list" and isinstance(result, list):
            rows = []
            for tab in result:
                group = (tab.get("group") or {}).get("title") if isinstance(tab.get("group"), dict) else None
                active = "*" if tab.get("active") else " "
                title = tab.get("title") or "(untitled)"
                rows.append(f"{tab.get('id')}\t{active}\t{('[' + group + '] ') if group else ''}{title}\t{tab.get('url')}")
            return "\n".join(rows) or "No tabs."
        return safe_json(result)

    add(
        "chrome_tab",
        "List, create, activate, close, group, ungroup, or inspect tabs in the user's existing Chrome profile.",
        {
            "action": {"type": "string", "enum": _TAB_ACTIONS},
            "url": {"type": "string", "description": "URL for action=new."},
            "targetId": {"type": "string", "description": "Chrome tab id for activate/close/group/ungroup."},
            "urlIncludes": {"type": "string", "description": "Match the target tab by URL substring."},
            "titleIncludes": {"type": "string", "description": "Match the target tab by title substring."},
            "group": {"type": "boolean", "description": "action=new only: pass false to open an ungrouped tab."},
            "groupTitle": {"type": "string", "description": "Tab group title for action=group/new."},
            "groupColor": {"type": "string", "description": "Tab group color: grey, blue, red, yellow, green, pink, purple, cyan, or orange."},
        },
        h_tab,
        required=["action"],
    )

    # -- chrome_snapshot ---------------------------------------------------
    def h_snapshot(args: dict) -> str:
        params = _wire(bridge, args)
        params["maxElements"] = args.get("maxElements") or MAX_ELEMENTS
        snapshot = _send(auth, bridge, "page.snapshot", params)
        return format_chrome_snapshot(snapshot)

    add(
        "chrome_snapshot",
        "Inspect a page in the user's existing Chrome profile. Returns a concise, agent-friendly observation: structural layout/context, stable uids, visible actions, form fields, page hints, and changes since the previous snapshot. Use mode/query/nearUid to zoom instead of dumping the whole page.",
        {
            **_target_props(),
            "maxElements": {"type": "number", "description": f"Default {MAX_ELEMENTS}."},
            "mode": {"type": "string", "enum": _SNAPSHOT_MODES},
            "query": {"type": "string", "description": "Find/rank elements, regions, and text matching this phrase, e.g. 'merge button'."},
            "maxTextChars": {"type": "number", "description": "Max body text chars included."},
            "containingText": {"type": "string", "description": "Only return elements whose label/text contains this string (case-insensitive)."},
            "roleFilter": {"type": "string", "description": "Only return elements matching this ARIA role or tag (e.g. 'button', 'link', 'textbox')."},
            "nearUid": {"type": "string", "description": "Sort elements by proximity to this snapshot uid."},
            **_bg_prop(),
        },
        h_snapshot,
    )

    # -- chrome_find -------------------------------------------------------
    def h_find(args: dict) -> str:
        params = _wire(bridge, args)
        params["mode"] = args.get("mode") or "auto"
        params["maxElements"] = args.get("maxElements") or MAX_ELEMENTS
        snapshot = _send(auth, bridge, "page.snapshot", params)
        return format_chrome_snapshot(snapshot)

    add(
        "chrome_find",
        "Find elements, page regions, or text on the current Chrome page by query. Returns ranked matches with stable uids and coordinates. A focused wrapper around chrome_snapshot({query}).",
        {
            "query": {"type": "string", "description": "What to find, e.g. 'merge button', 'email error', 'search box'."},
            "mode": {"type": "string", "enum": _SNAPSHOT_MODES},
            "maxElements": {"type": "number", "description": f"Default {MAX_ELEMENTS}."},
            **_target_props(),
            **_bg_prop(),
        },
        h_find,
        required=["query"],
    )

    # -- chrome_inspect ----------------------------------------------------
    def h_inspect(args: dict) -> str:
        try:
            inspect = _send(auth, bridge, "page.inspect", _wire(bridge, args))
            return format_chrome_inspect(inspect)
        except BridgeError as exc:
            if "Unknown action: page.inspect" not in str(exc):
                raise
            # Compatibility fallback for an extension service worker not reloaded since
            # chrome_inspect was added.
            fallback_args = {**args, "mode": "interactive", "maxElements": MAX_ELEMENTS, "nearUid": args.get("uid"), "query": args.get("selector")}
            snapshot = _send(auth, bridge, "page.snapshot", _wire(bridge, fallback_args))
            return (
                "chrome_inspect fallback: loaded Chrome extension does not yet support page.inspect; "
                "reload it at chrome://extensions for deep inspect.\n\n" + format_chrome_snapshot(snapshot)
            )

    add(
        "chrome_inspect",
        "Inspect one snapshot uid or selector deeply: nearby text, nearby actions, form context, ancestors, and suggested click target. Use after chrome_snapshot/chrome_find.",
        {
            "uid": {"type": "string", "description": "Stable element uid from chrome_snapshot/chrome_find."},
            "selector": {"type": "string", "description": "CSS selector if uid is unavailable."},
            "scrollIntoView": {"type": "boolean", "description": "Scroll the target into view before inspecting (default false)."},
            **_target_props(),
            **_bg_prop(),
        },
        h_inspect,
    )

    # -- chrome_navigate ---------------------------------------------------
    def h_navigate(args: dict) -> str:
        timeout = int(args.get("timeoutMs") or 15_000)
        _send(auth, bridge, "page.navigate", _wire(bridge, args), timeout + 2_000)
        suffix = " (with initScript)" if args.get("initScript") else ""
        return f"Navigated to {args.get('url')}{suffix}"

    add(
        "chrome_navigate",
        "Navigate an existing Chrome tab to a URL. Optionally waits for load completion. Supports an initScript that runs at document_start (MAIN world) for the next navigation.",
        {
            "url": {"type": "string"},
            **_target_props(),
            "waitUntilLoad": {"type": "boolean", "description": "Default true."},
            "timeoutMs": {"type": "number", "description": "Default 15000."},
            "initScript": {"type": "string", "description": "JS to run at document_start in MAIN world for the next navigation (seed localStorage, stub Date.now, etc.)."},
            **_bg_prop(),
        },
        h_navigate,
        required=["url"],
    )

    # -- chrome_evaluate ---------------------------------------------------
    def h_evaluate(args: dict) -> str:
        value = _send(auth, bridge, "page.evaluate", _wire(bridge, args))
        if value is None:
            text = "null"
        elif isinstance(value, str):
            text = value
        else:
            text = safe_json(value)
        return truncate_text(text)

    add(
        "chrome_evaluate",
        "Evaluate JavaScript in an existing Chrome tab (MAIN world via CDP Runtime.evaluate; not subject to page CSP). Returns JSON-serializable values when possible.",
        {
            "expression": {"type": "string"},
            "awaitPromise": {"type": "boolean", "description": "Default true."},
            **_target_props(),
            **_bg_prop(),
        },
        h_evaluate,
        required=["expression"],
    )

    # -- interaction tools with includeSnapshot ---------------------------
    def _interaction(action: str, args: dict, describe: Callable[[dict, Any], str]) -> str:
        raw = _send(auth, bridge, action, _wire(bridge, args))
        result = raw.get("result") if (args.get("includeSnapshot") and isinstance(raw, dict)) else raw
        summary = summarize_action_result(result)
        text = describe(args, summary)
        return format_included_snapshot_text(raw, text)

    def h_click(args: dict) -> str:
        def describe(a: dict, summary: str | None) -> str:
            target = a.get("uid") or a.get("selector") or f"{a.get('x')},{a.get('y')}"
            return f"Clicked {target} — {summary}" if summary else f"Clicked {target}"
        return _interaction("page.click", args, describe)

    add(
        "chrome_click",
        "Click a snapshot uid, CSS selector, or viewport coordinate using Chrome's real input layer. Pass includeSnapshot=true to return a fresh snapshot after the click.",
        {
            "uid": {"type": "string", "description": "Stable element uid from chrome_snapshot. Prefer uid over selector."},
            "selector": {"type": "string", "description": "CSS selector to click."},
            "x": {"type": "number", "description": "Viewport x if uid/selector omitted."},
            "y": {"type": "number", "description": "Viewport y if uid/selector omitted."},
            "domFallback": {"type": "boolean", "description": "Fall back to a DOM-dispatched click if CDP input is blocked (default true)."},
            **_snapshot_extra_props(),
            **_target_props(),
            **_bg_prop(),
        },
        h_click,
    )

    def h_type(args: dict) -> str:
        def describe(a: dict, summary: str | None) -> str:
            into = f" into {a.get('uid') or a.get('selector')}" if (a.get("uid") or a.get("selector")) else ""
            base = f"Typed {len(a.get('text') or '')} character(s){into}."
            return f"{base} ({summary})" if summary else base
        return _interaction("page.type", args, describe)

    add(
        "chrome_type",
        "Focus an optional snapshot uid or CSS selector, then type text using Chrome's real keyboard input.",
        {
            "text": {"type": "string"},
            "uid": {"type": "string", "description": "Stable element uid from chrome_snapshot."},
            "selector": {"type": "string", "description": "CSS selector to focus before typing."},
            "pressEnter": {"type": "boolean"},
            **_snapshot_extra_props(),
            **_target_props(),
            **_bg_prop(),
        },
        h_type,
        required=["text"],
    )

    def h_fill(args: dict) -> str:
        def describe(a: dict, summary: str | None) -> str:
            into = f" into {a.get('uid') or a.get('selector')}" if (a.get("uid") or a.get("selector")) else ""
            base = f"Filled {len(a.get('text') or '')} character(s){into}."
            return f"{base} ({summary})" if summary else base
        return _interaction("page.fill", args, describe)

    add(
        "chrome_fill",
        "Set the full value of a text input, textarea, or contenteditable using Chrome click/select/delete/type. Pass submit=true to press Enter after.",
        {
            "text": {"type": "string"},
            "uid": {"type": "string", "description": "Stable element uid from chrome_snapshot."},
            "selector": {"type": "string", "description": "CSS selector to fill if uid is omitted."},
            "submit": {"type": "boolean", "description": "Press Enter after filling."},
            "domFallback": {"type": "boolean", "description": "Fall back to DOM value-setting if CDP input is blocked (default true)."},
            **_snapshot_extra_props(),
            **_target_props(),
            **_bg_prop(),
        },
        h_fill,
        required=["text"],
    )

    def h_key(args: dict) -> str:
        def describe(a: dict, summary: str | None) -> str:
            base = f"Pressed {a.get('key')}."
            return f"{base} ({summary})" if summary else base
        return _interaction("page.key", args, describe)

    add(
        "chrome_key",
        "Send a keyboard key to an existing Chrome tab (Enter, Escape, Tab, Backspace, Delete, ArrowUp/Down/Left/Right, or one character), optionally with modifiers.",
        {
            "key": {"type": "string"},
            "modifiers": {
                "type": "object",
                "description": "Modifier keys to hold (chord).",
                "properties": {
                    "shiftKey": {"type": "boolean"},
                    "ctrlKey": {"type": "boolean"},
                    "altKey": {"type": "boolean"},
                    "metaKey": {"type": "boolean"},
                },
            },
            **_snapshot_extra_props(),
            **_target_props(),
            **_bg_prop(),
        },
        h_key,
        required=["key"],
    )

    # -- chrome_wait_for ---------------------------------------------------
    def h_wait_for(args: dict) -> str:
        timeout = int(args.get("timeoutMs") or 10_000)
        _send(auth, bridge, "page.waitFor", _clean(args), timeout + 2_000)
        return f"Observed {args.get('kind')}: {args.get('value')}"

    add(
        "chrome_wait_for",
        "Poll an existing Chrome tab until a selector exists or a JavaScript expression returns truthy.",
        {
            "kind": {"type": "string", "enum": _WAIT_KINDS},
            "value": {"type": "string", "description": "CSS selector when kind=selector; JS expression when kind=expression."},
            "timeoutMs": {"type": "number", "description": "Default 10000."},
            "intervalMs": {"type": "number", "description": "Default 250."},
            **_target_props(),
        },
        h_wait_for,
        required=["kind", "value"],
    )

    # -- observation tools -------------------------------------------------
    def _observe(action: str, args: dict) -> str:
        result = _send(auth, bridge, action, _wire(bridge, args))
        return truncate_text(safe_json(result))

    add(
        "chrome_list_console_messages",
        "List console messages captured in the page. Capture starts after any snapshot/evaluate/console/network call installs page instrumentation.",
        {"clear": {"type": "boolean", "description": "Clear the captured console log after reading."}, **_target_props(), **_bg_prop()},
        lambda args: _observe("page.console.list", args),
    )

    add(
        "chrome_list_network_requests",
        "List fetch/XMLHttpRequest activity captured in the page. Use includePreservedRequests=true to keep requests from earlier same-tab navigations.",
        {
            "includePreservedRequests": {"type": "boolean", "description": "Include captured requests from earlier locations in the same tab/session."},
            "clear": {"type": "boolean", "description": "Clear the captured request log after reading."},
            **_target_props(),
            **_bg_prop(),
        },
        lambda args: _observe("page.network.list", args),
    )

    add(
        "chrome_get_network_request",
        "Retrieve one captured fetch/XMLHttpRequest entry (including response body when available) by requestId from chrome_list_network_requests.",
        {"requestId": {"type": "string", "description": "Request id from chrome_list_network_requests."}, **_target_props(), **_bg_prop()},
        lambda args: _observe("page.network.get", args),
        required=["requestId"],
    )

    # -- chrome_screenshot -------------------------------------------------
    def h_screenshot(args: dict) -> str:
        fmt = args.get("format") or "png"
        cwd = os.getcwd()
        default_dir = os.path.join(cwd, ".hermes-chrome-screenshots")
        stamp = time.strftime("%Y-%m-%dT%H-%M-%S")
        out_path = os.path.abspath(args.get("path")) if args.get("path") else os.path.join(default_dir, f"{stamp}.{fmt}")
        timeout = 120_000 if args.get("fullPage") else DEFAULT_TIMEOUT_MS
        result = _send(auth, bridge, "page.screenshot", _wire(bridge, args), timeout)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        def _decode(data_url: str) -> bytes:
            comma = data_url.find(",")
            return base64.b64decode(data_url[comma + 1:] if comma >= 0 else data_url)

        if isinstance(result, dict) and result.get("fullPage") and result.get("tiles") and result.get("dimensions"):
            dims = result["dimensions"]
            manifest = []
            for i, tile in enumerate(result["tiles"]):
                tile_path = out_path.replace(f".{fmt}", f"-tile{i}.{fmt}")
                with open(tile_path, "wb") as fh:
                    fh.write(_decode(tile.get("dataUrl", "")))
                manifest.append({"path": tile_path, "y": tile.get("y")})
            with open(out_path + ".json", "w", encoding="utf-8") as fh:
                fh.write(safe_json({**dims, "tiles": manifest}))
            return f"Saved {len(result['tiles'])} full-page tile(s) for {dims.get('width')}×{dims.get('height')}px page. Manifest: {out_path}.json"

        data_url = result.get("dataUrl") if isinstance(result, dict) else None
        if not data_url:
            raise BridgeError("Screenshot returned no dataUrl")
        with open(out_path, "wb") as fh:
            fh.write(_decode(data_url))
        return f"Saved Chrome screenshot to {out_path}"

    add(
        "chrome_screenshot",
        "Capture a screenshot of an existing Chrome tab and save it to disk. The target tab is briefly activated within its window for the capture, then restored.",
        {
            "path": {"type": "string", "description": "Output path. Defaults to .hermes-chrome-screenshots/<timestamp>.<format>."},
            "format": {"type": "string", "enum": _IMAGE_FORMATS},
            "quality": {"type": "number", "description": "JPEG quality 0-100."},
            "fullPage": {"type": "boolean", "description": "Best-effort full-page tiles (viewport capture if unsupported)."},
            **_target_props(),
            **_bg_prop(),
        },
        h_screenshot,
        emoji="📸",
    )

    # -- gesture tools -----------------------------------------------------
    def h_hover(args: dict) -> str:
        _send(auth, bridge, "page.hover", _wire(bridge, args))
        target = args.get("uid") or args.get("selector") or f"{args.get('x')},{args.get('y')}"
        return f"Hovered {target}"

    add(
        "chrome_hover",
        "Hover over an element by uid, selector, or x/y using Chrome pointer movement.",
        {
            "uid": {"type": "string"},
            "selector": {"type": "string"},
            "x": {"type": "number"},
            "y": {"type": "number"},
            **_target_props(),
            **_bg_prop(),
        },
        h_hover,
    )

    def h_drag(args: dict) -> str:
        _send(auth, bridge, "page.drag", _wire(bridge, args))
        return f"Dragged from {args.get('fromUid') or args.get('fromSelector')} to {args.get('toUid') or args.get('toSelector')}"

    add(
        "chrome_drag",
        "Drag from one uid/selector/point to another using Chrome pointer input.",
        {
            "fromUid": {"type": "string"}, "fromSelector": {"type": "string"},
            "fromX": {"type": "number"}, "fromY": {"type": "number"},
            "toUid": {"type": "string"}, "toSelector": {"type": "string"},
            "toX": {"type": "number"}, "toY": {"type": "number"},
            "steps": {"type": "number", "description": "Default 12."},
            **_target_props(),
            **_bg_prop(),
        },
        h_drag,
    )

    def h_tap(args: dict) -> str:
        _send(auth, bridge, "page.tap", _wire(bridge, args))
        target = args.get("uid") or args.get("selector") or f"{args.get('x')},{args.get('y')}"
        return f"Tapped {target} (touch)"

    add(
        "chrome_tap",
        "Dispatch a real touchstart/touchend tap through Chrome's input layer (for sites gating on TouchEvent).",
        {
            "uid": {"type": "string"},
            "selector": {"type": "string"},
            "x": {"type": "number"},
            "y": {"type": "number"},
            **_target_props(),
            **_bg_prop(),
        },
        h_tap,
    )

    def h_scroll(args: dict) -> str:
        _send(auth, bridge, "page.scroll", _wire(bridge, args))
        return f"Scrolled dy={args.get('deltaY') or 0} dx={args.get('deltaX') or 0}"

    add(
        "chrome_scroll",
        "Scroll the page or a scrollable element via real wheel events. Positive deltaY scrolls down. Pass uid/selector to scroll within a container.",
        {
            "uid": {"type": "string"},
            "selector": {"type": "string"},
            "deltaY": {"type": "number", "description": "Pixels to scroll vertically. Positive = down."},
            "deltaX": {"type": "number", "description": "Pixels to scroll horizontally. Positive = right."},
            "steps": {"type": "number", "description": "Number of wheel events. Defaults to ceil(|deltaY|/100)."},
            **_target_props(),
            **_bg_prop(),
        },
        h_scroll,
    )

    # -- chrome_upload_file ------------------------------------------------
    def h_upload(args: dict) -> str:
        cwd = os.getcwd()
        paths = [os.path.abspath(os.path.join(cwd, p)) for p in (args.get("paths") or [])]
        _send(auth, bridge, "page.upload", _wire(bridge, {**args, "paths": paths}))
        return f"Uploaded {len(paths)} file(s) to {args.get('uid') or args.get('selector')}"

    add(
        "chrome_upload_file",
        "Attach local files to an <input type=file> using Chrome DevTools file-input control. Does NOT open the native file picker; works with controlled inputs.",
        {
            "uid": {"type": "string"},
            "selector": {"type": "string"},
            "paths": {"type": "array", "items": {"type": "string"}, "description": "Local file paths to upload."},
            **_target_props(),
            **_bg_prop(),
        },
        h_upload,
        required=["paths"],
        emoji="📎",
    )
