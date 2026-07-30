"""Record-health linting — read-only integrity findings over canonical records.

Every finding links to the exact record, carries a severity, and proposes a
reviewable next action. This module never mutates canonical records.

Stage 7.5 adds the self-healing exception: ``lint_fix(apply=True)`` repairs
ONLY mechanical orphan wikilinks — a link whose text exactly matches exactly
ONE canonical record title is retargeted to that record. Ambiguous or
unmatched links stay report-only. Every fix is journaled with pre/post
hashes and both link tokens; ``rollback_lint_fix`` replays the most recent
run in reverse, restoring original bytes exactly (verified by hash).
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .frontmatter import parse_frontmatter
from .models import generate_ulid
from .storage import atomic_write_text, sha256_file
from .vault import is_initialized


class RecordLintError(RuntimeError):
    """Raised when linting cannot proceed safely."""


def _scan(vault: Path, folder: str) -> list[tuple[str, dict[str, Any]]]:
    base = vault / folder
    if not base.is_dir():
        return []
    records: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(base.glob("*.md")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            metadata, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if metadata.get("id"):
            records.append((f"{folder}/{path.name}", metadata))
    return records


def lint_records(vault: Path | str) -> dict[str, Any]:
    """Lint canonical records and return cited, severity-ranked findings.

    Read-only: no record is created, modified, or deleted.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise RecordLintError("vault is not initialized")

    findings: list[dict[str, Any]] = []

    claims = _scan(vault, "claims")
    source_ids = {str(m["id"]) for _, m in _scan(vault, "source-items")}
    entity_ids = {str(m["id"]) for _, m in _scan(vault, "entities")}
    entity_ids |= {str(m["id"]) for _, m in _scan(vault, "people")}

    for record_path, metadata in claims:
        claim_id = str(metadata["id"])
        sources = [str(s) for s in metadata.get("source_ids", [])]
        if not sources:
            findings.append({
                "check": "claim_without_sources",
                "severity": "high",
                "record_id": claim_id,
                "record_path": record_path,
                "detail": f"claim '{metadata.get('title', claim_id)}' has no source anchors",
                "suggested_action": "attach source_ids via candidate patch or retract the claim",
            })
        for source_id in sources:
            if source_id not in source_ids:
                findings.append({
                    "check": "broken_source_reference",
                    "severity": "high",
                    "record_id": claim_id,
                    "record_path": record_path,
                    "detail": f"claim references missing source-item {source_id}",
                    "suggested_action": "restore the source-item or re-anchor the claim",
                })
        subject = str(metadata.get("subject_id", ""))
        if subject and subject not in entity_ids:
            findings.append({
                "check": "broken_subject_reference",
                "severity": "high",
                "record_id": claim_id,
                "record_path": record_path,
                "detail": f"claim subject {subject} has no canonical entity/person record",
                "suggested_action": "create the entity or repoint the claim",
            })

    # Contradictory active claims: same subject+predicate, differing object,
    # both active, no visible resolution state.
    by_key: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    for record_path, metadata in claims:
        if metadata.get("status") != "active":
            continue
        obj = metadata.get("object_id") or metadata.get("object_literal")
        if not obj:
            continue
        key = (str(metadata.get("subject_id", "")), str(metadata.get("predicate", "")))
        by_key[key].append((str(metadata["id"]), str(obj), record_path))
    for (subject, predicate), entries in sorted(by_key.items()):
        objects = {obj for _, obj, _ in entries}
        if len(objects) > 1:
            findings.append({
                "check": "contradictory_claims",
                "severity": "medium",
                "record_id": entries[0][0],
                "record_path": entries[0][2],
                "detail": (
                    f"{len(entries)} active claims on {subject} predicate "
                    f"'{predicate}' disagree: {sorted(objects)}"
                ),
                "suggested_action": "review and mark one claim superseded or retracted",
            })

    # Review backlog: pending-review candidates older than 7 days.
    candidates_dir = vault / ".constellation/candidates"
    if candidates_dir.is_dir():
        import json
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        for path in sorted(candidates_dir.glob("*.json")):
            try:
                candidate = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if candidate.get("status") != "pending-review":
                continue
            created = candidate.get("created_at", "")
            try:
                age_days = (now - datetime.fromisoformat(str(created).replace("Z", "+00:00"))).days
            except ValueError:
                continue
            if age_days >= 7:
                findings.append({
                    "check": "stale_pending_candidate",
                    "severity": "medium",
                    "record_id": str(candidate.get("id", path.stem)),
                    "record_path": f".constellation/candidates/{path.name}",
                    "detail": f"candidate pending review for {age_days} days",
                    "suggested_action": "promote, archive, or reject with an explicit decision",
                })

    severity_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (severity_order.get(str(f["severity"]), 3), str(f["check"]), str(f["record_id"])))
    summary: dict[str, int] = {"total": len(findings), "high": 0, "medium": 0, "low": 0}
    for finding in findings:
        severity = str(finding["severity"])
        summary[severity] = summary.get(severity, 0) + 1
    return {"findings": findings, "summary": summary}


