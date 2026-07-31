"""Cited evidence briefings rendered from the typed graph projection.

Composes the Wave 4 projection APIs into a compliance-readable briefing for
one canonical subject: canonical relationships, claims with confidence and
their citation chains to real source-items, typed records (decisions,
observations, events, opportunities), and review-required candidates —
always flagged, never blended into canonical facts.

Renders to markdown or fully offline HTML (inline CSS, no scripts, no
external resources) suitable for air-gapped review.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from .graph_surface import build_graph_projection

_CEILINGS = ("public", "internal", "confidential", "restricted")
_TYPED_KINDS = ("decision", "observation", "event", "opportunity")


class BriefingError(RuntimeError):
    """Raised when a briefing cannot be built."""


def build_entity_briefing(
    root: Path | str,
    entity_id: str,
    *,
    sensitivity_ceiling: str = "internal",
) -> dict[str, Any]:
    """Assemble the cited briefing model for one canonical subject."""
    if sensitivity_ceiling not in _CEILINGS:
        raise BriefingError(f"unknown sensitivity ceiling: {sensitivity_ceiling}")
    vault = Path(root).absolute()
    projection = build_graph_projection(
        vault, sensitivity_ceiling=sensitivity_ceiling, include_candidates=True
    )
    nodes = {n["id"]: n for n in projection["nodes"]}
    entity = nodes.get(entity_id)
    if entity is None:
        raise BriefingError(f"entity not found within ceiling: {entity_id}")

    touching = [
        e for e in projection["edges"]
        if e["subject_id"] == entity_id or e["object_id"] == entity_id
    ]

    citation_targets: dict[str, list[str]] = {}
    for edge in projection["edges"]:
        if edge["edge_kind"] != "citation" or edge["candidate"]:
            continue
        target = nodes.get(edge["object_id"])
        if target is None or target["type"] not in ("source_item", "source-item"):
            continue
        # citation edges carry the citing record (claim/decision) as record_id
        citation_targets.setdefault(edge["record_id"], []).append(target["record_path"])

    relationships: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    seen_claims: set[str] = set()
    typed: dict[str, list[dict[str, Any]]] = {k: [] for k in _TYPED_KINDS}
    candidates: list[dict[str, Any]] = []

    for edge in sorted(touching, key=lambda e: (e["edge_kind"], e["predicate"], e["record_path"])):
        record_id = edge["record_id"]
        record = nodes.get(record_id)
        title = edge.get("title") or (record["title"] if record else record_id)
        folder = edge["record_path"].split("/", 1)[0]
        if edge["candidate"]:
            candidates.append({
                "candidate": True,
                "kind": folder.rstrip("s"),
                "title": title,
                "record_path": edge["record_path"],
            })
            continue
        if edge["edge_kind"] == "relationship":
            outgoing = edge["subject_id"] == entity_id
            other_id = edge["object_id"] if outgoing else edge["subject_id"]
            other = nodes.get(other_id)
            relationships.append({
                "predicate": edge["predicate"],
                "other_title": other["title"] if other else other_id,
                "outgoing": outgoing,
                "evidence_class": edge.get("evidence_class"),
                "confidence": edge.get("confidence"),
                "record_path": edge["record_path"],
            })
        elif edge["edge_kind"] == "claim" or (
            edge["edge_kind"] == "citation" and folder == "claims"
        ):
            # claims without an object_id surface only as citation edges;
            # dedupe so multi-source claims appear once with all citations
            if record_id in seen_claims:
                continue
            seen_claims.add(record_id)
            claim_entry: dict[str, Any] = {
                "title": title,
                "confidence": edge.get("confidence"),
                "record_path": edge["record_path"],
                "citations": citation_targets.get(record_id, []),
            }
            # 7.2: derived live confidence (display artifact; record untouched)
            try:
                from datetime import UTC, datetime

                from .confidence import compute_confidence
                from .frontmatter import parse_frontmatter

                claim_meta, _ = parse_frontmatter(
                    (vault / edge["record_path"]).read_text(encoding="utf-8")
                )
                claim_entry["confidence_score"] = compute_confidence(
                    claim_meta, now=datetime.now(UTC)
                )
            except Exception:  # noqa: BLE001 — degrade, never break the briefing
                pass
            claims.append(claim_entry)
        elif edge["edge_kind"] in _TYPED_KINDS:
            typed[edge["edge_kind"]].append({
                "title": title,
                "record_path": edge["record_path"],
                "updated_at": edge.get("updated_at", ""),
            })

    # item 4: derived analytics rollup (display artifact; records untouched).
    # Degrade to None rather than break the briefing if aggregation fails.
    analytics: dict[str, Any] | None = None
    try:
        from .analytics import entity_analytics

        analytics = entity_analytics(
            vault, entity_id, sensitivity_ceiling=sensitivity_ceiling
        )
    except Exception:  # noqa: BLE001 — degrade, never break the briefing
        pass

    # Wave 3.4: citation-backed network position. Degree always comes from
    # the cited projection (no NetworkX needed); component size needs the
    # optional graph extra and degrades explicitly when absent.
    network_position = _network_position(
        vault, entity_id, projection, sensitivity_ceiling
    )

    return {
        "entity": {
            "id": entity_id,
            "title": entity["title"],
            "type": entity["type"],
            "sensitivity": entity["sensitivity"],
            "record_path": entity["record_path"],
        },
        "sensitivity_ceiling": sensitivity_ceiling,
        "generated_from": "typed graph projection (deterministic)",
        "relationships": relationships,
        "claims": claims,
        "decisions": typed["decision"],
        "observations": typed["observation"],
        "events": typed["event"],
        "opportunities": typed["opportunity"],
        "candidates": candidates,
        "analytics": analytics,
        "network_position": network_position,
    }


def _network_position(
    vault: Path,
    entity_id: str,
    projection: dict[str, Any],
    sensitivity_ceiling: str,
) -> dict[str, Any] | None:
    """Citation-backed network position for one entity.

    Degree counts canonical relationship edges only (every canonical
    relationship carries source_ids by schema). Component size uses the
    optional NetworkX extra; when it is absent the block degrades with an
    explicit flag instead of disappearing or failing.
    """
    edges = [
        e for e in projection["edges"]
        if e["edge_kind"] == "relationship"
        and not e["candidate"]
        and entity_id in {e["subject_id"], e["object_id"]}
    ]
    if not edges:
        return None
    scored = [float(e["confidence"]) for e in edges if e.get("confidence") is not None]
    if scored:
        confidence_note = (
            f"mean confidence {round(sum(scored) / len(scored), 2)} "
            f"across {len(scored)} scored edge(s)"
        )
    else:
        confidence_note = "no scored edges"
    updated = sorted(str(e.get("updated_at", "")) for e in edges if e.get("updated_at"))
    freshness_note = (
        f"{len(edges)} cited edge(s), newest update {updated[-1]}" if updated
        else f"{len(edges)} cited edge(s), no update timestamps"
    )
    component_size: int | None = None
    degraded = False
    degradation_note = ""
    try:
        from .graph_analytics import entity_component_size

        component_size = entity_component_size(
            vault, entity_id, sensitivity_ceiling=sensitivity_ceiling
        )
        if component_size is None:
            degraded = True
            degradation_note = (
                "component size unavailable: optional networkx dependency not installed"
            )
    except Exception:  # noqa: BLE001 — degrade, never break the briefing
        degraded = True
        degradation_note = (
            "component size unavailable: optional networkx dependency not installed"
        )
    return {
        "degree": len(edges),
        "component_size": component_size,
        "degraded": degraded,
        "degradation_note": degradation_note,
        "confidence_note": confidence_note,
        "freshness_note": freshness_note,
        "evidence_edge_count": len(edges),
    }


def render_briefing_markdown(briefing: dict[str, Any]) -> str:
    """Render the briefing model as cited markdown."""
    entity = briefing["entity"]
    lines = [
        f"# Briefing: {entity['title']}",
        "",
        f"**Type:** {entity['type']}  ",
        f"**Record:** {entity['record_path']}  ",
        f"**Ceiling:** {briefing['sensitivity_ceiling']}",
        "",
    ]
    if briefing["relationships"]:
        lines += ["## Canonical Relationships", ""]
        for rel in briefing["relationships"]:
            arrow = "→" if rel["outgoing"] else "←"
            conf = f" (confidence {rel['confidence']})" if rel["confidence"] is not None else ""
            lines.append(
                f"- {arrow} **{rel['predicate']}** {rel['other_title']}{conf} — `{rel['record_path']}`"
            )
        lines.append("")
    if briefing["claims"]:
        lines += ["## Claims (with citations)", ""]
        for claim in briefing["claims"]:
            conf = f" — confidence {claim['confidence']}" if claim["confidence"] is not None else ""
            lines.append(f"- {claim['title']}{conf} — `{claim['record_path']}`")
            for citation in claim["citations"]:
                lines.append(f"  - cited: `{citation}`")
        lines.append("")
    for kind in _TYPED_KINDS:
        records = briefing["opportunities" if kind == "opportunity" else kind + "s"]
        if records:
            lines += [f"## {kind.title()}s", ""]
            for rec in records:
                lines.append(f"- {rec['title']} — `{rec['record_path']}`")
            lines.append("")
    if briefing["candidates"]:
        lines += ["## Review-Required Candidates", ""]
        lines.append(
            "CANDIDATE records below are unverified and excluded from canonical facts "
            "until promoted via `constellation review`."
        )
        lines.append("")
        for cand in briefing["candidates"]:
            lines.append(f"- [CANDIDATE] {cand['title']} ({cand['kind']}) — `{cand['record_path']}`")
        lines.append("")
    position = briefing.get("network_position")
    if position:
        lines += ["## Network Position", ""]
        component = (
            f"component size {position['component_size']}"
            if position["component_size"] is not None
            else position["degradation_note"]
        )
        lines.append(f"- Degree: {position['degree']} cited relationship edge(s); {component}")
        lines.append(f"- {position['confidence_note']}; {position['freshness_note']}")
        if position["degraded"]:
            lines.append("- degraded: install the graph extra (networkx) for component metrics")
        lines.append("")
    analytics = briefing.get("analytics")
    if analytics:
        claims = analytics["claims"]
        bands = claims["by_confidence_band"]
        staleness = analytics["staleness"]
        contradictions = analytics["contradictions"]
        lines += [
            "## Derived Analytics",
            "",
            f"- Claims: {claims['total']} total"
            + ("".join(f", {status} ×{count}" for status, count in claims["by_status"].items())),
            f"- Confidence bands: high ×{bands['high']}, medium ×{bands['medium']}, "
            f"low ×{bands['low']}, unscored ×{bands['unscored']}",
            f"- Contradictions: {contradictions['open_pairs']} open pair(s), "
            f"{contradictions['declared_edges']} declared edge(s)",
            f"- Observations: {analytics['observations']['total']} · "
            f"Events: {analytics['events']['total']}",
            f"- Staleness: fresh ×{staleness['fresh']}, aging ×{staleness['aging']}, "
            f"stale ×{staleness['stale']}",
        ]
        if analytics["activity_by_month"]:
            lines.append("- Activity by month:")
            for bucket in analytics["activity_by_month"]:
                lines.append(
                    f"  - {bucket['month']}: claims ×{bucket['claims']}, "
                    f"observations ×{bucket['observations']}, events ×{bucket['events']}"
                )
        lines.append("")
    lines.append(f"_Generated from {briefing['generated_from']}._")
    return "\n".join(lines) + "\n"


_CSS = """
body { background: #0d1117; color: #c9d1d9; font-family: system-ui, sans-serif;
       max-width: 860px; margin: 24px auto; padding: 0 16px; }
