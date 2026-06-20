# hermes-chrome-plugin

English | [简体中文](README.zh-CN.md)

Let Hermes Agent drive your real, signed-in Chrome browser — with all your cookies, sessions, and extensions — through a companion Chrome extension over a local loopback bridge.

## What It Does

When you chat with Hermes, the Agent can directly control your Chrome just as you would. Typical use cases:

- **Authenticated operations** — Check Gmail, read GitHub Issues, access internal company dashboards, all with your existing login state
- **Web automation** — Fill forms, click buttons, paginate, upload files — the Agent does it for you
- **Data extraction** — Scrape content behind login walls, monitor network requests, read console logs
- **Screenshot analysis** — Capture page screenshots, inspect rendered output

> 💡 For simple page fetches that don't need authentication, use Hermes' built-in `web_search` / `web_fetch`. Need login state? Use `chrome_*`.

## Installation

### 1. Install the Plugin

In your Hermes terminal:

```bash
hermes plugins install waitkeeper/hermes-chrome-plugin
hermes plugins enable hermes-chrome-plugin
hermes gateway restart
```

### 2. Load the Chrome Extension

1. Open Chrome and go to `chrome://extensions`
2. Toggle **Developer mode** (top right)
3. Click **Load unpacked**
4. Select the `chrome-extension/` folder inside the plugin directory:
   ```
   ~/.hermes/plugins/hermes-chrome-plugin/chrome-extension/
   ```
5. Confirm **"Hermes Chrome Connector"** appears in the extensions panel

### 3. Verify

Keep Chrome open, then in Hermes run:

```
/chrome doctor
```

You should see `✓ Chrome is connected`.

## Using in Hermes

### Authorization (Required)

Chrome control is **locked by default** for security. Authorize before the Agent can use `chrome_*` tools:

```
/chrome authorize
```

| Command | Description |
|---------|-------------|
| `/chrome authorize` | Authorize for 30 minutes (default) |
| `/chrome authorize 2h` | Authorize for N minutes |
| `/chrome authorize indefinite` | Permanent (trusted devices only) |
| `/chrome revoke` | Immediately revoke access |
| `/chrome status` | Show current state |

Once authorized, just talk to Hermes in **natural language** — the Agent picks the right `chrome_*` tool automatically.

### Example Conversations

```
You: Open GitHub and show me my unread notifications
You: Log into the admin panel and export yesterday's data as CSV
You: Go to https://example.com, find the cheapest item and screenshot it
You: Fill out this form using data from ~/data/info.json
```

The Agent handles: open Chrome → navigate → read the page → click, type, screenshot → return results.

### Background Mode

By default, the Agent operates Chrome silently in the background (no pop-ups, no focus stealing). To **watch the Agent work**:

```
/chrome background off
```

Restore background mode:

```
/chrome background on
```

### Troubleshooting

```
/chrome doctor     # Full diagnostic: connection, extension version, permissions
/chrome status     # One-line summary: connection + auth + background state
/chrome onboard    # Re-display extension installation guide
```

## Tool Reference

The plugin provides **21 `chrome_*` tools**. The Agent selects the right one automatically:

| Tool | Purpose |
|------|---------|
| `chrome_navigate` | Open/navigate to a URL |
| `chrome_snapshot` | Capture page element tree with stable UIDs |
| `chrome_click` | Click a page element (button, link, etc.) |
| `chrome_type` | Type text into an input field |
| `chrome_fill` | Batch fill form fields |
| `chrome_find` | Find elements by text or selector |
| `chrome_inspect` | Inspect element details |
| `chrome_evaluate` | Execute JavaScript in the page |
| `chrome_screenshot` | Capture a page screenshot |
| `chrome_scroll` | Scroll the page (triggers lazy loading) |
| `chrome_wait_for` | Wait for an element to appear |
| `chrome_hover` | Mouse hover |
| `chrome_drag` | Drag and drop |
| `chrome_tap` | Touch tap (mobile emulation) |
| `chrome_key` | Send keyboard events (shortcuts, etc.) |
| `chrome_upload_file` | Upload local files |
| `chrome_tab` | Tab management (open/close/switch) |
| `chrome_list_console_messages` | Read browser console output |
| `chrome_list_network_requests` | Monitor network requests |
| `chrome_get_network_request` | Inspect a specific network request |

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HERMES_CHROME_AUTHORIZE` | unset | Standing authorization (for web-UI / headless environments) |
| `HERMES_CHROME_BRIDGE_HOST` | `127.0.0.1` | Bridge server bind address |
| `HERMES_CHROME_BRIDGE_PORT` | `16319` | Bridge server port |

### Config File (`~/.hermes/config.yaml`)

```yaml
# Permanent authorization (skip /chrome authorize)
hermes_chrome_plugin:
  authorize: indefinite
```

## How It Works

```
  Hermes Agent                        Chrome Browser
  ┌─────────────────┐     HTTP        ┌──────────────────────┐
  │ hermes-chrome-  │◄───127.0.0.1───│ Hermes Chrome        │
  │ plugin (Python) │    :16319       │ Connector (extension)│
  │                 │                 │  ├ service_worker.js │
  │ bridge.py       │                 │  └ snapshot_injected │
  │ tools.py        │                 │    .js (page inject) │
  └─────────────────┘                 └──────────────────────┘
```

- Agent conversation → Hermes calls `chrome_*` tools → tools send HTTP to Chrome extension via bridge
- Extension controls pages through Chrome DevTools Protocol (click, type, read, screenshot)
- All communication stays on `127.0.0.1` loopback — nothing leaves the machine

## License

MIT
