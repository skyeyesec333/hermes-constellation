from constellation.longform import build_document_map, segment_document
from constellation.segment_index import build_segment_index, search_segments
from constellation.vault import initialize_vault


def test_document_map_preserves_heading_hierarchy_and_anchors():
    result = build_document_map(
        source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        text=(
            "# Market Strategy\n\n"
            "Intro paragraph.\n\n"
            "## Competitive landscape\n\n"
            "Rivals remain fragmented.\n\n"
            "## Capital plan\n\n"
            "Raise only after traction.\n"
        ),
    )

    assert result["status"] == "review-required"
    assert result["title"] == "Market Strategy"
    assert [node["title"] for node in result["nodes"]] == [
        "Market Strategy",
        "Competitive landscape",
        "Capital plan",
    ]
    assert result["nodes"][1]["anchor"].startswith("H")
    assert result["whole_document_prompt_allowed"] is False


def test_segments_are_stable_and_bounded_with_source_anchors():
    text = "\n\n".join(f"Section {i}. " + ("evidence " * 120) for i in range(1, 6))
    mapped = build_document_map(source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", text=text)
    first = segment_document(document_map=mapped, text=text, target_tokens=200)
    second = segment_document(document_map=mapped, text=text, target_tokens=200)

    assert first["status"] == "review-required"
    assert first["segments"]
    assert all(seg["estimated_tokens"] <= 250 for seg in first["segments"])
    assert [seg["segment_id"] for seg in first["segments"]] == [
        seg["segment_id"] for seg in second["segments"]
    ]
    assert all(seg["anchor"] for seg in first["segments"])


def test_segment_index_is_rebuildable_and_returns_source_anchors(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    text = "Alpha thesis remains open.\n\nBeta checkpoint needs evidence."
    mapped = build_document_map(source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", text=text)
    segmented = segment_document(document_map=mapped, text=text, target_tokens=80)

    built = build_segment_index(vault, source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", segments=segmented["segments"])
    rebuilt = build_segment_index(
        vault, source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", segments=segmented["segments"]
    )
    hits = search_segments(vault, source_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", query="Alpha thesis")

    assert built["status"] == "built"
    assert rebuilt["status"] == "rebuilt"
    assert hits
    assert hits[0]["source_id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert hits[0]["anchor"]
