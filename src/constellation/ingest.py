"""Source-preserving, deterministic local ingest."""

from __future__ import annotations

import importlib.util
import json
import mimetypes
from datetime import UTC, datetime
from pathlib import Path

from .frontmatter import render_frontmatter
from .models import CandidatePatch, Sensitivity, SourceItem
from .storage import (
    atomic_write_bytes,
    atomic_write_text,
    safe_relative_path,
    sha256_bytes,
)
from .vault import is_initialized

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_CHARS = 5_000_000
MAX_PDF_PAGES = 500


class IngestError(RuntimeError):
    pass


class CapabilityError(IngestError):
    pass


def _id_from_hash(digest: str) -> str:
    value = int(digest[:32], 16)
    characters = ["0"] * 26
    for index in range(25, -1, -1):
        characters[index] = _ULID_ALPHABET[value & 31]
        value >>= 5
    return "".join(characters)


def _read_source(path: Path) -> tuple[bytes, str, str]:
    suffix = path.suffix.lower()
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise IngestError("source exceeds the configured size limit")
    data = path.read_bytes()
    if len(data) > MAX_SOURCE_BYTES:
        raise IngestError("source exceeds the configured size limit")
    if suffix in {".txt", ".md", ".markdown"}:
        if b"\x00" in data:
            raise IngestError("text source contains binary NUL bytes")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IngestError("text sources must be UTF-8") from exc
        if len(text) > MAX_EXTRACTED_CHARS:
            raise IngestError("extracted text exceeds the configured size limit")
        media_type = "text/markdown" if suffix in {".md", ".markdown"} else "text/plain"
        return data, text, media_type
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise IngestError("file extension and PDF signature do not match")
        if importlib.util.find_spec("fitz") is None:
            raise CapabilityError("PDF extraction requires the optional PyMuPDF capability")
        import fitz  # type: ignore[import-not-found]

        try:
            document = fitz.open(stream=data, filetype="pdf")
            if document.page_count > MAX_PDF_PAGES:
                document.close()
                raise IngestError("PDF exceeds the configured page limit")
            text = "\n".join(page.get_text() for page in document)
            document.close()
        except IngestError:
            raise
        except Exception as exc:
            raise IngestError("PDF extraction failed") from exc
        if len(text) > MAX_EXTRACTED_CHARS:
            raise IngestError("extracted text exceeds the configured size limit")
        return data, text, "application/pdf"
    guessed, _ = mimetypes.guess_type(path.name)
    raise IngestError(f"unsupported source type: {guessed or suffix or 'unknown'}")


def ingest_file(
    root: Path | str,
    source: Path | str,
    *,
    now: datetime | None = None,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
) -> dict[str, str]:
    """Ingest one regular in-vault file without promoting any claim."""
    vault = Path(root).absolute()
    if not is_initialized(vault):
        raise IngestError("vault is not initialized")
    source_value = Path(source)
    if source_value.is_absolute():
        try:
            relative = source_value.relative_to(vault)
        except ValueError as exc:
            raise IngestError("source must be inside the vault root") from exc
    else:
        relative = source_value
    try:
        source_path = safe_relative_path(vault, relative)
    except Exception as exc:
        raise IngestError("source path is unsafe") from exc
    if source_path.is_symlink() or not source_path.is_file():
        raise IngestError("source must be a regular non-symlink file")
    data, text, media_type = _read_source(source_path)
    digest = sha256_bytes(data)
    source_id = _id_from_hash(digest)
    manifest_relative = Path(".constellation/manifests") / f"{digest}.json"
    manifest_path = vault / manifest_relative
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        candidate_path = str(manifest["candidate_path"])
        return {
            "schema_version": "0.1",
            "status": "already_ingested",
            "source_id": str(manifest["source_id"]),
            "manifest_path": manifest_relative.as_posix(),
            "preserved_path": str(manifest["preserved_path"]),
            "text_path": str(manifest["text_path"]),
            "source_item_path": str(manifest["source_item_path"]),
            "candidate_id": str(manifest.get("candidate_id") or Path(candidate_path).stem),
            "candidate_path": candidate_path,
        }
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise IngestError("ingest timestamp must include a timezone")
    preserved_relative = Path("Library/Files") / str(instant.year) / source_id / source_path.name
    text_relative = Path("Library/Text") / f"{source_id}.txt"
    source_item_relative = Path("source-items") / f"{source_id}.md"
    atomic_write_bytes(vault, preserved_relative, data)
    atomic_write_text(vault, text_relative, text)
    item = SourceItem(
        id=source_id,
        type="source-item",
        title=source_path.stem,
        status="active",
        sensitivity=sensitivity,
        created_at=instant,
        updated_at=instant,
        source_hash=digest,
        original_path=preserved_relative.as_posix(),
        extracted_text_path=text_relative.as_posix(),
        media_type=media_type,
    )
    metadata = item.model_dump(mode="json", exclude_none=True)
    source_note = render_frontmatter(metadata, f"# {item.title}\n\n{text}")
    candidate = CandidatePatch(
        id=source_id,
        type="candidate-patch",
        title=f"Ingest source: {item.title}",
        status="pending-review",
        sensitivity=sensitivity,
        created_at=instant,
        updated_at=instant,
        target_path=source_item_relative.as_posix(),
        content=source_note,
        expected_base_hash=None,
    )
    candidate_relative = Path(".constellation/candidates") / f"{candidate.id}.json"
    atomic_write_text(vault, candidate_relative, candidate.model_dump_json(indent=2) + "\n")
    manifest = {
        "schema_version": "0.1",
        "mode": "deferred-canonical",
        "source_id": source_id,
        "source_hash": digest,
        "preserved_path": preserved_relative.as_posix(),
        "text_path": text_relative.as_posix(),
        "source_item_path": source_item_relative.as_posix(),
        "candidate_id": candidate.id,
        "candidate_path": candidate_relative.as_posix(),
    }
    atomic_write_text(vault, manifest_relative, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    result = {"status": "staged", **{key: str(value) for key, value in manifest.items()}}
    result["manifest_path"] = manifest_relative.as_posix()
    return result
