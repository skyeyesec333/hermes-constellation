# Architecture

Constellation uses a thin Hermes plugin over a reusable Python core. Markdown notes and preserved source files are canonical. Generated indexes and receipts live under `.constellation/` and can be rebuilt.

The trusted loop is:

```text
init → preserve/extract → automatic or reviewed source registration → automatic index rebuild → evidence search → research receipt
```

Ingest always preserves exact source bytes, records SHA-256 provenance, and extracts bounded text. With `source_registration: review`, it writes a create-only `CandidatePatch`. With `source_registration: automatic`, it promotes only that mechanical source record through the same validation, action-ledger, atomic-write, and index-rebuild path. Derived claims, entities, research conclusions, merges, and source updates remain reviewed changes.

Updates carry the exact reviewed base hash and fail on conflicts. Create-only candidates require an absent target and a null expected hash. Candidate extraction never silently changes canonical meaning. Concurrency is handled with expected hashes, same-directory staging, atomic replacement where supported, and conflict artifacts.
