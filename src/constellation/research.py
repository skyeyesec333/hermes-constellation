"""Token-aware research budgeting and immutable run receipts.

This module records resource use only; it does not call providers.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .models import ResearchTerminalState, generate_ulid
from .storage import atomic_write_text


class BudgetExhausted(RuntimeError):
    """Raised before an operation would exceed its available budget."""


@dataclass(frozen=True, slots=True)
class ResearchBudget:
    max_calls: int
    max_tokens: int
    max_cost_usd: float
    max_context_bytes: int
    synthesis_reserve: float = 0.25

    def __post_init__(self) -> None:
        if min(self.max_calls, self.max_tokens, self.max_context_bytes) <= 0:
            raise ValueError("integer budget limits must be positive")
        if self.max_cost_usd <= 0:
            raise ValueError("cost budget must be positive")
        if not 0.0 < self.synthesis_reserve < 1.0:
            raise ValueError("synthesis reserve must be between zero and one")


class ResearchLedger:
    """In-memory policy ledger with a locked synthesis/evaluation reserve."""

    _RESERVED_LANES = frozenset({"synthesis", "evaluation"})

    def __init__(self, budget: ResearchBudget, *, run_id: str | None = None) -> None:
        self.budget = budget
        self.run_id = run_id or generate_ulid()
        self.status: ResearchTerminalState | None = None
        self.stop_reason: str | None = None
        self.calls: list[dict[str, Any]] = []
        self.sources: list[dict[str, str]] = []
        self._source_hashes: set[str] = set()
        self._source_urls: set[str] = set()
        self.pivotal_claims: list[str] = []
        self.contradictions: list[str] = []
        self.unresolved_gaps: list[str] = []

    @property
    def promotion_allowed(self) -> bool:
        return self.status is ResearchTerminalState.COMPLETED

    def _used_for_enforcement(self, field: str) -> float:
        total = 0.0
        for call in self.calls:
            value = call[field]
            if value == "unknown":
                value = call[f"estimated_{field}"]
            total += float(value or 0)
        return total

    def _limit(self, field: str) -> float:
        return {
            "tokens": float(self.budget.max_tokens),
            "cost_usd": float(self.budget.max_cost_usd),
            "context_bytes": float(self.budget.max_context_bytes),
        }[field]

    def _check_capacity(self, lane: str, tokens: float, cost: float, context: float) -> None:
        reserved = lane in self._RESERVED_LANES
        multiplier = 1.0 if reserved else 1.0 - self.budget.synthesis_reserve
        call_limit = self.budget.max_calls if reserved else int(
            self.budget.max_calls * multiplier
        )
        if len(self.calls) + 1 > call_limit:
            self._exhaust("call budget exhausted")
        proposed = {
            "tokens": self._used_for_enforcement("tokens") + tokens,
            "cost_usd": self._used_for_enforcement("cost_usd") + cost,
            "context_bytes": self._used_for_enforcement("context_bytes") + context,
        }
        for field, value in proposed.items():
            if value > self._limit(field) * multiplier:
                self._exhaust(f"{field} budget exhausted")

    def _exhaust(self, reason: str) -> None:
        self.status = ResearchTerminalState.BUDGET_EXHAUSTED
        self.stop_reason = reason
        raise BudgetExhausted(reason)

    def record_call(
        self,
        *,
        lane: str,
        provider: str,
        success: bool,
        tokens: int | None,
        cost_usd: float | None,
        context_bytes: int,
        estimated_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
    ) -> None:
        if self.status is not None:
            raise RuntimeError("research run is already terminal")
        if lane not in {"collection", "adjudication", "synthesis", "evaluation"}:
            raise ValueError("unknown research lane")
        if context_bytes < 0 or any(value is not None and value < 0 for value in (tokens, cost_usd)):
            raise ValueError("usage values cannot be negative")
        token_charge = tokens if tokens is not None else estimated_tokens
        cost_charge = cost_usd if cost_usd is not None else estimated_cost_usd
        if token_charge is None or cost_charge is None:
            self._exhaust("provider usage unavailable and no estimate supplied")
        self._check_capacity(lane, float(token_charge), float(cost_charge), float(context_bytes))
        self.calls.append(
            {
                "lane": lane,
                "provider": provider,
                "success": success,
                "tokens": tokens if tokens is not None else "unknown",
                "cost_usd": cost_usd if cost_usd is not None else "unknown",
                "context_bytes": context_bytes,
                "estimated_tokens": estimated_tokens,
                "estimated_cost_usd": estimated_cost_usd,
            }
        )

    def add_source(self, *, source_hash: str, url: str) -> bool:
        if source_hash in self._source_hashes or url in self._source_urls:
            return False
        self._source_hashes.add(source_hash)
        self._source_urls.add(url)
        self.sources.append({"source_hash": source_hash, "url": url})
        return True

    def finish(
        self,
        status: ResearchTerminalState,
        *,
        stop_reason: str,
        pivotal_claims: list[str] | None = None,
        contradictions: list[str] | None = None,
        unresolved_gaps: list[str] | None = None,
    ) -> None:
        if self.status is not None:
            raise RuntimeError("research run is already terminal")
        self.status = status
        self.stop_reason = stop_reason
        self.pivotal_claims = list(pivotal_claims or [])
        self.contradictions = list(contradictions or [])
        self.unresolved_gaps = list(unresolved_gaps or [])

    def _usage(self, field: str) -> int | float | str:
        if any(call[field] == "unknown" for call in self.calls):
            return "unknown"
        return sum(call[field] for call in self.calls)

    def receipt(self) -> dict[str, Any]:
        lanes: dict[str, dict[str, int]] = {}
        for call in self.calls:
            lane = lanes.setdefault(call["lane"], {"calls": 0, "failed_calls": 0})
            lane["calls"] += 1
            lane["failed_calls"] += int(not call["success"])
        return {
            "version": 1,
            "run_id": self.run_id,
            "status": self.status.value if self.status else "in_progress",
            "stop_reason": self.stop_reason,
            "promotion_allowed": self.promotion_allowed,
            "budget": asdict(self.budget),
            "usage": {
                "calls": len(self.calls),
                "tokens": self._usage("tokens"),
                "cost_usd": self._usage("cost_usd"),
                "context_bytes": self._usage("context_bytes"),
            },
            "lanes": lanes,
            "calls": list(self.calls),
            "sources": list(self.sources),
            "pivotal_claims": list(self.pivotal_claims),
            "contradictions": list(self.contradictions),
            "unresolved_gaps": list(self.unresolved_gaps),
        }

    def receipt_json(self) -> str:
        return json.dumps(self.receipt(), sort_keys=True, separators=(",", ":"))


def research_command(vault: Path, values: dict[str, Any]) -> dict[str, Any]:
    """Minimal offline CLI receipt creation/status helper."""
    state = vault / ".constellation" / "research-runs"
    action = values.get("action")
    if action == "start":
        ledger = ResearchLedger(
            ResearchBudget(max_calls=8, max_tokens=8000, max_cost_usd=8.0, max_context_bytes=80_000)
        )
        relative = Path(".constellation/research-runs") / f"{ledger.run_id}.json"
        atomic_write_text(vault, relative, ledger.receipt_json() + "\n")
        return ledger.receipt()
    if action == "status":
        run_id = str(values.get("run_id") or "")
        if not run_id:
            raise ValueError("run_id is required for research status")
        path = state / f"{run_id}.json"
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError("research run receipt not found")
        return json.loads(path.read_text(encoding="utf-8"))
    raise ValueError("research action must be start or status")
