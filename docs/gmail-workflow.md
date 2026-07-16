# Bounded Gmail → Constellation Workflow

Constellation core does **not** store Gmail OAuth credentials, sync mailboxes, or send mail.
Hermes (CSO/CoS) performs a narrow, user-authorized Gmail search via Google Workspace tooling,
writes a local capture document, then Constellation ingests that capture as review-only evidence.

## Rules

1. Bryan authorizes a specific query/person/company/topic and time window.
2. Search only the explicitly authorized Hermes Gmail alias for the session.
3. Cap results (default 10, hard max 50). No bulk mailbox sync.
4. Capture message/thread IDs, headers, body text, attachment refs, query, and receipt only.
5. Never write tokens, cookies, OAuth material, or raw transport blobs into the vault.
6. `send_enabled` and `bulk_sync` are always false in the capture receipt.
7. Ingest creates a staged source candidate only; no automatic entity/relationship writes.
8. Attachments are separate local files, ingested individually and linked by source ID later.

## Capture

Preferred Hermes-side helper:

```bash
python3 ~/.hermes/scripts/gmail-constellation-capture.py \
  --account-alias 'scheduler@mail.example.test' \
  --query 'from:example.com newer_than:30d' \
  --max-results 10 \
  --messages-json /tmp/gmail-messages.json \
  --out /path/to/vault/Inbox/gmail-capture.json
```

`messages-json` must already contain messages fetched by the Google Workspace/Gmail skill or another
Hermes-local connector. The helper only formalizes, validates, dedupes, and writes the capture.

## Ingest

```bash
constellation ingest /path/to/vault Inbox/gmail-capture.json --kind gmail-capture
```

Optional provenance URL remains non-fetched:

```bash
constellation ingest /path/to/vault Inbox/gmail-capture.json \
  --kind gmail-capture \
  --source-url 'https://mail.google.com/mail/u/0/#search/from%3Aexample.com'
```

## Review

- `constellation review <vault> list`
- Promote source-item candidates only after human review.
- Do not append Gmail body text directly into canonical people/company notes.

## Useful workflows

- relationship dossier refresh before a meeting
- what changed since last contact
- commitment / owner / date extraction from a selected thread
- pre-meeting brief combining Gmail evidence + Constellation graph
- attachment intake with source links

## Privacy

Public fixtures must use fictional `*.example.test` addresses constructed from fragments in tests.
Real mailbox content stays private and never enters public release artifacts.
