import json
from datetime import UTC, datetime

import pytest
import yaml

from constellation.frontmatter import FrontmatterError, parse_frontmatter, render_frontmatter
from constellation.ingest import CapabilityError, IngestError, ingest_file
from constellation.models import CandidatePatch
from constellation.retrieval import build_index, exact_lookup
from constellation.review import promote_candidate
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


def test_text_ingest_preserves_source_and_stages_canonical_candidate(tmp_path):
    root = make_vault(tmp_path)
    source = root / "Inbox/example.txt"
    source.write_text("Fictional evidence.\n", encoding="utf-8")
    build_index(root)

    result = ingest_file(root, "Inbox/example.txt", now=NOW)

    assert result["status"] == "staged"
    assert result["source_id"]
    assert (root / result["preserved_path"]).read_bytes() == source.read_bytes()
    assert (root / result["text_path"]).read_text(encoding="utf-8") == "Fictional evidence.\n"
    assert (root / result["manifest_path"]).is_file()
    manifest = json.loads((root / result["manifest_path"]).read_text(encoding="utf-8"))
    extraction = manifest["extraction"]
    assert extraction["status"] == "complete"
    assert extraction["engine"]["name"] == "python-utf8"
    assert extraction["source_sha256"] == manifest["source_hash"]
    assert extraction["extracted_text_sha256"]
    assert extraction["units"] == [
        {
            "anchor": "L000001-L000001",
            "characters": 20,
            "index": 1,
            "kind": "text",
            "line_end": 1,
            "line_start": 1,
            "status": "extracted",
            "text_sha256": extraction["units"][0]["text_sha256"],
        }
    ]
    assert not (root / result["source_item_path"]).exists()
    candidate = CandidatePatch.model_validate_json(
        (root / result["candidate_path"]).read_text(encoding="utf-8")
    )
    assert candidate.id == result["candidate_id"]
    assert candidate.status == "pending-review"
    assert candidate.expected_base_hash is None
    assert candidate.target_path == result["source_item_path"]
    assert candidate.content.startswith("---\n")
    source_metadata, _ = parse_frontmatter(candidate.content)
    assert source_metadata["extraction_manifest_path"] == result["manifest_path"]
    assert source_metadata["extraction_status"] == "complete"
    assert exact_lookup(root, result["source_id"])["status"] == "no_evidence_found"
    assert not list((root / "claims").iterdir())


def test_explicit_automatic_registration_promotes_only_the_source_record(tmp_path):
    root = make_vault(tmp_path)
    config_path = root / ".constellation/config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["source_registration"] = "automatic"
    config_path.write_text(yaml.safe_dump(config, sort_keys=True), encoding="utf-8")
    source = root / "Inbox/automatic.txt"
    source.write_text("Fictional automatic evidence.\n", encoding="utf-8")

    result = ingest_file(root, source, now=NOW)

    assert result["status"] == "registered"
    assert (root / result["source_item_path"]).is_file()
    assert not (root / result["candidate_path"]).exists()
    assert exact_lookup(root, result["source_id"])["status"] == "evidence_found"
    assert not list((root / "claims").iterdir())
    assert not list((root / "entities").iterdir())
    manifest = json.loads((root / result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["registration"] == {"mode": "automatic", "status": "canonical"}


def test_ingest_is_hash_idempotent(tmp_path):
    root = make_vault(tmp_path)
    source = root / "Inbox/example.md"
    source.write_text("# Same\n", encoding="utf-8")
    first = ingest_file(root, "Inbox/example.md", now=NOW)
    second = ingest_file(root, "Inbox/example.md", now=NOW)
    assert second["status"] == "already_ingested"
    assert first["source_id"] == second["source_id"]
    assert first["candidate_id"] == second["candidate_id"]
    assert not list((root / "source-items").glob("*.md"))
    assert len(list((root / ".constellation/candidates").glob("*.json"))) == 1


def test_ingest_upgrades_an_old_manifest_through_a_reviewable_source_patch(tmp_path):
    root = make_vault(tmp_path)
    source = root / "Inbox/example.txt"
    source.write_text("Fictional evidence.\n", encoding="utf-8")
    first = ingest_file(root, "Inbox/example.txt", now=NOW)
    promote_candidate(root, first["candidate_id"], confirm=True, expected_base_hash=None)

    source_note_path = root / first["source_item_path"]
    metadata, body = parse_frontmatter(source_note_path.read_text(encoding="utf-8"))
    metadata.pop("extraction_manifest_path")
    metadata.pop("extraction_status")
    source_note_path.write_text(render_frontmatter(metadata, body), encoding="utf-8")
    manifest_path = root / first["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("extraction")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    upgraded = ingest_file(root, "Inbox/example.txt", now=NOW)

    assert upgraded["manifest_upgraded"] == "true"
    candidate = CandidatePatch.model_validate_json(
        (root / upgraded["candidate_path"]).read_text(encoding="utf-8")
    )
    assert candidate.expected_base_hash
    updated_metadata, _ = parse_frontmatter(candidate.content)
    assert updated_metadata["extraction_manifest_path"] == first["manifest_path"]
    assert updated_metadata["extraction_status"] == "complete"


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


def test_native_pdf_records_page_anchors_and_rejects_an_all_blank_pdf(tmp_path):
    fitz = pytest.importorskip("fitz")
    root = make_vault(tmp_path)
    pdf = root / "Inbox/anchored.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Fictional page one evidence")
    document.new_page()
    document.save(pdf)
    document.close()

    result = ingest_file(root, "Inbox/anchored.pdf", now=NOW)

    extracted = (root / result["text_path"]).read_text(encoding="utf-8")
    manifest = json.loads((root / result["manifest_path"]).read_text(encoding="utf-8"))
    extraction = manifest["extraction"]
    assert extracted.startswith("[P0001]\nFictional page one evidence")
    assert "[P0002]" in extracted
    assert extraction["status"] == "complete-with-gaps"
    assert extraction["expected_units"] == 2
    assert extraction["extracted_units"] == 1
    assert extraction["blank_units"] == 1
    assert extraction["units"][0]["anchor"].startswith("P0001:L0001-L")
    assert extraction["units"][1]["anchor"] == "P0002"
    assert extraction["units"][1]["status"] == "blank-needs-ocr"

    blank = root / "Inbox/blank.pdf"
    document = fitz.open()
    document.new_page()
    document.save(blank)
    document.close()
    with pytest.raises(CapabilityError, match="requires OCR"):
        ingest_file(root, "Inbox/blank.pdf", now=NOW)
