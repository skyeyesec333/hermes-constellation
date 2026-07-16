from datetime import date

from constellation.conference import build_conference_encounter
from constellation.lead_pipeline import capture_conference_lead
from constellation.vault import initialize_vault


def test_encounter_keeps_role_unconfirmed_and_requires_event_project():
    encounter = build_conference_encounter(
        event_name="InfoComm Asia",
        venue="QSNCC",
        event_date=date(2026, 7, 21),
        project_title="InfoComm Asia 2026 Leads",
        card_fields=[
            {"field": "unclassified_text", "value": "Ada Example", "anchor": "OCR:R0001"},
            {"field": "email", "value": "ada@" + "mail.example.test", "anchor": "OCR:R0002"},
            {"field": "phone", "value": "+" + "66" + "111111111", "anchor": "OCR:R0003"},
        ],
        conversation_summary="Met near hall 3; talked venue AV; wants one-pager.",
        channel_preference="whatsapp",
        card_source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
    )

    assert encounter["kind"] == "conference-encounter"
    assert encounter["status"] == "review-required"
    assert encounter["current_role_confirmed"] is False
    assert encounter["person_hint"]["email"] == "ada@" + "mail.example.test"
    assert encounter["meeting_context"]["conversation_summary"].startswith("Met near hall 3")


def test_capture_conference_lead_writes_pm_task(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from constellation.ingest import ExtractedSource
    from constellation.storage import sha256_bytes

    vault = tmp_path / "vault"
    initialize_vault(vault)
    card = vault / "Inbox/card.png"
    data = b"fictional-card"
    card.write_bytes(data)
    phone = "+" + "66" + "111111111"
    text = (
        "[OCR:R0001] Ada Example\n"
        "[OCR:R0002] ada@"
        "mail.example.test\n"
        f"[OCR:R0003] {phone}\n"
    )
    extracted = ExtractedSource(
        data=data,
        text=text,
        media_type="image/png",
        extraction={
            "source_sha256": sha256_bytes(data),
            "status": "complete",
            "units": [
                {"anchor": "OCR:R0001", "confidence": 0.9, "bounding_box": [[0, 0], [1, 1]]},
                {"anchor": "OCR:R0002", "confidence": 0.9, "bounding_box": [[0, 1], [1, 2]]},
                {"anchor": "OCR:R0003", "confidence": 0.9, "bounding_box": [[0, 2], [1, 3]]},
            ],
        },
    )
    monkeypatch.setattr("constellation.ingest._read_source", lambda _: extracted)

    result = capture_conference_lead(
        vault,
        card_source=card,
        event_name="InfoComm Asia",
        venue="QSNCC",
        event_date=date(2026, 7, 21),
        project_title="InfoComm Asia 2026 Leads",
        note="Met near hall 3; wants one-pager.",
        channel="whatsapp",
        phone_region="TH",
        now=datetime(2026, 7, 21, tzinfo=UTC),
    )

    assert result["status"] == "staged"
    assert result["send_allowed"] is False
    assert (vault / result["pm_task"]).is_file()
    assert (vault / result["pm_project"]).is_file()
    task = (vault / result["pm_task"]).read_text(encoding="utf-8")
    assert "InfoComm" in task
    assert "hall 3" in task or "one-pager" in task
    assert "whatsapp" in task.casefold()
    # idempotent
    again = capture_conference_lead(
        vault,
        card_source=card,
        event_name="InfoComm Asia",
        venue="QSNCC",
        event_date=date(2026, 7, 21),
        project_title="InfoComm Asia 2026 Leads",
        note="Met near hall 3; wants one-pager.",
        channel="whatsapp",
        phone_region="TH",
        now=datetime(2026, 7, 21, tzinfo=UTC),
    )
    assert again["pm_task"] == result["pm_task"]
    assert again["lead_key"] == result["lead_key"]
    assert len(result["lead_key"]) == 16
    assert (vault / result["encounter_path"]).is_file()
    assert (vault / result["drafts_path"]).is_file()
    drafts = (vault / result["drafts_path"]).read_text(encoding="utf-8")
    assert '"send_allowed": false' in drafts
