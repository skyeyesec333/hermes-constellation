# Connector Capture Contract

Constellation core never fetches a URL. A connector or user captures content locally, then invokes:

```text
constellation ingest <vault> <local-capture-file> --source-url https://example.test/page
```

`source_url` is provenance only; it is not evidence on its own. The local capture bytes, extraction manifest, content hash, and anchored text remain the evidence record.

## Receipt required from any future connector

- connector name and version;
- source URL or dataset identifier;
- query or watch identifier;
- retrieval timestamp;
- content hash and previous content hash;
- changed/not-changed result;
- local capture path;
- credentials boundary, never credentials;
- retry/error status;
- license or terms note; and
- suggested sensitivity.

## Safety rules

- Capture locally before ingestion; do not add network clients to the core package.
- URLs must be absolute HTTP(S) URLs and must not include credentials.
- Unchanged bytes are hash-idempotent and do not trigger synthesis.
- Changed bytes create a separate source/candidate path; they never silently rewrite canonical records.
- RSS, page-diff, registry, and news adapters remain unsupported until they emit this receipt and have focused tests.
