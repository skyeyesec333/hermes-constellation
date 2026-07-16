# Token-Aware Research and Task Preflight

Constellation controls waste without weakening the reasoning stage.

- Budgets are applied before calls.
- `constellation preflight <vault> <file> --task <kind>` produces a local, no-egress plan and does not ingest the file.
- `constellation synthesize <vault> plan --task <kind>` returns the full preserve→extract→map→retrieve→reason→pressure-test→synthesize→review plan for that task.
- Profiles separately cap source bytes, pages, audio duration, OCR/extraction work, model calls, input/output tokens, context bytes, and cost.
- Local extraction and transcription have a zero LLM-token allocation; only retrieval and synthesis receive model budgets.
- A book exceeding its default profile requires explicit long-form mode instead of silently changing global ingest limits.
- Source acquisition cannot consume the locked synthesis/evaluation reserve.
- Cached derived artifacts (maps, transcripts, indexes) reduce estimated model calls when their content hashes are supplied.
- Hierarchical retrieval prefers document/meeting/deck maps before raw segments for books, papers, decks, and meetings.
- Competitive analysis allocates a larger contradiction/pressure-test share inside the LLM budget.
- Search stops after two bounded lanes produce no adjudicated evidence delta and mandatory contradiction checks are complete.
- Evidence packets cannot exceed the task profile byte budget; oversized packets fail closed.
- Unknown usage is not recorded as zero.
- Budget exhaustion returns a gap list and blocks promotion.
- Partial/no-delta runs cannot promote.
- Budgeted configurations are compared with an unbudgeted reference on fixed public benchmarks.