# ── Stage 7.5 self-healing lint --fix ──────────────────────────────────────

_WIKILINK = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]]*))?\]\]")
_LINT_FOLDERS = (
    "claims", "entities", "people", "source-items", "decisions",
    "opportunities", "interactions", "events", "observations", "analyses",
)
_JOURNAL_REL = Path(".constellation/lint-fixes.jsonl")


def _title_index(vault: Path) -> dict[str, list[tuple[str, str, str]]]:
    """title.casefold() -> [(folder, stem, title)] across canonical folders."""
    index: dict[str, list[tuple[str, str, str]]] = {}
    for folder in _LINT_FOLDERS:
        for record_path, metadata in _scan(vault, folder):
            title = str(metadata.get("title", "")).strip()
            if title:
                stem = Path(record_path).stem
                index.setdefault(title.casefold(), []).append((folder, stem, title))
    return index


def _all_stems(vault: Path) -> set[str]:
    stems: set[str] = set()
    for folder in _LINT_FOLDERS:
        base = vault / folder
        if base.is_dir():
            stems.update(p.stem for p in base.glob("*.md") if p.is_file() and not p.is_symlink())
    return stems


def _link_resolves(vault: Path, target: str, stems: set[str]) -> bool:
    target = target.split("#", 1)[0].split("^", 1)[0].strip()
    if not target:
        return True  # pure anchor links are not orphans
    if (vault / f"{target}.md").is_file():
        return True
    if "/" in target:
        folder, stem = target.split("/", 1)
        return (vault / folder / f"{stem}.md").is_file()
    return target in stems


