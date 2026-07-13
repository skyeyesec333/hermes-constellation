# Architecture Reference

- Markdown and preserved source files are canonical.
- SQLite indexes are disposable and rebuilt from canonical files.
- Collectors preserve source packages and may register only mechanical source records when vault policy explicitly enables it.
- Meaning-bearing canonical writes require explicit promotion, validation, expected-hash checks, and atomic replacement.
- Public code/data and private instances are physically separate.
- Obsidian is an optional UI, not a runtime dependency.
