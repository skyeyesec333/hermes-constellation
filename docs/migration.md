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

## Apply phase—not implemented

A future private apply adapter requires:

1. A verified backup and restorable snapshot.
2. Review of every mapping and duplicate-ID decision.
3. Input hashes and a versioned migration plan.
4. Explicit apply confirmation.
5. A migration journal with forward-repair instructions.
6. Post-migration schema, link, source, and retrieval validation.

No private migration mappings, aliases, reports, or snapshots belong in the public repository.
