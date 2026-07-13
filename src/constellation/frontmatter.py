"""Deterministic YAML frontmatter parsing and rendering."""

from __future__ import annotations

from collections.abc import Mapping

import yaml


class FrontmatterError(ValueError):
    pass


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        raise FrontmatterError("document must begin with YAML frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise FrontmatterError("frontmatter closing delimiter is missing")
    raw = text[4:end]
    try:
        metadata = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise FrontmatterError("frontmatter is invalid YAML") from exc
    if not isinstance(metadata, dict) or not all(isinstance(key, str) for key in metadata):
        raise FrontmatterError("frontmatter must be a string-keyed mapping")
    return metadata, text[end + 5 :]


def render_frontmatter(metadata: Mapping[str, object], body: str = "") -> str:
    if not all(isinstance(key, str) for key in metadata):
        raise FrontmatterError("frontmatter must have string keys")
    serialized = yaml.safe_dump(
        dict(metadata), allow_unicode=True, default_flow_style=False, sort_keys=False
    ).rstrip("\n")
    return f"---\n{serialized}\n---\n{body}"
