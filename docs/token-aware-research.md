# Token-Aware Research and Task Preflight

Constellation controls waste without weakening the reasoning stage.

- Budgets are applied before calls.
- `constellation preflight <vault> <file> --task <kind>` produces a local, no-egress plan and does not ingest the file.
- Profiles separately cap source bytes, pages, audio duration, OCR/extraction work, model calls, input/output tokens, context bytes, and cost.
- Local extraction and transcription have a zero LLM-token allocation; only retrieval and synthesis receive model budgets.
- A book exceeding its default profile requires explicit long-form mode instead of silently changing global ingest limits.
- Source acquisition cannot consume the locked synthesis/evaluation reserve.
- Search stops after two bounded lanes produce no adjudicated evidence delta and mandatory contradiction checks are complete.
- Unknown usage is not recorded as zero.
- Budget exhaustion returns a gap list and blocks promotion.
- Budgeted configurations are compared with an unbudgeted reference on fixed public benchmarks.
