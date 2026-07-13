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
- counts canonical, legacy, Markdown, and source files;
- detects missing/invalid frontmatter and duplicate record IDs;
- validates already-canonical notes against the executable schema;
- returns metadata and relative paths, never note bodies.

The resulting report is still private: filenames, relative paths, record IDs, schema versions, and aggregate relationship data can be sensitive. Do not place it in the public repository.

## Apply phase—not implemented

A future private apply adapter requires:

1. A verified backup and restorable snapshot.
2. Review of every mapping and duplicate-ID decision.
3. Input hashes and a versioned migration plan.
4. Explicit apply confirmation.
5. A migration journal with forward-repair instructions.
6. Post-migration schema, link, source, and retrieval validation.

No private migration mappings, aliases, reports, or snapshots belong in the public repository.
