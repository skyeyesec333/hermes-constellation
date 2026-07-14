from datetime import UTC, datetime

from constellation.frontmatter import render_frontmatter
from constellation.intelligence import build_evidence_packet
from constellation.retrieval import build_index
from constellation.vault import initialize_vault


SOURCE = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def test_evidence_packet_is_bounded_and_hashes_exact_retrieved_evidence(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    (vault / "claims/cobalt.md").write_text(
        render_frontmatter(
            {
                "schema_version": "0.1",
                "id": "01ARZ3NDEKTSV4RRFFQ69G5FAW",
                "type": "claim",
                "title": "Fictional cobalt opportunity",
                "status": "active",
                "sensitivity": "internal",
                "created_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                "updated_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                "statement": "Fictional cobalt demand is increasing.",
                "source_ids": [SOURCE],
            },
            "Fictional cobalt demand is increasing.\n",
        ),
        encoding="utf-8",
    )
    build_index(vault)

    packet = build_evidence_packet(vault, "cobalt demand", limit=1, max_bytes=4096)

    assert packet["status"] == "evidence_ready"
    assert packet["query"] == "cobalt demand"
    assert len(packet["evidence"]) == 1
    assert packet["evidence"][0]["path"] == "claims/cobalt.md"
    assert packet["packet_sha256"]
    assert packet["bytes"] <= 4096
