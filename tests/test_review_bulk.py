"""Bulk promotion: owner-gated batch promote with dry-run plan + ordering."""
from datetime import UTC, datetime

import pytest

from constellation.frontmatter import render_frontmatter
from constellation.models import CandidatePatch, Sensitivity
from constellation.review import (
    list_candidates,
    plan_bulk_promotion,
    promote_candidates_bulk,
    write_candidate,
)
from constellation.vault import initialize_vault

NOW = datetime(2026, 2, 3, tzinfo=UTC)


def _entity_text(rid, title):
    return render_frontmatter(
        {
            "schema_version": "0.1",
            "id": rid,
            "type": "person",
            "title": title,
            "status": "active",
            "sensitivity": "internal",
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
            "aliases": [],
            "source_ids": [],
            "external_ids": {},
            "resolution_state": "unresolved",
        },
        f"# {title}\n",
    )


def _make_patch(root, cid, target_path, content, title, expected_base_hash=None):
    candidate = CandidatePatch(
        type="candidate-patch",
        id=cid,
        title=title,
        status="pending-review",
        sensitivity=Sensitivity.INTERNAL,
        created_at=NOW,
        updated_at=NOW,
        target_path=target_path,
        content=content,
        expected_base_hash=expected_base_hash,
    )
    return write_candidate(root, candidate)


@pytest.fixture()
def vault(tmp_path):
    root = tmp_path / "vault"
    initialize_vault(root)
    # an existing entity that two merge patches will target
    (root / "people").mkdir(exist_ok=True)
    (root / "people" / "keeper.md").write_text(
        _entity_text("01ARZ3NDEKTSV4RRFFQ69G5FAV", "Keeper Person"), encoding="utf-8"
    )
    # merge pair: keeper patch then stub patch (same target ordering matters)
    import hashlib

    keeper_hash = hashlib.sha256((root / "people" / "keeper.md").read_bytes()).hexdigest()
    _make_patch(
        root, "01JAAAAAAAAAAAAAAAAAAAAAA1", "people/keeper.md",
        _entity_text("01ARZ3NDEKTSV4RRFFQ69G5FAV", "Keeper Person"),
        "Merge duplicate person entities: keeper gets clean title + aliases",
        expected_base_hash=keeper_hash,
    )
    _make_patch(
        root, "01JAAAAAAAAAAAAAAAAAAAAAA2", "people/stub.md",
        _entity_text("01ARZ3NDEKTSV4RRFFQ69G5FAW", "Stub Person"),
        "Merge duplicate person entities: stub marked stale, points at keeper",
        expected_base_hash=None,
    )
    # an ingest candidate via the real ingest path
    from constellation.ingest import ingest_file

    feed = root / "Inbox/Files/feed.md"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text("# Fictional source\n\nSome evidence text.\n", encoding="utf-8")
    ingest_file(root, feed, now=NOW)
    return root


def _ingest_id(vault):
    return next(
        str(c["id"])
        for c in list_candidates(vault)
        if str(c.get("target_path") or "").startswith("source-items/")
    )


def test_plan_orders_ingests_before_merges_and_keeper_before_stub(vault):
    plan = plan_bulk_promotion(vault)
    ids = [str(i["id"]) for i in plan]
    assert ids == [
        _ingest_id(vault),  # source-items ingest first
        "01JAAAAAAAAAAAAAAAAAAAAAA1",  # keeper before stub (title sort)
        "01JAAAAAAAAAAAAAAAAAAAAAA2",
    ]


def test_plan_kind_and_prefix_filters(vault):
    only_merges = plan_bulk_promotion(vault, target_prefix="people/")
    assert [str(i["id"]) for i in only_merges] == [
        "01JAAAAAAAAAAAAAAAAAAAAAA1",
        "01JAAAAAAAAAAAAAAAAAAAAAA2",
    ]
    none = plan_bulk_promotion(vault, kinds={"claim_candidate"})
    assert none == []


