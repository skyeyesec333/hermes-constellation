# Architecture

Constellation uses a thin Hermes plugin over a reusable Python core. Markdown notes and preserved source files are canonical. Generated indexes and receipts live under `.constellation/` and can be rebuilt.

The trusted loop is:

```text
init → preserve/extract → candidate review → explicit promotion → automatic index rebuild → evidence search → research receipt
```

Ingest is deliberately deferred. It preserves exact source bytes, records SHA-256 provenance, extracts bounded text, and writes a complete create-only `CandidatePatch`; it does not write canonical Markdown. Explicit promotion validates and atomically creates the source-item, appends the action ledger, removes the candidate, and rebuilds retrieval. This keeps the active evidence index current while a candidate is waiting for human review.

Updates carry the exact reviewed base hash and fail on conflicts. Create-only candidates require an absent target and a null expected hash. Candidate extraction never silently changes canonical meaning. Concurrency is handled with expected hashes, same-directory staging, atomic replacement where supported, and conflict artifacts.
