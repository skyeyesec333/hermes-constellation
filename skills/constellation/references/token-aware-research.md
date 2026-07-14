# Token-Aware Research Reference

Constellation research receipts are evidence and accounting records, not provider callers.

## Budget rules

- Budget calls, total tokens, cost, context bytes, synthesis, and evaluation separately.
- Lock the final 25% of calls, tokens, cost, and context for synthesis/evaluation.
- Stop before a proposed call would exceed its lane's available budget.
- Budget exhaustion is a terminal state and blocks canonical promotion.
- `constellation preflight <vault> <file> --task <kind>` builds a local no-egress plan before ingest; it separately budgets source bytes, pages, audio minutes, extraction, model input/output tokens, context, calls, and cost.
- Local extraction and transcription receive zero LLM-token allocation. Books beyond their default profile require explicit long-form mode rather than silently changing global limits.

## Receipt version 2

Every recorded model call includes:

- lane: collection, adjudication, synthesis, or evaluation;
- provider, model, and model version (`unknown` when the provider omits a version);
- provider request ID when available;
- success/failure and retry attempt;
- provider-reported total, input, output, reasoning, cache-read, and cache-write tokens;
- separately labelled token estimates;
- provider-reported and estimated cost;
- context bytes and duration;
- prompt SHA-256 and evidence-packet SHA-256 when available.

Unknown provider usage remains `unknown`, never zero. When a budget decision must be made without provider usage, the receipt records both the estimate and that the estimate—not a provider measurement—was used for enforcement.

Every evidence source includes its content hash, URL, local source ID when present, citation anchor, sensitivity, retrieval time, and origin cluster. The receipt hashes the complete evidence set so later changes are detectable. A source count does not establish independence; derivative sources should share an origin cluster.

## Terminal states

- `completed`: quality gates passed; promotion may be considered.
- `partial`: useful result with unresolved evidence or quality gaps; promotion blocked.
- `budget_exhausted`: stopped by a hard budget; promotion blocked.
- `failed`: no reliable result; promotion blocked.
- `cancelled`: intentionally stopped; promotion blocked.

A terminal canonical `ResearchRun` may carry a receipt only when the receipt version, run ID, status, promotion decision, and finish timestamp agree with the canonical note.
