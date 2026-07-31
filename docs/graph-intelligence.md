# Graph intelligence contracts (i2-successor program)

Status: frozen for private implementation (Wave 0, 2026-07-30).
Authority: owner-approved i2-successor graph intelligence program
specification held in the private maintenance lane. This document freezes the
behavior contracts of that specification in repository-durable form so any
implementer can continue without the private document.

## 1. Central differentiator

Constellation is not a cheaper i2 clone. The core promise:

> Every node, link, analytic score, path, alert, and risk signal can be traced
> to canonical records and preserved evidence; machine-generated changes
> remain review-gated; knowledge can age, contradict, and be superseded
> without history loss.

## 2. User-visible outcomes

An analyst must be able to:

1. represent a directed, typed, time-bounded, sourced relationship;
2. use a controlled predicate vocabulary without losing legacy predicates;
3. import/export a useful FollowTheMoney-compatible subset from local files;
4. discover mentions and possible links without treating co-occurrence as fact;
5. stage high-quality relationship candidates from existing evidence;
6. rank intermediaries and influential nodes using explainable SNA metrics;
7. find filtered directed paths valid at a selected date;
8. scrub a timeline and see the graph change with it;
9. filter via histograms/facets with graph-linked selection;
10. detect material graph changes and receive a bounded alert;
11. execute evidence-backed risk typologies that stage review candidates;
12. generate a 360° dossier and clearance-bounded export with a manifest;
13. integrate future connectors through explicit capability contracts.

## 3. Non-goals

- no Neo4j, Elasticsearch, message broker, or new always-on service;
- no real-time multi-user canvas collaboration;
- no generic low-code workflow builder or ComfyUI integration;
- no auto-promotion of extracted relationships or risk findings;
- no opaque LLM-generated graph score;
- no direct copy of ASD Constellation, OpenCTI, MISP, FtM, i2, or Maltego code/data;
- no live OpenSanctions connector in this program;
- no public push or rename decision.

## 4. Canonical data design

### 4.1 Relationship schema v1 (additive extension of `RelationshipRecord`)

New optional fields: `observed_at`, `first_seen`, `last_seen`, `valid_from`,
`valid_to`, `role`, `qualifiers`, `supersedes`.

Semantics and validation:

- `valid_to >= valid_from` when both exist; `last_seen >= first_seen` when both exist.
- At most 20 qualifiers; qualifier keys match `^[a-z][a-z0-9_]{0,63}$`;
  qualifier values are bounded strings.
- `observed_at` = when evidence observed the relationship (not file-write time).
- `first_seen`/`last_seen` bound repeated observations; distinct from real-world validity.
- `valid_from`/`valid_to` = real-world validity interval.
- `created_at`/`updated_at` remain local technical lifecycle dates and must
  never be interpreted as functional validity.
- Correcting endpoints, predicate, qualifiers, or functional dates creates a
  reviewed superseding relationship; it never silently rewrites assertion identity.
- No current canonical relationship is invalidated by missing new fields.
- Predicate-registry enforcement starts in advisory mode so legacy predicates
  remain readable; candidates created after registry delivery must use a
  canonical predicate or explicitly mark an experimental predicate.

Design rationale: FollowTheMoney models membership/ownership as
relationship-like interval records with start/end dates, role, percentage, and
proof. Constellation already treats relationships as first-class
property-bearing records, avoiding FtM's documented edge-contraction
workaround. `qualifiers` supplies bounded relationship-specific metadata
without multiplying record classes. Not all time intervals become
relationships — FtM explicitly warns against that.

### 4.2 Predicate registry (`resources/predicates/core.yaml`)

Each entry: `name`, `label`, `inverse`, `directed`, `symmetric`, `domains`,
`ranges`, `stability` (`durable|standard|transient`), `allowed_qualifiers`,
`aliases`, `external_mappings` (e.g. `ftm`, `stix`), `deprecated_by`.

Invariants:

- unique `name` and alias across the registry;
- inverse exists or is null;
- symmetric predicates cannot have a different inverse;
- no alias chain/cycle;
- domain/range values are known entity kinds or `any`;
- stability replaces the hard-coded predicate map in `confidence.py` through
  one shared lookup;
- deprecation preserves old values and proposes review-gated normalization;
  it never silently rewrites canonical files.

