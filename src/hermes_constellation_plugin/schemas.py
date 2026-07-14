"""JSON schemas exposed to Hermes Agent."""

from __future__ import annotations


def _schema(description: str, properties: dict, required: list[str]) -> dict:
    return {
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


VAULT = {"type": "string", "description": "Absolute path to the Constellation vault"}

STATUS_SCHEMA = _schema(
    "Inspect a Constellation vault without modifying it.", {"vault": VAULT}, ["vault"]
)
INGEST_SCHEMA = _schema(
    "Preserve and extract one local source into a Constellation candidate packet.",
    {"vault": VAULT, "source": {"type": "string"}, "source_url": {"type": "string", "format": "uri"}},
    ["vault", "source"],
)
VALIDATE_SCHEMA = _schema(
    "Validate canonical Constellation records and return bounded errors.",
    {"vault": VAULT, "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
    ["vault"],
)
SEARCH_SCHEMA = _schema(
    "Search canonical Constellation evidence using exact and SQLite FTS routes.",
    {
        "vault": VAULT,
        "query": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "sensitivity": {"type": "string", "enum": ["public", "internal", "confidential", "restricted"]},
    },
    ["vault", "query"],
)
REVIEW_SCHEMA = _schema(
    "List candidates or explicitly promote one conflict-checked candidate.",
    {
        "vault": VAULT,
        "action": {"type": "string", "enum": ["list", "promote"]},
        "candidate": {"type": "string"},
        "expected_base_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "confirm": {"type": "boolean"},
    },
    ["vault", "action"],
)
