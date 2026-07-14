import json
from datetime import UTC, datetime

import pytest
import yaml
from pydantic import ValidationError

from constellation.cli import main
from constellation.frontmatter import render_frontmatter
from constellation.intelligence import (
    IntelligenceError,
    ProfessionalHypothesis,
    build_evidence_packet,
    classify_novelty,
    stage_strategy_candidate,
)
from constellation.retrieval import build_index
from constellation.vault import initialize_vault


SOURCE = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
NOTE = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
CANDIDATE = "01ARZ3NDEKTSV4RRFFQ69G5FAY"


def invoke(capsys, *args):
    assert main(args) == 0
    return json.loads(capsys.readouterr().out)


def indexed_packet(tmp_path):
    vault = tmp_path / "vault"
    initialize_vault(vault)
    (vault / "claims/cobalt.md").write_text(
        render_frontmatter(
            {
                "schema_version": "0.1",
                "id": NOTE,
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
    return vault, packet


def option_values():
    return {
        "move": "Run a bounded customer-discovery exercise before committing capital.",
        "rationale": "The evidence signals demand but does not establish willingness to pay.",
        "enabling_actors": ["Three fictional target customers"],
        "constraints": ["No capital commitment before customer evidence."],
        "expected_upside": "Clarify demand before making an irreversible investment.",
        "failure_modes": ["Interviews reflect curiosity rather than purchase intent."],
        "first_reversible_test": "Interview three target customers.",
        "kill_criteria": ["Stop if interviews show no material demand signal."],
    }


def candidate_input(tmp_path, packet, **updates):
    values = {
        "version": 1,
        "candidate_id": CANDIDATE,
        "status": "draft",
        "question": "Should the fictional company investigate cobalt demand?",
        "option": option_values(),
        "evidence_packet_sha256": packet["packet_sha256"],
        "evidence_note_ids": [NOTE],
        "confidence": 0.55,
        "assumptions": ["The indexed claim remains current."],
        "uncertainties": ["Customer willingness to pay is unknown."],
        "falsifiers": ["Three target customers report no expected demand growth."],
        "next_tests": ["Interview three target customers."],
        "human_review_required": True,
    }
    values.update(updates)
    path = tmp_path / "candidate.yaml"
    path.write_text(yaml.safe_dump(values, sort_keys=True), encoding="utf-8")
    return path


def test_evidence_packet_is_bounded_and_hashes_exact_retrieved_evidence(tmp_path):
    _, packet = indexed_packet(tmp_path)

    assert packet["status"] == "evidence_ready"
    assert packet["query"] == "cobalt demand"
    assert len(packet["evidence"]) == 1
    assert packet["evidence"][0]["path"] == "claims/cobalt.md"
    assert packet["packet_sha256"]
    assert packet["bytes"] <= 4096


def test_strategy_candidate_is_staged_for_review_without_canonical_write(tmp_path):
    vault, packet = indexed_packet(tmp_path)
    input_path = candidate_input(tmp_path, packet)

    result = stage_strategy_candidate(vault, packet, input_path)

    staged = vault / ".constellation/candidates" / f"strategy-{CANDIDATE}.yaml"
    staged_packet = vault / ".constellation/candidates" / f"evidence-{packet['packet_sha256']}.json"
    assert result == {
        "status": "draft",
        "candidate_id": CANDIDATE,
        "evidence_packet_sha256": packet["packet_sha256"],
        "path": staged.relative_to(vault).as_posix(),
        "packet_path": staged_packet.relative_to(vault).as_posix(),
    }
    assert staged.is_file()
    assert staged_packet.is_file()
    assert not list((vault / "claims").glob("*strategy*"))


def test_strategy_candidate_rejects_a_tampered_evidence_packet(tmp_path):
    vault, packet = indexed_packet(tmp_path)
    input_path = candidate_input(tmp_path, packet)
    tampered = {**packet, "query": "different question"}

    with pytest.raises(IntelligenceError, match="hash or byte count"):
        stage_strategy_candidate(vault, tampered, input_path)


def test_strategy_candidate_rejects_evidence_outside_its_packet(tmp_path):
    vault, packet = indexed_packet(tmp_path)
    input_path = candidate_input(
        tmp_path,
        packet,
        evidence_note_ids=["01ARZ3NDEKTSV4RRFFQ69G5FB0"],
    )

    with pytest.raises(IntelligenceError, match="outside its packet"):
        stage_strategy_candidate(vault, packet, input_path)


def test_strategy_candidate_requires_explicit_kill_criteria(tmp_path):
    vault, packet = indexed_packet(tmp_path)
    option = option_values()
    option.pop("kill_criteria")
    input_path = candidate_input(tmp_path, packet, option=option)

    with pytest.raises(ValidationError, match="kill_criteria"):
        stage_strategy_candidate(vault, packet, input_path)


def test_strategy_candidate_rejects_unstructured_option_text(tmp_path):
    vault, packet = indexed_packet(tmp_path)
    input_path = candidate_input(tmp_path, packet, option="Do it.")

    with pytest.raises(ValidationError, match="option"):
        stage_strategy_candidate(vault, packet, input_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [("move", "   "), ("failure_modes", ["   "])],
)
def test_strategy_candidate_rejects_blank_option_content(tmp_path, field, value):
    vault, packet = indexed_packet(tmp_path)
    option = option_values()
    option[field] = value
    input_path = candidate_input(tmp_path, packet, option=option)

    with pytest.raises(ValidationError, match=field):
        stage_strategy_candidate(vault, packet, input_path)


def test_strategy_cli_builds_a_packet_then_stages_its_review_candidate(tmp_path, capsys):
    vault, _ = indexed_packet(tmp_path)

    packet_output = invoke(
        capsys,
        "strategy",
        str(vault),
        "packet",
        "--query",
        "cobalt demand",
        "--limit",
        "1",
        "--max-bytes",
        "4096",
    )
    packet = packet_output["result"]
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(packet_output), encoding="utf-8")
    input_path = candidate_input(tmp_path, packet)

    staged_output = invoke(
        capsys,
        "strategy",
        str(vault),
        "stage",
        "--packet",
        str(packet_path),
        "--input",
        str(input_path),
    )

    assert staged_output["result"]["status"] == "draft"
    assert staged_output["result"]["candidate_id"] == CANDIDATE


def test_strategy_candidate_cannot_overwrite_an_existing_review_artifact(tmp_path):
    vault, packet = indexed_packet(tmp_path)
    input_path = candidate_input(tmp_path, packet)
    stage_strategy_candidate(vault, packet, input_path)

    with pytest.raises(IntelligenceError, match="already exists"):
        stage_strategy_candidate(vault, packet, input_path)


def test_strategy_candidate_rejects_a_tampered_stored_packet(tmp_path):
    vault, packet = indexed_packet(tmp_path)
    first_input = candidate_input(tmp_path, packet)
    first = stage_strategy_candidate(vault, packet, first_input)
    (vault / first["packet_path"]).write_text("{}\n", encoding="utf-8")
    second_input = candidate_input(
        tmp_path,
        packet,
        candidate_id="01ARZ3NDEKTSV4RRFFQ69G5FB0",
    )

    with pytest.raises(IntelligenceError, match="does not match its hash"):
        stage_strategy_candidate(vault, packet, second_input)


@pytest.mark.parametrize(
    ("new_facts", "existing_note_ids", "identity_resolved", "contradictions", "expected"),
    [
        ([], [], False, False, "needs_identity_resolution"),
        ([], [], True, True, "contradiction_only"),
        ([], [], True, False, "insufficient_novelty"),
        (["Verified mandate update"], [NOTE], True, False, "update_existing"),
        (["Verified new signal"], [], True, False, "novel"),
    ],
)
def test_novelty_classification_blocks_empty_or_unresolved_note_creation(
    new_facts, existing_note_ids, identity_resolved, contradictions, expected
):
    assessment = classify_novelty(
        new_facts=new_facts,
        existing_note_ids=existing_note_ids,
        source_note_ids=[SOURCE],
        identity_resolved=identity_resolved,
        contradictions=contradictions,
        why_it_matters="It determines whether the delta can change a decision.",
        uncertainties=["The source could be incomplete."],
    )

    assert assessment.classification == expected
    assert assessment.why_it_matters
    assert assessment.uncertainties


def test_professional_hypotheses_are_evidence_backed_and_reject_covert_profiling():
    hypothesis = ProfessionalHypothesis(
        dimension="decision_tempo",
        observation="The executive requested a one-week diligence window.",
        working_hypothesis="The current decision cadence appears deliberate; verify it in the next meeting.",
        source_note_ids=[SOURCE],
    )

    assert hypothesis.is_working_hypothesis is True
    with pytest.raises(ValidationError, match="professional hypothesis"):
        ProfessionalHypothesis(
            dimension="decision_tempo",
            observation="The executive requested a one-week diligence window.",
            working_hypothesis="This reveals a mental health diagnosis.",
            source_note_ids=[SOURCE],
        )


def test_strategy_candidate_rejects_covert_professional_profiling(tmp_path):
    vault, packet = indexed_packet(tmp_path)
    input_path = candidate_input(
        tmp_path,
        packet,
        professional_hypotheses=[
            {
                "dimension": "decision_tempo",
                "observation": "The executive requested a one-week diligence window.",
                "working_hypothesis": "This reveals a mental health diagnosis.",
                "source_note_ids": [SOURCE],
            }
        ],
    )

    with pytest.raises(ValidationError, match="professional hypothesis"):
        stage_strategy_candidate(vault, packet, input_path)
