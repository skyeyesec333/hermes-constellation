import io
import json
import zipfile
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

    result = ingest_file(
        root,
        "Inbox/example.txt",
        now=NOW,
        source_url="https://example.test/evidence",
    )

    assert result["status"] == "staged"
    assert result["source_id"]
    assert (root / result["preserved_path"]).read_bytes() == source.read_bytes()
    assert (root / result["text_path"]).read_text(encoding="utf-8") == "Fictional evidence.\n"
    assert (root / result["manifest_path"]).is_file()
    manifest = json.loads((root / result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["source_url"] == "https://example.test/evidence"
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
    assert source_metadata["source_url"] == "https://example.test/evidence"
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


def test_changed_local_capture_stages_a_new_candidate_without_canonical_rewrite(tmp_path):
    root = make_vault(tmp_path)
    source = root / "Inbox/watch.txt"
    source.write_text("Initial fictional capture.\n", encoding="utf-8")

    first = ingest_file(root, "Inbox/watch.txt", now=NOW, source_url="https://example.test/watch")
    source.write_text("Changed fictional capture.\n", encoding="utf-8")
    changed = ingest_file(root, "Inbox/watch.txt", now=NOW, source_url="https://example.test/watch")

    assert changed["status"] == "staged"
    assert changed["source_id"] != first["source_id"]
    assert changed["candidate_id"] != first["candidate_id"]
    assert len(list((root / ".constellation/candidates").glob("*.json"))) == 2
    assert not list((root / "source-items").glob("*.md"))


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
    safe_text = root / "Inbox/source-url.txt"
    safe_text.write_text("Fictional evidence.", encoding="utf-8")
    with pytest.raises(IngestError, match="credentials"):
        ingest_file(
            root,
            "Inbox/source-url.txt",
            now=NOW,
            source_url="https://example.test/evidence?access_token=secret",
        )

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
    assert extraction["units"][1]["status"] == "blank-needs-vision"

    blank = root / "Inbox/blank.pdf"
    document = fitz.open()
    document.new_page()
    document.save(blank)
    document.close()
    with pytest.raises(CapabilityError, match="requires OCR"):
        ingest_file(root, "Inbox/blank.pdf", now=NOW)


def test_docx_extracts_paragraphs_and_table_cells_with_anchors(tmp_path):
    docx = pytest.importorskip("docx")
    root = make_vault(tmp_path)
    source = root / "Inbox/brief.docx"
    document = docx.Document()
    document.add_heading("Project Signal", level=1)
    document.add_paragraph("Fictional partnership evidence.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Field"
    table.cell(0, 1).text = "Value"
    table.cell(1, 0).text = "Region"
    table.cell(1, 1).text = "Thailand"
    document.save(source)

    result = ingest_file(root, source, now=NOW)

    text = (root / result["text_path"]).read_text(encoding="utf-8")
    manifest = json.loads((root / result["manifest_path"]).read_text(encoding="utf-8"))
    assert "[PARA0001] Project Signal" in text
    assert "[PARA0002] Fictional partnership evidence." in text
    assert "[TABLE0001:R0002:C0002] Thailand" in text
    assert manifest["media_type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert manifest["extraction"]["engine"]["name"] == "python-docx"
    assert [unit["kind"] for unit in manifest["extraction"]["units"]] == [
        "paragraph",
        "paragraph",
        "table-cell",
        "table-cell",
        "table-cell",
        "table-cell",
    ]


def test_ooxml_rejects_extension_content_type_mismatch(tmp_path):
    docx = pytest.importorskip("docx")
    root = make_vault(tmp_path)
    source = root / "Inbox/not-a-deck.pptx"
    document = docx.Document()
    document.add_paragraph("Fictional Word content")
    document.save(source)

    with pytest.raises(IngestError, match="OOXML content type do not match"):
        ingest_file(root, source, now=NOW)


def test_ooxml_rejects_oversized_expanded_archive(tmp_path, monkeypatch):
    root = make_vault(tmp_path)
    source = root / "Inbox/oversized.docx"
    content_types = (
        '<?xml version="1.0"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", "x" * 1024)
    monkeypatch.setattr("constellation.ingest.MAX_OFFICE_UNCOMPRESSED_BYTES", 512)

    with pytest.raises(IngestError, match="expanded size"):
        ingest_file(root, source, now=NOW)


def test_pptx_extracts_slide_text_tables_and_speaker_notes_with_anchors(tmp_path):
    pptx = pytest.importorskip("pptx")
    root = make_vault(tmp_path)
    source = root / "Inbox/deck.pptx"
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "Project Atlas"
    text_box = slide.shapes.add_textbox(pptx.util.Inches(1), pptx.util.Inches(1), pptx.util.Inches(4), pptx.util.Inches(1))
    text_box.text = "Fictional market evidence"
    table = slide.shapes.add_table(2, 2, pptx.util.Inches(1), pptx.util.Inches(2), pptx.util.Inches(4), pptx.util.Inches(1)).table
    table.cell(0, 0).text = "Market"
    table.cell(0, 1).text = "Signal"
    table.cell(1, 0).text = "Thailand"
    table.cell(1, 1).text = "Positive"
    slide.notes_slide.notes_text_frame.text = "Fictional speaker note"
    presentation.save(source)

    result = ingest_file(root, source, now=NOW)

    text = (root / result["text_path"]).read_text(encoding="utf-8")
    manifest = json.loads((root / result["manifest_path"]).read_text(encoding="utf-8"))
    assert "[SLIDE0001:TEXT0001] Project Atlas" in text
    assert "[SLIDE0001:TABLE0001:R0002:C0002] Positive" in text
    assert "[SLIDE0001:NOTES] Fictional speaker note" in text
    assert manifest["extraction"]["engine"]["name"] == "python-pptx"
    assert manifest["extraction"]["expected_units"] == 1
    assert manifest["extraction"]["units"][0]["anchor"] == "SLIDE0001"
    assert manifest["extraction"]["units"][0]["notes"] is True


def test_xlsx_extracts_sheet_cells_and_formulas_with_anchors(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    root = make_vault(tmp_path)
    source = root / "Inbox/signals.xlsx"
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Pipeline"
    worksheet["A1"] = "Company"
    worksheet["B1"] = "Signal"
    worksheet["A2"] = "Fictional Co"
    worksheet["B2"] = "=1+1"
    workbook.save(source)
    workbook.close()

    result = ingest_file(root, source, now=NOW)

    text = (root / result["text_path"]).read_text(encoding="utf-8")
    manifest = json.loads((root / result["manifest_path"]).read_text(encoding="utf-8"))
    assert "[SHEET0001] Pipeline" in text
    assert "[SHEET0001:A2] Fictional Co" in text
    assert "[SHEET0001:B2] =1+1" in text
    assert manifest["extraction"]["engine"]["name"] == "openpyxl"
    assert [unit["anchor"] for unit in manifest["extraction"]["units"]] == [
        "SHEET0001:A1",
        "SHEET0001:B1",
        "SHEET0001:A2",
        "SHEET0001:B2",
    ]
    assert manifest["extraction"]["units"][-1]["formula"] is True


def test_png_uses_rapidocr_and_records_region_confidence(tmp_path):
    image_module = pytest.importorskip("PIL.Image")
    draw_module = pytest.importorskip("PIL.ImageDraw")
    font_module = pytest.importorskip("PIL.ImageFont")
    root = make_vault(tmp_path)
    source = root / "Inbox/business-card.png"
    image = image_module.new("RGB", (1200, 420), "white")
    draw = draw_module.Draw(image)
    font = font_module.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64
    )
    draw.text((50, 60), "BRYAN TEST CARD", fill="black", font=font)
    draw.text((50, 180), "THAILAND 12345", fill="black", font=font)
    image.save(source)

    result = ingest_file(root, source, now=NOW)

    text = (root / result["text_path"]).read_text(encoding="utf-8")
    manifest = json.loads((root / result["manifest_path"]).read_text(encoding="utf-8"))
    extraction = manifest["extraction"]
    assert all(word in text for word in ("BRYAN", "TEST CARD", "THAILAND", "12345"))
    assert extraction["engine"]["name"] == "RapidOCR"
    assert extraction["detected_media_type"] == "image/png"
    assert extraction["average_confidence"] >= 0.5
    assert extraction["units"][0]["anchor"] == "OCR:R0001"
    assert len(extraction["units"][0]["bounding_box"]) == 4
    assert extraction["units"][0]["confidence"] >= 0.5


def test_image_rejects_extension_signature_mismatch(tmp_path):
    image_module = pytest.importorskip("PIL.Image")
    root = make_vault(tmp_path)
    source = root / "Inbox/renamed.jpg"
    image = image_module.new("RGB", (100, 100), "white")
    image.save(source, format="PNG")

    with pytest.raises(IngestError, match="image signature do not match"):
        ingest_file(root, source, now=NOW)


def test_mixed_pdf_uses_native_text_then_rapidocr_with_page_region_anchors(tmp_path):
    fitz = pytest.importorskip("fitz")
    image_module = pytest.importorskip("PIL.Image")
    draw_module = pytest.importorskip("PIL.ImageDraw")
    font_module = pytest.importorskip("PIL.ImageFont")
    root = make_vault(tmp_path)
    source = root / "Inbox/mixed.pdf"

    scanned = image_module.new("RGB", (1200, 420), "white")
    draw = draw_module.Draw(scanned)
    font = font_module.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64
    )
    draw.text((50, 100), "SCANNED PROJECT SIGNAL", fill="black", font=font)
    image_bytes = io.BytesIO()
    scanned.save(image_bytes, format="PNG")

    document = fitz.open()
    native_page = document.new_page()
    native_page.insert_text((72, 72), "Native project evidence")
    scanned_page = document.new_page(width=1200, height=420)
    scanned_page.insert_image(scanned_page.rect, stream=image_bytes.getvalue())
    document.save(source)
    document.close()

    result = ingest_file(root, source, now=NOW)

    text = (root / result["text_path"]).read_text(encoding="utf-8")
    manifest = json.loads((root / result["manifest_path"]).read_text(encoding="utf-8"))
    extraction = manifest["extraction"]
    assert "[P0001]\nNative project evidence" in text
    assert all(word in text for word in ("SCANNED", "PROJECT", "SIGNAL"))
    assert "[P0002:OCR:R0001]" in text
    assert extraction["engine"]["name"] == "PyMuPDF+RapidOCR"
    assert [unit["status"] for unit in extraction["units"]] == [
        "extracted",
        "ocr-extracted",
    ]
    assert extraction["units"][1]["regions"][0]["anchor"] == "P0002:OCR:R0001"
    assert extraction["units"][1]["average_confidence"] >= 0.5
