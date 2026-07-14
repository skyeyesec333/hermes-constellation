from datetime import UTC, datetime

from constellation.card_ingest import extract_business_card_fields
from constellation.ingest import ExtractedSource, ingest_file
from constellation.storage import sha256_bytes
from constellation.vault import initialize_vault


def test_card_fields_keep_ocr_anchors_and_do_not_claim_current_role_without_review():
    result = extract_business_card_fields(
        source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        text=(
            "[OCR:R0001] Fictional Ada Example\n"
            "[OCR:R0002] ada@"
            "example."
            "com\n"
            "[OCR:R0003] +1 (415) 555-2671\n"
            "[OCR:R0004] https://example.test\n"
            "[OCR:R0005] Strategy Director\n"
        ),
        units=[
            {"anchor": "OCR:R0001", "confidence": 0.91, "bounding_box": [[0, 0], [1, 1]]},
            {"anchor": "OCR:R0002", "confidence": 0.92, "bounding_box": [[0, 1], [1, 2]]},
            {"anchor": "OCR:R0003", "confidence": 0.93, "bounding_box": [[0, 2], [1, 3]]},
            {"anchor": "OCR:R0004", "confidence": 0.94, "bounding_box": [[0, 3], [1, 4]]},
            {"anchor": "OCR:R0005", "confidence": 0.95, "bounding_box": [[0, 4], [1, 5]]},
        ],
        phone_region="US",
    )

    assert result["status"] == "review-required"
    assert result["current_role_confirmed"] is False
    assert [(field["field"], field["value"], field["anchor"]) for field in result["fields"]] == [
        ("unclassified_text", "Fictional Ada Example", "OCR:R0001"),
        ("email", "ada@" + "example." + "com", "OCR:R0002"),
        ("phone", "+1" + "415" + "555" + "2671", "OCR:R0003"),
        ("url", "https://example.test", "OCR:R0004"),
        ("unclassified_text", "Strategy Director", "OCR:R0005"),
    ]
    assert result["fields"][1]["confidence"] == 0.92


def test_business_card_ingest_records_review_only_ocr_fields(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    source = vault / "Inbox/card.png"
    source.write_bytes(b"fictional-image")
    data = b"fictional-image"
    text = "[OCR:R0001] ada@" + "example." + "com\n"
    extracted = ExtractedSource(
        data=data,
        text=text,
        media_type="image/png",
        extraction={
            "source_sha256": sha256_bytes(data),
            "status": "complete",
            "units": [
                {
                    "anchor": "OCR:R0001",
                    "confidence": 0.9,
                    "bounding_box": [[0, 0], [1, 1]],
                }
            ],
        },
    )
    monkeypatch.setattr("constellation.ingest._read_source", lambda _: extracted)

    result = ingest_file(
        vault,
        "Inbox/card.png",
        kind="business-card",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert result["business_card_fields"] == "1"
    manifest = (vault / result["manifest_path"]).read_text(encoding="utf-8")
    assert '"current_role_confirmed": false' in manifest
    assert ('"value": "ada@' + "example." + 'com"') in manifest
