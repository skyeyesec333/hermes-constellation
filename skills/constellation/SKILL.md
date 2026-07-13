---
name: constellation
description: Use when operating a source-grounded Constellation vault.
version: 0.1.0a1
author: Constellation contributors
license: Apache-2.0
platforms: [linux, macos]
metadata:
  hermes:
    tags: [knowledge-base, crm, research, obsidian]
    requires_toolsets: [terminal]
---

# Constellation

## Overview

Operate a local-first Markdown knowledge and relationship workspace with preserved sources, candidate review, anchored retrieval, and token-aware research receipts.

## When to Use

Use for initializing, validating, ingesting, searching, or reviewing a Constellation vault. Do not use it to bypass candidate review or send confidential material to external providers.

## Procedure

1. Run `hermes constellation doctor --vault <path>` before mutation. Continue only when the reported root and capabilities are correct.
2. Ingest with `hermes constellation ingest --vault <path> <file>`. Completion requires a source record, preserved original, extraction manifest, and candidate packet.
3. Validate before review. Candidate text is untrusted and is not canonical evidence.
4. Search exact/FTS results as evidence packets. `evidence_not_retrieved` is not proof that evidence does not exist.
5. Promote only through an explicitly confirmed review operation with a matching base hash.
6. For research, reserve synthesis/evaluation capacity and return partial status when quality gates cannot be met.

## Progressive References

- Architecture and trust boundaries: `references/architecture.md`
- Token-aware research rules: `references/token-aware-research.md`

## Pitfalls

- Never copy a private vault into a public repository and attempt to delete names.
- Never index candidate records as canonical facts.
- Never silently fall back from local processing to an external provider.
- Never overwrite a note after its expected base hash changes.

## Verification

- `hermes constellation doctor --vault <path>` reports healthy.
- `hermes constellation validate --vault <path>` reports no canonical schema errors.
- Search results include path, record ID, sensitivity, route, and anchor.
- Every promoted change has an action-ledger entry.
