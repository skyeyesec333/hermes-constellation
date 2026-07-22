import json
from datetime import date

from constellation.conference import (
    _is_likely_address,
    _is_likely_person_name,
    _hint_from_card_fields,
    build_conference_encounter,
)
from constellation.lead_pipeline import capture_conference_lead
from constellation.vault import initialize_vault


def test_likely_person_name_rejects_address_and_region():
    """Regression: MATRADE cards where address/states were parsed as person name."""
    # Address garbage — must NOT be treated as person names
    assert _is_likely_address("Bang Rak, Bangkok 10500") is True
    assert _is_likely_address("Maine; Maryland, Massachusetts, New York") is True
    assert _is_likely_address("Sathorn Road, Bangkok") is True
    assert _is_likely_address("1234 Thailand Lane") is True

    # Person names — must be recognised
    assert _is_likely_person_name("JIRA MENGERN") is True
    assert _is_likely_person_name("Mr. Mohd Hafizi Yusoff") is True
    assert _is_likely_person_name("Ada Example") is True

    # Addresses must NOT be recognised as person names
    assert _is_likely_person_name("Bang Rak, Bangkok 10500") is False
    assert _is_likely_person_name("Maine; Maryland, Massachusetts, New York") is False
    assert _is_likely_person_name("1234 Sathorn Road") is False
    assert _is_likely_person_name("T") is False
    assert _is_likely_person_name("Solar Energy") is False


def test_hint_prefers_name_over_address():
    """Regression: _hint_from_card_fields picks name, not first unclassified address."""
    hint = _hint_from_card_fields(
        [
            # Address garbage first (as happened with MATRADE cards)
            {"field": "unclassified_text", "value": "Bang Rak, Bangkok 10500", "anchor": "OCR:R0004"},
            # Real name second
            {"field": "unclassified_text", "value": "JIRA MENGERN", "anchor": "OCR:R0005"},
            {"field": "email", "value": "jira@" + "example.test", "anchor": "OCR:R0009"},
        ]
    )
    assert hint["raw_name"] == "JIRA MENGERN", f"expected JIRA MENGERN, got {hint['raw_name']}"
    assert hint["email"] == "jira@" + "example.test"


def test_hint_marks_uncertain_ocr_text_for_review_without_inventing_identity():
    """No credible name means no person or company hint is inferred."""
    hint = _hint_from_card_fields(
        [
            {"field": "unclassified_text", "value": "T", "anchor": "OCR:R0001"},
            {"field": "unclassified_text", "value": "Solar Energy", "anchor": "OCR:R0002"},
            {"field": "email", "value": "office@" + "trade.example.test", "anchor": "OCR:R0003"},
        ]
    )
    assert hint["raw_name"] is None
    assert hint["company_hint"] is None
    assert hint["name_review_required"] is True
    assert hint["email"] == "office@" + "trade.example.test"


def test_name_scoring_prefers_person_over_company():
    """Regression: Jane Lee card — 'I-Solar Energy' (company) should NOT beat 'Jane Lee' (person)."""
    hint = _hint_from_card_fields(
        [
            {"field": "unclassified_text", "value": "Jane Lee", "anchor": "OCR:R0001"},
            {"field": "unclassified_text", "value": "Manager", "anchor": "OCR:R0002"},
            {"field": "unclassified_text", "value": "I-Solar Energy", "anchor": "OCR:R0003"},
            {"field": "unclassified_text", "value": "Co., Ltd.", "anchor": "OCR:R0004"},
            {"field": "email", "value": "janalee@" + "example.test", "anchor": "OCR:R0010"},
        ]
    )
    assert hint["raw_name"] == "Jane Lee", f"expected 'Jane Lee', got {hint['raw_name']}"
    # Company hint should be the second-highest name candidate
    assert hint["company_hint"] == "I-Solar Energy" or hint["company_hint"] == "Manager"


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


def test_low_confidence_ocr_card_creates_unknown_contact_review_task(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from constellation.ingest import ExtractedSource
    from constellation.storage import sha256_bytes

    vault = tmp_path / "vault"
    initialize_vault(vault)
    card = vault / "Inbox/uncertain-card.png"
    data = b"fictional-uncertain-card"
    card.write_bytes(data)
    extracted = ExtractedSource(
        data=data,
        text="[OCR:R0001] T\n[OCR:R0002] Solar Energy\n",
        media_type="image/png",
        extraction={
            "source_sha256": sha256_bytes(data),
            "status": "complete",
            "units": [
                {"anchor": "OCR:R0001", "confidence": 0.2, "bounding_box": [[0, 0], [1, 1]]},
                {"anchor": "OCR:R0002", "confidence": 0.2, "bounding_box": [[0, 1], [1, 2]]},
            ],
        },
    )
    monkeypatch.setattr("constellation.ingest._read_source", lambda _: extracted)

    result = capture_conference_lead(
        vault,
        card_source=card,
        event_name="Fictional Expo",
        event_date=date(2026, 7, 21),
        project_title="Fictional Expo Leads",
        now=datetime(2026, 7, 21, tzinfo=UTC),
    )

    assert result["name_review_required"] is True
    encounter = json.loads((vault / result["encounter_path"]).read_text(encoding="utf-8"))
    assert encounter["person_hint"]["raw_name"] is None
    assert encounter["person_hint"]["company_hint"] is None
    assert encounter["person_hint"]["name_review_required"] is True
    manifest_files = list((vault / ".constellation/manifests").glob("*.json"))
    assert len(manifest_files) == 1
    manifest = json.loads(manifest_files[0].read_text(encoding="utf-8"))
    assert [field["anchor"] for field in manifest["business_card"]["fields"]] == [
        "OCR:R0001",
        "OCR:R0002",
    ]
    task = (vault / result["pm_task"]).read_text(encoding="utf-8")
    assert "Unknown contact" in task
    assert "Name review: required" in task
