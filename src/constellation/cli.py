"""Command-line interface for the Constellation core."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


def _parse_aware_timestamp(value: object | None, option: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{option} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{option} must include a timezone")
    return parsed


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
    interaction.add_argument("--occurred-at", help="ISO-8601 timestamp from source evidence")
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
    decision.add_argument("--decided-at", help="ISO-8601 timestamp from source evidence")
    decision.add_argument("--limit", type=int, default=50, help="Max to list")

    inquiry = sub.add_parser("inquiry", help="Stage or list research inquiries")
    inquiry.add_argument("vault", type=Path)
    inquiry.add_argument("action", choices=["stage", "list", "run"])
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
    inquiry.add_argument("--synthesis-reserve", type=int, default=25, help="Reserve percent for synthesis (0-50)")
    inquiry.add_argument("--profile", default="low", choices=["off", "low", "standard", "deep"],
                         help="Research profile: off=read-only, low=default targeted, standard, deep=explicit escalation")
    inquiry.add_argument("--stop-conditions", nargs="+", help="When to stop")
    inquiry.add_argument("--sensitivity", default="internal",
                         choices=["public", "internal", "confidential", "restricted"])
    inquiry.add_argument("--max-pages", type=int, default=5, help="Max pages to extract")
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

    prep = sub.add_parser("prep", help="Compile a one-page operator brief for an entity")
    prep.add_argument("vault", type=Path)
    prep.add_argument("entity_id", help="Canonical entity ULID")

    decay = sub.add_parser("decay", help="Detect aging contacts needing follow-up")
    decay.add_argument("vault", type=Path)
    decay.add_argument("--threshold", type=int, default=14, help="Stale threshold in days")

    patterns = sub.add_parser("patterns", help="Detect cross-entity claim graph clusters")
    patterns.add_argument("vault", type=Path)
    patterns.add_argument("--min-cluster", type=int, default=2, help="Minimum entities per cluster")

    search_books = sub.add_parser("search-books", help="Semantic search across ingested books")
    search_books.add_argument("vault", type=Path)
    search_books.add_argument("query", help="Natural language query")
    search_books.add_argument("--limit", type=int, default=5, help="Max results")
    search_books.add_argument("--sensitivity", default="internal", help="Sensitivity ceiling")

    extract_claims = sub.add_parser("extract-claims", help="Auto-extract claims from preserved sources via LLM")
    extract_claims.add_argument("vault", type=Path)
    extract_claims.add_argument("run_id", help="Research run ID to extract from")
    extract_claims.add_argument("--subject-id", required=True, help="Entity ULID the claims are about")
    extract_claims.add_argument("--provider", required=True, help="Egress-policy provider name")
    extract_claims.add_argument("--model", required=True, help="Egress-policy model name")

    enrich = sub.add_parser("enrich", help="Query external intelligence APIs")
    enrich_subs = enrich.add_subparsers(dest="enrich_action", required=True)

    enrich_collect = enrich_subs.add_parser("collect", help="Query an API and preserve results as a source candidate")
    enrich_collect.add_argument("vault", type=Path)
    enrich_collect.add_argument("source", choices=["gdelt", "edgar", "polymarket"])
    enrich_collect.add_argument("query", help="Entity name, ticker, or search query")
    enrich_collect.add_argument("--subject-id", required=True, help="Entity ULID")
    enrich_collect.add_argument("--provider", required=True, help="Egress-policy provider name")
    enrich_collect.add_argument("--model", required=True, help="Egress-policy model name")

    enrich_extract = enrich_subs.add_parser("extract", help="Extract claims from a promoted feeder source-item")
    enrich_extract.add_argument("vault", type=Path)
    enrich_extract.add_argument("source_id", help="Promoted source-item ULID")
    enrich_extract.add_argument("--subject-id", required=True, help="Entity ULID")
    enrich_extract.add_argument("--provider", required=True, help="Egress-policy provider name")
    enrich_extract.add_argument("--model", required=True, help="Egress-policy model name")

    classify = sub.add_parser("classify", help="Stage or list OSINT entity classifications")
    classify.add_argument("vault", type=Path)
    classify.add_argument("classify_action", choices=["stage", "list"])
    classify.add_argument("--entity-id", help="Entity ULID to classify (required for stage)")
    classify.add_argument("--category", choices=["buyer", "partner", "channel", "competitor", "false_lead"], help="Classification category")
    classify.add_argument("--methodology", help="Classification methodology (max 500 chars)")
    classify.add_argument("--confidence", choices=["low", "medium", "high"], default="medium")
    classify.add_argument("--rationale", help="Evidence-based rationale (max 5000 chars)")
    classify.add_argument("--supporting-claim-ids", nargs="*", default=[], help="Supporting claim ULIDs")
    classify.add_argument("--supporting-source-ids", nargs="*", default=[], help="Supporting source-item ULIDs")
    classify.add_argument("--limit", type=int, default=50)

    book = sub.add_parser("book", help="Manage ingested book intelligence")
    book_subs = book.add_subparsers(dest="book_action", required=True)

    book_ingest = book_subs.add_parser("ingest", help="Index a preserved book source into ChromaDB")
    book_ingest.add_argument("vault", type=Path)
    book_ingest.add_argument("source_path", type=Path, help="Path to preserved source markdown")
    book_ingest.add_argument("--source-id", required=True, help="Canonical source-item ULID")
    book_ingest.add_argument("--title", help="Book title (defaults to filename stem)")

    book_status = book_subs.add_parser("status", help="Show book collection stats")
    book_status.add_argument("vault", type=Path)

    book_search = book_subs.add_parser("search", help="Semantic search across books")
    book_search.add_argument("vault", type=Path)
    book_search.add_argument("query", help="Natural language query")
    book_search.add_argument("--limit", type=int, default=5, help="Max results")

    book_delete = book_subs.add_parser("delete", help="Remove all chunks for a source")
    book_delete.add_argument("vault", type=Path)
    book_delete.add_argument("source_id", help="Canonical source-item ULID")

    book_rebuild = book_subs.add_parser("rebuild", help="Delete and re-ingest a book")
    book_rebuild.add_argument("vault", type=Path)
    book_rebuild.add_argument("source_path", type=Path, help="Path to preserved source markdown")
    book_rebuild.add_argument("--source-id", required=True, help="Canonical source-item ULID")
    book_rebuild.add_argument("--title", help="Book title")

    analyze = sub.add_parser("analyze", help="Run a strategic framework analysis")
    analyze.add_argument("vault", type=Path)
    analyze.add_argument("framework", choices=["porter", "swot"], help="Framework to run")
    analyze.add_argument("--entity-id", required=True, help="Entity ULID to analyze")

    crm = sub.add_parser("crm", help="Deterministic CRM derivation from canonical records")
    crm_subs = crm.add_subparsers(dest="crm_action", required=True)

    crm_plan_p = crm_subs.add_parser("plan", help="Generate CRM plan for entities")
    crm_plan_p.add_argument("vault", type=Path)
    crm_plan_p.add_argument("--entity-id", help="Single entity ULID (omit for all)")

    crm_apply_p = crm_subs.add_parser("apply", help="Apply a CRM plan to one entity")
    crm_apply_p.add_argument("vault", type=Path)
    crm_apply_p.add_argument("--entity-id", required=True, help="Entity ULID")
    crm_apply_p.add_argument("--expected-sha256", required=True, help="Expected file hash from plan")
    crm_apply_p.add_argument("--changes", required=True, help="JSON-encoded changes dict")
    crm_apply_p.add_argument("--dry-run", action="store_true")

    crm_status_p = crm_subs.add_parser("status", help="CRM coverage report")
    crm_status_p.add_argument("vault", type=Path)

    pm_sync = sub.add_parser("pm-sync", help="Synchronize opportunities with Project Manager kanban")
    pm_subs = pm_sync.add_subparsers(dest="pm_sync_action", required=True)

    pm_plan = pm_subs.add_parser("plan", help="Plan PM sync for an opportunity")
    pm_plan.add_argument("vault", type=Path)
    pm_plan.add_argument("--opportunity-id", required=True, help="Promoted opportunity ULID")

    pm_apply = pm_subs.add_parser("apply", help="Execute PM sync for an opportunity")
    pm_apply.add_argument("vault", type=Path)
    pm_apply.add_argument("--opportunity-id", required=True, help="Promoted opportunity ULID")
    pm_apply.add_argument("--expected-sha256", required=True, help="Expected file hash from plan")
    pm_apply.add_argument("--dry-run", action="store_true")

    health = sub.add_parser("health", help="Probe research infrastructure health")
    health.add_argument("vault", type=Path)

    hybrid = sub.add_parser("hybrid", help="Fused lexical + semantic search")
    hybrid.add_argument("vault", type=Path)
    hybrid.add_argument("query", help="Search query")
    hybrid.add_argument("--limit", type=int, default=10)
    hybrid.add_argument("--sensitivity", default="internal")

    semantic = sub.add_parser("semantic", help="Manage the local semantic index")
    semantic.add_argument("semantic_action", choices=["build", "status", "delete"])
    semantic.add_argument("vault", type=Path)
    semantic.add_argument("--provider", help="Explicit embedding provider name")

    watchlist = sub.add_parser("watchlist", help="Stage a watchlist to monitor entities/terms")
    watchlist.add_argument("vault", type=Path)
    watchlist.add_argument("--title", required=True)
    watchlist.add_argument("--entity-ids", nargs="*", default=[])
    watchlist.add_argument("--query-terms", nargs="*", default=[])
    watchlist.add_argument("--sources", nargs="*", default=[], choices=["gdelt", "edgar", "polymarket"])
    watchlist.add_argument("--schedule", default="")

    watch_run = sub.add_parser("watch-run", help="Execute a source-grounded deterministic watchlist snapshot")
    watch_run.add_argument("vault", type=Path)
    watch_run.add_argument("--watchlist-id", required=True)
    watch_run.add_argument("--source-ids", nargs="+", required=True)
    watch_run.add_argument("--content", required=True)
    watch_run.add_argument("--previous-snapshot-id")

    watch_collect = sub.add_parser("watch-collect", help="Run a watchlist through a bounded connector")
    watch_collect.add_argument("vault", type=Path)
    watch_collect.add_argument("--watchlist-id", required=True)
    watch_collect.add_argument("--fixture-dir", type=Path, required=True)
    watch_collect.add_argument("--max-items", type=int, default=50)
    watch_collect.add_argument("--max-bytes", type=int, default=5_000_000)
    watch_collect.add_argument("--previous-snapshot-id")

    timeline = sub.add_parser("timeline", help="Cited as-of entity timeline")
    timeline.add_argument("vault", type=Path)
    timeline.add_argument("entity_id")
    timeline.add_argument("--as-of", help="ISO-8601 with timezone")
    timeline.add_argument("--sensitivity", default="internal")

    graph_surface = sub.add_parser("graph-surface", help="Render the offline graph review surface")
    graph_surface.add_argument("vault", type=Path)
    graph_surface.add_argument("--output", type=Path, required=True)
    graph_surface.add_argument("--entity", help="Focus on one entity's neighborhood")
    graph_surface.add_argument("--sensitivity", default="internal")

    timeline_surface = sub.add_parser("timeline-surface", help="Render the offline entity timeline surface")
    timeline_surface.add_argument("vault", type=Path)
    timeline_surface.add_argument("entity_id")
    timeline_surface.add_argument("--output", type=Path, required=True)
    timeline_surface.add_argument("--as-of", help="ISO-8601 with timezone")
    timeline_surface.add_argument("--sensitivity", default="internal")

    lint = sub.add_parser("lint", help="Read-only record-health findings")
    lint.add_argument("vault", type=Path)

    snapshot = sub.add_parser("snapshot", help="Stage a point-in-time snapshot")
    snapshot.add_argument("vault", type=Path)
    snapshot.add_argument("--watchlist-id", required=True)
    snapshot.add_argument("--source-ids", nargs="*", default=[])
    snapshot.add_argument("--content", default="")
    snapshot.add_argument("--previous-snapshot-id")

    observation = sub.add_parser("observation", help="Stage a material-change observation")
    observation.add_argument("vault", type=Path)
    observation.add_argument("--watchlist-id", required=True)
    observation.add_argument("--snapshot-id", required=True)
    observation.add_argument("--change-summary", required=True)
    observation.add_argument("--previous-snapshot-id")
    observation.add_argument("--entity-ids", nargs="*", default=[])
    observation.add_argument("--source-ids", nargs="*", default=[])

    event = sub.add_parser("event", help="Stage a time-anchored canonical event")
    event.add_argument("vault", type=Path)
    event.add_argument("--title", required=True)
    event.add_argument("--description", required=True)
    event.add_argument("--entity-ids", nargs="*", default=[])
    event.add_argument("--event-date", default="")
    event.add_argument("--event-type", default="general")
    event.add_argument("--observation-ids", nargs="*", default=[])
    event.add_argument("--source-ids", nargs="*", default=[])

    cockpit = sub.add_parser("cockpit", help="Obsidian-native review dashboard")
    cockpit_subs = cockpit.add_subparsers(dest="cockpit_action", required=True)

    cp_plan = cockpit_subs.add_parser("plan", help="Plan cockpit generation")
    cp_plan.add_argument("vault", type=Path)

    cp_apply = cockpit_subs.add_parser("apply", help="Generate cockpit HOME.md")
    cp_apply.add_argument("vault", type=Path)
    cp_apply.add_argument("--dry-run", action="store_true")

    cp_status = cockpit_subs.add_parser("status", help="Check cockpit state")
    cp_status.add_argument("vault", type=Path)

    trail = sub.add_parser("trail", help="Trace full provenance chain for a decision")
    trail.add_argument("vault", type=Path)
    trail.add_argument("decision_id", help="Canonical decision ULID")

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

        from constellation.models import Interaction, InteractionType
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
        now = dt.now().astimezone()
        interaction_obj = Interaction(
            type="interaction",
            title=f"interaction-{subject_ids[0][:8]}",
            status="review-required",
            sensitivity=_Sensitivity.INTERNAL,
            interaction_type=InteractionType(str(values.get("interaction_type", "meeting"))),
            subject_ids=[str(s) for s in subject_ids],
            participants=[str(p) for p in participants],
            channel=values.get("channel", "in-person"),
            summary=values.get("summary") or "No summary provided.",
            follow_ups=list(values.get("follow_ups") or []),
            source_ids=[str(s) for s in source_ids],
            location=values.get("location"),
            occurred_at=_parse_aware_timestamp(values.get("occurred_at"), "--occurred-at"),
            created_at=now,
            updated_at=now,
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
        now = dt.now().astimezone()
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
            decided_at=_parse_aware_timestamp(values.get("decided_at"), "--decided-at"),
            created_at=now,
            updated_at=now,
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
        if values.get("action") == "run":
            from constellation.research_runner import run_inquiry as _run

            question = values.get("question")
            if not question:
                raise ValueError("--question is required for inquiry run")
            sensitivity_str = values.get("sensitivity", "internal")
            sensitivity_map = {
                "public": _Sens3.PUBLIC,
                "internal": _Sens3.INTERNAL,
                "confidential": _Sens3.CONFIDENTIAL,
                "restricted": _Sens3.RESTRICTED,
            }
            sensitivity = sensitivity_map.get(sensitivity_str, _Sens3.INTERNAL)
            inquiry = Inquiry(
                type="inquiry",
                title=f"inquiry-{question[:40]}",
                status="active",
                sensitivity=sensitivity,
                question=str(question),
                why_it_matters=values.get("why") or "",
                target_scope=values.get("scope") or "",
                evidence_needed=values.get("evidence_needed") or "",
                source_priority=values.get("source_priority", "primary"),
                promotion_policy=values.get("promotion_policy", "review-all"),
                subject_ids=[str(s) for s in (values.get("subject_ids") or [])],
                max_search_queries=int(values.get("max_searches", 5)),
                max_unique_sources=int(values.get("max_sources", 10)),
                max_model_calls=int(values.get("max_model_calls", 3)),
                synthesis_reserve_percent=int(values.get("synthesis_reserve", 25)),
                stop_conditions=list(values.get("stop_conditions") or []),
                max_pages=int(values.get("max_pages", 5)),
                created_at=dt.now().astimezone(),
                updated_at=dt.now().astimezone(),
            )
            return _run(vault, inquiry, sensitivity=sensitivity,
                        profile=str(values.get("profile", "low")))
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

        from constellation.models import Opportunity, OpportunityStage as _OppStage, Sensitivity as _Sens4

        if values.get("action") == "list":
            from constellation.review import list_candidates as _list_all
            all_candidates = _list_all(vault)
            return [c for c in all_candidates if isinstance(c, dict) and c.get("kind") == "opportunity_candidate"]
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
            stage=_OppStage(values.get("stage", "test")),
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
    if action == "prep":
        from constellation.prep import compile_prep

        return compile_prep(vault, str(values["entity_id"]))
    if action == "decay":
        from constellation.decay import detect_decay

        return detect_decay(vault, threshold_days=int(values.get("threshold", 14)))
    if action == "patterns":
        from constellation.patterns import detect_patterns

        return detect_patterns(vault, min_cluster_size=int(values.get("min_cluster", 2)))
    if action == "trail":
        from constellation.trail import trace_decision

        return trace_decision(vault, str(values["decision_id"]))
    if action == "search-books":
        from constellation.book_intelligence import search_books

        return search_books(
            vault,
            str(values["query"]),
            n_results=int(values.get("limit", 5)),
            sensitivity_ceiling=str(values.get("sensitivity", "internal")),
        )
    if action == "extract-claims":
        from constellation.claim_extractor import extract_claims_from_run

        return extract_claims_from_run(
            vault,
            str(values["run_id"]),
            subject_id=str(values["subject_id"]),
            provider=str(values["provider"]),
            model=str(values["model"]),
        )
    if action == "enrich":
        enrich_action = str(values.get("enrich_action", ""))
        if enrich_action == "collect":
            from constellation.feeders import FeederRequest, collect_from_feeder

            req = FeederRequest(
                source=str(values["source"]),
                query=str(values["query"]),
                subject_id=str(values["subject_id"]),
                provider=str(values["provider"]),
                model=str(values["model"]),
            )
            result = collect_from_feeder(vault, req)
            return {
                "status": result.status,
                "source_ids": list(result.source_ids),
                "candidate_ids": list(result.candidate_ids),
                "receipt_path": result.receipt_path,
                "items_found": result.items_found,
                "error": result.error,
            }
        elif enrich_action == "extract":
            from constellation.feeders import extract_from_feeder_source

            return extract_from_feeder_source(
                vault,
                str(values["source_id"]),
                subject_id=str(values["subject_id"]),
                provider=str(values["provider"]),
                model=str(values["model"]),
            )
        else:
            raise ValueError(f"Unknown enrich action: {enrich_action}")
    if action == "classify":
        classify_action = str(values.get("classify_action", ""))
        if classify_action == "list":
            from constellation.review import list_candidates as list_classifications

            return list_classifications(vault)
        elif classify_action == "stage":
            from constellation.classification import stage_classification

            entity_id = str(values.get("entity_id") or "")
            if not entity_id:
                raise ValueError("--entity-id is required for stage")
            category = str(values.get("category") or "")
            if not category:
                raise ValueError("--category is required for stage")
            methodology = str(values.get("methodology") or "")
            if not methodology:
                raise ValueError("--methodology is required for stage")
            rationale = str(values.get("rationale") or "")
            if not rationale:
                raise ValueError("--rationale is required for stage")

            return stage_classification(
                vault,
                entity_id=entity_id,
                category=category,
                methodology=methodology,
                rationale=rationale,
                supporting_claim_ids=[str(c) for c in values.get("supporting_claim_ids") or []],
                supporting_source_ids=[str(s) for s in values.get("supporting_source_ids") or []],
                confidence=str(values.get("confidence", "medium")),
            )
    if action == "book":
        book_action = str(values.get("book_action", ""))
        if book_action == "ingest":
            from constellation.book_intelligence import ingest_book

            title = str(values.get("title") or "")
            return ingest_book(
                vault, values["source_path"],
                source_id=str(values["source_id"]),
                title=title or None,
            )
        elif book_action == "status":
            from constellation.book_intelligence import book_status

            return book_status(vault)
        elif book_action == "search":
            from constellation.book_intelligence import search_books

            return search_books(vault, str(values["query"]), n_results=int(values.get("limit", 5)))
        elif book_action == "delete":
            from constellation.book_intelligence import delete_book

            return delete_book(vault, str(values["source_id"]))
        elif book_action == "rebuild":
            from constellation.book_intelligence import rebuild_books

            title = str(values.get("title") or "")
            return rebuild_books(
                vault, values["source_path"],
                source_id=str(values["source_id"]),
                title=title or None,
            )
    if action == "analyze":
        from constellation.frameworks import run_framework

        fw = str(values["framework"])
        framework = "porter_five_forces" if fw == "porter" else "swot"
        return run_framework(vault, str(values["entity_id"]), framework)
    if action == "crm":
        crm_action = str(values.get("crm_action", ""))
        if crm_action == "plan":
            from constellation.crm import crm_plan

            eid = str(values.get("entity_id") or "")
            return crm_plan(vault, entity_id=eid or None)
        elif crm_action == "apply":
            from constellation.crm import crm_apply

            return crm_apply(
                vault,
                str(values["entity_id"]),
                expected_sha256=str(values["expected_sha256"]),
                changes=json.loads(str(values["changes"])),
                dry_run=bool(values.get("dry_run")),
            )
        elif crm_action == "status":
            from constellation.crm import crm_status

            return crm_status(vault)
    if action == "pm-sync":
        pm_action = str(values.get("pm_sync_action", ""))
        if pm_action == "plan":
            from constellation.pm_sync import pm_sync_plan

            return pm_sync_plan(vault, str(values["opportunity_id"]))
        elif pm_action == "apply":
            from constellation.pm_sync import pm_sync_apply

            return pm_sync_apply(
                vault,
                str(values["opportunity_id"]),
                expected_sha256=str(values["expected_sha256"]),
                dry_run=bool(values.get("dry_run")),
            )
    if action == "health":
        from constellation.research_health import probe_research_health

        return probe_research_health(vault)
    if action == "semantic":
        from constellation.semantic_index import (
            build_from_vault,
            delete_semantic_index,
            semantic_index_status,
        )

        semantic_action = str(values.get("semantic_action", ""))
        if semantic_action == "status":
            return semantic_index_status(vault)
        if semantic_action == "delete":
            return delete_semantic_index(vault)
        from constellation.embedding_providers import resolve_embedding_provider

        provider_name = values.get("provider")
        provider = resolve_embedding_provider(
            vault, name=str(provider_name) if provider_name else None
        )
        return build_from_vault(vault, provider=provider)
    if action == "hybrid":
        from constellation.hybrid_retrieval import hybrid_search

        embed_fn = None
        try:
            from constellation.embedding_providers import resolve_embedding_provider

            embed_fn = resolve_embedding_provider(vault)
        except Exception:
            embed_fn = None
        return hybrid_search(
            vault,
            str(values["query"]),
            n_results=int(values.get("limit", 10)),
            sensitivity_ceiling=str(values.get("sensitivity", "internal")),
            embed_fn=embed_fn,
        )
    if action == "watchlist":
        from constellation.watchlists import stage_watchlist

        return stage_watchlist(
            vault,
            title=str(values["title"]),
            entity_ids=[str(e) for e in (values.get("entity_ids") or [])],
            query_terms=[str(q) for q in (values.get("query_terms") or [])],
            sources=[str(s) for s in (values.get("sources") or [])],
            schedule=str(values.get("schedule", "")),
        )
    if action == "watch-run":
        from constellation.watchlists import execute_watchlist_snapshot

        previous_snapshot_id = values.get("previous_snapshot_id")
        return execute_watchlist_snapshot(
            vault,
            watchlist_id=str(values["watchlist_id"]),
            source_ids=[str(source_id) for source_id in values["source_ids"]],
            preserved_content=str(values["content"]),
            previous_snapshot_id=str(previous_snapshot_id) if previous_snapshot_id else None,
        )
    if action == "watch-collect":
        from constellation.watchlists import LocalFixtureConnector, RunCaps, run_watchlist

        previous_snapshot_id = values.get("previous_snapshot_id")
        return run_watchlist(
            vault,
            watchlist_id=str(values["watchlist_id"]),
            connector=LocalFixtureConnector(Path(values["fixture_dir"])),
            caps=RunCaps(max_items=int(values["max_items"]), max_bytes=int(values["max_bytes"])),
            previous_snapshot_id=str(previous_snapshot_id) if previous_snapshot_id else None,
        )
    if action == "timeline":
        from constellation.temporal import entity_timeline

        as_of = values.get("as_of")
        return entity_timeline(
            vault,
            str(values["entity_id"]),
            as_of=str(as_of) if as_of else None,
            sensitivity_ceiling=str(values.get("sensitivity", "internal")),
        )
    if action == "lint":
        from constellation.record_lint import lint_records

        return lint_records(vault)
    if action == "timeline-surface":
        from constellation.temporal import entity_timeline
        from constellation.timeline_surface import render_timeline_surface

        as_of = values.get("as_of")
        timeline = entity_timeline(
            vault,
            str(values["entity_id"]),
            as_of=str(as_of) if as_of else None,
            sensitivity_ceiling=str(values.get("sensitivity", "internal")),
        )
        output = Path(values["output"]).expanduser().absolute()
        output.write_text(render_timeline_surface(timeline), encoding="utf-8")
        return {
            "status": "written",
            "output_path": str(output),
            "bytes_written": output.stat().st_size,
            "total_entries": timeline["total_entries"],
            "truncated_by_as_of": timeline["truncated_by_as_of"],
        }
    if action == "graph-surface":
        from constellation.graph_surface import build_graph_projection, render_graph_surface

        focus = values.get("entity")
        projection = build_graph_projection(
            vault,
            sensitivity_ceiling=str(values.get("sensitivity", "internal")),
            entity_id=str(focus) if focus else None,
        )
        output = Path(values["output"]).expanduser().absolute()
        rendered = render_graph_surface(projection)
        output.write_text(rendered, encoding="utf-8")
        return {
            "status": "written",
            "output_path": str(output),
            "bytes_written": output.stat().st_size,
            "total_nodes": projection["total_nodes"],
            "total_edges": projection["total_edges"],
            "degraded": projection["degraded"],
        }
    if action == "snapshot":
        from constellation.watchlists import stage_snapshot

        _psid = values.get("previous_snapshot_id")
        return stage_snapshot(
            vault,
            watchlist_id=str(values["watchlist_id"]),
            source_ids=[str(s) for s in (values.get("source_ids") or [])],
            preserved_content=str(values.get("content", "")),
            previous_snapshot_id=str(_psid) if _psid else None,
        )
    if action == "observation":
        from constellation.watchlists import stage_observation

        _psid2 = values.get("previous_snapshot_id")
        return stage_observation(
            vault,
            watchlist_id=str(values["watchlist_id"]),
            snapshot_id=str(values["snapshot_id"]),
            change_summary=str(values["change_summary"]),
            previous_snapshot_id=str(_psid2) if _psid2 else None,
            entity_ids=[str(e) for e in (values.get("entity_ids") or [])],
            source_ids=[str(s) for s in (values.get("source_ids") or [])],
        )
    if action == "event":
        from constellation.watchlists import stage_event

        return stage_event(
            vault,
            title=str(values["title"]),
            description=str(values["description"]),
            entity_ids=[str(e) for e in (values.get("entity_ids") or [])],
            event_date=str(values.get("event_date", "")),
            event_type=str(values.get("event_type", "general")),
            observation_ids=[str(o) for o in (values.get("observation_ids") or [])],
            source_ids=[str(s) for s in (values.get("source_ids") or [])],
        )
    if action == "cockpit":
        cockpit_action = str(values.get("cockpit_action", ""))
        if cockpit_action == "plan":
            from constellation.cockpit import cockpit_plan

            return cockpit_plan(vault)
        elif cockpit_action == "apply":
            from constellation.cockpit import cockpit_apply

            return cockpit_apply(vault, dry_run=bool(values.get("dry_run")))
        elif cockpit_action == "status":
            from constellation.cockpit import cockpit_status

            return cockpit_status(vault)
    raise ValueError(f"Unknown action: {action}")


def main(argv: Sequence[str] | None = None) -> int:
    args = vars(build_parser().parse_args(argv))
    action = args.pop("command")
    result = run_action(action, args)
    print(json.dumps({"version": 1, "ok": True, "result": result}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
