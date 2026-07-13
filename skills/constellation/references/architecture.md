# Architecture Reference

- Markdown and preserved source files are canonical.
- SQLite indexes are disposable and rebuilt from canonical files.
- Collectors write source packages and candidates only.
- Canonical writes require explicit promotion, validation, expected-hash checks, and atomic replacement.
- Public code/data and private instances are physically separate.
- Obsidian is an optional UI, not a runtime dependency.
