"""Command-line interface for the Constellation core."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="constellation")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize a new vault")
    init.add_argument("vault", type=Path)

    doctor = sub.add_parser("doctor", help="Inspect vault health")
    doctor.add_argument("vault", type=Path)

    operator = sub.add_parser("operator", help="Stage or activate a local manual operator context")
    operator.add_argument("vault", type=Path)
    operator.add_argument("operator_action", choices=["stage", "activate", "status", "delete"])
    operator.add_argument("--input", type=Path)
    operator.add_argument("--confirm", action="store_true")

    strategy = sub.add_parser("strategy", help="Build bounded evidence or stage a review-only option")
    strategy.add_argument("vault", type=Path)
    strategy.add_argument("strategy_action", choices=["packet", "stage"])
    strategy.add_argument("--query")
    strategy.add_argument("--limit", type=int, default=10)
    strategy.add_argument("--max-bytes", type=int, default=32_768)
    strategy.add_argument("--sensitivity", default="internal")
    strategy.add_argument("--packet", type=Path)
    strategy.add_argument("--input", type=Path)

    graph = sub.add_parser("graph", help="Query bounded sourced relationship paths")
    graph.add_argument("vault", type=Path)
    graph.add_argument("graph_action", choices=["neighbors", "path"])
    graph.add_argument("--entity")
    graph.add_argument("--from", dest="start_entity")
    graph.add_argument("--to", dest="end_entity")
    graph.add_argument("--max-hops", type=int, default=4)

    resolve = sub.add_parser("resolve", help="Propose review-only identity matches")
    resolve.add_argument("vault", type=Path)
    resolve.add_argument("action", choices=["propose"])

    ingest = sub.add_parser("ingest", help="Preserve a local source and stage its canonical candidate")
    ingest.add_argument("vault", type=Path)
    ingest.add_argument("source", type=Path)
    ingest.add_argument("--source-url", help="Original capture URL; Constellation never fetches it")
    ingest.add_argument(
        "--kind",
        choices=["generic", "business-card", "pdf-deck", "meeting-transcript", "meeting-notes", "long-form", "gmail-capture"],
        default="generic",
    )
    ingest.add_argument(
        "--phone-region",
        help="Explicit ISO region for business-card phone normalization (for example, US)",
    )
    ingest.add_argument(
        "--meeting-format",
        choices=["tactiq", "meetily", "openwhispr", "generic"],
        help="Optional meeting export format hint for meeting-transcript intake",
    )

    preflight = sub.add_parser("preflight", help="Plan bounded local processing without ingesting")
    preflight.add_argument("vault", type=Path)
    preflight.add_argument("source", type=Path)
    preflight.add_argument(
        "--task",
        required=True,
        choices=[
            "business_card",
            "meeting",
            "deck",
            "paper",
            "book",
            "email_refresh",
            "competitive_analysis",
        ],
    )

    bundle = sub.add_parser("bundle", help="Create a review-only compound evidence manifest")
    bundle.add_argument("vault", type=Path)
    bundle.add_argument("action", choices=["create"])
    bundle.add_argument("--kind", required=True, choices=["meeting", "deck", "business-card", "long-document"])
    bundle.add_argument("--title", required=True)
    bundle.add_argument("--members", type=Path, required=True)

    validate = sub.add_parser("validate", help="Validate canonical records")
    validate.add_argument("vault", type=Path)
    validate.add_argument("--limit", type=int, default=100)

    index = sub.add_parser("index", help="Rebuild the SQLite FTS index")
    index.add_argument("vault", type=Path)

    search = sub.add_parser("search", help="Search canonical evidence")
    search.add_argument("vault", type=Path)
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--sensitivity", default="internal")

    review = sub.add_parser("review", help="List or promote candidates")
    review.add_argument("vault", type=Path)
    review.add_argument("action", choices=["list", "promote"])
    review.add_argument("--candidate")
    review.add_argument("--expected-base-hash")
    review.add_argument("--confirm", action="store_true")

    research = sub.add_parser("research", help="Create or inspect a research receipt")
    research.add_argument("vault", type=Path)
    research.add_argument("action", choices=["start", "status"])
    research.add_argument("--run-id")
    research.add_argument(
        "--task",
        choices=[
            "business_card",
            "meeting",
            "deck",
            "paper",
            "book",
            "email_refresh",
            "competitive_analysis",
        ],
        help="Optional task profile for start; applies synthesis-aware budget defaults",
    )

    synthesize = sub.add_parser("synthesize", help="Plan task-specific synthesis budgets without calling a model")
    synthesize.add_argument("vault", type=Path)
    synthesize.add_argument("action", choices=["plan"])
    synthesize.add_argument(
        "--task",
        required=True,
        choices=[
            "business_card",
            "meeting",
            "deck",
            "paper",
            "book",
            "email_refresh",
            "competitive_analysis",
        ],
    )
    synthesize.add_argument("--source-bytes", type=int, default=0)
    synthesize.add_argument("--pages", type=int)
    synthesize.add_argument("--audio-minutes", type=float)

    claim = sub.add_parser("claim", help="Stage or list review-only claims")
    claim.add_argument("vault", type=Path)
    claim.add_argument("action", choices=["stage", "list"])
    claim.add_argument("--subject-id", help="ULID of the subject entity (required for stage)")
    claim.add_argument("--predicate", help="Relationship predicate, e.g. works_at (required for stage)")
    claim.add_argument("--object-id", help="ULID of the object entity")
    claim.add_argument("--object-literal", help="Literal value when no object entity exists")
    claim.add_argument("--source-ids", nargs="+", help="ULIDs of supporting sources (required for stage)")
    claim.add_argument("--evidence-anchor", help="Section/page anchor in the source")
    claim.add_argument("--evidence-excerpt", help="Short quoted excerpt from source")
    claim.add_argument("--claim-status", default="source-claimed",
                       choices=["source-claimed", "corroborated", "disputed", "inferred", "superseded", "stale"])
    claim.add_argument("--confidence", type=float)
    claim.add_argument("--limit", type=int, default=50, help="Max claims to list")

    interaction = sub.add_parser("interaction", help="Stage or list review-only interactions")
    interaction.add_argument("vault", type=Path)
    interaction.add_argument("action", choices=["stage", "list"])
    interaction.add_argument("--subject-ids", nargs="+", help="ULIDs of primary subjects (required for stage)")
    interaction.add_argument("--interaction-type", default="meeting",
                             choices=["meeting", "call", "email", "introduction", "conference", "other"])
    interaction.add_argument("--participants", nargs="+", help="ULIDs of all participants")
    interaction.add_argument("--channel", default="in-person", help="Channel: in-person, zoom, phone, email, whatsapp")
    interaction.add_argument("--summary", help="What happened")
    interaction.add_argument("--follow-ups", nargs="+", help="Follow-up items")
    interaction.add_argument("--source-ids", nargs="+", help="ULIDs of evidence sources")
    interaction.add_argument("--location", help="Where it occurred")
    interaction.add_argument("--limit", type=int, default=50, help="Max to list")

    decision = sub.add_parser("decision", help="Stage or list review-only decisions")
    decision.add_argument("vault", type=Path)
    decision.add_argument("action", choices=["stage", "list"])
    decision.add_argument("--subject-id", help="ULID of the subject entity (required for stage)")
    decision.add_argument("--decision", dest="decision_text", help="What was decided (required for stage)")
    decision.add_argument("--rationale", help="Why this decision was made")
    decision.add_argument("--options-considered", nargs="+", help="Alternatives that were rejected")
    decision.add_argument("--assumptions", nargs="+", help="What was assumed")
    decision.add_argument("--owner", help="Who owns this decision")
    decision.add_argument("--source-ids", nargs="+", help="ULIDs of evidence sources")
    decision.add_argument("--limit", type=int, default=50, help="Max to list")

    inquiry = sub.add_parser("inquiry", help="Stage or list research inquiries")
    inquiry.add_argument("vault", type=Path)
    inquiry.add_argument("action", choices=["stage", "list"])
    inquiry.add_argument("--question", help="Research question (required for stage)")
    inquiry.add_argument("--why", help="Why this matters")
    inquiry.add_argument("--scope", help="Target scope")
    inquiry.add_argument("--evidence-needed", help="What kind of evidence")
    inquiry.add_argument("--source-priority", default="primary",
                          choices=["primary", "primary-and-secondary", "any"])
    inquiry.add_argument("--subject-ids", nargs="+", help="ULIDs of relevant entities")
    inquiry.add_argument("--max-searches", type=int, default=5, help="Max search queries")
    inquiry.add_argument("--max-sources", type=int, default=10, help="Max unique sources")
    inquiry.add_argument("--max-model-calls", type=int, default=3, help="Max LLM calls")
    inquiry.add_argument("--synthesis-reserve", type=int, default=25, help="Reserve % for synthesis (0-50)")
    inquiry.add_argument("--stop-conditions", nargs="+", help="When to stop")
    inquiry.add_argument("--promotion-policy", default="review-all",
                          choices=["review-all", "auto-source-only", "manual-only"])
    inquiry.add_argument("--limit", type=int, default=50, help="Max to list")

    opportunity = sub.add_parser("opportunity", help="Stage or list review-only opportunities")
    opportunity.add_argument("vault", type=Path)
    opportunity.add_argument("action", choices=["stage", "list"])
    opportunity.add_argument("--subject-ids", nargs="+", help="ULIDs of linked people/companies (required for stage)")
    opportunity.add_argument("--stage", default="test",
                             choices=["test", "review", "qualifying", "proposal", "negotiation",
                                      "closed-won", "closed-lost", "on-hold"])
    opportunity.add_argument("--probability", type=float, help="Estimated probability 0.0-1.0")
    opportunity.add_argument("--expected-value", help="Expected value if applicable")
    opportunity.add_argument("--next-action", help="Concrete next step")
    opportunity.add_argument("--feeding-interactions", nargs="+", help="ULIDs of related interactions")
    opportunity.add_argument("--supporting-claims", nargs="+", help="ULIDs of supporting claims")
    opportunity.add_argument("--supporting-decisions", nargs="+", help="ULIDs of supporting decisions")
    opportunity.add_argument("--source-ids", nargs="+", help="ULIDs of evidence sources")
    opportunity.add_argument("--kanban-card", help="Path to the PM kanban card")
    opportunity.add_argument("--limit", type=int, default=50, help="Max to list")

    lead = sub.add_parser(
        "lead",
        help="Conference lead capture into Project Manager CRM notes (review-only drafts)",
    )
    lead.add_argument("vault", type=Path)
    lead.add_argument("action", choices=["capture"])
    lead.add_argument("--event", required=True, help="Event name, e.g. InfoComm Asia")
    lead.add_argument("--date", required=True, help="Event date YYYY-MM-DD")
    lead.add_argument("--project", required=True, help="Project Manager project title")
    lead.add_argument("--card", required=True, type=Path, help="Card image path inside the vault")
    lead.add_argument("--venue", help="Venue name")
    lead.add_argument("--note", help="How you met them / conversation hook")
    lead.add_argument(
        "--channel",
        choices=["whatsapp", "sms", "email", "unknown"],
        default="whatsapp",
    )
    lead.add_argument("--phone-region", help="ISO region for card phone normalization")
    lead.add_argument("--where", help="Booth / hall / panel location")
    lead.add_argument(
        "--todos",
        nargs="*",
        default=None,
        help="Confirmed per-card todos to attach to the Project Manager task (only after Bryan finalizes the list)",
    )

    migrate = sub.add_parser("migrate-plan", help="Inventory a legacy vault without writing")
    migrate.add_argument("vault", type=Path)
    migrate.add_argument("--action-limit", type=int, default=1_000)
    migrate.add_argument("--max-files", type=int, default=100_000)

    rehearse = sub.add_parser(
        "migrate-rehearse", help="Build a destination-only disposable migration rehearsal"
    )
    rehearse.add_argument("vault", type=Path)
    rehearse.add_argument("destination", type=Path)
    rehearse.add_argument("--max-files", type=int, default=100_000)
    rehearse.add_argument("--confirm-disposable", action="store_true")

    prepare = sub.add_parser(
        "migrate-prepare", help="Build a verified sibling vault for canonical cutover"
    )
    prepare.add_argument("vault", type=Path)
    prepare.add_argument("rehearsal", type=Path)
    prepare.add_argument("destination", type=Path)
    prepare.add_argument("--expected-source-sha256", required=True)
    prepare.add_argument("--confirm-apply-staging", action="store_true")

    activate = sub.add_parser(
        "migrate-activate", help="Atomically activate a prepared vault with rollback"
    )
    activate.add_argument("vault", type=Path)
    activate.add_argument("prepared", type=Path)
    activate.add_argument("rollback", type=Path)
    activate.add_argument("--expected-source-sha256", required=True)
    activate.add_argument("--confirm-canonical-apply", action="store_true")

    migrate_entities = sub.add_parser("migrate-entities", help="Plan or execute legacy entity migration into canonical entities/")
    migrate_entities.add_argument("vault", type=Path)
    migrate_entities.add_argument("action", choices=["plan", "execute"])
    migrate_entities.add_argument("--dry-run", action="store_true", default=True,
                                  help="Preview without changes (default)")
    migrate_entities.add_argument("--apply", dest="dry_run", action="store_false",
                                  help="Execute the migration")

    return parser


def run_action(action: str, values: dict[str, Any]) -> Any:
    """Dispatch through the shared core. Imports are lazy for plugin startup safety."""
    vault = Path(values["vault"]).expanduser()
    if action == "init":
        from constellation.vault import initialize_vault

        return initialize_vault(vault)
    if action == "doctor":
        from constellation.doctor import doctor_report

        return doctor_report(vault)
    if action == "operator":
        from constellation.operator import (
            activate_operator_context,
            delete_operator_context,
            operator_context_status,
            stage_operator_context,
        )

        if values["operator_action"] == "status":
            return operator_context_status(vault)
        if values["operator_action"] == "delete":
            return delete_operator_context(vault, confirm=bool(values.get("confirm")))
        if values["operator_action"] == "activate":
            context = activate_operator_context(vault, confirm=bool(values.get("confirm")))
        else:
            input_path = values.get("input")
            if input_path is None:
                raise ValueError("operator stage requires --input")
            context = stage_operator_context(vault, Path(input_path).expanduser())
        return {"status": context.status, "version": context.version}
    if action == "strategy":
        from constellation.intelligence import build_evidence_packet, stage_strategy_candidate

        if values["strategy_action"] == "packet":
            query = values.get("query")
            if not query:
                raise ValueError("strategy packet requires --query")
            return build_evidence_packet(
                vault,
                str(query),
                limit=int(values["limit"]),
                max_bytes=int(values["max_bytes"]),
                sensitivity_ceiling=str(values["sensitivity"]),
            )
        packet_path = values.get("packet")
        input_path = values.get("input")
        if packet_path is None or input_path is None:
            raise ValueError("strategy stage requires --packet and --input")
        packet_source = Path(packet_path).expanduser()
        if packet_source.is_symlink() or not packet_source.is_file():
            raise ValueError("strategy packet must be a regular file")
        packet_document = json.loads(packet_source.read_text(encoding="utf-8"))
        if not isinstance(packet_document, dict):
            raise ValueError("strategy packet JSON must contain an object")
        packet = packet_document.get("result", packet_document)
        if not isinstance(packet, dict):
            raise ValueError("strategy packet result must contain an object")
        return stage_strategy_candidate(vault, packet, Path(input_path).expanduser())
    if action == "graph":
        from constellation.graph import neighbors, path

        if values["graph_action"] == "neighbors":
            entity_id = values.get("entity")
            if not entity_id:
                raise ValueError("graph neighbors requires --entity")
            return neighbors(vault, str(entity_id))
        start_entity = values.get("start_entity")
        end_entity = values.get("end_entity")
        if not start_entity or not end_entity:
            raise ValueError("graph path requires --from and --to")
        return path(vault, str(start_entity), str(end_entity), max_hops=int(values["max_hops"]))
    if action == "resolve":
        from constellation.identity import propose_identity_candidates_from_vault

        candidates = propose_identity_candidates_from_vault(vault)
        return {
            "status": "candidates_found" if candidates else "no_candidates",
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        }
    if action == "bundle":
        from constellation.bundles import create_evidence_bundle

        members_path = Path(values["members"]).expanduser()
        if members_path.is_symlink() or not members_path.is_file():
            raise ValueError("bundle members must be a regular JSON file")
        members = json.loads(members_path.read_text(encoding="utf-8"))
        if not isinstance(members, list) or not all(isinstance(member, dict) for member in members):
            raise ValueError("bundle members JSON must contain an array of objects")
        return create_evidence_bundle(
            vault,
            kind=values["kind"],
            title=str(values["title"]),
            members=members,
        )
    if action == "preflight":
        from constellation.budgeting import build_budget_plan

        source = Path(values["source"]).expanduser()
        if source.is_symlink() or not source.is_file():
            raise ValueError("preflight source must be a regular file")
        return asdict(
            build_budget_plan(
                task_kind=values["task"],
                source_bytes=source.stat().st_size,
            )
        )
    if action == "ingest":
        from constellation.ingest import ingest_file

        return ingest_file(
            vault,
            Path(values["source"]).expanduser(),
            source_url=values.get("source_url"),
            kind=str(values.get("kind", "generic")),
            phone_region=values.get("phone_region"),
            meeting_format=values.get("meeting_format"),
        )
    if action == "validate":
        from constellation.validation import validate_vault

        return validate_vault(vault, limit=int(values.get("limit", 100)))
    if action == "index":
        from constellation.retrieval import build_index

        return build_index(vault)
    if action == "search":
        from constellation.retrieval import search

        return search(
            vault,
            str(values["query"]),
            limit=int(values.get("limit", 10)),
            sensitivity_ceiling=str(values.get("sensitivity", "internal")),
        )
    if action == "review":
        from constellation.review import list_candidates, promote_candidate

        if values.get("action") == "list":
            return list_candidates(vault)
        return promote_candidate(
            vault,
            str(values.get("candidate") or ""),
            confirm=bool(values.get("confirm")),
            expected_base_hash=values.get("expected_base_hash"),
        )
    if action == "claim":
        from constellation.claim import list_staged_claims, stage_claim

        if values.get("action") == "list":
            return list_staged_claims(vault, limit=int(values.get("limit", 50)))
        subject_id = values.get("subject_id")
        predicate = values.get("predicate")
        source_ids = values.get("source_ids") or []
        if not subject_id or not predicate or not source_ids:
            raise ValueError("--subject-id, --predicate, and --source-ids are required for claim stage")
        return stage_claim(
            vault,
            subject_id=str(subject_id),
            predicate=str(predicate),
            object_id=values.get("object_id"),
            object_literal=values.get("object_literal"),
            source_ids=[str(s) for s in source_ids],
            evidence_anchor=values.get("evidence_anchor"),
            evidence_excerpt=values.get("evidence_excerpt"),
            claim_status=str(values.get("claim_status", "source-claimed")),
            confidence=values.get("confidence"),
        )
    if action == "interaction":
        from datetime import datetime as dt

        from constellation.models import Interaction
        from constellation.models import Sensitivity as _Sensitivity
        from constellation.storage import atomic_write_text as _atomic_write, safe_relative_path as _safe_rel

        if values.get("action") == "list":
            from constellation.review import list_candidates as list_interactions
            return list_interactions(vault)
        subject_ids = values.get("subject_ids") or []
        if not subject_ids:
            raise ValueError("--subject-ids is required for interaction stage")
        source_ids = values.get("source_ids") or []
        participants = values.get("participants") or []
        interaction_obj = Interaction(
            type="interaction",
            title=f"interaction-{subject_ids[0][:8]}",
            status="review-required",
            sensitivity=_Sensitivity.INTERNAL,
            interaction_type=values.get("interaction_type", "meeting"),
            subject_ids=[str(s) for s in subject_ids],
            participants=[str(p) for p in participants],
            channel=values.get("channel", "in-person"),
            summary=values.get("summary") or "No summary provided.",
            follow_ups=list(values.get("follow_ups") or []),
            source_ids=[str(s) for s in source_ids],
            location=values.get("location"),
            occurred_at=dt.now().astimezone(),
            created_at=dt.now().astimezone(),
            updated_at=dt.now().astimezone(),
        )
        candidate_path = _safe_rel(vault, Path(".constellation/candidates") / f"interaction-{interaction_obj.id}.json")
        _atomic_write(vault, candidate_path.relative_to(vault), interaction_obj.model_dump_json(indent=2) + "\n")
        return {"status": "staged", "interaction_id": interaction_obj.id, "candidate_path": candidate_path.relative_to(vault).as_posix()}
    if action == "decision":
        from datetime import datetime as dt

        from constellation.models import Decision
        from constellation.models import Sensitivity as _Sens2
        from constellation.storage import atomic_write_text as _awt2, safe_relative_path as _sr2

        if values.get("action") == "list":
            from constellation.review import list_candidates as list_decisions
            return list_decisions(vault)
        subject_id = values.get("subject_id")
        decision_text_val = values.get("decision_text")
        if not subject_id or not decision_text_val:
            raise ValueError("--subject-id and --decision are required for decision stage")
        source_ids = values.get("source_ids") or []
        decision_obj = Decision(
            type="decision",
            title=f"decision-{subject_id[:8]}",
            status="review-required",
            sensitivity=_Sens2.INTERNAL,
            subject_id=str(subject_id),
            decision=str(decision_text_val),
            rationale=values.get("rationale") or "",
            options_considered=list(values.get("options_considered") or []),
            assumptions=list(values.get("assumptions") or []),
            owner=values.get("owner"),
            source_ids=[str(s) for s in source_ids],
            decided_at=dt.now().astimezone(),
            created_at=dt.now().astimezone(),
            updated_at=dt.now().astimezone(),
        )
        candidate_path = _sr2(vault, Path(".constellation/candidates") / f"decision-{decision_obj.id}.json")
        _awt2(vault, candidate_path.relative_to(vault), decision_obj.model_dump_json(indent=2) + "\n")
        return {"status": "staged", "decision_id": decision_obj.id, "candidate_path": candidate_path.relative_to(vault).as_posix()}
    if action == "inquiry":
        from datetime import datetime as dt

        from constellation.models import Inquiry, Sensitivity as _Sens3

        if values.get("action") == "list":
            from constellation.review import list_candidates as list_inquiries
            return list_inquiries(vault)
        question = values.get("question")
        if not question:
            raise ValueError("--question is required for inquiry stage")
        subject_ids = values.get("subject_ids") or []
        stop_conditions = values.get("stop_conditions") or []
        inquiry_obj = Inquiry(
            type="inquiry",
            title=f"inquiry-{question[:40]}",
            status="review-required",
            sensitivity=_Sens3.INTERNAL,
            question=str(question),
            why_it_matters=values.get("why") or "",
            target_scope=values.get("scope") or "",
            evidence_needed=values.get("evidence_needed") or "",
            source_priority=values.get("source_priority", "primary"),
            promotion_policy=values.get("promotion_policy", "review-all"),
            subject_ids=[str(s) for s in subject_ids],
            max_search_queries=int(values.get("max_searches", 5)),
            max_unique_sources=int(values.get("max_sources", 10)),
            max_model_calls=int(values.get("max_model_calls", 3)),
            synthesis_reserve_percent=int(values.get("synthesis_reserve", 25)),
            stop_conditions=list(stop_conditions),
            created_at=dt.now().astimezone(),
            updated_at=dt.now().astimezone(),
        )
        from constellation.storage import atomic_write_text as _awt3, safe_relative_path as _sr3
        candidate_path = _sr3(vault, Path(".constellation/candidates") / f"inquiry-{inquiry_obj.id}.json")
        _awt3(vault, candidate_path.relative_to(vault), inquiry_obj.model_dump_json(indent=2) + "\n")
        return {"status": "staged", "inquiry_id": inquiry_obj.id, "candidate_path": candidate_path.relative_to(vault).as_posix()}
    if action == "opportunity":
        from datetime import datetime as dt

        from constellation.models import Opportunity, Sensitivity as _Sens4

        if values.get("action") == "list":
            from constellation.review import list_candidates as list_opportunities
            return list_opportunities(vault)
        subject_ids = values.get("subject_ids") or []
        if not subject_ids:
            raise ValueError("--subject-ids is required for opportunity stage")
        feeding = values.get("feeding_interactions") or []
        claims = values.get("supporting_claims") or []
        decisions = values.get("supporting_decisions") or []
        source_ids = values.get("source_ids") or []
        opp = Opportunity(
            type="opportunity",
            title=f"opportunity-{subject_ids[0][:8]}",
            status="review-required",
            sensitivity=_Sens4.INTERNAL,
            subject_ids=[str(s) for s in subject_ids],
            stage=values.get("stage", "test"),
            probability=values.get("probability"),
            expected_value=values.get("expected_value"),
            next_action=values.get("next_action") or "",
            feeding_interactions=[str(f) for f in feeding],
            supporting_claims=[str(c) for c in claims],
            supporting_decisions=[str(d) for d in decisions],
            source_ids=[str(s) for s in source_ids],
            kanban_card_path=values.get("kanban_card"),
            created_at=dt.now().astimezone(),
            updated_at=dt.now().astimezone(),
        )
        from constellation.storage import atomic_write_text as _awt4, safe_relative_path as _sr4
        candidate_path = _sr4(vault, Path(".constellation/candidates") / f"opportunity-{opp.id}.json")
        _awt4(vault, candidate_path.relative_to(vault), opp.model_dump_json(indent=2) + "\n")
        return {"status": "staged", "opportunity_id": opp.id, "candidate_path": candidate_path.relative_to(vault).as_posix()}
    if action == "research":
        from constellation.research import research_command

        return research_command(vault, values)
    if action == "synthesize":
        from constellation.synthesis import build_synthesis_plan
        from constellation.vault import is_initialized

        if not is_initialized(vault):
            raise ValueError("vault is not initialized")
        if values.get("action") != "plan":
            raise ValueError("synthesize action must be plan")
        return build_synthesis_plan(
            task_kind=values["task"],
            source_bytes=int(values.get("source_bytes") or 0),
            estimated_pages=values.get("pages"),
            estimated_audio_minutes=values.get("audio_minutes"),
        )
    if action == "lead":
        from datetime import date

        from constellation.lead_pipeline import capture_conference_lead

        if values.get("action") != "capture":
            raise ValueError("lead action must be capture")
        return capture_conference_lead(
            vault,
            card_source=Path(values["card"]).expanduser(),
            event_name=str(values["event"]),
            event_date=date.fromisoformat(str(values["date"])),
            project_title=str(values["project"]),
            venue=values.get("venue"),
            note=values.get("note"),
            channel=str(values.get("channel") or "whatsapp"),
            phone_region=values.get("phone_region"),
            where=values.get("where"),
            todos=values.get("todos"),
        )
    if action == "migrate-plan":
        from constellation.migration import plan_migration

        return plan_migration(
            vault,
            action_limit=int(values.get("action_limit", 1_000)),
            max_files=int(values.get("max_files", 100_000)),
        )
    if action == "migrate-rehearse":
        from constellation.migration import rehearse_migration

        return rehearse_migration(
            vault,
            Path(values["destination"]).expanduser(),
            confirm_disposable=bool(values.get("confirm_disposable")),
            max_files=int(values.get("max_files", 100_000)),
        )
    if action == "migrate-prepare":
        from constellation.apply import build_cutover_vault

        return build_cutover_vault(
            vault,
            Path(values["rehearsal"]).expanduser(),
            Path(values["destination"]).expanduser(),
            expected_source_sha256=str(values["expected_source_sha256"]),
            confirm_apply_staging=bool(values.get("confirm_apply_staging")),
        )
    if action == "migrate-activate":
        from constellation.apply import activate_cutover

        return activate_cutover(
            vault,
            Path(values["prepared"]).expanduser(),
            Path(values["rollback"]).expanduser(),
            expected_source_sha256=str(values["expected_source_sha256"]),
            confirm_canonical_apply=bool(values.get("confirm_canonical_apply")),
        )
    if action == "migrate-entities":
        from constellation.entity_migration import execute_entity_migration

        dry_run = bool(values.get("dry_run", True))
        return execute_entity_migration(vault, dry_run=dry_run)
    raise ValueError(f"Unknown action: {action}")


def main(argv: Sequence[str] | None = None) -> int:
    args = vars(build_parser().parse_args(argv))
    action = args.pop("command")
    result = run_action(action, args)
    print(json.dumps({"version": 1, "ok": True, "result": result}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
