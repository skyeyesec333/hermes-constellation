# Migration

The public v0.1 core does not rewrite existing vaults during installation or startup.

Private migration will be implemented separately with:

1. `doctor` and read-only inventory.
2. A versioned migration plan and machine-readable report.
3. Required backup and input hashes.
4. Dry-run fixtures.
5. Explicit apply confirmation.
6. A migration journal and forward-repair instructions.

No private migration mappings or aliases belong in the public repository.
