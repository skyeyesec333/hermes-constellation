# Architecture

Constellation uses a thin Hermes plugin over a reusable Python core. Markdown notes and preserved source files are canonical. Generated indexes and receipts live under `.constellation/` and can be rebuilt.

The trusted loop is:

```text
init → ingest → candidate review → explicit promotion → index → evidence search → research receipt
```

Candidate extraction never silently changes canonical meaning. Concurrency is handled with expected hashes, same-directory staging, atomic replacement where supported, and conflict artifacts.
