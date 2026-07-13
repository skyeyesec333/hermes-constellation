"""Source-preserving, deterministic local ingest."""

from __future__ import annotations

import importlib.util
import json
import mimetypes
import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from .frontmatter import parse_frontmatter, render_frontmatter
from .models import CandidatePatch, Sensitivity, SourceItem
from .storage import (
    atomic_write_bytes,
    atomic_write_text,
    safe_relative_path,
    sha256_bytes,
    sha256_file,
)
from .vault import CONFIG_RELATIVE, is_initialized

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_CHARS = 5_000_000
MAX_PDF_PAGES = 500


class IngestError(RuntimeError):
    pass


class CapabilityError(IngestError):
    pass


def _source_registration_mode(vault: Path) -> str:
    config_path = safe_relative_path(vault, CONFIG_RELATIVE)
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise IngestError("vault configuration is unreadable") from exc
    mode = config.get("source_registration", "review") if isinstance(config, dict) else None
    if mode not in {"review", "automatic"}:
        raise IngestError("source_registration must be review or automatic")
    return mode


@dataclass(frozen=True)
class ExtractedSource:
    data: bytes
    text: str
    media_type: str
    extraction: dict[str, Any]


def _id_from_hash(digest: str) -> str:
    value = int(digest[:32], 16)
    characters = ["0"] * 26
    for index in range(25, -1, -1):
        characters[index] = _ULID_ALPHABET[value & 31]
        value >>= 5
    return "".join(characters)


def _line_count(text: str) -> int:
    return len(text.splitlines()) or 1


def _text_extraction(data: bytes, text: str, media_type: str) -> ExtractedSource:
    if not text.strip():
        raise IngestError("text source contains no extractable content")
    lines = _line_count(text)
    return ExtractedSource(
        data=data,
        text=text,
        media_type=media_type,
        extraction={
            "schema_version": "0.1",
            "status": "complete",
            "engine": {
                "name": "python-utf8",
                "version": platform.python_version(),
                "options": {"encoding": "utf-8", "errors": "strict"},
            },
            "source_sha256": sha256_bytes(data),
            "extracted_text_sha256": sha256_bytes(text.encode("utf-8")),
            "characters": len(text),
            "expected_units": 1,
            "extracted_units": 1,
            "blank_units": 0,
            "failed_units": 0,
            "truncated_units": 0,
            "warnings": [],
            "units": [
                {
                    "kind": "text",
                    "index": 1,
                    "status": "extracted",
                    "anchor": f"L000001-L{lines:06d}",
                    "line_start": 1,
                    "line_end": lines,
                    "characters": len(text),
                    "text_sha256": sha256_bytes(text.encode("utf-8")),
                }
            ],
        },
    )


def _pdf_extraction(data: bytes) -> ExtractedSource:
    if importlib.util.find_spec("fitz") is None:
        raise CapabilityError("PDF extraction requires the optional PyMuPDF capability")
    import fitz  # type: ignore[import-not-found]

    document = None
    try:
        document = fitz.open(stream=data, filetype="pdf")
        if document.page_count > MAX_PDF_PAGES:
            raise IngestError("PDF exceeds the configured page limit")
        if document.page_count < 1:
            raise IngestError("PDF contains no pages")
        sections: list[str] = []
        units: list[dict[str, Any]] = []
        extracted_units = 0
        blank_units = 0
        for page_index in range(1, document.page_count + 1):
            page = document.load_page(page_index - 1)
            page_text = cast(str, page.get_text("text"))
            marker = f"[P{page_index:04d}]"
            sections.append(f"{marker}\n{page_text.rstrip()}" if page_text.strip() else marker)
            if page_text.strip():
                extracted_units += 1
                lines = _line_count(page_text)
                status = "extracted"
                anchor = f"P{page_index:04d}:L0001-L{lines:04d}"
            else:
                blank_units += 1
                lines = 0
                status = "blank-needs-ocr"
                anchor = f"P{page_index:04d}"
            units.append(
                {
                    "kind": "page",
                    "index": page_index,
                    "status": status,
                    "anchor": anchor,
                    "line_start": 1 if lines else None,
                    "line_end": lines or None,
                    "characters": len(page_text),
                    "text_sha256": sha256_bytes(page_text.encode("utf-8")),
                }
            )
        if extracted_units == 0:
            raise CapabilityError("PDF contains no native text and requires OCR")
        text = "\n\n".join(sections) + "\n"
        if len(text) > MAX_EXTRACTED_CHARS:
            raise IngestError("extracted text exceeds the configured size limit")
        warnings = []
        status = "complete"
        if blank_units:
            status = "complete-with-gaps"
            warnings.append(f"{blank_units} page(s) contain no native text and require OCR")
        version = str(getattr(fitz, "VersionBind", getattr(fitz, "__version__", "unknown")))
        return ExtractedSource(
            data=data,
            text=text,
            media_type="application/pdf",
            extraction={
                "schema_version": "0.1",
                "status": status,
                "engine": {
                    "name": "PyMuPDF",
                    "version": version,
                    "options": {"method": "page.get_text", "mode": "text"},
                },
                "source_sha256": sha256_bytes(data),
                "extracted_text_sha256": sha256_bytes(text.encode("utf-8")),
                "characters": len(text),
                "expected_units": document.page_count,
                "extracted_units": extracted_units,
                "blank_units": blank_units,
                "failed_units": 0,
                "truncated_units": 0,
                "warnings": warnings,
                "units": units,
            },
        )
    except (IngestError, CapabilityError):
        raise
    except Exception as exc:
        raise IngestError("PDF extraction failed") from exc
    finally:
        if document is not None:
            document.close()


