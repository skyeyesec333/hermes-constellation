# Migration

Installation and startup never rewrite existing vaults.

## Read-only inventory and dry run

The alpha CLI can inspect a legacy vault and emit a machine-readable plan without writing to it:

```bash
constellation migrate-plan /path/to/existing-vault \
  --action-limit 1000 \
  --max-files 100000 > migration-plan.private.json
```

The inventory:

- does not follow symlinks;
- excludes `.constellation` derived state from migration inputs;
- counts but does not migrate operational state and noise such as `.obsidian`, `.trash`, `.git`, `node_modules`, `__pycache__`, AppleDouble metadata, and `indexes/generated`;
- counts canonical, legacy, Markdown, and source files;
- detects missing/invalid frontmatter and duplicate record IDs;
- validates already-canonical notes against the executable schema;
- returns metadata and relative paths, never note bodies.

The resulting report is still private: filenames, relative paths, record IDs, schema versions, and aggregate relationship data can be sensitive. Do not place it in the public repository.

## Disposable rehearsal

After reviewing the plan, build a candidate bundle only in a new disposable destination:

```bash
constellation migrate-rehearse /path/to/existing-vault /path/to/new-disposable-output \
  --confirm-disposable
```

The command rejects existing, symlinked, or overlapping destinations. It preserves every selected input byte-for-byte under `preserved/`, writes normalized entity candidates under `candidate-vault/entities/`, writes source-item candidates under `candidate-vault/source-items/`, keeps unsupported legacy notes under `candidate-vault/legacy/`, quarantines malformed or specialized-schema records, preserves non-Markdown sources under `candidate-vault/sources/`, and records hashes in a private journal. Interior symlinks are never followed or copied.

The migration adapter recognizes the legacy `--- auto-discovered degree-2 skeleton below confidence threshold` defect as one narrowly scoped normalization rule. It merges only supplemental keys that are absent from the primary mapping, records conflicting keys in the private plan, and keeps the primary value. Original note bytes are always retained. Legacy source-item candidates use an exact preserved copy of the legacy source note as their initial provenance artifact; this avoids inventing a relationship to an unavailable external file. Misfiled `person`, `company`, or `organization` records found under `source-items/` are mapped as entities according to their declared type.

The rehearsal bundle contains private note bodies and source files. Keep it on trusted local storage, never in the public repository, and delete it after review.

## Gated apply phase

The apply path is intentionally split into a destination-only preparation step and a short atomic activation step. First build a sibling replacement vault from an unchanged source and a matching rehearsal bundle:

```bash
constellation migrate-prepare /path/to/canonical-vault /path/to/rehearsal \
  /path/to/canonical-vault.prepared \
  --expected-source-sha256 <approved-tree-sha256> \
  --confirm-apply-staging
```

Preparation refuses stale source hashes, stale rehearsal mappings, existing or overlapping destinations, candidate/provenance hash mismatches, output collisions, invalid canonical records, duplicate candidate IDs, and missing canonical vault initialization. Source-item paths and filenames are retained so existing Obsidian wikilinks continue resolving. Legacy notes in noncanonical folders remain at their working paths; invalid records from canonical folders move to `quarantine/`; originals displaced by normalized candidates remain under `legacy/` or `sources/legacy-source-items/`. A recognized `.constellation/config.yaml` is retained; a missing or legacy config is replaced with the current canonical manifest while an existing unknown config is preserved under `.migration/legacy-config.yaml`. `.obsidian` state is copied, but generated indexes are not. Symlinks are never followed; any skipped links are listed in the private apply manifest for explicit review.

After a fresh recovery snapshot, stop all vault writers and atomically activate the prepared sibling:

```bash
constellation migrate-activate /path/to/canonical-vault \
  /path/to/canonical-vault.prepared \
  /path/to/canonical-vault.pre-migration \
  --expected-source-sha256 <approved-tree-sha256> \
  --confirm-canonical-apply
```

Activation requires all three paths to be siblings on the same filesystem. It renames the original to the rollback path, renames the prepared vault to the canonical path, fsyncs the parent directory, and validates the activated vault against its private apply manifest. If post-swap validation fails, both original paths are restored automatically. The rollback vault is never deleted automatically.

Operator gates remain mandatory:

1. Verify fresh encrypted recovery snapshots and a recent restore drill.
2. Confirm the canonical tree hash still matches the approved input.
3. Pause gateway, cron, sync, index, and Obsidian writers for the cutover window.
4. Activate once, validate schemas and stable links, then rebuild disposable indexes.
5. Retain the rollback vault until private dogfooding succeeds.

No private migration mappings, aliases, reports, apply manifests, rollback trees, or snapshots belong in the public repository.
