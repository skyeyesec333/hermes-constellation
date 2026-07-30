"""Disposable generation-built SQLite FTS5 retrieval."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path

from .frontmatter import parse_frontmatter
from .models import Sensitivity, generate_ulid
from .storage import atomic_write_text, safe_relative_path, sha256_file
from .validation import ALLOWED_CANONICAL_FOLDERS, CanonicalValidationError, validate_canonical_text
from .vault import is_initialized

_PACKET_VERSION = "2"
_PAGE_MARKER = re.compile(r"(?m)^\[P(\d{4})\]\s*$")
_SENSITIVITY_RANK = {
    Sensitivity.PUBLIC.value: 0,
    Sensitivity.INTERNAL.value: 1,
    Sensitivity.CONFIDENTIAL.value: 2,
    Sensitivity.RESTRICTED.value: 3,
}


class RetrievalError(RuntimeError):
    pass


def _canonical_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for folder in sorted(ALLOWED_CANONICAL_FOLDERS):
        base = safe_relative_path(root, folder)
        if not base.exists():
            continue
        pending = [base]
        while pending:
            directory = pending.pop()
            for child in sorted(directory.iterdir(), key=lambda item: item.name):
                if child.is_symlink():
                    continue
                if child.is_dir():
                    pending.append(child)
                elif child.is_file() and child.suffix == ".md" and not child.name.startswith("._"):
                    files.append(child)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _corpus_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _canonical_files(root):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _render_markdown_index(records: list[dict[str, str]], fingerprint: str) -> str:
    sections = {
        folder: [record for record in records if record["path"].split("/", 1)[0] == folder]
        for folder in sorted(ALLOWED_CANONICAL_FOLDERS)
    }
    lines = [
        "# Constellation — Canonical Index",
        "",
        "> Generated from schema-valid records in canonical folders only.",
        f"> **Records:** {len(records)} | **Corpus fingerprint:** `{fingerprint}`",
        "> Compatibility, quarantine, templates, and private migration artifacts are excluded.",
        "",
        "See also: [[HOME]] · [[MOC]]",
    ]
    for folder, entries in sections.items():
        lines.extend(
            [
                "",
                f"## {folder.replace('-', ' ').title()} ({len(entries)})",
                "",
                "| Note | Type | Status | Sensitivity |",
                "|---|---|---|---|",
            ]
        )
        for record in sorted(entries, key=lambda item: (item["title"].casefold(), item["path"])):
            link = record["path"].removesuffix(".md")
            title = " ".join(record["title"].split()).replace("|", "\\|")
            lines.append(
                f"| [[{link}|{title}]] | {record['type']} | {record['status']} | {record['sensitivity']} |"
            )
    return "\n".join(lines) + "\n"


def build_index(root: Path | str) -> dict[str, object]:
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise RetrievalError("vault is not initialized")
    state = safe_relative_path(vault, ".constellation/state")
    generation = generate_ulid()
    database_relative = Path(".constellation/state") / f"index-{generation}.sqlite3"
    database = vault / database_relative
    descriptor, temporary_name = tempfile.mkstemp(prefix=".index-", suffix=".sqlite3", dir=state)
    os.close(descriptor)
    temporary = Path(temporary_name)
    indexed = 0
    skipped: list[str] = []
    human_records: list[dict[str, str]] = []
    try:
        connection = sqlite3.connect(temporary)
        connection.execute(
            "CREATE TABLE documents (note_id TEXT PRIMARY KEY, path TEXT NOT NULL, title TEXT NOT NULL, "
            "body TEXT NOT NULL, source_hash TEXT NOT NULL, sensitivity TEXT NOT NULL)"
        )
        connection.execute("CREATE VIRTUAL TABLE search_index USING fts5(note_id UNINDEXED, title, body)")
        for path in _canonical_files(vault):
            relative = path.relative_to(vault).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
                record = validate_canonical_text(text, relative)
                metadata, body = parse_frontmatter(text)
                title = str(metadata["title"])
                sensitivity = str(metadata["sensitivity"])
                source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                connection.execute(
                    "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
                    (record.id, relative, title, body, source_hash, sensitivity),
                )
                connection.execute(
                    "INSERT INTO search_index VALUES (?, ?, ?)", (record.id, title, body)
                )
                human_records.append(
                    {
                        "path": relative,
                        "title": title,
                        "type": str(metadata["type"]),
                        "status": str(metadata["status"]),
                        "sensitivity": sensitivity,
                    }
                )
                indexed += 1
            except (CanonicalValidationError, UnicodeError, sqlite3.IntegrityError):
                skipped.append(relative)
        connection.commit()
        connection.execute("PRAGMA optimize")
        connection.close()
        os.replace(temporary, database)
    finally:
        temporary.unlink(missing_ok=True)
    fingerprint = _corpus_fingerprint(vault)
    atomic_write_text(vault, "INDEX.md", _render_markdown_index(human_records, fingerprint))
    active = {
        "schema_version": "0.1",
        "generation": generation,
        "database": database_relative.as_posix(),
        "fingerprint": fingerprint,
    }
    atomic_write_text(
        vault,
        ".constellation/state/active-index.json",
        json.dumps(active, sort_keys=True, separators=(",", ":")) + "\n",
    )
    pruned = 0
    for old_database in state.glob("index-*.sqlite3"):
        if old_database == database or old_database.is_symlink() or not old_database.is_file():
            continue
        old_database.unlink()
        pruned += 1
    return {
        "schema_version": "0.1",
        "generation": generation,
        "indexed": indexed,
        "skipped": skipped,
        "pruned_generations": pruned,
    }


def _active_database(root: Path) -> tuple[Path | None, str | None]:
    pointer = safe_relative_path(root, ".constellation/state/active-index.json")
    if not pointer.is_file() or pointer.is_symlink():
        return None, "index_missing"
    try:
        active = json.loads(pointer.read_text(encoding="utf-8"))
        database = safe_relative_path(root, active["database"])
        if not database.is_file() or database.is_symlink():
            return None, "index_missing"
        if active["fingerprint"] != _corpus_fingerprint(root):
            return None, "index_stale"
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return None, "index_invalid"
    return database, None


def _packet(status: str, evidence: list[dict[str, object]], *, reason: str | None = None) -> dict[str, object]:
    packet: dict[str, object] = {
        "schema_version": "0.1",
        "packet_version": _PACKET_VERSION,
        "status": status,
        "evidence": evidence,
    }
    if reason:
        packet["reason"] = reason
    return packet


def _best_match_position(body: str, terms: tuple[str, ...]) -> int:
    folded = body.casefold()
    markers = list(_PAGE_MARKER.finditer(body))
    if markers and terms:
        best_score = 0
        best_position = -1
        for index, marker in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else len(body)
            section = folded[marker.end():end]
            score = sum(term.casefold() in section for term in terms)
            positions = [section.find(term.casefold()) for term in terms]
            positions = [position for position in positions if position >= 0]
            if score > best_score and positions:
                best_score = score
                best_position = marker.end() + min(positions)
        if best_position >= 0:
            return best_position
    positions = [folded.find(term.casefold()) for term in terms]
    positions = [position for position in positions if position >= 0]
    if positions:
        return min(positions)
    if markers:
        return min(markers[0].end() + 1, len(body))
    return 0


def _anchored_excerpt(body: str, terms: tuple[str, ...]) -> str:
    position = _best_match_position(body, terms)
    line_start = body.rfind("\n", 0, position) + 1
    line_end = body.find("\n", position)
    if line_end < 0:
        line_end = len(body)
    excerpt = " ".join(body[line_start:line_end].split())
    markers = [marker for marker in _PAGE_MARKER.finditer(body) if marker.start() <= position]
    if markers:
        marker = markers[-1]
        line_number = max(1, body[marker.end():line_start].count("\n"))
        location = f"P{marker.group(1)}:L{line_number:04d}"
    else:
        location = f"note:L{body[:line_start].count(chr(10)) + 1:04d}"
    if not excerpt:
        excerpt = " ".join(body.split())
    available = max(0, 240 - len(location) - 3)
    return f"{location} | {excerpt[:available]}"


def _evidence(
    row: sqlite3.Row,
    route: str,
    score: float,
    terms: tuple[str, ...] = (),
) -> dict[str, object]:
    anchor = _anchored_excerpt(str(row["body"]), terms)
    return {
        "note_id": row["note_id"],
        "path": row["path"],
        "anchor": anchor,
        "source_hash": row["source_hash"],
        "sensitivity": row["sensitivity"],
        "route": route,
        "score": round(float(score), 6),
    }


def _ceiling_rank(value: Sensitivity | str) -> int:
    key = value.value if isinstance(value, Sensitivity) else value
    try:
        return _SENSITIVITY_RANK[key]
    except KeyError as exc:
        raise RetrievalError("invalid sensitivity ceiling") from exc


def exact_lookup(
    root: Path | str,
    note_id: str,
    *,
    sensitivity_ceiling: Sensitivity | str = Sensitivity.RESTRICTED,
) -> dict[str, object]:
    vault = Path(root).absolute()
    database, reason = _active_database(vault)
    if database is None:
        return _packet("evidence_not_retrieved", [], reason=reason)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT * FROM documents WHERE note_id = ?", (note_id,)).fetchone()
    connection.close()
    if row is None or _SENSITIVITY_RANK[row["sensitivity"]] > _ceiling_rank(sensitivity_ceiling):
        return _packet("no_evidence_found", [])
    return _packet("evidence_found", [_evidence(row, "exact_id", 1.0)])


def _attach_derived_confidence(vault: Path, evidence: list[dict[str, object]]) -> None:
    """Attach the 7.2 derived confidence score to claim evidence rows.

    Pure derivation over canonical metadata — the stored record is never
    touched; the score rides along as an index/display artifact.
    """
    from datetime import UTC, datetime

    from .confidence import compute_confidence
    from .frontmatter import parse_frontmatter

    now = datetime.now(UTC)
    for item in evidence:
        path = str(item.get("path", ""))
        if not path.startswith("claims/"):
            continue
        try:
            metadata, _ = parse_frontmatter((vault / path).read_text(encoding="utf-8"))
            item["confidence"] = compute_confidence(metadata, now=now)
        except Exception:  # noqa: BLE001 — missing/unparseable record degrades, never breaks search
            continue


def _confidence_of(item: dict[str, object]) -> float:
    conf = item.get("confidence")
    if isinstance(conf, dict):
        return float(conf.get("score", 0.0))
    return 0.0


def _tiebreak_by_confidence(evidence: list[dict[str, object]]) -> list[dict[str, object]]:
    """Order equal-scored evidence by derived confidence (FTS rank stays primary)."""
    result: list[dict[str, object]] = []
    index = 0
    while index < len(evidence):
        end = index + 1
        while end < len(evidence) and evidence[end]["score"] == evidence[index]["score"]:
            end += 1
        group = evidence[index:end]
        group.sort(key=_confidence_of, reverse=True)
        result.extend(group)
        index = end
    return result


def search(
    root: Path | str,
    query: str,
    *,
    sensitivity_ceiling: Sensitivity | str = Sensitivity.RESTRICTED,
    limit: int = 10,
) -> dict[str, object]:
    vault = Path(root).absolute()
    database, reason = _active_database(vault)
    if database is None:
        return _packet("evidence_not_retrieved", [], reason=reason)
    terms = re.findall(r"[\w-]+", query, flags=re.UNICODE)
    if not terms:
        return _packet("no_evidence_found", [])
    expression = " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms[:20])
    bounded_limit = max(1, min(int(limit), 50))
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT d.*, bm25(search_index) AS rank FROM search_index "
        "JOIN documents d ON d.note_id = search_index.note_id "
        "WHERE search_index MATCH ? ORDER BY rank LIMIT ?",
        (expression, bounded_limit * 4),
    ).fetchall()
    connection.close()
    ceiling = _ceiling_rank(sensitivity_ceiling)
    evidence = [
        _evidence(row, "fts5", -float(row["rank"]), tuple(terms))
        for row in rows
        if _SENSITIVITY_RANK[row["sensitivity"]] <= ceiling
    ][:bounded_limit]
    _attach_derived_confidence(vault, evidence)
    evidence = _tiebreak_by_confidence(evidence)
    return _packet("evidence_found" if evidence else "no_evidence_found", evidence)


lookup_id = exact_lookup
rebuild_index = build_index