def _read_source(path: Path) -> ExtractedSource:
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
        return _text_extraction(data, text, media_type)
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise IngestError("file extension and PDF signature do not match")
        return _pdf_extraction(data)
    guessed, _ = mimetypes.guess_type(path.name)
    raise IngestError(f"unsupported source type: {guessed or suffix or 'unknown'}")


def _stage_source_extraction_upgrade(
    vault: Path,
    manifest: dict[str, Any],
    manifest_relative: Path,
    extraction: dict[str, Any],
    text: str,
    instant: datetime,
) -> bool:
    """Stage a hash-checked source-note update for an already-promoted source."""
    source_item_relative = str(manifest["source_item_path"])
    source_item_path = safe_relative_path(vault, source_item_relative)
    if not source_item_path.is_file() or source_item_path.is_symlink():
        return False
    current_text = source_item_path.read_text(encoding="utf-8")
    metadata, _ = parse_frontmatter(current_text)
    metadata["extraction_manifest_path"] = manifest_relative.as_posix()
    metadata["extraction_status"] = str(extraction["status"])
    metadata["updated_at"] = instant.isoformat()
    source_item = SourceItem.model_validate(metadata, strict=False)
    updated_note = render_frontmatter(
        source_item.model_dump(mode="json", exclude_none=True),
        f"# {source_item.title}\n\n{text}",
    )
    if updated_note == current_text:
        return False
    candidate = CandidatePatch(
        id=source_item.id,
        type="candidate-patch",
        title=f"Upgrade extraction record: {source_item.title}",
        status="pending-review",
        sensitivity=source_item.sensitivity,
        created_at=instant,
        updated_at=instant,
        target_path=source_item_relative,
        content=updated_note,
        expected_base_hash=sha256_file(source_item_path),
    )
    candidate_relative = Path(".constellation/candidates") / f"{candidate.id}.json"
    candidate_path = vault / candidate_relative
    serialized = candidate.model_dump_json(indent=2) + "\n"
    if candidate_path.exists():
        try:
            existing = CandidatePatch.model_validate_json(candidate_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise IngestError("existing source candidate is invalid") from exc
        if (
            existing.target_path != source_item_relative
            or existing.expected_base_hash != candidate.expected_base_hash
            or not existing.title.startswith("Upgrade extraction record:")
        ):
            raise IngestError("a different source upgrade candidate already exists")
    else:
        atomic_write_text(vault, candidate_relative, serialized)
    manifest["candidate_id"] = candidate.id
    manifest["candidate_path"] = candidate_relative.as_posix()
    return True


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
    registration_mode = _source_registration_mode(vault)
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
    extracted = _read_source(source_path)
    data, text, media_type = extracted.data, extracted.text, extracted.media_type
    digest = sha256_bytes(data)
    if extracted.extraction["source_sha256"] != digest:
        raise IngestError("extraction source hash does not match preserved bytes")
    source_id = _id_from_hash(digest)
    manifest_relative = Path(".constellation/manifests") / f"{digest}.json"
    manifest_path = vault / manifest_relative
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise IngestError("ingest timestamp must include a timezone")
    extraction = dict(extracted.extraction)
    extraction["extracted_at"] = instant.isoformat()
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("source_hash") != digest:
            raise IngestError("existing manifest does not match source bytes")
        candidate_path = str(manifest["candidate_path"])
        upgraded = "extraction" not in manifest
        if upgraded:
            atomic_write_text(vault, str(manifest["text_path"]), text)
            manifest.update(
                {
                    "source_size_bytes": len(data),
                    "media_type": media_type,
                    "extraction": extraction,
                }
            )
        source_patch_staged = _stage_source_extraction_upgrade(
            vault,
            manifest,
            manifest_relative,
            manifest.get("extraction", extraction),
            text,
            instant,
        )
        candidate_path = str(manifest["candidate_path"])
        if upgraded or source_patch_staged:
            atomic_write_text(
                vault,
                manifest_relative,
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            )
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
            "extraction_status": str(manifest.get("extraction", extraction)["status"]),
            "manifest_upgraded": str(upgraded).lower(),
            "source_patch_staged": str(source_patch_staged).lower(),
        }
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
        extraction_manifest_path=manifest_relative.as_posix(),
        extraction_status=extraction["status"],
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
        "source_size_bytes": len(data),
        "media_type": media_type,
        "ingested_at": instant.isoformat(),
        "input_path": relative.as_posix(),
        "preserved_path": preserved_relative.as_posix(),
        "text_path": text_relative.as_posix(),
        "source_item_path": source_item_relative.as_posix(),
        "candidate_id": candidate.id,
        "candidate_path": candidate_relative.as_posix(),
        "registration": {
            "mode": registration_mode,
            "status": "pending-review" if registration_mode == "review" else "pending-automatic",
        },
        "extraction": extraction,
    }
    atomic_write_text(vault, manifest_relative, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    promotion: dict[str, str] | None = None
    if registration_mode == "automatic":
        from .review import promote_candidate

        promotion = promote_candidate(
            vault,
            candidate.id,
            confirm=True,
            expected_base_hash=None,
        )
        manifest["registration"] = {"mode": "automatic", "status": "canonical"}
        atomic_write_text(
            vault,
            manifest_relative,
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
    result = {
        "status": "registered" if promotion else "staged",
        **{
            key: str(value)
            for key, value in manifest.items()
            if key not in {"extraction", "registration"}
        },
    }
    if promotion:
        result["index_generation"] = promotion["index_generation"]
    result["manifest_path"] = manifest_relative.as_posix()
    result["extraction_status"] = str(extraction["status"])
    return result
