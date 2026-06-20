# hermes-chrome-plugin

Drive your real, signed-in Chrome profile from Hermes — cookies, sessions, extensions and all — through a companion Chrome extension over a loopback-only bridge.

Ported from the [Pi coding agent](https://github.com/NousResearch/hermes-agent) Chrome extension (`pi-chrome/extensions/chrome-profile-bridge/`).

## Features

Provides **21 `chrome_*` tools** for full browser control:

| Tool | Description |
|------|-------------|
| `chrome_launch` | Open/activate Chrome |
| `chrome_tab` | Tab management (list, create, close, switch) |
| `chrome_snapshot` | Accessibility snapshot with stable `uid`s |
| `chrome_find` | Find elements by text/selector |
| `chrome_inspect` | Inspect a specific element |
| `chrome_navigate` | Navigate to URL |
| `chrome_evaluate` | Execute JavaScript in page |
| `chrome_click` | Click element by uid/selector |
| `chrome_type` | Type text into element |
| `chrome_fill` | Fill form fields |
| `chrome_key` | Send keyboard events |
| `chrome_wait_for` | Wait for element to appear |
| `chrome_hover` | Hover over element |
| `chrome_drag` | Drag and drop |
| `chrome_tap` | Touch tap |
| `chrome_scroll` | Scroll page |
| `chrome_upload_file` | Upload files via file input |
| `chrome_screenshot` | Capture page screenshot |
| `chrome_list_console_messages` | Read console output |
| `chrome_list_network_requests` | Monitor network activity |
| `chrome_get_network_request` | Inspect a specific request |

## Installation

```bash
hermes plugins install waitkeeper/hermes-chrome-plugin
hermes plugins enable hermes-chrome-plugin
hermes gateway restart
```

Then load the companion Chrome extension:

1. Open `chrome://extensions`
2. Enable **Developer mode**
3. Click **Load unpacked**
4. Select the `chrome-extension/` directory inside the plugin

## Usage

Chrome control is **locked by default** for security. Authorize it:

```
/chrome authorize
```

Options:
- `/chrome authorize indefinite` — never expire
- `/chrome authorize 30m` — expire after 30 minutes (default)
- `/chrome authorize 45` — expire after N seconds
- `/chrome revoke` — lock immediately
- `/chrome status` — check connection and auth state
- `/chrome doctor` — full health check
- `/chrome background on|off` — toggle background mode

## Standing Authorization

For headless or web-UI environments where `/chrome` commands aren't available:

```bash
export HERMES_CHROME_AUTHORIZE=indefinite
```

Or in `~/.hermes/config.yaml`:

```yaml
hermes_chrome_plugin:
  authorize: indefinite
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HERMES_CHROME_AUTHORIZE` | (unset) | Standing authorization grant |
| `HERMES_CHROME_BRIDGE_HOST` | `127.0.0.1` | Bridge server bind address |
| `HERMES_CHROME_BRIDGE_PORT` | `17318` | Bridge server port |

## Architecture

```
Hermes Agent                    Chrome
┌──────────────┐    HTTP       ┌─────────────────┐
│ bridge.py    │◄────127.0.0.1─│ service_worker  │
│ (Python)     │   :17318      │ (Manifest V3)   │
├──────────────┤               ├─────────────────┤
│ tools.py     │               │ snapshot_       │
│ commands.py  │               │ injected.js     │
│ auth.py      │               │ (page MAIN)     │
└──────────────┘               └─────────────────┘
```

The bridge runs as a local HTTP server. The Chrome extension connects to it. All communication stays on `127.0.0.1` — nothing leaves the machine.

## License

MIT
