"""Cross-entity pattern detection — finding emergent clusters in the claim graph.

Phase 10: scans claims for shared predicates, objects, and entities to surface
clusters the operator might not have noticed. No LLM required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .frontmatter import parse_frontmatter
from .vault import is_initialized


class PatternError(RuntimeError):
    """Raised when pattern detection cannot complete."""


def _entity_title(vault: Path, entity_id: str) -> str:
    """Look up an entity's title by ID."""
    for path in sorted((vault / "entities").glob("*.md")):
        try:
            fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            if isinstance(fm, dict) and str(fm.get("id", "")) == entity_id:
                return str(fm.get("title", entity_id))
        except Exception:
            continue
    return entity_id


def detect_patterns(root: Path | str, *, min_cluster_size: int = 2) -> str:
    """Scan claims and return emergent entity clusters.

    Groups entities by:
    - Shared object_literal (same company, role, location)
    - Shared predicate with similar objects
    - Same source_ids (discovered together)

    Returns a compact markdown report of clusters with a thesis statement.
    """
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise PatternError("vault is not initialized")

    now = datetime.now(UTC)

    # ── Collect all claims ──
    claims: list[dict[str, object]] = []
    claims_dir = vault / "claims"
    if claims_dir.is_dir():
        for path in sorted(claims_dir.glob("*.md")):
            try:
                fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
                if not isinstance(fm, dict):
                    continue
                claims.append({
                    "subject_id": str(fm.get("subject_id", "")),
                    "predicate": str(fm.get("predicate", "")),
                    "object_literal": str(fm.get("object_literal", "")),
                    "object_id": str(fm.get("object_id", "")),
                })
            except Exception:
                continue

    if len(claims) < 2:
        return ""

    # ── Cluster by shared object_literal ──
    obj_clusters: dict[str, list[str]] = {}
    for c in claims:
        obj = c["object_literal"].strip().casefold()
        if not obj or len(obj) < 3:
            continue
        sid = c["subject_id"]
        if obj not in obj_clusters:
            obj_clusters[obj] = []
        if sid not in obj_clusters[obj]:
            obj_clusters[obj].append(sid)

    # Filter to clusters with >= min_cluster_size unique subjects
    obj_clusters = {
        k: v for k, v in obj_clusters.items()
        if len(set(v)) >= min_cluster_size
    }

    # ── Cluster by shared predicate ──
    pred_clusters: dict[str, list[str]] = {}
    for c in claims:
        pred = c["predicate"].strip().casefold()
        if not pred:
            continue
        if pred not in pred_clusters:
            pred_clusters[pred] = []
        sid = c["subject_id"]
        if sid not in pred_clusters[pred]:
            pred_clusters[pred].append(sid)

    pred_clusters = {
        k: v for k, v in pred_clusters.items()
        if len(set(v)) >= min_cluster_size
    }

    # ── Cluster by shared source_ids (discovered together) ──
    source_clusters: dict[str, list[str]] = {}
    for path in sorted((vault / "claims").glob("*.md")):
        try:
            fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            if not isinstance(fm, dict):
                continue
            sid = str(fm.get("subject_id", ""))
            for src in (fm.get("source_ids") or []):
                src_str = str(src)
                if src_str not in source_clusters:
                    source_clusters[src_str] = []
                if sid not in source_clusters[src_str]:
                    source_clusters[src_str].append(sid)
        except Exception:
            continue

    source_clusters = {
        k: v for k, v in source_clusters.items()
        if len(set(v)) >= min_cluster_size
    }

    # ── Compile report ──
    if not (obj_clusters or pred_clusters or source_clusters):
        return ""

    lines: list[str] = []
    lines.append("# Cross-Entity Pattern Report")
    lines.append("")
    lines.append(f"Generated: {now.strftime('%Y-%m-%d %H:%M UTC')}  ")
    lines.append("")

    if obj_clusters:
        lines.append(f"## Shared Object Clusters ({len(obj_clusters)})")
        lines.append("")
        lines.append("Entities linked by the same claim object (company, role, location):")
        lines.append("")
        seen: set[frozenset[str]] = set()
        for obj, subjects in sorted(obj_clusters.items(), key=lambda x: -len(x[1])):
            titles = [_entity_title(vault, s) for s in subjects]
            key = frozenset(titles)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"### {', '.join(titles[:3])}")
            lines.append(f"**Shared:** {obj}")
            lines.append(f"**Entities ({len(subjects)}):** {', '.join(titles)}")
            lines.append("")

    if pred_clusters:
        lines.append(f"## Shared Predicate Clusters ({len(pred_clusters)})")
        lines.append("")
        lines.append("Entities described by the same claim predicate:")
        lines.append("")
        for pred, subjects in sorted(pred_clusters.items(), key=lambda x: -len(x[1])):
            titles = [_entity_title(vault, s) for s in subjects]
            lines.append(f"### {pred}")
            lines.append(f"**Entities ({len(subjects)}):** {', '.join(titles)}")
            lines.append("")

    if source_clusters:
        lines.append(f"## Shared Source Clusters ({len(source_clusters)})")
        lines.append("")
        lines.append("Entities discovered from the same source:")
        lines.append("")
        for src, subjects in sorted(source_clusters.items(), key=lambda x: -len(x[1])):
            titles = [_entity_title(vault, s) for s in subjects]
            lines.append(f"### Source: {src[:8]}...")
            lines.append(f"**Entities ({len(subjects)}):** {', '.join(titles)}")
            lines.append("")

    # ── Thesis statement ──
    lines.append("## Thesis")
    lines.append("")
    total_clusters = len(obj_clusters) + len(pred_clusters) + len(source_clusters)
    all_entities = set()
    for v in obj_clusters.values():
        all_entities.update(v)
    for v in pred_clusters.values():
        all_entities.update(v)

    if all_entities:
        lines.append(
            f"Found {total_clusters} pattern clusters across {len(all_entities)} entities. "
            f"Consider: are there connections here worth acting on?"
        )
    lines.append("")

    return "\n".join(lines) + "\n"
