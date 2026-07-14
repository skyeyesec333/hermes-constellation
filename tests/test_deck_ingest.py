from datetime import UTC, datetime

from constellation.deck_ingest import build_pdf_deck_map
from constellation.ingest import ExtractedSource, ingest_file
from constellation.storage import sha256_bytes
from constellation.vault import initialize_vault


def test_pdf_deck_map_preserves_page_anchors_and_suppresses_repeated_boilerplate_only_in_view():
    result = build_pdf_deck_map(
        source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        text=(
            "[P0001]\nFictional Confidential\nMarket overview\nPage footer\n\n"
            "[P0002]\nFictional Confidential\nInvestment thesis\nPage footer\n"
        ),
        units=[
            {"index": 1, "anchor": "P0001:L0001-L0003", "status": "extracted", "method": "native-text"},
            {"index": 2, "anchor": "P0002:L0001-L0003", "status": "extracted", "method": "native-text"},
        ],
    )

    assert result["status"] == "review-required"
    assert result["source_id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert [slide["title"] for slide in result["slides"]] == ["Market overview", "Investment thesis"]
    assert all(slide["suppressed_boilerplate"] == ["Fictional Confidential", "Page footer"] for slide in result["slides"])
    assert all(not slide["visual_verification_required"] for slide in result["slides"])


def test_pdf_deck_ingest_records_slide_map(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    source = vault / "Inbox/deck.pdf"
    source.write_bytes(b"fictional-pdf")
    data = b"fictional-pdf"
    text = "[P0001]\nInvestment thesis\n"
    extracted = ExtractedSource(
        data=data,
        text=text,
        media_type="application/pdf",
        extraction={
            "source_sha256": sha256_bytes(data),
            "status": "complete",
            "units": [
                {
                    "index": 1,
                    "anchor": "P0001:L0001-L0001",
                    "status": "extracted",
                    "method": "native-text",
                }
            ],
        },
    )
    monkeypatch.setattr("constellation.ingest._read_source", lambda _: extracted)

    result = ingest_file(
        vault,
        "Inbox/deck.pdf",
        kind="pdf-deck",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert result["deck_slides"] == "1"
    manifest = (vault / result["manifest_path"]).read_text(encoding="utf-8")
    assert '"title": "Investment thesis"' in manifest
