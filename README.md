# Constellation

Constellation is a private intelligence system for people and teams whose work depends on relationships, evidence, decisions, and timing. It turns the material behind that work into a connected record, then helps you understand it, act on it, and notice when it changes.

This public repository is a product overview, not a package installer. Constellation deployments are designed and operated privately around an organization's sources, workflows, governance, and model choices.

## The job

Most important work is scattered across cards, meeting notes, decks, email, research, calendars, chat, and somebody's memory. Files can be stored, but the context does not compound. A normal CRM remembers fields. A normal AI assistant remembers a conversation. Neither reliably answers:

- Who is this person in the context of our relationship?
- What do we know about this company, and where did that information come from?
- What did we decide, why, and what has changed since?
- Which follow-up, risk, relationship, or opportunity needs attention now?

Constellation is built to answer those questions from the underlying evidence rather than from an old chat summary.

## What it does

| Job | What Constellation does | Useful result |
|---|---|---|
| Captures evidence | Preserves files, pages, cards, transcripts, emails, decks, recordings, and research with hashes and stable anchors. | The original material remains available and readable. |
| Builds a working memory | Connects people, companies, sources, claims, interactions, decisions, inquiries, opportunities, observations, and events. | A person or company has a history instead of a loose pile of notes. |
| Separates evidence from judgment | Keeps source statements, corroborated facts, analyst inference, and strategic judgment distinct. | You can see what is known, inferred, disputed, stale, or still unknown. |
| Runs bounded research | Searches approved sources, extracts relevant pages, keeps receipts, and stages findings for review. | Research becomes reusable evidence instead of a one-off answer. |
| Prepares action | Produces meeting briefs, decision trails, opportunity views, follow-up tasks, and strategic analyses with citations. | You start the next conversation with context and a clear next move. |
| Watches for change | Tracks people, companies, markets, filings, open questions, and commitments over time. | A concise alert explains what changed, why it may matter, and links to the evidence. |

## How it works

```text
capture a source or ask a question
  -> preserve the original material
  -> extract text, structure, metadata, and source anchors
  -> link it to people, companies, claims, meetings, decisions, and opportunities
  -> stage any new interpretation for review
  -> promote trusted records into the private vault
  -> search, reason, monitor, and brief from the connected evidence
```

The data layer stays simple:

- Markdown records and preserved source files are canonical;
- search indexes, caches, and dashboards can be rebuilt;
- every meaningful statement can point back to a source, page, slide, line, cell, timestamp, or OCR region;
- changes are versioned and conflict-checked;
- models can propose work, but they do not silently rewrite the record of truth.

This lets Constellation keep working when the model, interface, or deployment changes. The knowledge is not trapped in a vendor database or a chat thread.

## Constellation and LLM wikis

