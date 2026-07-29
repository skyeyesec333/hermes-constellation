# Constellation

## Private intelligence for work that depends on evidence

Constellation turns the files, meetings, research, relationships, and decisions behind your work into a connected, inspectable record.

It is for people and teams who need to answer questions such as:

- What do we know about this person or company, and where did it come from?
- What did we decide, why, and what has changed since?
- What did we promise after the last meeting?
- Which relationship, risk, opportunity, or research question needs attention now?

Constellation is not a generic chatbot. It is the evidence and operating record a private AI system can work from.

Built by [Skylance](https://skylance.org/) and [Zynolabs](https://www.zynolabs.com/).

## The difference

Most AI stacks help an agent answer questions from data. Constellation gives the organization a durable record of what it knows, why it believes it, what it decided, and what changed.

That record keeps several things separate:

- original sources and mechanical extraction;
- source claims and corroborated facts;
- analyst or model inference;
- decisions, commitments, and follow-up work.

A useful answer links back to evidence. A model can suggest a contact, claim, relationship, or next action, but it does not silently rewrite trusted knowledge.

## What using it looks like

```text
capture a source or ask a bounded question
  -> preserve the original material
  -> extract text, structure, metadata, and anchors
  -> connect it to people, companies, claims, meetings, and decisions
  -> stage new interpretations for review
  -> promote approved records
  -> search, brief, and monitor from connected evidence
```

### Example: a conference contact

You meet someone at a conference, receive a business card, write a short encounter note, and later get a deck.

1. Constellation preserves the card, note, and deck with hashes and source anchors.
2. Local extraction stages reviewable details rather than creating a contact or guessing a role.
3. It proposes a person, company, interaction, follow-up, or opportunity for review.
4. A deployment may run a bounded inquiry into the firm using approved public sources.
5. Each useful source is preserved before a claim or summary is proposed.
6. You approve exact records and can later generate a cited briefing before the next conversation.

The same loop works for a customer account, supplier, investment thesis, competitor, board decision, research program, or personal network.

## What makes it useful

| Capability | Why it matters |
|---|---|
| Preserved evidence | Original files, pages, slides, cells, and OCR regions remain available. |
| Reviewable knowledge | Facts, inferences, conflicts, and decisions do not collapse into one opaque summary. |
| Relationship history | People, companies, meetings, commitments, and opportunities accumulate context over time. |
| Portable records | Markdown and preserved files are canonical; indexes and caches can be rebuilt. |
| Policy-bound model use | A provider/model/purpose/sensitivity decision is explicit before source material can leave the machine. |
| Budget-governed research | Bounded inquiries run under explicit call/token/cost/context profiles (`off`/`low`/`standard`/`deep`) resolved before any network work; synthesis holds a reserve source acquisition cannot consume. |
| Layered OSINT depths | From bounded inquiry, to single-source capture, to multi-source deep dives on existing records, to judgment classifications — each tier feeds the next through the same review gate. |
| Temporal monitoring | Watchlists produce preserved snapshots, deterministic diffs, and material-change candidates; receipts make every run auditable. |
| Inspectable structure | Typed graph projections, timelines, and cited briefings are derived from validated records — never the other way around. |
| Actionable output | The record supports meeting briefs, decision trails, research packets, follow-up tasks, and watchlists. |

## Where it is going

Constellation is an evidence-first answer to the "LLM wiki" pattern: instead of an agent silently rewriting prose pages, knowledge compounds through cited records and review gates. The next stage deepens that difference into a full knowledge lifecycle: explicit supersession links between claims, confidence that decays and strengthens with evidence, contradiction detection with resolution proposals, and self-healing consistency checks — all journaled, all reversible. See [Roadmap](ROADMAP.md) stage 7.

## The core and the deployment around it

The open-source core is mostly Python plus human-readable Markdown and preserved source files. It provides the record model, validation, source-preservation and intake paths, rebuildable indexes, review workflow, CLI, and bounded Hermes plugin tools.

Obsidian is the default human workspace because it reads Markdown directly and works well with tools such as Dataview, Tasks, Kanban, and Git. It is not required. The same records can be used from another editor, a terminal, a custom web interface, or another agent framework.

The core does **not** pre-package:

- a hosted application UI;
- a chat agent or autonomous worker;
- an LLM provider or credentials;
- automatic web research, outreach, messaging, or background monitoring.

Those are deployment choices. A complete private deployment may add approved models, connectors, research adapters, monitoring, notifications, and interfaces while keeping the same canonical record and review contract.

## Models and languages

Constellation is provider-neutral. A deployment can connect local models, approved cloud models, or both through its own adapter and egress policy.

Models are not interchangeable in practice. Evaluate candidates against the work that matters: structured output, citation discipline, uncertainty handling, long-context reasoning, tool use, and the languages used by the team. Smaller local models can fit bounded or sensitive work; stronger models are often needed for difficult cross-source analysis, visual documents, and high-stakes synthesis.

Canonical records use UTF-8, so they can preserve Thai, Chinese, Japanese, Korean, Arabic, Latin-script, and mixed-language material without forced transliteration. Storage support is not a blanket multilingual-quality claim: OCR, tokenization, retrieval, entity resolution, and model reasoning need evaluation on the scripts and terminology a deployment actually serves.

## Current public status

Constellation is alpha software and an active open-source foundation.

The public core currently includes:

- a standalone `constellation` CLI;
- local Markdown and plain-text intake with source preservation;
- strict records and rebuildable SQLite full-text search;
- review-only candidate workflows;
- Hermes Agent plugin tools and command surfaces;
- optional local extraction for PDF, Office documents, images, and audio;
- synthetic examples and clean-room release checks.

It does not silently call an LLM, browse the web, send messages, or start background automation. Read [Integrations and tool status](docs/integrations.md) for the exact boundary between bundled, optional, external, and planned components.

## Try the core locally

The core runs without a cloud key.

```bash
git clone https://github.com/skyeyesec333/hermes-constellation.git
cd hermes-constellation
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/constellation init ./demo-vault
cp examples/synthetic-demo-vault/Inbox/Files/demo-brief.txt ./demo-vault/demo-brief.txt
.venv/bin/constellation ingest ./demo-vault ./demo-vault/demo-brief.txt
.venv/bin/constellation review ./demo-vault list
```

Review an exact candidate before promotion, then validate and search the vault:

```bash
.venv/bin/constellation review ./demo-vault promote \
  --candidate <candidate-id> \
  --confirm
.venv/bin/constellation validate ./demo-vault
.venv/bin/constellation search ./demo-vault "Northstar Field Labs"
```

See [Installation](docs/installation.md) for wheel and Hermes-plugin setup.

## Who it is for

- founders, executives, advisors, and operators with a long memory of relationships and commitments;
- consulting, research, investment, professional-services, and business-development teams;
- organizations that need private, source-grounded intelligence inside their own data, model, and governance boundaries;
- integrators building a specific interface or workflow around an evidence-first core.

It is the wrong starting point for a generic hosted chatbot, an opaque knowledge-base SaaS, or an agent that is allowed to alter records and contact people without review.

## Commercial deployments

The repository is a reference implementation and proof of engineering. The commercial work is private deployment: source and system integration, data migration, workflow design, model and egress governance, tailored interfaces, and ongoing operation around a customer's actual material.

## Learn more

- [Architecture](docs/architecture.md)
- [Installation](docs/installation.md)
- [Integrations and tool status](docs/integrations.md)
- [Model-provider portability](docs/model-provider-portability.md)
- [Egress policy](docs/egress-policy.md)
- [Security](SECURITY.md)
- [Privacy](PRIVACY.md)
- [Roadmap](ROADMAP.md)
- [Contributing](CONTRIBUTING.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
