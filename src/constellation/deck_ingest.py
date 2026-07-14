"""Bounded local page-map generation for PDF slide decks."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

_PAGE_MARKER = re.compile(r"^\[P(\d{4})]$")


def _pages_from_text(text: str) -> list[tuple[int, list[str]]]:
    pages: list[tuple[int, list[str]]] = []
    number: int | None = None
    lines: list[str] = []
    for raw_line in text.splitlines():
        marker = _PAGE_MARKER.fullmatch(raw_line.strip())
        if marker is not None:
            if number is not None:
                pages.append((number, lines))
            number = int(marker.group(1))
            lines = []
        elif number is not None and raw_line.strip():
            lines.append(raw_line.strip())
    if number is not None:
        pages.append((number, lines))
    return pages


def _repeated_edge_lines(pages: list[tuple[int, list[str]]]) -> set[str]:
    edges: list[str] = []
    for _, lines in pages:
        if lines:
            edges.extend(set((lines[0], lines[-1])))
    return {line for line, count in Counter(edges).items() if count >= 2}


def build_pdf_deck_map(
    *, source_id: str, text: str, units: list[dict[str, Any]]
) -> dict[str, Any]:
    """Create a review-only deck map; source text and slide anchors remain authoritative."""
    pages = _pages_from_text(text)
    unit_by_index = {int(unit.get("index", 0)): unit for unit in units}
    boilerplate = _repeated_edge_lines(pages)
    slides: list[dict[str, Any]] = []
    for number, original_lines in pages:
        lines = list(original_lines)
        suppressed: list[str] = []
        while lines and lines[0] in boilerplate:
            suppressed.append(lines.pop(0))
        while lines and lines[-1] in boilerplate:
            footer = lines.pop()
            if footer not in suppressed:
                suppressed.append(footer)
        unit = unit_by_index.get(number, {})
        method = str(unit.get("method", "unknown"))
        status = str(unit.get("status", "unknown"))
        slides.append(
            {
                "slide_number": number,
                "anchor": str(unit.get("anchor", f"P{number:04d}")),
                "title": lines[0] if lines else None,
                "text": "\n".join(lines),
                "suppressed_boilerplate": suppressed,
                "visual_verification_required": status != "extracted" or method != "native-text",
            }
        )
    return {
        "version": 1,
        "status": "review-required",
        "source_id": source_id,
        "slides": slides,
    }