| LLM-wiki approach | How Constellation differs |
|---|---|
| [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) compiles raw sources into a persistent, linked Markdown wiki instead of rebuilding an answer from RAG chunks each time. | Constellation shares the compounding-record idea, but makes evidence, entities, claims, relationships, decisions, sensitivity, and review first-class. It is not only a generated prose wiki. |
| [nvk/llm-wiki](https://github.com/nvk/llm-wiki) packages multi-agent research, source ingestion, wiki compilation, query depths, and portable agent plugins. | Constellation uses bounded inquiries and policy-controlled research to keep organization-specific intelligence useful without letting every agent run an open-ended research loop. The durable output is a reviewed operating record, not just a research wiki. |
| [LLM Wiki](https://github.com/nashsu/llm_wiki) is a desktop application that turns documents into an interlinked wiki maintained by an LLM. | Constellation is interface-neutral: Obsidian is a useful default, but a deployment can use another editor, a web workspace, a terminal, or an agent surface against the same canonical records. It also carries the source anchors and review trail behind the wiki. |
| [karpathy-llm-wiki](https://github.com/Astro-Han/karpathy-llm-wiki) is an Agent Skills workflow for raw-source ingestion, cited wiki pages, and wiki-health linting. | Constellation adopts the discipline of source-first compilation and health checks, then extends it to entity resolution, claims-versus-inference, relationship history, decision trails, egress controls, and reviewable actions. |

The distinction is simple: an LLM wiki compiles what it knows into pages. Constellation compiles what an organization knows into an inspectable record of evidence, judgment, relationships, and action.

## A conference contact, step by step

Assume you meet Arun at a conference. You receive a business card, write three lines about the conversation, and later receive a slide deck.

1. **Capture.** The card image, encounter note, and deck are copied into the vault. Each becomes a preserved source with an immutable hash, media type, capture time, and original path. Nothing has become a contact record yet.
2. **Extract locally.** OCR reads visible card text and records regions and confidence. The deck extractor records slide text, notes, tables, and anchors. The encounter note stays as authored. Low-confidence OCR stays visible instead of becoming a fact.
3. **Stage proposals.** Constellation creates review candidates: a possible person, a possible company, an interaction, a source-backed follow-up, and perhaps an opportunity. It does not invent Arun's current role, merge him with a similar name, or send a message.
4. **Check the existing record.** Identity matching compares normalized names and any explicit email or phone evidence. A match is a suggestion for review, never an automatic merge.
5. **Create a bounded inquiry.** If the question is "Who is Arun's firm and is there a relevant partnership path?", the inquiry states its scope, sensitivity, source preference, budget, and stop condition before web research begins.
6. **Run approved discovery lanes.** For an `internal` or `public` inquiry, and only when those adapters are configured and authorized:
   - SearXNG runs broad keyword searches for the person, company, event, and stated topic.
   - EXA runs a separate semantic search to find conceptually related material that exact keywords may miss.
   - Brave runs an independent search and can apply a freshness window when recent news matters.
   The system deduplicates results, records which lane produced them, and rejects obvious identity or relevance noise. Confidential and restricted material does not go to these external search services.
7. **Fetch selected sources, not the whole web.** Only relevant URLs enter the extraction ladder. Each attempt is egress-gated, URL-checked, bounded by time and size limits, and recorded. The retrieved page is preserved before any claim is staged. A failed or irrelevant page produces a failed or partial receipt, not a made-up conclusion.
8. **Compile an evidence packet.** The system links the card, encounter, deck, selected official pages, filings, and other accepted sources. A model or analyst may prepare a company summary, relationship map, risks, and recommended next move, but the packet marks what is sourced, inferred, uncertain, or contradictory.
9. **Review and promote.** A human approves exact candidates. The promoted person, company, interaction, claim, inquiry result, and opportunity remain linked to their sources. The review log shows what changed.
10. **Use the record.** Before a follow-up, Constellation can build a brief with the last interaction, shared context, promises, open questions, recent changes, and citations. It can later watch approved public sources and create a new, reviewable observation when something material changes.

This is the intended full workflow. The public core should label each configured adapter, model call, monitor, and interface honestly rather than imply it arrives pre-wired.

## Uploading a pitch deck, consulting deck, or startup deck

A deck is not just a PDF to summarize. It is usually a compact map of entities, claims, numbers, relationships, positioning, and unanswered questions.

1. **Preserve and extract locally.** Constellation keeps the original PDF or PPTX, reads native slide text and notes where available, runs OCR on image-only or low-text slides, and retains slide, table, chart, and OCR-region anchors. No external research is required for this first pass.
2. **Parse the working facts.** It identifies reviewable candidates such as companies, people, products, customers, partners, investors, competitors, markets, dates, metrics, pricing, funding claims, technical claims, and stated risks. Each candidate stays tied to the slide or region that produced it.
3. **Resolve entities cautiously.** A company logo, abbreviated name, or familiar executive is a lead, not a fact. Constellation checks the existing record and stages possible matches for review instead of silently merging similar names.
4. **Build a small research plan.** The user can choose the scope and depth: no external research, a low default pass, a standard pass, or a deeper investigation. The plan states which entities and claims matter, which sources are allowed, and when to stop.
5. **Use discovery lanes selectively.** At the default low setting, Constellation makes only a small number of targeted discovery calls and fetches only the most relevant results. SearXNG is useful for broad factual discovery, EXA for semantically related material, and Brave for an independent or time-sensitive check. It does not spray every extracted name across every provider.
6. **Stop before the rabbit hole.** Query, fetch, token, time, and depth ceilings are policy-configurable. The workflow stops when the evidence is sufficient for the stated question, when the budget is reached, or when new searches are only repeating the same weak signal. A deeper OSINT or research pass must be requested deliberately.
7. **Return an evidence packet.** The output links the original deck to extracted facts, approved entities, selected external sources, open questions, contradictions, and proposed next actions. A human can accept, reject, or defer each meaning-bearing update.

This makes the default useful without turning every uploaded slide deck into an expensive research project. More depth remains available when the decision justifies it.

## The full toolchain

### Capture and intake

Constellation accepts the material people actually work with:

| Input                                           | What happens                                                                                               |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Documents, PDFs, decks, spreadsheets, and books | Text, tables, notes, sections, pages, slides, and cells are extracted with stable anchors.                 |
| Scanned documents, images, and business cards   | Local OCR extracts reviewable fields while retaining the original image and region references.             |
| Meetings and voice notes                        | Local transcription and transcript capture create evidence bundles without inventing speaker identity.     |
| Email and attachments                           | Messages, attachments, headers, and query receipts become source-backed relationship and decision context. |
| Web pages and public research                   | Approved sources are fetched through a controlled extraction ladder and preserved before claims are made.  |
| Existing Obsidian or file archives              | Material can be brought in gradually, without forcing a migration into a proprietary database.             |

### Research and external intelligence

Research starts with an Inquiry: a question, scope, budget, sensitivity, source preference, and stop condition.

Constellation then uses the right discovery and extraction tools for the question:

| Tool | Role |
|---|---|
| SearXNG | Broad keyword discovery. |
| EXA | Semantic discovery when exact keywords are not enough. |
| Brave Search | Independent and time-sensitive discovery. |
| Firecrawl, Crawl4AI, Scrapling, raw HTTP, and browser capture | An extraction ladder for pages that need progressively stronger handling. |
| EDGAR and official registries | Filings, ownership, and formal company evidence. |
| RSS, blogs, news, market, and specialist research connectors | Monitored public signals for entities and questions that matter. |
| Hermes research, OSINT, document, email, and browser skills | Operator workflows that feed the same evidence and review path. |

Every external call passes an egress policy first. The policy decides what can leave the machine, which provider may receive it, for what purpose, and at what sensitivity. The system records both approvals and denials. A failed or irrelevant run remains visible as a failed or partial run; it does not become an invented answer.

### Retrieval and analysis

Constellation uses several ways to retrieve information without treating any index as the source of truth:

| Capability | What it does |
|---|---|
| Full-text search | Finds exact words and phrases across validated records and extracted evidence. |
| Semantic search | Finds conceptually related material when the wording differs. |
| Hybrid retrieval | Fuses lexical and semantic results while retaining citations, anchors, sensitivity boundaries, and an explanation of ranking. |
| Relationship graph | Traverses sourced links among people, companies, claims, interactions, decisions, and opportunities. |
| Timelines and decision trails | Shows what happened, what was believed at the time, and what changed later. |
| Strategic framework library | Applies methods such as Porter, SWOT, jobs-to-be-done, systems thinking, and custom lenses to cited evidence. |
| Book intelligence | Turns useful books and long-form material into reusable frameworks and searchable source-backed concepts. |

## Models and languages

### Which LLMs can be used?

Constellation is provider-neutral. The core does not bundle an LLM, a provider credential, or a generic agent. A deployment can connect a local runtime or an approved cloud provider through its own adapter and egress policy.

That does not mean every model performs equally well. The model matters most when it must follow an evidence packet, return strict structured output, use tools carefully, compare conflicting material, reason across long context, or work in more than one language. Smaller or local models can be a good fit for bounded extraction, classification, and privacy-sensitive work. More capable models are usually worth using for complex research synthesis, relationship analysis, decision support, and multilingual work where a bad inference costs time or trust.

Choose models against representative sources and tasks, not a benchmark or vendor claim. For each model, test:

- structured output and schema compliance;
- source citation and anchor discipline;
- correct handling of uncertainty and contradictions;
- tool-use reliability and refusal on denied egress;
- the languages, scripts, and local terminology that the deployment actually uses.

An allowed model call receives only the material authorized for that provider, model, purpose, and sensitivity. It returns a candidate with sources and assumptions. Review decides what becomes durable knowledge.

### Languages and scripts

Canonical notes, source text, hashes, and frontmatter use UTF-8. Constellation preserves names and content in Thai, Chinese, Japanese, Korean, Arabic, Latin-script languages, and mixed-language records rather than forcing English transliteration. Identity normalization uses Unicode-aware NFKC normalization and case folding, but that is not a promise of correct cross-language identity resolution.

Storage is the easy part. Retrieval, OCR, segmentation, entity resolution, and model reasoning need language-specific validation. A Thai or APAC deployment needs a retrieval evaluation set, appropriate OCR and embedding/model choices, and a tokenizer or query strategy that is tested on the languages it serves. Do not market generic UTF-8 storage as language-aware retrieval without that evaluation.

## Operating intelligence

Constellation is designed to run the work around a relationship or decision, not only store the record of it.

| Capability               | What it does                                                                                                |
| ------------------------ | ----------------------------------------------------------------------------------------------------------- |
| Meeting preparation      | Builds a concise brief from relationship history, current context, open questions, decisions, and evidence. |
| Decisions                | Preserves the choice, rationale, assumptions, alternatives, owners, review date, and later contradictions.  |
| Opportunities and CRM    | Connects account, partner, or deal activity to evidence, pipeline stage, next action, and a Kanban view.    |
| Relationship health      | Finds stale relationships, overdue promises, missing follow-ups, and changing contact context.              |
| Watchlists and snapshots | Compares an entity or topic over time and records material changes.                                         |
| Observations and events  | Turns monitored changes into dated, sourced intelligence.                                                   |
| Patterns                 | Finds clusters, repeated themes, shared relationships, and recurring risk or opportunity signals.           |
| Briefs and alerts        | Delivers a cited memo, task, dashboard item, or concise Telegram nudge when action is warranted.            |

## What the core ships, and what a deployment adds

The open-source core is mostly Python plus human-readable Markdown and preserved files. It ships the record model, validation, source-preservation and intake paths, rebuildable indexes, review workflow, CLI, and bounded Hermes plugin tools. Those records remain readable without any Constellation application.

Obsidian is the default human workspace because Markdown, wikilinks, Dataview, Tasks, Kanban, and file history make the record easy to inspect. It is a default, not a requirement. A team can use another editor, a custom web interface, a terminal workflow, a different agent framework, or an internal application as long as it respects the canonical records and review contract.

The core does **not** pre-package a hosted application UI, a chat agent, a model provider, credentials, or autonomous background workers. Those are deployment decisions. A complete private deployment can add:

- Obsidian for direct reading, editing, Kanban, task, dashboard, and timeline views;
- a local web workspace for review queues, source viewers, entity profiles, relationship graphs, watchlists, and decision or opportunity views;
- Hermes in Telegram, TUI, WebUI, and the Obsidian console for natural-language work and approvals;
- a CLI, API, and MCP-compatible tool surface for power users and custom systems;
- local or approved cloud models, selected adapters, scheduled monitoring, and notifications;
- Git and plain files for portability, backup, audit, and recovery.

Each interface should read the same records. Changing the interface should not require migrating the knowledge.

## Trust rules that stay in the product

Constellation is useful only if people can tell the difference between source material, a proposal, and a trusted conclusion.

- Sources are preserved before interpretation.
- Claims retain their evidence, confidence, and time context.
- Contradictory or superseded information is kept visible rather than erased.
- A proposed merge, claim, decision, identity match, or follow-up remains reviewable.
- Outgoing research and model calls are policy-gated and logged.
- Search can report stale, incomplete, or degraded results instead of pretending it found everything.
- Canonical data stays in human-readable files that you can inspect without the application.

## Who it is for

### Individuals

Founders, advisors, investors, researchers, executives, and other people whose work depends on a long memory of conversations, documents, decisions, and commitments.

### Teams and SMEs

Business development, consulting, investment, strategy, professional services, research, and operational teams that need shared context without putting sensitive work into an opaque SaaS knowledge base.

### Enterprises

Organizations that need a controlled intelligence layer across internal documents, approved external research, relationship history, decisions, and monitored change. Constellation can run inside their own data, model, identity, and governance boundaries.

## What it is not

| It is not                 | Because                                                                                                                       |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| A generic chatbot         | Chat is an interface. The record, evidence, and decision history live outside the conversation.                               |
| A note-taking replacement | Obsidian and other editors remain useful interfaces. Constellation provides the evidence and intelligence layer beneath them. |
| A conventional CRM        | CRM fields alone do not explain why a relationship matters or what supports the current view.                                 |
| A vector database         | Embeddings help retrieval, but they are not the system of record.                                                             |
| A black-box agent         | The system keeps sources, receipts, candidates, policy decisions, and review history visible.                                 |
| An outreach robot         | It can prepare follow-ups and alerts, but it does not silently contact people or change canonical facts.                      |
|                           |                                                                                                                               |

## The short explanation

> Constellation is a private intelligence system that turns the files, meetings, research, relationships, and decisions behind your work into connected, evidence-backed memory. It helps you prepare, decide, follow through, and notice change without giving up control of your data or losing track of why you believe something.