The initial vocabulary is built from an inventory of predicates already used
in the synthetic fixture and the private vault (see
`scripts/predicate_inventory.py`), plus a deliberately small
general-intelligence seed set: `owns`, `controls`, `employed_by`, `directs`,
`member_of`, `advises`, `funds`, `partners_with`, `competes_with`, `supplies`,
`located_at`, `reports_to`, `associated_with`, `served_with`. `supersedes`,
`supports`, and contradiction links remain claim lifecycle semantics, not
ordinary entity relationships.

### 4.3 Three identity concepts — kept separate

1. `record_id`: the stable ULID of one canonical assertion record.
2. `assertion_fingerprint`: deterministic SHA-256 over normalized subject,
   canonical predicate, object, functional interval, and sorted qualifiers
   (endpoints sorted first for symmetric predicates). Used for deduplication,
   re-review, backfill idempotency, and delta comparison. Never replaces the
   record ULID. Corrected assertions supersede, producing a new fingerprint
   while retaining history.
3. `edge_id`: deterministic projection key for one rendered edge:
   `sha256(edge_kind | record_id | subject_id | predicate | object_id)`.
   Exposed in projections; required for delta alerts, UI selection
   persistence, and deterministic exports. Array-position edge IDs are
   retired.

### 4.4 Mentions are not relationships

A source mentioning two entities does not prove they are related. Three
distinct outputs:

1. `MentionHit` — derived, noncanonical source/entity reference with anchor
   and match method;
2. `CoMentionCandidate` — investigative lead only; excluded from canonical
   path/SNA by default;
3. `RelationshipRecord` candidate — requires explicit predicate, endpoints,
   source IDs, and evidence excerpt/anchor.

Co-mentions are never auto-converted into `associated_with` canonical edges.

## 5. Source-project adaptations and limits

All implementation is independently written against behavior contracts.
Concepts are adapted; code and data are not copied. The full URL/license
ledger is `docs/references/graph-intelligence-sources.md`.

| Source | Adapt | Do not copy/adopt |
|---|---|---|
| FollowTheMoney (MIT) / OpenSanctions | Mapping layer; interval semantics; role/percentage qualifiers; local FtM JSON import/export; two-stage candidate matching | Full FtM ontology, edge contraction, Aleph stack; OpenSanctions repository data/content without honoring its separate CC BY-NC 4.0 terms |
| OpenCTI Community (Apache-2.0) | Connector capability taxonomy; controlled relationship vocabulary; import/enrich/export/stream concepts | STIX cyber lock-in, RabbitMQ/Elastic stack, enterprise code |
| MISP decaying models (CC0/BSD-2) | Configurable model files, threshold/simulation, sightings/reinforcement distinction | Replace the current confidence model wholesale |
| MISP galaxies/warninglists (JSON CC0/BSD-2; warninglists CC0) | Versioned vocabulary/list shape and, where useful, attributed compatible data | MISP core AGPL implementation; unreviewed third-party content embedded in individual packs |
| ASD Constellation (Apache-2.0) | Analyst interactions: filtered centrality, selected subgraph, timeline clusters, linked histogram selection, directed paths | Java/NetBeans implementation or product name/branding |
| NetworkX (BSD-3) | Optional local algorithms: degree, betweenness, PageRank, components | Graph database or hidden canonical state |
| Maltego | Capability manifests and installable adapters later | Transform marketplace/cloud dependency now |
| Sayari/Quantexa concepts | Evidence-backed typology library and graph-pattern findings | Proprietary scoring, opaque entity resolution, copied typologies |
| i2 manuals/concepts | Briefing charts, directed paths, link-analysis workflow, audience exports | Proprietary code/assets/file formats |

`resources/public-lineage.yaml` is updated for every new generalized
public-root file before release audit.

## 6. Execution boundary

- Private implementation lane: implement and dogfood locally; never push,
  never publicly promote without explicit owner approval.
- The private live vault is never a test fixture; only bounded read-only
  acceptance commands run against it until the owner promotes candidates.
- The dashboard plugin is a separate git repository; read the cross-agent
  journal before changing it and commit it separately.
- Synthetic fixtures only in this repository. No private names, URLs, paths,
  note bodies, or datasets in commits.
- New network providers, paid APIs, and live OpenSanctions/OpenCTI/MISP egress
  are owner-gated and out of scope. File import/export is in scope.
- Relationship candidates are staged automatically but never bulk-promoted;
  promotion remains the owner's decision.
