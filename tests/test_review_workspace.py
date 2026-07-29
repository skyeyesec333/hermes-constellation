"""Tests for the source-to-entity review workspace."""

import json
from datetime import UTC, datetime
from pathlib import Path

from constellation.frontmatter import render_frontmatter
from constellation.models import Sensitivity, SourceItem, generate_ulid
from constellation.review_workspace import build_review_workspace, render_review_workspace
from constellation.storage import atomic_write_text
from constellation.vault import initialize_vault

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _setup(tmp_path: Path) -> tuple[Path, str]:
    vault = tmp_path / "vault"
    initialize_vault(vault)
    source_id = generate_ulid()
    text_path = Path("Library/Text") / f"{source_id}.txt"
    atomic_write_text(
        vault, text_path,
        "TestCo announced expansion into Thailand logistics.\n\n"
        "The BOI FastPass program offers incentives for regional hubs.\n",
    )
    source = SourceItem(
        id=source_id, type="source-item", title="TestCo deck", status="active",
        sensitivity=Sensitivity.INTERNAL,
        source_hash="ab" * 32, original_path="Library/Files/deck.pdf",
        media_type="application/pdf",
        extracted_text_path=text_path.as_posix(),
        extraction_status="complete",
        created_at=NOW, updated_at=NOW,
    )
    (vault / "source-items" / f"{source_id}.md").write_text(
        render_frontmatter(source.model_dump(mode="json", exclude_none=True), "# TestCo deck\n"),
        encoding="utf-8",
    )
    return vault, source_id


def _stage_candidate(vault: Path, source_id: str) -> str:
    candidate_id = generate_ulid()
    atomic_write_text(
        vault,
        Path(".constellation/candidates") / f"{candidate_id}.json",
        json.dumps({
            "schema_version": "0.1", "id": candidate_id,
            "type": "candidate-patch", "title": "claim: TestCo expands",
            "status": "pending-review", "sensitivity": "internal",
            "source_ids": [source_id],
            "content": f"claim staged from {source_id}",
            "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
        }) + "\n",
    )
    return candidate_id


def test_workspace_projects_source_anchors_and_candidates(tmp_path: Path) -> None:
    vault, source_id = _setup(tmp_path)
    candidate_id = _stage_candidate(vault, source_id)

    workspace = build_review_workspace(vault, source_id)

    assert workspace["source"]["id"] == source_id
    assert workspace["source"]["media_type"] == "application/pdf"
    anchors = workspace["anchors"]
    assert len(anchors) >= 2
    assert all(a["anchor_id"] and a["text"] for a in anchors)
    related = workspace["related_candidates"]
    assert any(c["id"] == candidate_id and c["status"] == "pending-review" for c in related)
    for candidate in related:
        assert candidate["approve_command"]
        assert candidate["reject_command"]


def test_workspace_without_candidates_is_honest(tmp_path: Path) -> None:
    vault, source_id = _setup(tmp_path)

    workspace = build_review_workspace(vault, source_id)

    assert workspace["related_candidates"] == []
    assert workspace["empty"] is True


def test_rendered_workspace_is_offline_and_anchored(tmp_path: Path) -> None:
    vault, source_id = _setup(tmp_path)
    _stage_candidate(vault, source_id)

    html = render_review_workspace(build_review_workspace(vault, source_id))

    assert "http://" not in html and "https://" not in html
    assert "<script" not in html
    assert "Thailand logistics" in html
    assert "approve" in html.lower()
    assert "--profile" in html  # research-depth selector visible


def test_workspace_cli_writes_with_confirmation(tmp_path: Path) -> None:
    from constellation.cli import build_parser, run_action

    vault, source_id = _setup(tmp_path)
    output = tmp_path / "review.html"

    values = vars(build_parser().parse_args(
        ["source-review", str(vault), source_id, "--output", str(output)]
    ))
    result = run_action(str(values.pop("command")), values)

    assert result["status"] == "written"
    assert result["bytes_written"] == output.stat().st_size
    assert "TestCo" in output.read_text(encoding="utf-8")