def lint_fix(vault: Path | str, *, apply: bool = False) -> dict[str, Any]:
    """Detect orphan wikilinks; repair only single-unambiguous-target cases.

    apply=False is a dry run: everything is reported, nothing is written.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise RecordLintError("vault is not initialized")

    titles = _title_index(vault)
    stems = _all_stems(vault)

    fixable: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    # path -> list of (original_token, replacement_token, link, replacement)
    per_file: dict[Path, list[tuple[str, str, str, str]]] = {}

    for folder in _LINT_FOLDERS:
        base = vault / folder
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            seen_links: set[str] = set()
            for match in _WIKILINK.finditer(text):
                token = match.group(0)
                link = match.group(1).strip()
                alias = match.group(2)
                if link in seen_links:
                    continue
                seen_links.add(link)
                if _link_resolves(vault, link, stems):
                    continue
                matches = titles.get(link.casefold(), [])
                rel = path.relative_to(vault).as_posix()
                if len(matches) == 1:
                    target_folder, target_stem, _ = matches[0]
                    replacement = f"{target_folder}/{target_stem}"
                    replacement_token = (
                        f"[[{replacement}|{alias}]]" if alias
                        else f"[[{replacement}|{link}]]"
                    )
                    entry = {
                        "path": rel,
                        "link": link,
                        "replacement": replacement,
                        "original_token": token,
                        "replacement_token": replacement_token,
                    }
                    fixable.append(entry)
                    per_file.setdefault(path, []).append(
                        (token, replacement_token, link, replacement)
                    )
                else:
                    remaining.append({
                        "check": "orphan_wikilink",
                        "severity": "low",
                        "record_path": rel,
                        "detail": (
                            f"link [[{link}]] has {'ambiguous' if matches else 'no'} "
                            f"canonical target ({len(matches)} match(es))"
                        ),
                        "suggested_action": "repoint manually — not a mechanical repair",
                    })

    fixes: list[dict[str, Any]] = []
    if apply and per_file:
        run_id = generate_ulid()
        journal_entries: list[dict[str, Any]] = []
        for path, replacements in sorted(per_file.items()):
            old_sha = sha256_file(path)
            text = path.read_text(encoding="utf-8")
            for original_token, replacement_token, link, replacement in replacements:
                if original_token not in text:
                    raise RecordLintError(
                        f"link token vanished before write: {original_token} in {path}"
                    )
                text = text.replace(original_token, replacement_token)
            atomic_write_text(
                vault, path.relative_to(vault), text, expected_hash=old_sha
            )
            new_sha = sha256_file(path)
            for original_token, replacement_token, link, replacement in replacements:
                journal_entries.append({
                    "run_id": run_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "path": path.relative_to(vault).as_posix(),
                    "link": link,
                    "replacement": replacement,
                    "original_token": original_token,
                    "replacement_token": replacement_token,
                    "old_sha256": old_sha,
                    "new_sha256": new_sha,
                })
                fixes.append({
                    "path": path.relative_to(vault).as_posix(),
                    "link": link,
                    "replacement": replacement,
                })
        journal = vault / _JOURNAL_REL
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a", encoding="utf-8") as handle:
            for entry in journal_entries:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")

    return {
        "applied": apply,
        "fixes": fixes,
        "fixable": fixes if apply else [
            {k: f[k] for k in ("path", "link", "replacement")} for f in fixable
        ],
        "remaining": remaining,
    }


def rollback_lint_fix(vault: Path | str) -> dict[str, Any]:
    """Replay the most recent --fix run in reverse, byte-exact by hash.

    Fail closed: a missing journal, a file whose current hash differs from
    the journaled post-fix hash, or a post-rollback hash mismatch all raise
    before any further writes.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise RecordLintError("vault is not initialized")
    journal = vault / _JOURNAL_REL
    if not journal.is_file():
        raise RecordLintError("no lint-fix journal — nothing to roll back")
    entries = [
        json.loads(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not entries:
        raise RecordLintError("lint-fix journal is empty — nothing to roll back")

    last_run = str(entries[-1]["run_id"])
    run_entries = [e for e in entries if e["run_id"] == last_run]

    # preflight every file before any write
    for entry in run_entries:
        path = vault / str(entry["path"])
        if path.is_symlink() or not path.is_file():
            raise RecordLintError(f"rollback target missing: {entry['path']}")
        if sha256_file(path) != entry["new_sha256"]:
            raise RecordLintError(
                f"rollback refused: {entry['path']} changed since the fix run"
            )

    # group by file: one write per file, all token inverses applied together
    by_path: dict[str, list[dict[str, Any]]] = {}
    for entry in run_entries:
        by_path.setdefault(str(entry["path"]), []).append(entry)

    rolled_back = 0
    for rel_path in sorted(by_path, reverse=True):
        entries_for_file = by_path[rel_path]
        path = vault / rel_path
        text = path.read_text(encoding="utf-8")
        for entry in reversed(entries_for_file):
            if str(entry["replacement_token"]) not in text:
                raise RecordLintError(
                    f"rollback token missing in {rel_path}: {entry['replacement_token']}"
                )
            text = text.replace(str(entry["replacement_token"]), str(entry["original_token"]))
        atomic_write_text(
            vault, Path(rel_path), text,
            expected_hash=str(entries_for_file[0]["new_sha256"]),
        )
        if sha256_file(path) != entries_for_file[0]["old_sha256"]:
            raise RecordLintError(
                f"rollback hash mismatch for {rel_path} — vault left inconsistent"
            )
        rolled_back += len(entries_for_file)

    survivors = [e for e in entries if e["run_id"] != last_run]
    atomic_write_text(
        vault,
        _JOURNAL_REL,
        "".join(json.dumps(e, sort_keys=True) + "\n" for e in survivors),
    )
    return {"rolled_back": rolled_back, "run_id": last_run}
