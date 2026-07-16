# Project Manager CRM notes

Constellation writes Obsidian **Project Manager** markdown notes under `Projects/`.
Those notes power Project Manager’s kanban + gantt UI. Do not invent a parallel CRM UI.

## Layout

```text
Projects/
  <Project Title>.md              # pm-project: true
  <Project Title>_tasks/
    <task-slug>.md                # pm-task: true
.constellation/leads/<lead_key>.json   # idempotency map
```

## Conference lead capture

```bash
constellation lead <vault> capture \
  --event "InfoComm Asia" \
  --date 2026-07-21 \
  --project "InfoComm Asia 2026 Leads" \
  --venue "QSNCC" \
  --card Inbox/cards/person.jpg \
  --note "Met near hall 3; wants one-pager" \
  --channel whatsapp \
  --phone-region TH
```

This will:
1. ingest the card (`business-card`)
2. optional note as meeting-notes
3. stage encounter + follow-up drafts (send_allowed=false)
4. create/update a Project Manager task on the event project

Open the project in Obsidian Project Manager to review on kanban/gantt.

## Rules

- No auto-send
- No auto-merge of people/companies
- Card titles stay unconfirmed current roles
- Re-running the same card+event updates the same PM task
