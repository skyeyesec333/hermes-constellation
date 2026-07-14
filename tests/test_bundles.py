import json

import pytest

from constellation.bundles import BundleError, create_evidence_bundle
from constellation.cli import main
from constellation.vault import initialize_vault


def test_meeting_bundle_preserves_distinct_evidence_and_is_idempotent(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    members = [
        {"source_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "role": "audio-original", "sha256": "a" * 64},
        {"source_id": "01ARZ3NDEKTSV4RRFFQ69G5FAW", "role": "tactiq-transcript", "sha256": "b" * 64},
        {"source_id": "01ARZ3NDEKTSV4RRFFQ69G5FAX", "role": "typed-notes", "sha256": "c" * 64},
    ]

    first = create_evidence_bundle(vault, kind="meeting", title="Fictional meeting", members=members)
    second = create_evidence_bundle(vault, kind="meeting", title="Fictional meeting", members=members)

    assert first == second
    manifest = json.loads((vault / first["path"]).read_text(encoding="utf-8"))
    assert manifest["kind"] == "meeting"
    assert [member["role"] for member in manifest["members"]] == [
        "audio-original",
        "tactiq-transcript",
        "typed-notes",
    ]
    assert manifest["canonical_candidates"] == []


def test_bundle_cli_creates_review_only_manifest(tmp_path, capsys):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    members = tmp_path / "members.json"
    members.write_text(
        json.dumps(
            [{"source_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "role": "typed-notes", "sha256": "a" * 64}]
        ),
        encoding="utf-8",
    )

    assert main(
        [
            "bundle",
            str(vault),
            "create",
            "--kind",
            "meeting",
            "--title",
            "Fictional meeting",
            "--members",
            str(members),
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)["result"]
    assert result["status"] == "created"
    assert not list((vault / "entities").glob("*.md"))


def test_bundle_rejects_duplicate_members_and_unsafe_roles(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    member = {"source_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "role": "typed-notes", "sha256": "a" * 64}

    with pytest.raises(BundleError):
        create_evidence_bundle(vault, kind="meeting", title="Fictional", members=[member, member])
    with pytest.raises(BundleError):
        create_evidence_bundle(
            vault,
            kind="meeting",
            title="Fictional",
            members=[{**member, "role": "invented-speaker-identity"}],
        )
