"""Snapshot/inspect/result formatting — Python port of the TS formatters in
``hermes-chrome-plugin/chrome-extension/`` (originally from pi-chrome).

These turn the raw JSON produced by the in-page ``snapshot_injected.js`` into the
concise, agent-friendly text the model reads. The field shapes are owned by the
Chrome extension; as long as the extension is unchanged, this output is stable.
"""

from __future__ import annotations

import json
import re
from typing import Any

MAX_TEXT_CHARS = 30_000
MAX_ELEMENTS = 80

_WS_RE = re.compile(r"\s+")


def truncate_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n\n[truncated {len(text) - max_chars} characters]"


def safe_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def compact_line(value: Any, max_len: int = 140) -> str:
    text = _WS_RE.sub(" ", str(value if value is not None else "")).strip()
    return f"{text[: max_len - 1]}…" if len(text) > max_len else text


def rect_text(rect: Any) -> str:
    if not rect:
        return "?"
    return f"{rect.get('x')},{rect.get('y')} {rect.get('width')}x{rect.get('height')}"


def _get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Safe nested dict get: _get(d, 'a', 'b') -> d['a']['b'] or default."""
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def format_chrome_snapshot(snapshot: Any) -> str:
    if not isinstance(snapshot, dict):
        return safe_json(snapshot)
    if snapshot.get("mode") == "full":
        return truncate_text(safe_json(snapshot))

    lines: list[str] = []
    mode = snapshot.get("mode")
    lines.append(f"# Chrome snapshot{f' ({mode})' if mode else ''}")
    lines.append(f"{snapshot.get('title') or '(untitled)'}")
    if snapshot.get("url"):
        lines.append(f"{snapshot['url']}")
    vp = snapshot.get("viewport")
    if vp:
        lines.append(
            f"viewport={vp.get('width')}x{vp.get('height')} "
            f"scroll={vp.get('scrollX') or 0},{vp.get('scrollY') or 0}"
        )

    summary = snapshot.get("summary") or {}
    if summary.get("modal"):
        m = summary["modal"]
        lines.append(f"modal: {m.get('uid')} {compact_line(m.get('label'))}")
    if summary.get("focused"):
        f = summary["focused"]
        lines.append(f"focused: {f.get('uid')} {f.get('role') or ''} {compact_line(f.get('label'))}")
    hints = summary.get("hints")
    if isinstance(hints, list) and hints:
        lines.append("\n## Hints")
        for hint in hints[:6]:
            lines.append(f"- {hint}")

    diff = snapshot.get("diff")
    if diff and not diff.get("firstSnapshot"):
        changed: list[str] = []
        for c in diff.get("changes") or []:
            if c.get("kind") == "textChanged":
                changed.append("text changed")
            else:
                changed.append(
                    f"{c.get('kind')}: {compact_line(c.get('before'), 50)} → {compact_line(c.get('after'), 50)}"
                )
        for e in (diff.get("added") or [])[:4]:
            changed.append(f"added {e.get('uid')} {e.get('role') or ''} {compact_line(e.get('label'))}")
        for u in (diff.get("updated") or [])[:4]:
            after_label = _get(u, "after", "label") or _get(u, "before", "label")
            changed.append(f"updated {u.get('uid')} {compact_line(after_label)}")
        if changed:
            lines.append("\n## Changed since last snapshot")
            for item in changed[:10]:
                lines.append(f"- {item}")

    matches = snapshot.get("matches")
    if isinstance(matches, list) and matches:
        lines.append(f'\n## Matches for "{snapshot.get("query")}"')
        for match in matches[:12]:
            kind = match.get("kind")
            if kind == "text":
                lines.append(f"- {match.get('uid')} text {compact_line(match.get('text'))} @ {rect_text(match.get('rect'))}")
            elif kind == "region":
                headings = " | ".join(compact_line(h, 50) for h in (match.get("headings") or []))
                lines.append(f"- {match.get('uid')} region {compact_line(match.get('label'))} headings={headings}")
            else:
                disabled = " disabled" if match.get("disabled") else ""
                label = match.get("label") or match.get("selector")
                role = match.get("role") or match.get("tag") or "element"
                lines.append(f"- {match.get('uid')} {role}{disabled} {compact_line(label)} @ {rect_text(match.get('rect'))}")

    if mode == "pageMap" and snapshot.get("pageMap"):
        page_map = snapshot["pageMap"]
        lines.append("\n## Page map")
        for region in (page_map.get("regions") or [])[:18]:
            lines.append(f"- {region.get('uid')} {region.get('kind')}: {compact_line(region.get('label'))}")
            for action in (region.get("actions") or [])[:5]:
                disabled = " disabled" if action.get("disabled") else ""
                lines.append(f"  - {action.get('uid')} {action.get('role') or ''}{disabled} {compact_line(action.get('label'))}")
        if page_map.get("headings"):
            lines.append("\nHeadings:")
            for h in page_map["headings"][:20]:
                lines.append(f"- {h.get('uid')} h{h.get('level') or ''} {compact_line(h.get('text'))}")

    layout = snapshot.get("layout")
    if isinstance(layout, list) and layout and mode != "changes":
        lines.append("\n## Layout / context")
        for section in layout[: 18 if mode == "pageMap" else 8]:
            bits = [
                str(section.get("uid")),
                section.get("role") or section.get("tag"),
                compact_line(section.get("label") or section.get("text") or "(unnamed section)", 110),
                f"@ {rect_text(section.get('rect'))}",
            ]
            lines.append(f"- {' '.join(b for b in bits if b)}")
            field_labels = [
                f"{f.get('uid')} {compact_line(f.get('label') or f.get('role'), 40)}"
                for f in (section.get("fields") or [])[:4]
            ]
            action_labels = [
                f"{a.get('uid')}{' disabled' if a.get('disabled') else ''} {compact_line(a.get('label') or a.get('role'), 40)}"
                for a in (section.get("actions") or [])[:5]
            ]
            if field_labels:
                lines.append(f"  fields: {'; '.join(field_labels)}")
            if action_labels:
                lines.append(f"  actions: {'; '.join(action_labels)}")

    forms = snapshot.get("forms") or {}
    if (mode == "forms" or forms.get("fields")) and mode != "pageMap":
        fields = forms.get("fields") or []
        submits = forms.get("submits") or []
        if fields or submits:
            lines.append("\n## Forms")
        for field_ in fields[: 40 if mode == "forms" else 12]:
            bits = [
                field_.get("uid"),
                field_.get("role") or field_.get("tag"),
                "required" if field_.get("required") else "",
                "invalid" if field_.get("invalid") else "",
                "disabled" if field_.get("disabled") else "",
                compact_line(field_.get("label") or field_.get("selector"), 90),
            ]
            if field_.get("value"):
                bits.append(f"value={compact_line(field_['value'], 50)}")
            elif field_.get("valueRedacted"):
                bits.append("value=[redacted]")
            lines.append(f"- {' '.join(b for b in bits if b)} @ {rect_text(field_.get('rect'))}")
        for submit in submits[:8]:
            disabled = " disabled" if submit.get("disabled") else ""
            lines.append(f"- {submit.get('uid')} submit/action{disabled} {compact_line(submit.get('label') or submit.get('selector'))} @ {rect_text(submit.get('rect'))}")

    elements = snapshot.get("elements")
    if isinstance(elements, list) and mode != "pageMap":
        lines.append("\n## Visible actions")
        limit = 60 if mode == "interactive" else 25
        for el in elements[:limit]:
            flags = ",".join(
                f for f in [
                    "disabled" if el.get("disabled") else "",
                    f"occluded-by-{_get(el, 'occluded', 'tag')}" if el.get("occluded") else "",
                ] if f
            )
            ctx_label = _get(el, "context", "label")
            context = f" in {_get(el, 'context', 'uid')} {compact_line(ctx_label, 60)}" if ctx_label else ""
            role = el.get("role") or el.get("tag")
            label = el.get("label") or el.get("selector")
            lines.append(f"- {el.get('uid')} {role}{f' [{flags}]' if flags else ''} {compact_line(label)}{context} @ {rect_text(el.get('rect'))}")
        if len(elements) > limit:
            lines.append(f"- … {len(elements) - limit} more; retry with maxElements or mode=interactive")

    if mode in ("text", "auto"):
        snippets = snapshot.get("textSnippets")
        if isinstance(snippets, list) and snippets:
            lines.append("\n## Text snippets")
            limit = 40 if mode == "text" else 14
            char_limit = 240 if mode == "text" else 160
            for snip in snippets[:limit]:
                lines.append(f"- {snip.get('uid')} {compact_line(snip.get('text'), char_limit)}")
            if snapshot.get("textTruncated"):
                lines.append("- … page text truncated; retry with mode=text or maxTextChars for more")

    lines.append("\nTip: use chrome_snapshot({query:'...', mode:'interactive|forms|pageMap|text|changes|full'}) or nearUid to zoom in.")
    return truncate_text("\n".join(lines))


def format_included_snapshot_text(raw: Any, text: str) -> str:
    snapshot = raw.get("snapshot") if isinstance(raw, dict) else None
    return f"{text}\n\n{format_chrome_snapshot(snapshot)}" if snapshot else text


def format_chrome_inspect(inspect: Any) -> str:
    if not isinstance(inspect, dict):
        return safe_json(inspect)
    t = inspect.get("target") or {}
    lines: list[str] = []
    lines.append(f"# Chrome inspect {t.get('uid') or ''}".strip())
    occluded = f" occluded-by-{_get(t, 'occluded', 'tag')}" if t.get("occluded") else ""
    disabled = " disabled" if t.get("disabled") else ""
    lines.append(f"{t.get('role') or t.get('tag') or 'element'}{disabled}{occluded} {compact_line(t.get('label') or t.get('selector'))}")
    if t.get("selector"):
        lines.append(f"selector: {t['selector']}")
    if t.get("rect"):
        lines.append(f"rect: {rect_text(t['rect'])}")
    cs = inspect.get("clickSuggestion")
    if cs:
        lines.append(f'suggested click: chrome_click({{ uid: "{cs.get("uid")}" }}) or x={cs.get("x")}, y={cs.get("y")}')

    nearby_text = inspect.get("nearbyText")
    if isinstance(nearby_text, list) and nearby_text:
        lines.append("\n## Nearby text")
        for item in nearby_text[:12]:
            lines.append(f"- {item.get('uid')} {compact_line(item.get('text'), 180)}")

    form_ctx = inspect.get("formContext")
    if form_ctx:
        lines.append("\n## Form context")
        for field_ in (form_ctx.get("fields") or [])[:20]:
            value = (
                f" value={compact_line(field_['value'], 60)}" if field_.get("value")
                else " value=[redacted]" if field_.get("valueRedacted") else ""
            )
            disabled = " disabled" if field_.get("disabled") else ""
            lines.append(f"- {field_.get('uid')} {field_.get('role') or field_.get('tag')}{disabled} {compact_line(field_.get('label') or field_.get('selector'))}{value}")
        for action in (form_ctx.get("actions") or [])[:10]:
            disabled = " disabled" if action.get("disabled") else ""
            lines.append(f"- {action.get('uid')} action{disabled} {compact_line(action.get('label') or action.get('selector'))}")

    nearby_actions = inspect.get("nearbyActions")
    if isinstance(nearby_actions, list) and nearby_actions:
        lines.append("\n## Nearby actions")
        for action in nearby_actions[:18]:
            disabled = " disabled" if action.get("disabled") else ""
            lines.append(f"- {action.get('uid')} {action.get('role') or action.get('tag')}{disabled} {compact_line(action.get('label') or action.get('selector'))} @ {rect_text(action.get('rect'))}")

    ancestors = inspect.get("ancestors")
    if isinstance(ancestors, list) and ancestors:
        lines.append("\n## Ancestors")
        for a in ancestors[:6]:
            lines.append(f"- {a.get('uid')} {a.get('role') or a.get('tag')} {compact_line(a.get('label') or a.get('selector'), 120)}")

    return truncate_text("\n".join(lines))


def summarize_action_result(result: Any) -> str | None:
    """Surface significant click/type/fill signals so the agent doesn't guess.

    pageMutated is a coarse heuristic; a False value is NOT proof nothing happened.
    """
    if not isinstance(result, dict):
        return None
    parts: list[str] = []
    if result.get("pageMutated") is False:
        parts.append("no coarse DOM change detected (may still have taken effect — verify with includeSnapshot)")
    if result.get("defaultPrevented") is True:
        parts.append("defaultPrevented=true")
    if result.get("elementVisible") is False:
        parts.append("element NOT visible")
    if result.get("occludedBy"):
        o = result["occludedBy"]
        tag = o.get("tag") or "?"
        oid = f"#{o['id']}" if o.get("id") else ""
        parts.append(f"occluded by <{tag}{oid}>")
    if result.get("valueMatches") is False:
        parts.append("input value did not stick")
    if result.get("autoplayHint"):
        parts.append("autoplay-gated affordance")
    return "; ".join(parts) if parts else None