h1 { color: #f0f6fc; }
h2 { color: #58a6ff; border-bottom: 1px solid #21262d; padding-bottom: 4px; }
code { background: #161b22; padding: 1px 4px; border-radius: 4px; font-size: 12px; }
.meta { color: #8b949e; font-size: 13px; }
.candidate { color: #f0883e; }
ul { line-height: 1.6; }
.cites li { color: #3fb950; font-size: 13px; }
"""


def render_briefing_html(briefing: dict[str, Any]) -> str:
    """Render the briefing model as fully offline, self-contained HTML."""
    entity = briefing["entity"]
    parts = [
        "<!DOCTYPE html>",
        "<html><head><meta charset=\"utf-8\">",
        f"<title>Briefing: {escape(entity['title'])}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>Briefing: {escape(entity['title'])}</h1>",
        f"<p class=\"meta\">Type: {escape(entity['type'])} · Record: "
        f"<code>{escape(entity['record_path'])}</code> · Ceiling: "
        f"{escape(briefing['sensitivity_ceiling'])}</p>",
    ]
    if briefing["relationships"]:
        parts.append("<h2>Canonical Relationships</h2><ul>")
        for rel in briefing["relationships"]:
            arrow = "→" if rel["outgoing"] else "←"
            conf = f" (confidence {rel['confidence']})" if rel["confidence"] is not None else ""
            parts.append(
                f"<li>{arrow} <b>{escape(rel['predicate'])}</b> "
                f"{escape(rel['other_title'])}{escape(conf)} — "
                f"<code>{escape(rel['record_path'])}</code></li>"
            )
        parts.append("</ul>")
    if briefing["claims"]:
        parts.append("<h2>Claims (with citations)</h2><ul>")
        for claim in briefing["claims"]:
            conf = f" — confidence {claim['confidence']}" if claim["confidence"] is not None else ""
            parts.append(
                f"<li>{escape(claim['title'])}{escape(conf)} — "
                f"<code>{escape(claim['record_path'])}</code>"
            )
            if claim["citations"]:
                parts.append("<ul class=\"cites\">")
                for citation in claim["citations"]:
                    parts.append(f"<li>cited: <code>{escape(citation)}</code></li>")
                parts.append("</ul>")
            parts.append("</li>")
        parts.append("</ul>")
    for kind in _TYPED_KINDS:
        records = briefing["opportunities" if kind == "opportunity" else kind + "s"]
        if records:
            parts.append(f"<h2>{kind.title()}s</h2><ul>")
            for rec in records:
                parts.append(
                    f"<li>{escape(rec['title'])} — <code>{escape(rec['record_path'])}</code></li>"
                )
            parts.append("</ul>")
    if briefing["candidates"]:
        parts.append("<h2 class=\"candidate\">Review-Required Candidates</h2>")
        parts.append(
            "<p class=\"candidate\">CANDIDATE records are unverified and excluded "
            "from canonical facts until promoted via the review CLI.</p><ul>"
        )
        for cand in briefing["candidates"]:
            parts.append(
                f"<li class=\"candidate\">[CANDIDATE] {escape(cand['title'])} "
                f"({escape(cand['kind'])}) — <code>{escape(cand['record_path'])}</code></li>"
            )
        parts.append("</ul>")
    analytics = briefing.get("analytics")
    if analytics:
        claims = analytics["claims"]
        bands = claims["by_confidence_band"]
        staleness = analytics["staleness"]
        contradictions = analytics["contradictions"]
        status_bits = "".join(
            f", {escape(status)} ×{count}" for status, count in claims["by_status"].items()
        )
        parts.append("<h2>Derived Analytics</h2><ul>")
        parts.append(f"<li>Claims: {claims['total']} total{status_bits}</li>")
        parts.append(
            f"<li>Confidence bands: high ×{bands['high']}, medium ×{bands['medium']}, "
            f"low ×{bands['low']}, unscored ×{bands['unscored']}</li>"
        )
        parts.append(
            f"<li>Contradictions: {contradictions['open_pairs']} open pair(s), "
            f"{contradictions['declared_edges']} declared edge(s)</li>"
        )
        parts.append(
            f"<li>Observations: {analytics['observations']['total']} · "
            f"Events: {analytics['events']['total']}</li>"
        )
        parts.append(
            f"<li>Staleness: fresh ×{staleness['fresh']}, aging ×{staleness['aging']}, "
            f"stale ×{staleness['stale']}</li>"
        )
        if analytics["activity_by_month"]:
            parts.append("<li>Activity by month:<ul>")
            for bucket in analytics["activity_by_month"]:
                parts.append(
                    f"<li>{escape(bucket['month'])}: claims ×{bucket['claims']}, "
                    f"observations ×{bucket['observations']}, events ×{bucket['events']}</li>"
                )
            parts.append("</ul></li>")
        parts.append("</ul>")
    parts.append(f"<p class=\"meta\">Generated from {escape(briefing['generated_from'])}.</p>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"
