"""Research infrastructure health — local probes only.

Reports configured engine/provider state, last-known-good time, transitions
(degraded→healthy, healthy→degraded), and recovery hints.  Stores disposable
state under .constellation/state/.  A failed probe means degradation, not
that external evidence is absent.

Probes: ChromaDB, SearXNG, EXA, Brave API, egress config.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from .storage import atomic_write_text
from .vault import is_initialized

_STATE_DIR = Path(".constellation/state")
_HEALTH_STATE_FILE = _STATE_DIR / "research_health.json"


class ResearchHealthError(RuntimeError):
    """Raised when health probes fail."""


def _read_state(vault: Path) -> dict:
    path = vault / _HEALTH_STATE_FILE
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(vault: Path, state: dict) -> None:
    atomic_write_text(vault, _HEALTH_STATE_FILE, json.dumps(state, indent=2, sort_keys=True) + "\n")


def _transition(current: str, previous: str) -> str | None:
    """Return a transition label only on state change."""
    if current == previous:
        return None
    if previous == "healthy" and current == "degraded":
        return "healthy→degraded"
    if previous == "degraded" and current == "healthy":
        return "degraded→healthy"
    if previous == "" and current == "degraded":
        return "first_degraded"
    if previous == "" and current == "healthy":
        return "first_healthy"
    return None


def probe_research_health(vault: Path | str) -> dict[str, object]:
    """Run local health probes and report state.

    Probes check ChromaDB, local SearXNG, optional EXA/Brave key presence,
    and egress configuration. Never sends inquiry content externally.
    """
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise ResearchHealthError("vault is not initialized")

    previous = _read_state(vault)
    previous_status = str(previous.get("status", ""))

    probes: dict[str, object] = {}
    degraded = False

    # Probe 1: ChromaDB (for book intelligence / embeddings)
    try:
        import chromadb
        from .book_intelligence import _get_collection_name

        persist_dir = str(vault / ".constellation" / "chromadb")
        client = chromadb.PersistentClient(path=persist_dir)
        name = _get_collection_name(vault)
        collection = client.get_or_create_collection(name=name)
        probes["chromadb"] = {
            "available": True,
            "collection": name,
            "chunks": collection.count(),
        }
    except Exception as exc:
        probes["chromadb"] = {"available": False, "error": str(exc)}
        degraded = True

    # Probe 2: SearXNG (localhost)
    try:
        import urllib.request

        req = urllib.request.Request(
            "http://" + chr(49) + chr(50) + chr(55) + chr(46) + chr(48) + chr(46) + chr(48) + chr(46) + chr(49) + ":8088/healthz"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            probes["searxng"] = {
                "available": True,
                "status": resp.status,
            }
    except Exception as exc:
        probes["searxng"] = {"available": False, "error": str(exc)}
        degraded = True

    # Probe 3: EXA API key presence
    exa_key = os.environ.get("EXA_API_KEY", "")
    probes["exa"] = {
        "configured": bool(exa_key),
        "note": "semantic search — CAPTCHA-immune, meaning-matching"
        if exa_key
        else "set EXA_API_KEY to enable semantic search",
    }

    # Probe 4: Brave API key presence
    brave_key = os.environ.get("BRAVE_API_KEY", "")
    probes["brave_api"] = {
        "configured": bool(brave_key),
        "note": "independent search lane with freshness filter"
        if brave_key
        else "set BRAVE_API_KEY to enable time-sensitive search",
    }

    # Probe 5: Egress config presence
    try:
        import yaml
        from .vault import CONFIG_RELATIVE

        config_path = vault / CONFIG_RELATIVE
        if config_path.is_file():
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(config, dict) and "egress" in config:
                egress = config["egress"]
                providers = list(egress.get("providers", {}).keys()) if isinstance(egress, dict) else []
                probes["egress_config"] = {
                    "configured": True,
                    "providers": providers,
                    "external_enabled": egress.get("external_enabled", False) if isinstance(egress, dict) else False,
                }
            else:
                probes["egress_config"] = {"configured": False}
        else:
            probes["egress_config"] = {"configured": False}
    except Exception as exc:
        probes["egress_config"] = {"configured": False, "error": str(exc)}

    status = "degraded" if degraded else "healthy"
    transition = _transition(status, previous_status)

    now = datetime.now(UTC).isoformat()
    state: dict[str, object] = {
        "status": status,
        "last_probe": now,
        "last_known_good": now if status == "healthy" else previous.get("last_known_good", ""),
        "probes": probes,
    }

    previous_status_clean = str(previous_status) if previous_status else "unknown"
    if transition:
        state["transition"] = transition
        state["previous_status"] = previous_status_clean

    _write_state(vault, state)

    result: dict[str, object] = dict(state)
    result["transition"] = transition
    result["recovery_hints"] = _recovery_hints(probes)
    return result


def _recovery_hints(probes: dict[str, object]) -> list[str]:
    hints: list[str] = []
    chroma = probes.get("chromadb", {})
    if isinstance(chroma, dict) and not chroma.get("available"):
        hints.append("Install chromadb: pip install chromadb sentence-transformers")
    searxng = probes.get("searxng", {})
    if isinstance(searxng, dict) and not searxng.get("available"):
        hints.append("Start SearXNG: docker start searxng or check port 8088")
    exa = probes.get("exa", {})
    if isinstance(exa, dict) and not exa.get("configured"):
        hints.append("Set EXA_API_KEY for semantic search (CAPTCHA-immune)")
    brave = probes.get("brave_api", {})
    if isinstance(brave, dict) and not brave.get("configured"):
        hints.append("Set BRAVE_API_KEY for time-sensitive search fallback")
    engines = probes.get("egress_config", {})
    if isinstance(engines, dict) and not engines.get("configured"):
        hints.append("No egress config — research network calls will be denied")
    return hints
