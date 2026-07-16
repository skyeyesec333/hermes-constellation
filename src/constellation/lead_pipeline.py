"""End-to-end conference lead capture into Project Manager CRM notes."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .conference import ConferenceError, build_conference_encounter
from .ingest import IngestError, ingest_file
from .project_manager import ProjectManagerError, create_or_update_task, lead_key
from .storage import atomic_write_text, safe_relative_path
from .vault import is_initialized


class LeadPipelineError(RuntimeError):
    """Raised when conference lead capture fails closed."""


def _task_title(person_hint: dict[str, Any], event_name: str) -> str:
    name = person_hint.get("raw_name") or "Unknown contact"
    company = person_hint.get("company_hint")
    if company:
        return f"{name} ({company}) — {event_name} follow-up"
    return f"{name} — {event_name} follow-up"


def _drafts_stub(
    *,
    channel: str,
    person_hint: dict[str, Any],
    event_name: str,
    summary: str | None,
) -> dict[str, Any]:
    name = person_hint.get("raw_name") or "there"
    hook = summary.strip() if summary else f"meeting at {event_name}"
    whatsapp = (
        f"Hi {name} — great meeting you at {event_name}. "
        f"Enjoyed our chat about {hook}. "
        f"Happy to send a short follow-up on what we discussed when useful."
    )
    email_body = (
        f"Hi {name},\n\n"
        f"Good meeting you at {event_name}. "
        f"I appreciated our conversation about {hook}.\n\n"
        f"Happy to share a concise follow-up if useful.\n\n"
        f"Best regards"
    )
    drafts: dict[str, Any] = {
        "send_allowed": False,
        "channels": {},
    }
    if channel in {"whatsapp", "sms", "unknown"}:
        drafts["channels"]["whatsapp" if channel != "sms" else "sms"] = {
            "channel": "whatsapp" if channel != "sms" else "sms",
            "subject": None,
            "body": whatsapp,
            "send_allowed": False,
        }
    if channel in {"email", "unknown"} or person_hint.get("email"):
        drafts["channels"]["email"] = {
            "channel": "email",
            "subject": f"Good meeting you at {event_name}",
            "body": email_body,
            "send_allowed": False,
        }
    return drafts


def capture_conference_lead(
    root: Path | str,
    *,
    card_source: Path | str,
    event_name: str,
    event_date: date,
    project_title: str,
    venue: str | None = None,
    note: str | None = None,
    channel: str = "whatsapp",
    phone_region: str | None = None,
    where: str | None = None,
    todos: list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Ingest a card, stage an encounter, and upsert a Project Manager lead task."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise LeadPipelineError("vault is not initialized")
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise LeadPipelineError("timestamp must include a timezone")

    try:
        card_result = ingest_file(
            vault,
            card_source,
            kind="business-card",
            phone_region=phone_region,
            now=instant,
        )
    except IngestError as exc:
        raise LeadPipelineError(str(exc)) from exc

    card_source_id = str(card_result["source_id"])
    manifest_path = safe_relative_path(vault, card_result["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    business_card = manifest.get("business_card") or {}
    fields = list(business_card.get("fields") or [])

    notes_source_id = None
    if note and note.strip():
        note_rel = Path("Inbox") / f"encounter-note-{card_source_id}.txt"
        note_path = safe_relative_path(vault, note_rel)
        note_path.parent.mkdir(parents=True, exist_ok=True)
        if not note_path.exists():
            atomic_write_text(vault, note_rel, note.strip() + "\n")
        try:
            note_result = ingest_file(
                vault,
                note_rel,
                kind="meeting-notes",
                now=instant,
            )
            notes_source_id = str(note_result["source_id"])
        except IngestError as exc:
            raise LeadPipelineError(str(exc)) from exc

    try:
        encounter = build_conference_encounter(
            event_name=event_name,
            venue=venue,
            event_date=event_date,
            project_title=project_title,
            card_fields=fields,
            conversation_summary=note,
            channel_preference=channel if channel in {"whatsapp", "sms", "email", "unknown"} else "unknown",
            card_source_id=card_source_id,
            notes_source_id=notes_source_id,
            where=where,
        )
    except ConferenceError as exc:
        raise LeadPipelineError(str(exc)) from exc

    person = encounter["person_hint"]
    key = lead_key(
        event_date=event_date.isoformat(),
        event_name=event_name,
        email=person.get("email"),
        phone=person.get("phone"),
        name=person.get("raw_name"),
        company=person.get("company_hint"),
    )

    encounter_relative = Path(".constellation/candidates") / f"encounter-{key}.json"
    atomic_write_text(
        vault,
        encounter_relative,
        json.dumps(encounter, indent=2, sort_keys=True) + "\n",
    )

    drafts = _drafts_stub(
        channel=channel,
        person_hint=person,
        event_name=event_name,
        summary=note,
    )
    drafts_relative = Path(".constellation/candidates") / f"followup-drafts-{key}.json"
    atomic_write_text(
        vault,
        drafts_relative,
        json.dumps(drafts, indent=2, sort_keys=True) + "\n",
    )

    summary = (note or "").strip()
    body_lines = [
        "Status: open · card captured",
        f"Event: {event_name}"
        + (f" @ {venue}" if venue else "")
        + f" ({event_date.isoformat()})",
        f"Met: {summary or 'conversation note not provided'}",
        f"Channel: {channel}",
        "Next: review drafts + approve send manually",
        f"Drafts: `{drafts_relative.as_posix()}`",
        "Evidence:",
        f"- card source: {card_source_id}",
        f"- encounter: {encounter_relative.as_posix()}",
    ]
    if notes_source_id:
        body_lines.append(f"- notes source: {notes_source_id}")
    if person.get("email"):
        body_lines.append(f"- email: {person['email']}")
    if person.get("phone"):
        body_lines.append(f"- phone: {person['phone']}")
    body_lines.append("Links:")
    if person.get("raw_name"):
        body_lines.append(f"- person hint: {person['raw_name']}")
    if person.get("company_hint"):
        body_lines.append(f"- company hint: {person['company_hint']}")
    if todos:
        body_lines.append("")
        body_lines.append("## Todos (confirmed with Bryan)")
        for item in todos:
            item = str(item).strip()
            if not item:
                continue
            if item.startswith("- ["):
                body_lines.append(item)
            else:
                body_lines.append(f"- [ ] {item}")

    try:
        task = create_or_update_task(
            vault,
            project_title=project_title,
            title=_task_title(person, event_name),
            lead_key=key,
            status="open",
            priority="high",
            body_lines=body_lines,
            start=event_date,
        )
    except ProjectManagerError as exc:
        raise LeadPipelineError(str(exc)) from exc

    return {
        "status": "staged",
        "lead_key": key,
        "card_source_id": card_source_id,
        "notes_source_id": notes_source_id,
        "encounter_path": encounter_relative.as_posix(),
        "drafts_path": drafts_relative.as_posix(),
        "pm_project": task["project_path"],
        "pm_task": task["task_path"],
        "task_id": task["task_id"],
        "send_allowed": False,
    }
