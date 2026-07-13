# Threat Model

## Protected assets

Canonical notes, preserved sources, relationship data, credentials, research receipts, derived indexes, and release integrity.

## Untrusted inputs

Documents, OCR output, web pages, Markdown/HTML/SVG, archives, model output, plugin arguments, candidate patches, filenames, and URLs.

## Primary controls

- Offline core and default-deny provider use.
- Explicit vault roots with path/symlink containment.
- Candidate-only collection and explicit canonical promotion.
- Expected-hash conflict checks and atomic replacement.
- Disposable retrieval indexes with sensitivity ceilings.
- Bounded versioned tool responses.
- One-way allowlist release compiler and exact-artifact privacy scan.

## Explicit non-goals for v0.1

The alpha does not execute active document content, provide network crawling, run OCR, offer multi-user authorization, or guarantee atomicity on remote/network filesystems.
