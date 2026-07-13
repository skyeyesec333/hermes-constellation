"""Deterministic JSON Schema and Markdown template generation."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .models import RECORD_MODELS, SCHEMA_VERSION


def json_schema_text(model: type[BaseModel]) -> str:
    """Return stable, newline-terminated JSON Schema for a model."""
    return json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"


def note_template(model: type[BaseModel]) -> str:
    """Return a deterministic canonical-note template for a record model."""
    properties = model.model_json_schema().get("properties", {})
    ordered = [
        "schema_version", "id", "type", "title", "status",
        "sensitivity", "created_at", "updated_at",
    ]
    ordered.extend(sorted(name for name in properties if name not in ordered and name != "can_promote"))
    lines = ["---"]
    for name in ordered:
        if name not in properties:
            continue
        if name == "schema_version":
            value = f"'{SCHEMA_VERSION}'"
        elif name == "id":
            value = "''"
        elif name == "sensitivity":
            value = "internal"
        elif properties[name].get("type") == "array":
            value = "[]"
        elif properties[name].get("type") == "object":
            value = "{}"
        else:
            value = "''"
        lines.append(f"{name}: {value}")
    lines.extend(["---", "", f"# {model.__name__}", ""])
    return "\n".join(lines)


def generate_artifacts(schema_dir: Path, template_dir: Path) -> list[Path]:
    """Generate all schemas/templates, returning files written in stable order."""
    schema_dir.mkdir(parents=True, exist_ok=True)
    template_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for model in sorted(RECORD_MODELS, key=lambda item: item.__name__):
        stem = model.__name__.lower()
        schema_path = schema_dir / f"{stem}.json"
        template_path = template_dir / f"{stem}.md"
        schema_path.write_text(json_schema_text(model), encoding="utf-8")
        template_path.write_text(note_template(model), encoding="utf-8")
        written.extend((schema_path, template_path))
    return written
