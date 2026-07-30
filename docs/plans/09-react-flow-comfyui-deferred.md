# Plan 09 — React Flow workflow editing + ComfyUI (DEFERRED — isolated)

Status: `ACTIVE — promoted by owner 2026-07-30. Isolated branch, do not entangle with other workstreams.`
Captured: 2026-07-30. Origin: S6-B deferred list; owner 2026-07-30: "6 is
important, that plus comfyUI (if that's necessary) but needs to be an
isolated plan for later." Promoted same day: "go forward with react flow."

## Scope (as currently understood)

- React Flow read/write workflow editing on top of the Constellation
  dashboard plugin (today's surfaces are read-only projections: graph,
  timeline, briefing, review).
- ComfyUI integration ONLY if the design proves it necessary (e.g. visual
  pipeline authoring maps onto a ComfyUI-style node graph engine). Treat as
  an open question, not a dependency.

## Open questions to settle before promotion

1. Is ComfyUI needed at all, or is React Flow alone sufficient?
2. Read/write v1 vs read-only-first? Writes would need the review-gate
   bridge (dashboard writes → candidates, never direct canonical mutation).
3. What is being edited: watchlists, ingest pipelines, kanban boards?
4. New egress/dependency surface: React Flow is an npm dep — public release
   lineage implications.

## Guardrails when promoted

- TDD + verify_fast.sh per repo discipline; public-lineage.yaml entries for
  any new public file; dashboard writes MUST route through the review gate
  (Stage 6/7 invariant: no canonical mutation without an auditable trail).
- Isolated branch; do not entangle with items 2/3/4/7 workstreams.

## First step when promoted

Spike (throwaway): React Flow in the dashboard plugin rendering the existing
read-only graph projection, to validate the rendering path before any write
semantics are designed.
