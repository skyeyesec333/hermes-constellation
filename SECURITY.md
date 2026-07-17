# Security policy

## Reporting a vulnerability

Use GitHub private vulnerability reporting for this repository:

https://github.com/skyeyesec333/hermes-constellation/security/advisories/new

Do not include real vault contents, credentials, personal data, or exploit material from a private deployment in a public issue.

## Security posture

Constellation treats documents, OCR, web pages, archives, filenames, URLs, model output, candidate patches, and plugin arguments as untrusted.

Primary controls include:

- explicit vault-root and symlink containment;
- preserved source hashes and anchored evidence;
- review-only canonical promotion;
- expected-base hash conflict checks and atomic writes;
- fail-closed provider/model/purpose/sensitivity egress authorization;
- sensitivity ceilings in retrieval;
- one-way allowlist release compilation;
- exact-tree privacy scanning before publication.

See `docs/threat-model.md`, `docs/egress-policy.md`, and `docs/clean-room-release.md`.

## High-risk surfaces

Changes involving filesystem paths, archives, URL fetching, browser automation, model egress, candidate promotion, retrieval completeness, release compilation, plugins, subprocesses, or dependencies require focused tests and independent review.

The optional network research adapters are not part of the offline trusted core. They must never receive confidential or restricted material, silently select an external provider, or bypass the egress and sensitivity contracts.

## Supported releases

The project is alpha software. Security fixes target the latest public release and the active development line. Users should not expose Constellation as a multi-user or internet-facing service unless a future release explicitly documents that deployment model.

## Public-release boundary

A passing automated scan reduces accidental disclosure risk but is not a guarantee. Release publication requires review of the exact compiled tree and explicit owner approval. Private vaults and private Git history are never release inputs.
