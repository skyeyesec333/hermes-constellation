"""Offline entity-timeline review surface.

Renders a cited ``entity_timeline`` projection as a self-contained offline
HTML page — no external resources, no scripts. Truncation by as-of boundary
and sensitivity exclusions are always visible.
"""

from __future__ import annotations

import html
from typing import Any

_TYPE_COLOR = {
    "claim": "#58a6ff",
    "event": "#3fb950",
    "observation": "#d29922",
    "decision": "#bc8cff",
    "opportunity": "#f778ba",
}


def render_timeline_surface(
    timeline: dict[str, Any], *, entity_title: str = ""
) -> str:
    """Render a timeline projection as offline, cited HTML."""
    entries: list[dict[str, Any]] = timeline.get("entries", [])
    title = html.escape(entity_title or str(timeline.get("entity_id", "")))

    bars: list[str] = []
    for index, entry in enumerate(entries):
        color = _TYPE_COLOR.get(str(entry["type"]), "#8b949e")
        top = 40 + index * 56
        bars.append(
            f'<rect x="180" y="{top}" width="14" height="14" fill="{color}" rx="3"/>'
            f'<text x="170" y="{top + 12}" fill="#8b949e" font-size="11" '
            f'font-family="system-ui,sans-serif" text-anchor="end">'
            f"{html.escape(str(entry['timestamp'])[:10])}</text>"
            f'<text x="206" y="{top + 12}" fill="#c9d1d9" font-size="13" '
            f'font-family="system-ui,sans-serif">'
            f"{html.escape(str(entry['title']))}"
            f'<tspan fill="#8b949e" font-size="11"> '
            f"[{html.escape(str(entry['type']))} — "
            f"{html.escape(str(entry['path']))}]</tspan></text>"
        )
    height = 60 + max(len(entries), 1) * 56
    svg = (
        f'<svg viewBox="0 0 1100 {height}" style="background:#0d1117;max-width:100%">'
        f'<line x1="187" y1="30" x2="187" y2="{height - 20}" stroke="#30363d" stroke-width="2"/>'
        + "".join(bars)
        + "</svg>"
    )

    notices: list[str] = []
    if timeline.get("truncated_by_as_of"):
        notices.append(
            f'<p style="color:#f0883e">Timeline truncated by as-of boundary: '
            f"{timeline.get('excluded_by_as_of', 0)} later record(s) excluded.</p>"
        )
    if timeline.get("excluded_by_sensitivity"):
        notices.append(
            f'<p style="color:#8b949e">{timeline["excluded_by_sensitivity"]} record(s) '
            "excluded by sensitivity ceiling.</p>"
        )
    if not entries:
        notices.append('<p style="color:#8b949e">No records reference this entity within the current filters.</p>')

    as_of = timeline.get("as_of") or "none"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Timeline — {title}</title></head>"
        '<body style="background:#0d1117;color:#c9d1d9;font-family:system-ui,sans-serif">'
        f"<h1>Entity Timeline — {title}</h1>"
        f"<p>{timeline.get('total_entries', 0)} entries · as-of: {html.escape(str(as_of))} · "
        f"ceiling: {html.escape(str(timeline.get('sensitivity_ceiling', '')))}</p>"
        + svg
        + "".join(notices)
        + "</body></html>"
    )
