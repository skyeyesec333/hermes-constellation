"""Local embedding providers and provider resolution.

Provider-neutral: the semantic index consumes any ``EmbeddingProvider``
callable. The built-in ``local-hashing`` provider is deterministic, requires
no network or model download, and gives genuine lexical-similarity signal by
hashing normalized tokens into a fixed-dimension signed vector. Optional
providers (e.g. sentence-transformers) can be registered later without
changing the protocol.

Resolution order: explicit name → vault config ``semantic.embedding_provider``
→ fail closed. An unconfigured or unknown provider is an explicit error,
never a silent fallback.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from .semantic_index import EmbeddingProvider
from .vault import is_initialized

_DIMENSIONS = 128
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class EmbeddingProviderError(RuntimeError):
    """Raised when embedding provider resolution fails closed."""


def local_hashing_embedding(texts: list[str]) -> list[list[float]]:
    """Deterministic local token-hashing embedding (no network, no deps)."""
    vectors: list[list[float]] = []
    for text in texts:
        buckets = [0.0] * _DIMENSIONS
        for token in _TOKEN_PATTERN.findall(text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            bucket = digest[0] % _DIMENSIONS
            sign = 1.0 if digest[1] % 2 == 0 else -1.0
            buckets[bucket] += sign
        norm = sum(value * value for value in buckets) ** 0.5
        if norm == 0.0:
            vectors.append(buckets)
        else:
            vectors.append([value / norm for value in buckets])
    return vectors


_PROVIDERS: dict[str, EmbeddingProvider] = {
    "local-hashing": local_hashing_embedding,
}

_CONFIG_PATH = Path(".constellation/config.yaml")


def resolve_embedding_provider(
    vault: Path | str,
    *,
    name: str | None = None,
) -> EmbeddingProvider:
    """Resolve the configured embedding provider or fail closed."""
    vault = Path(vault).absolute()
    if not is_initialized(vault):
        raise EmbeddingProviderError("vault is not initialized")

    provider_name = name
    if provider_name is None:
        config_file = vault / _CONFIG_PATH
        configured: object = None
        if config_file.is_file() and not config_file.is_symlink():
            try:
                config = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise EmbeddingProviderError("vault config is not valid YAML") from exc
            if isinstance(config, dict):
                semantic = config.get("semantic")
                if isinstance(semantic, dict):
                    configured = semantic.get("embedding_provider")
        if configured is None:
            raise EmbeddingProviderError(
                "no embedding provider configured; set semantic.embedding_provider "
                "in .constellation/config.yaml or pass an explicit provider name"
            )
        provider_name = str(configured)

    provider = _PROVIDERS.get(provider_name)
    if provider is None:
        raise EmbeddingProviderError(f"unknown embedding provider: {provider_name}")
    return provider
