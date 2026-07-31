# Graph intelligence source ledger

Frozen 2026-07-30 (Wave 0). Every external source inspected for the
i2-successor graph intelligence program, the license posture verified at
inspection time, and the independent-reimplementation rule.

**Rule:** concepts and public interfaces inform this project; all code is
independently written. No source project's code, data, branding, or assets are
copied unless a row below explicitly says so under a compatible license, and
even then notices are preserved. Any new generalized public-root file inspired
by a source here is recorded in `resources/public-lineage.yaml` before the
release audit.

## Primary sources

| Source | URL | License (as inspected) | What is adapted | What is excluded |
|---|---|---|---|---|
| FollowTheMoney graph semantics/exporters | https://followthemoney.tech/docs/graphs/ | MIT | Interval relationship semantics, mapping-layer concept | Full ontology, edge contraction |
| FollowTheMoney schemas | https://github.com/opensanctions/followthemoney (schema/*.yaml: Ownership, Membership, Interval) | MIT | Schema/property names in mapping profiles | FtM ontology wholesale |
| OpenSanctions ecosystem | https://github.com/opensanctions/opensanctions | Code MIT; repository data/content CC BY-NC 4.0 | Two-stage candidate matching concept | Repository data/content (non-commercial terms conflict with permissive core); no live connector in this program |
| OpenCTI connector development/types | https://docs.opencti.io/latest/development/connectors/ | Apache-2.0 (Community) | Connector capability taxonomy, import/enrich/export/stream concepts | RabbitMQ/Elastic infrastructure |
| OpenCTI deployment connector classes | https://docs.opencti.io/latest/deployment/connectors/ | Apache-2.0 (Community) | Connector classification concepts | Enterprise-edition code/terms |
| OpenCTI relationship vocabulary | https://github.com/OpenCTI-Platform/opencti | Apache-2.0 (Community) | Controlled relationship vocabulary concept | STIX cyber lock-in |
| MISP decaying models | https://github.com/MISP/misp-decaying-models | CC0/BSD-2 | Configurable model files, threshold/simulation concept, sightings vs reinforcement distinction | Wholesale replacement of the current confidence model; MISP core is AGPL-3.0 and must not be copied |
| MISP decay training/model formula | https://www.misp-project.org/misp-training/a.5-decaying-indicators.pdf | MISP project publication | Decay-model education/parameterization concept | Polynomial model transplant without a measured use case |
| MISP galaxy schema | https://github.com/MISP/misp-galaxy/blob/main/schema_clusters.json | CC0/BSD-2 per repository READMEs | Versioned vocabulary shape | Unreviewed third-party content in individual packs |
| MISP warninglist schema | https://github.com/MISP/misp-warninglists/blob/main/schema.json | CC0 per repository README | Simple list contract shape (exact/substring/hostname/regex match types) | MISP core AGPL implementation; operational list content stays in the private vault, not the public repo |
| ASD Constellation source/manuals | https://github.com/constellation-app/constellation | Apache-2.0 | Analyst interaction logic: filtered centrality, selected subgraph, timeline clusters, linked histogram selection, directed shortest paths | Java/NetBeans implementation; product name/branding (name collision requires prelaunch clearance) |
| NetworkX centrality docs | https://networkx.org/documentation/stable/reference/algorithms/centrality.html | BSD-3-Clause | Degree, closeness, betweenness, PageRank, components as optional local algorithms | Graph database or hidden canonical state |
| NetworkX betweenness docs | https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.centrality.betweenness_centrality.html | BSD-3-Clause | Exact/approximate betweenness parameterization (fixed seed, k from node count) | — |
| NetworkX PageRank docs | https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.link_analysis.pagerank_alg.pagerank.html | BSD-3-Clause | PageRank damping defaults and convergence handling | — |
| NetworkX license | https://github.com/networkx/networkx/blob/main/LICENSE.txt | BSD-3-Clause | Optional dependency eligibility | — |
| STIX 2.1 relationship temporal semantics | https://docs.oasis-open.org/cti/stix/v2.1/os/stix-v2.1-os.html | OASIS specification | Temporal relationship semantics reference | STIX cyber lock-in |

## Licensing posture summary

- NetworkX may ship as an optional dependency under BSD-3-Clause (notices preserved).
- FtM mappings may name schemas/properties under MIT compatibility; adapters
  are independently written.
- OpenSanctions **code** is MIT; its repository **data/content** is separately
  CC BY-NC 4.0 and stays out of the permissive public core. Dataset terms are
  recorded in every import receipt.
- ASD/OpenCTI Community behavior can inform independent Apache-compatible
  implementation; OpenCTI Enterprise files require separate review and are
  excluded.
- MISP decay-model and Galaxy JSON are CC0/BSD-2 and warninglists are CC0 per
  their repositories; MISP core is AGPL-3.0 and must not be copied into the
  permissive clean-room core.
- Default confidence-decay behavior remains Constellation's own.

## Paid-commercial-deployment gate (forward-looking)

The planned implementation is compatible with paid deployments when these
controls hold:

| Component or inspiration | Commercial posture | Required control |
|---|---|---|
| Constellation public core | Apache-2.0; commercial use allowed | Ship the Apache license; retain notices; mark modified files; no trademark implication |
| NetworkX | BSD-3-Clause; commercial use allowed | Preserve copyright/license/disclaimer in distributed notices |
| React Flow / `@xyflow/react` | MIT; commercial use allowed | Preserve MIT notice; never copy separately licensed Pro examples/templates |
| FollowTheMoney and yente code/schema | MIT; commercial use allowed | Preserve MIT notices for copied/substantial code; prefer independent adapters |
| OpenCTI Community concepts/code | Apache-2.0; commercial use allowed | Community files only; preserve required notices if code is incorporated |
| OpenCTI Enterprise | Proprietary/EE terms | No copying or dependency without a separate license |
| ASD Constellation | Apache-2.0; commercial use allowed | Borrow behavior concepts or comply with Apache notices for incorporated code; never reuse branding/assets |
