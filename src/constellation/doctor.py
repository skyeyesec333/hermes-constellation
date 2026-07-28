"""Bounded, machine-readable local capability diagnostics."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

from .models import SCHEMA_VERSION
from .operator import operator_context_status
from .vault import is_initialized


def _fts5_available() -> bool:
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(value)")
        connection.close()
        return True
    except sqlite3.Error:
        return False


_HYGIENE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("apple_double", "._*"),
    ("ds_store", "**/.DS_Store"),
)


def _vault_hygiene(root: Path) -> dict[str, object]:
    """Report on common filesystem cruft (AppleDouble, DS_Store, etc)."""
    cruft_count = 0
    details: dict[str, dict[str, object]] = {}
    for key, pattern in _HYGIENE_PATTERNS:
        matches = list(root.glob(pattern))
        if matches:
            details[key] = {
                "count": len(matches),
                "pattern": pattern,
                "sample_paths": [str(m.relative_to(root)) for m in matches[:5]],
            }
            cruft_count += len(matches)
    return {
        "clean": cruft_count == 0,
        "cruft_count": cruft_count,
        "items": details,
    }


def _referential_integrity(root: Path) -> dict[str, object]:
    """Check cross-record references for orphan IDs."""
    from .frontmatter import parse_frontmatter

    CANONICAL_DIRS = (
        "entities", "people", "source-items", "claims", "interactions",
        "decisions", "inquiries", "opportunities",
    )

    ids: dict[str, set[str]] = {d: set() for d in CANONICAL_DIRS}
    for folder in CANONICAL_DIRS:
        base = root / folder
        if not base.is_dir():
            continue
        for path in base.glob("*.md"):
            try:
                fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(fm, dict) and fm.get("id"):
                ids[folder].add(str(fm["id"]))

    entity_ids = ids["entities"] | ids["people"]
    source_ids = ids["source-items"]
    orphans: dict[str, list[str]] = {}

    orphan_subjects = []
    orphan_claim_sources = []
    for path in (root / "claims").glob("*.md"):
        try:
            fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(fm, dict):
            continue
        subject_id = fm.get("subject_id")
        if subject_id and str(subject_id) not in entity_ids:
            orphan_subjects.append(str(subject_id))
        for sid in (fm.get("source_ids") or []):
            if str(sid) not in source_ids:
                orphan_claim_sources.append(str(sid))

    if orphan_subjects:
        orphans["claim_subjects_without_entity"] = orphan_subjects
    if orphan_claim_sources:
        orphans["claim_sources_without_source_item"] = orphan_claim_sources

    orphan_opp_subjects = []
    for path in (root / "opportunities").glob("*.md"):
        try:
            fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(fm, dict):
            continue
        for sid in (fm.get("subject_ids") or []):
            if str(sid) not in entity_ids:
                orphan_opp_subjects.append(str(sid))

    if orphan_opp_subjects:
        orphans["opportunity_subjects_without_entity"] = orphan_opp_subjects

    total = sum(len(v) for v in orphans.values())
    return {
        "clean": total == 0,
        "orphan_count": total,
        "orphans": orphans,
    }


def _crm_coverage(root: Path) -> dict[str, object]:
    """Report global and pipeline-relevant CRM field coverage.

    Research-only entities should not be bulk-filled with meaningless pipeline
    placeholders. An entity is pipeline-relevant when it already has a
    ``pipeline_stage`` inline field or is referenced by an Interaction,
    Decision, or Opportunity.
    """
    from .frontmatter import parse_frontmatter

    base = root / "entities"
    if not base.is_dir():
        return {
            "total_entities": 0,
            "with_stage": 0,
            "with_touch": 0,
            "with_action": 0,
            "coverage_pct": 0.0,
            "legacy_inline_stage": 0,
            "legacy_inline_touch": 0,
            "legacy_inline_action": 0,
            "pipeline_relevant_entities": 0,
            "research_only_entities": 0,
            "pipeline_relevant_with_stage": 0,
            "pipeline_relevant_with_touch": 0,
            "pipeline_relevant_with_action": 0,
            "pipeline_relevant_coverage_pct": 0.0,
        }

    entities: dict[str, dict[str, bool]] = {}
    for path in base.glob("*.md"):
        try:
            metadata, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(body, str):
            continue
        entity_id = str(metadata.get("id", ""))
        if not entity_id:
            continue
        entities[entity_id] = {
            "stage": bool(metadata.get("stage")) or "pipeline_stage::" in body,
            "touch": bool(metadata.get("last_touch")) or "last_touch::" in body,
            "action": bool(metadata.get("next_action")) or "next_action::" in body,
            "legacy_stage": not metadata.get("stage") and "pipeline_stage::" in body,
            "legacy_touch": not metadata.get("last_touch") and "last_touch::" in body,
            "legacy_action": not metadata.get("next_action") and "next_action::" in body,
        }

    referenced: set[str] = set()
    reference_fields = {
        "interactions": ("subject_ids", "participants"),
        "decisions": ("subject_id",),
        "opportunities": ("subject_ids",),
    }
    for folder, fields in reference_fields.items():
        record_root = root / folder
        if not record_root.is_dir():
            continue
        for path in record_root.glob("*.md"):
            try:
                metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for field in fields:
                value = metadata.get(field)
                if isinstance(value, str):
                    referenced.add(value)
                elif isinstance(value, list):
                    referenced.update(str(item) for item in value)

    pipeline_relevant_ids = {
        entity_id
        for entity_id, fields in entities.items()
        if fields["stage"] or entity_id in referenced
    }
    total = len(entities)
    with_stage = sum(fields["stage"] for fields in entities.values())
    with_touch = sum(fields["touch"] for fields in entities.values())
    with_action = sum(fields["action"] for fields in entities.values())
    relevant_total = len(pipeline_relevant_ids)
    relevant_with_stage = sum(entities[entity_id]["stage"] for entity_id in pipeline_relevant_ids)
    relevant_with_touch = sum(entities[entity_id]["touch"] for entity_id in pipeline_relevant_ids)
    relevant_with_action = sum(entities[entity_id]["action"] for entity_id in pipeline_relevant_ids)

    return {
        "total_entities": total,
        "with_stage": with_stage,
        "with_touch": with_touch,
        "with_action": with_action,
        "coverage_pct": round(with_stage / total * 100, 1) if total else 0.0,
        "legacy_inline_stage": sum(fields["legacy_stage"] for fields in entities.values()),
        "legacy_inline_touch": sum(fields["legacy_touch"] for fields in entities.values()),
        "legacy_inline_action": sum(fields["legacy_action"] for fields in entities.values()),
        "pipeline_relevant_entities": relevant_total,
        "research_only_entities": total - relevant_total,
        "pipeline_relevant_with_stage": relevant_with_stage,
        "pipeline_relevant_with_touch": relevant_with_touch,
        "pipeline_relevant_with_action": relevant_with_action,
        "pipeline_relevant_coverage_pct": (
            round(relevant_with_stage / relevant_total * 100, 1) if relevant_total else 0.0
        ),
    }


def doctor_report(root: Path | str) -> dict[str, object]:
    target = Path(root).absolute()
    initialized = is_initialized(target)
    rapidocr = importlib.util.find_spec("rapidocr_onnxruntime") is not None
    pymupdf = importlib.util.find_spec("fitz") is not None
    pillow = importlib.util.find_spec("PIL") is not None
    return {
        "schema_version": SCHEMA_VERSION,
        "vault": {
            "initialized": initialized,
            "root_exists": target.exists(),
            "root_is_symlink": target.is_symlink(),
            "writable": initialized and target.is_dir(),
        },
        "operator_context": operator_context_status(target) if initialized else {"status": "absent"},
        "capabilities": {
            "sqlite_fts5": _fts5_available(),
            "text_ingest": True,
            "pdf_pymupdf": pymupdf,
            "pdf_scanned_rapidocr": pymupdf and rapidocr,
            "docx_python_docx": importlib.util.find_spec("docx") is not None,
            "pptx_python_pptx": importlib.util.find_spec("pptx") is not None,
            "pptx_markitdown": importlib.util.find_spec("markitdown") is not None,
            "xlsx_openpyxl": importlib.util.find_spec("openpyxl") is not None,
            "image_rapidocr": pillow and rapidocr,
            "mime_libmagic": importlib.util.find_spec("magic") is not None,
            "ooxml_archive_safety": True,
        },
        "vault_hygiene": _vault_hygiene(target) if initialized else {"clean": False, "cruft_count": 0, "items": {}},
        "referential_integrity": _referential_integrity(target) if initialized else {"clean": False, "orphan_count": 0, "orphans": {}},
        "crm_coverage": _crm_coverage(target) if initialized else {"total_entities": 0, "with_stage": 0, "with_touch": 0, "with_action": 0},
    }


def doctor_json(root: Path | str) -> str:
    return json.dumps(doctor_report(root), sort_keys=True, separators=(",", ":"))
