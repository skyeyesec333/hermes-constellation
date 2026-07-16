from datetime import UTC, datetime

from constellation.frontmatter import render_frontmatter
from constellation.retrieval import build_index, exact_lookup, search
from constellation.vault import initialize_vault

SOURCE = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def note(note_id, title, predicate, object_literal, sensitivity="internal"):
    return render_frontmatter(
        {
            "schema_version": "0.1",
            "id": note_id,
            "type": "claim",
            "title": title,
            "status": "active",
            "sensitivity": sensitivity,
            "created_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "updated_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "subject_id": SOURCE,
            "predicate": predicate,
            "object_literal": object_literal,
            "source_ids": [SOURCE],
        },
        f"# {title}\n\n{object_literal}\n",
    )


def make_vault(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    (root / "claims/public.md").write_text(
        note("01ARZ3NDEKTSV4RRFFQ69G5FAW", "Public nebula", "located_in", "azure nebula", "public"),
        encoding="utf-8",
    )
    (root / "claims/internal.md").write_text(
        note("01ARZ3NDEKTSV4RRFFQ69G5FAX", "Internal comet", "classified_as", "copper comet"),
        encoding="utf-8",
    )
    (root / ".constellation/candidates/hidden.md").write_text(
        note("01ARZ3NDEKTSV4RRFFQ69G5FAY", "Candidate", "named", "candidate-only quasar"),
        encoding="utf-8",
    )
    return root


def test_build_supports_exact_id_and_fts_with_versioned_evidence(tmp_path):
    root = make_vault(tmp_path)
    report = build_index(root)
    assert report["indexed"] == 2
    human_index = (root / "INDEX.md").read_text(encoding="utf-8")
    assert "# Constellation — Canonical Index" in human_index
    assert "[[claims/public|Public nebula]]" in human_index
    assert "[[claims/internal|Internal comet]]" in human_index
    assert "Candidate" not in human_index
    exact = exact_lookup(root, "01ARZ3NDEKTSV4RRFFQ69G5FAW")
    assert exact["status"] == "evidence_found"
    assert exact["evidence"][0]["route"] == "exact_id"
    packet = search(root, "azure nebula")
    assert packet["schema_version"] == "0.1"
    assert packet["packet_version"] == "2"
    evidence = packet["evidence"][0]
    assert set(evidence) == {
        "note_id", "path", "anchor", "source_hash", "sensitivity", "route", "score"
    }
    assert len(evidence["anchor"]) <= 240


def test_search_returns_the_matching_pdf_page_and_line_anchor(tmp_path):
    root = make_vault(tmp_path)
    source = render_frontmatter(
        {
            "schema_version": "0.1",
            "id": SOURCE,
            "type": "source-item",
            "title": "Anchored PDF",
            "status": "active",
            "sensitivity": "internal",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "updated_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "source_hash": "a" * 64,
            "original_path": "Library/Files/anchored.pdf",
            "media_type": "application/pdf",
            "extracted_text_path": "Library/Text/anchored.txt",
            "extraction_manifest_path": ".constellation/manifests/anchored.json",
            "extraction_status": "complete",
        },
        "# Anchored PDF\n\n[P0001]\nUnrelated first page.\n\n[P0002]\nUnrelated line.\n"
        "Fictional cobalt harbor evidence.\n",
    )
    (root / "source-items/anchored.md").write_text(source, encoding="utf-8")
    build_index(root)

    packet = search(root, "cobalt harbor")
    evidence = packet["evidence"]
    assert isinstance(evidence, list) and evidence
    result = evidence[0]
    assert isinstance(result, dict)

    assert result["path"] == "source-items/anchored.md"
    assert result["anchor"].startswith("P0002:L0002 | Fictional cobalt harbor evidence.")


def test_candidates_are_excluded_and_sensitivity_ceiling_is_enforced(tmp_path):
    root = make_vault(tmp_path)
    build_index(root)
    assert search(root, "candidate-only")["status"] == "no_evidence_found"
    assert search(root, "copper comet", sensitivity_ceiling="public")["status"] == "no_evidence_found"
    assert search(root, "copper comet", sensitivity_ceiling="internal")["status"] == "evidence_found"


def test_missing_or_stale_index_reports_not_retrieved(tmp_path):
    root = make_vault(tmp_path)
    assert search(root, "nebula")["status"] == "evidence_not_retrieved"
    build_index(root)
    (root / "claims/public.md").write_text(
        note("01ARZ3NDEKTSV4RRFFQ69G5FAW", "Changed", "described_as", "changed evidence"), encoding="utf-8"
    )
    assert search(root, "nebula")["status"] == "evidence_not_retrieved"


def test_rebuild_removes_deleted_notes_and_prunes_old_generation(tmp_path):
    root = make_vault(tmp_path)
    first = build_index(root)
    first_database = root / ".constellation/state" / f"index-{first['generation']}.sqlite3"
    assert first_database.is_file()
    (root / "claims/public.md").unlink()
    second = build_index(root)
    second_database = root / ".constellation/state" / f"index-{second['generation']}.sqlite3"
    assert second_database.is_file()
    assert not first_database.exists()
    assert exact_lookup(root, "01ARZ3NDEKTSV4RRFFQ69G5FAW")["status"] == "no_evidence_found"
