import json
from datetime import UTC, datetime

import pytest

from constellation.frontmatter import FrontmatterError, parse_frontmatter, render_frontmatter
from constellation.ingest import CapabilityError, IngestError, ingest_file
from constellation.vault import initialize_vault

NOW = datetime(2026, 2, 3, 4, 5, tzinfo=UTC)


def make_vault(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    (root / "Inbox").mkdir(exist_ok=True)
    return root


def test_frontmatter_round_trip_is_deterministic():
    metadata = {"schema_version": "0.1", "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "title": "Example"}
    text = render_frontmatter(metadata, "Body\n")
    assert parse_frontmatter(text) == (metadata, "Body\n")
    assert text == render_frontmatter(metadata, "Body\n")
    with pytest.raises(FrontmatterError):
        parse_frontmatter("not frontmatter")


def test_text_ingest_preserves_source_and_writes_all_packets(tmp_path):
    root = make_vault(tmp_path)
    source = root / "Inbox/example.txt"
    source.write_text("Fictional evidence.\n", encoding="utf-8")

    result = ingest_file(root, "Inbox/example.txt", now=NOW)

    assert result["status"] == "ingested"
    assert result["source_id"]
    assert (root / result["preserved_path"]).read_bytes() == source.read_bytes()
    assert (root / result["text_path"]).read_text(encoding="utf-8") == "Fictional evidence.\n"
    assert (root / result["manifest_path"]).is_file()
    assert (root / result["source_item_path"]).is_file()
    candidate = json.loads((root / result["candidate_path"]).read_text(encoding="utf-8"))
    assert candidate["status"] == "pending_review"
    assert not list((root / "claims").iterdir())


def test_ingest_is_hash_idempotent(tmp_path):
    root = make_vault(tmp_path)
    source = root / "Inbox/example.md"
    source.write_text("# Same\n", encoding="utf-8")
    first = ingest_file(root, "Inbox/example.md", now=NOW)
    second = ingest_file(root, "Inbox/example.md", now=NOW)
    assert second["status"] == "already_ingested"
    assert first["source_id"] == second["source_id"]
    assert len(list((root / "source-items").glob("*.md"))) == 1


def test_ingest_rejects_unsafe_and_unsupported_inputs(tmp_path, monkeypatch):
    root = make_vault(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    with pytest.raises(IngestError):
        ingest_file(root, outside, now=NOW)
    linked = root / "Inbox/link.txt"
    linked.symlink_to(outside)
    with pytest.raises(IngestError):
        ingest_file(root, "Inbox/link.txt", now=NOW)
    active = root / "Inbox/page.html"
    active.write_text("<script>x</script>", encoding="utf-8")
    with pytest.raises(IngestError):
        ingest_file(root, "Inbox/page.html", now=NOW)

    oversized = root / "Inbox/oversized.txt"
    oversized.write_bytes(b"12345")
    monkeypatch.setattr("constellation.ingest.MAX_SOURCE_BYTES", 4)
    with pytest.raises(IngestError, match="size limit"):
        ingest_file(root, "Inbox/oversized.txt", now=NOW)


def test_pdf_without_extractor_has_clear_capability_error(tmp_path, monkeypatch):
    root = make_vault(tmp_path)
    pdf = root / "Inbox/example.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr("constellation.ingest.importlib.util.find_spec", lambda name: None)
    with pytest.raises(CapabilityError, match="PyMuPDF"):
        ingest_file(root, "Inbox/example.pdf", now=NOW)