def test_bulk_dry_run_changes_nothing(vault):
    before = len(list_candidates(vault))
    result = promote_candidates_bulk(vault, confirm=False)
    assert result["status"] == "dry_run"
    assert result["planned"] == before
    assert len(list_candidates(vault)) == before


def test_bulk_promotes_in_order_and_reports(vault):
    result = promote_candidates_bulk(vault, confirm=True)
    assert result["status"] == "completed"
    assert result["promoted"] == 3
    assert result["failed"] == []
    assert (vault / "people" / "stub.md").is_file()
    assert list_candidates(vault) == []


def test_bulk_continues_on_conflict_and_reports_failure(vault):
    # poison the keeper file so the keeper patch base hash no longer matches
    (vault / "people" / "keeper.md").write_text(
        _entity_text("01ARZ3NDEKTSV4RRFFQ69G5FAV", "Changed"), encoding="utf-8"
    )
    result = promote_candidates_bulk(vault, confirm=True)
    assert result["status"] == "completed"
    assert result["promoted"] == 2  # ingest + stub still land
    assert len(result["failed"]) == 1
    assert result["failed"][0]["id"] == "01JAAAAAAAAAAAAAAAAAAAAAA1"
    # failed candidate remains queued
    remaining = [str(c["id"]) for c in list_candidates(vault)]
    assert remaining == ["01JAAAAAAAAAAAAAAAAAAAAAA1"]


def _stage_legacy_ingest_packet(vault):
    """Write a legacy ingest_candidate packet (kind=ingest_candidate)."""
    import json as _json

    source_id = "01ARZ3NDEKTSV4RRFFQ69G5FAZ"
    text = render_frontmatter(
        {
            "schema_version": "0.1",
            "id": source_id,
            "type": "source-item",
            "title": "Legacy ingest",
            "status": "active",
            "sensitivity": "internal",
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
            "media_type": "text/markdown",
            "source_hash": "1" * 64,
            "original_path": "Inbox/Files/legacy-ingest.md",
        },
        "legacy body\n",
    )
    target = vault / "source-items" / f"{source_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    packet = {
        "schema_version": "0.1",
        "kind": "ingest_candidate",
        "status": "pending_review",
        "source_id": source_id,
        "source_hash": "1" * 64,
    }
    packet_path = vault / ".constellation" / "candidates" / f"ingest-{source_id}.json"
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(_json.dumps(packet), encoding="utf-8")
    return f"ingest-{source_id}"


def test_plan_groups_legacy_ingest_candidate_first(vault):
    legacy_id = _stage_legacy_ingest_packet(vault)
    plan = plan_bulk_promotion(vault)
    kinds = {str(i["id"]): str(i["kind"]) for i in plan}
    assert legacy_id in kinds
    assert kinds[legacy_id] == "ingest_candidate"
    # ingest group (0) precedes merges (2) regardless of title sort
    assert [str(i["id"]) for i in plan][0] == legacy_id


def test_plan_rejects_nonpositive_limit(vault):
    import pytest as _pytest

    with _pytest.raises(ValueError):
        plan_bulk_promotion(vault, limit=-2)


def test_bulk_isolates_non_promotion_errors(vault, monkeypatch):
    import constellation.review as review_module

    poisoned = "01JAAAAAAAAAAAAAAAAAAAAAA2"  # the stub patch
    original = review_module.promote_candidate

    def flaky(root, candidate_id, *, confirm, expected_base_hash):
        if candidate_id == poisoned:
            raise TypeError("simulated malformed packet")
        return original(
            root, candidate_id, confirm=confirm, expected_base_hash=expected_base_hash
        )

    monkeypatch.setattr(review_module, "promote_candidate", flaky)
    result = promote_candidates_bulk(vault, confirm=True)
    assert result["status"] == "completed"
    assert result["promoted"] == 2
    assert len(result["failed"]) == 1
    assert result["failed"][0]["id"] == poisoned
    assert "TypeError" in result["failed"][0]["error"]
