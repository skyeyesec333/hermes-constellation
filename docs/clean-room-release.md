# Clean-Room Release

Public artifacts are compiled one way from an explicit allowlist into a new directory outside the working repository. The compiler never copies a private vault or tries to sanitize private notes.

## Fail-closed process

1. `resources/public-lineage.yaml` names every public file and its lineage.
2. Files under a declared public root that are not allowlisted stop the build.
3. Missing allowlisted files stop the build.
4. Known runtime caches such as `__pycache__` are ignored; other undeclared source files are not.
5. Files are copied into a temporary sibling directory.
6. The staged tree must exactly equal the allowlist.
7. The privacy audit scans the staged bytes and paths.
8. Every file receives a SHA-256 digest; the ordered file list receives one tree digest.
9. Only a passing staged tree is atomically renamed to the requested destination.
10. Failure deletes staging and publishes no release directory.

The audit rejects symlinks, databases, browser/auth artifacts, non-example email addresses, IP addresses, private home paths, likely phone numbers, common token/key formats, private-key blocks, generic credential assignments, and caller-supplied private canaries.

## Build locally

Use a destination outside this repository. It must not exist or must be empty.

```bash
export CONSTELLATION_RELEASE_CANARY='a private marker that must never ship'
.venv/bin/python scripts/build_release.py \
  . /tmp/hermes-constellation-public \
  resources/public-lineage.yaml \
  --canary "$CONSTELLATION_RELEASE_CANARY" \
  > /tmp/hermes-constellation-release-report.json
```

The report belongs outside the public tree. It records the manifest hash, every released path/lineage/size/hash, the final tree hash, and the privacy-audit result.

## Environment boundary

The release environment must not mount or copy:

- a private Constellation vault or migration output;
- home/profile state or browser state;
- credentials or private environment files;
- private logs, indexes, embeddings, model transcripts, caches, or backups.

A passing automated audit reduces accidental disclosure risk but is not a guarantee. Publication still requires review of the exact compiled tree and explicit owner approval. GitHub history should start from that compiled tree, never from a private working repository or private-vault history.