"""Relationship decay detection — surfacing aging contacts before they go cold.

Phase 9: scans entities for stale contacts, missed follow-ups, and overdue
decisions. Designed for cron delivery — returns a compact nudge, not a dashboard.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from .frontmatter import parse_frontmatter
from .vault import is_initialized


class DecayError(RuntimeError):
    """Raised when decay detection cannot complete."""


def _extract_date(fm: dict[str, object], key: str) -> datetime | None:
    """Extract and parse a date from frontmatter."""
    value = fm.get(key)
    if not value:
        return None
    raw = str(value)[:19]  # YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


def _crm_from_body(body: str) -> dict[str, str]:
    """Extract CRM inline fields from body text."""
    crm: dict[str, str] = {}
    for line in body.splitlines():
        if "::" in line:
            key, _, value = line.partition("::")
            key = key.strip()
            value = value.strip()
            if key and value:
                crm[key] = value
    return crm


def detect_decay(root: Path | str, *, threshold_days: int = 14) -> str:
    """Scan all active entities and return a decay report.

    Returns a compact markdown report of:
    - Aging contacts (last_touch > threshold_days)
    - Contacts with no recorded touch date
    - Outreach drafts never sent (next_action set but no recent touch)
    - Overdue decisions (review_date passed)
    """
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise DecayError("vault is not initialized")

    now = datetime.now(UTC)
    threshold = now - timedelta(days=threshold_days)

    aging: list[str] = []
    untouched: list[str] = []
    draft_unsent: list[str] = []
    overdue_decisions: list[str] = []

    # ── Scan entities ──
    entities_dir = vault / "entities"
    if entities_dir.is_dir():
        for path in sorted(entities_dir.glob("*.md")):
            try:
                fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
                if not isinstance(fm, dict):
                    continue
            except Exception:
                continue

            title = str(fm.get("title", path.stem))
            status = str(fm.get("status", ""))
            if status not in ("active", ""):
                continue

            crm = _crm_from_body(body if isinstance(body, str) else "")
            last_touch = crm.get("last_touch") or str(fm.get("last_touch", ""))
            next_action = crm.get("next_action") or str(fm.get("next_action", ""))
            pipeline = crm.get("pipeline_stage") or str(fm.get("pipeline_stage", ""))

            # Parse last_touch
            touch_dt: datetime | None = None
            if last_touch:
                try:
                    touch_dt = datetime.fromisoformat(last_touch[:10])
                    if touch_dt.tzinfo is None:
                        touch_dt = touch_dt.replace(tzinfo=UTC)
                except (ValueError, TypeError):
                    pass

            if touch_dt and touch_dt < threshold:
                days = (now - touch_dt).days
                context = f"{title} ({days}d, stage={pipeline})" if pipeline else f"{title} ({days}d)"
                aging.append(context)
            elif not touch_dt and (next_action or pipeline):
                untouched.append(title)

            # Draft written but never sent
            if next_action and (not touch_dt or (touch_dt and touch_dt < threshold)):
                if "draft" in next_action.lower() or "email" in next_action.lower():
                    draft_unsent.append(f"{title}: {next_action[:80]}")

    # ── Scan decisions ──
    decisions_dir = vault / "decisions"
    if decisions_dir.is_dir():
        for path in sorted(decisions_dir.glob("*.md")):
            try:
                fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
                if not isinstance(fm, dict):
                    continue
            except Exception:
                continue

            review_date = _extract_date(fm, "review_date")
            decided_at = _extract_date(fm, "decided_at")
            if review_date and review_date < now:
                title = str(fm.get("title", path.stem))
                overdue_decisions.append(f"{title} (review was {review_date.strftime('%Y-%m-%d')})")
            elif not review_date and decided_at and decided_at < threshold:
                title = str(fm.get("title", path.stem))
                overdue_decisions.append(f"{title} (decided {decided_at.strftime('%Y-%m-%d')}, no review set)")

    # ── Compile report ──
    if not (aging or untouched or draft_unsent or overdue_decisions):
        return ""

    lines: list[str] = []
    lines.append("# Relationship Decay Report")
    lines.append("")
    lines.append(f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}  ")
    lines.append(f"Threshold: {threshold_days} days  ")
    lines.append("")

    if aging:
        lines.append(f"## Aging Contacts ({len(aging)})")
        lines.append("")
        for item in aging:
            lines.append(f"- {item}")
        lines.append("")

    if untouched:
        lines.append(f"## Active, No Touch Date ({len(untouched)})")
        lines.append("")
        for item in untouched:
            lines.append(f"- {item}")
        lines.append("")

    if draft_unsent:
        lines.append(f"## Drafts Not Sent ({len(draft_unsent)})")
        lines.append("")
        for item in draft_unsent:
            lines.append(f"- {item}")
        lines.append("")

    if overdue_decisions:
        lines.append(f"## Overdue Reviews ({len(overdue_decisions)})")
        lines.append("")
        for item in overdue_decisions:
            lines.append(f"- {item}")
        lines.append("")

    return "\n".join(lines) + "\n"
