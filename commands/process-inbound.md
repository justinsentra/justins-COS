---
name: process-inbound
description: Qualify new inbound demo signups from Attio and draft personalized outreach in Superhuman (primary email only; follow-ups run on a separate autonomous cron)
---

# /process-inbound — Manual Inbound Lead Drafting

## Description

Pull new demo signups from the Attio "Landing Page Inbound Sales" list, qualify
by country OR company size, filter out spam, and draft personalized outreach in
Superhuman. Does **not** send. Does **not** manage follow-ups (those run on a
separate daily cron: `scripts/follow_ups.py`).

## Arguments

- `(no argument)` — Full run: pull new entries + draft for qualified leads
- `summary` — Show qualification results only, no drafts

## Constants

```yaml
attio_list_id: "8fb5ba5a-0132-438f-aa77-0cadfec0e667"
state_sheet_id: "1GCrRrEM8uT-m40PTkMXhmqjLDYgt_b0qk7NSD0B4VUk"
state_config_tab: "Config" # Cell B2 = last_run timestamp
state_leads_tab: "Leads"
email_1_subject: "Sentra Exploratory Call"
calendly_link: "https://calendly.com/justin-sentra/intro"

qualified_countries:
  - US
  - CA
  - GB
  - AT
  - BE
  - BG
  - HR
  - CY
  - CZ
  - DK
  - EE
  - FI
  - FR
  - DE
  - GR
  - HU
  - IE
  - IT
  - LV
  - LT
  - LU
  - MT
  - NL
  - PL
  - PT
  - RO
  - SK
  - SI
  - ES
  - SE

large_company_sizes:
  - "101-250"
  - "251-1000"
  - "1001-5000"
  - "5001-10,000"
  - "10,000+"

spam_keywords:
  - "i help businesses"
  - "book meetings for"
  - "targeted outreach"
  - "freelance"
  - "your project caught our attention"
  - "schedule a time with me"
  - "hack4brahma"
  - "dealvora"
```

## Instructions

### Step 0: Load state

1. Read `Config!B2` (last_run) and `Leads!A:A` (existing entry_ids for dedup) from the state sheet.
2. If `last_run` is empty, this is the first run.

### Step 1: Fetch new entries from Attio

If `last_run` exists:
- Call `mcp__attio__filter-list-entries` with `listId: 8fb5ba5a-0132-438f-aa77-0cadfec0e667`, `attributeSlug: created_at`, `condition: greater_than`, `value: <last_run>`, `limit: 50`.

If `last_run` is empty:
- Call `mcp__attio__get-list-entries` with `limit: 50` and paginate.

Fallback: if `filter-list-entries` fails, pull full list with `get-list-entries` and filter client-side on `created_at > last_run`.

Deduplicate against `existing_entry_ids` loaded in Step 0.

### Step 2: Enrich each new entry

For each entry:
1. Extract from `entry_values`:
   - `company_size.option.title` (may be empty)
   - `motivations[0].value` (may be empty)
   - `country_6[0].country_code` (may be empty)
   - `hear[0].value` (may be empty)
   - `created_at`
2. Call `mcp__attio__get_record_details` on `parent_record_id` (resource_type: "people") to get first/last name and email.
3. If the person has a `company` relation, call `get_record_details` on that company to get the name.

If email is missing: flag as "MISSING EMAIL", skip drafting, log as skipped.

### Step 3: Qualify

A lead is **qualified** if **either**:
- Path A: `country_code` ∈ `qualified_countries`, OR
- Path B: `company_size` ∈ `large_company_sizes` (regardless of country)

Otherwise: **skipped_country**.

If qualified but `motivations` matches a spam pattern (see `is_spam` logic below), mark as **skipped_spam**.

**Spam check (`is_spam`):**
- If `len(motivation.strip()) < 10` → spam (too short / empty).
- If any keyword in `spam_keywords` appears in `motivation.lower()` → spam.
- If `"calendly.com"` in motivation AND NOT `"calendly.com/justin-sentra"` → spam (they want you to book *their* calendar).

### Step 4: Present results

```
INBOUND — [X] new entries since [last_run or "first run"]

QUALIFIED ([count])
1. Jane Smith (jane@acme.com) — Acme Corp — 31-100 — US
   Motivation: "Looking for better context retention..."
   Heard via: "Twitter"

SKIPPED — country ([count])
- John Doe (IN, size: 1-30) — outside target market

SKIPPED — spam ([count])
- Bob Null — "i help businesses book meetings for..."
```

If `summary` mode: update `last_run` and stop.

Otherwise prompt:
```
Draft [X] emails in Superhuman? (Y / skip)
```

### Step 5: Draft Email 1 via Superhuman MCP

For each qualified lead, build the HTML:

```html
<div dir="ltr">
  <div>Hi [first_name],</div>
  <div><br></div>
  <div>Thanks for reaching out. I'd love to learn more about how you're operating at [company_name] and explore a few potential use cases for Sentra.</div>
  <div><br></div>
  <div>Would you be open to a quick meeting in the coming weeks? If it's easier for you, here's a link to my calendar: <a href="https://calendly.com/justin-sentra/intro">https://calendly.com/justin-sentra/intro</a></div>
  <div><br></div>
  <div>Best,</div>
  <div>Justin</div>
</div>
```

**Do not** append a signature block — Superhuman auto-appends Justin's default signature on send.

Call:
```
mcp__superhuman-mail__create_or_update_draft with:
  type: "new"
  to: ["<lead.email>"]
  subject: "Sentra Exploratory Call"
  body: "<the HTML above with placeholders filled>"
```

Capture the returned `draft_id` and `thread_id`.

**Never send.** Justin reviews each draft in Superhuman and sends manually.

### Step 6: Update state

For each processed lead (qualified or skipped), append one row to `Leads!A:L`:

| Column | Value |
| --- | --- |
| A: entry_id | Attio entry ID |
| B: person_name | Full name |
| C: first_name | First name |
| D: email | Email (blank if missing) |
| E: company_name | Company name (blank if none) |
| F: country | Country code (blank if none) |
| G: disposition | `qualified` / `skipped_country` / `skipped_spam` / `missing_email` |
| H: sequence_step | `1` if drafted, else `0` |
| I: sequence_complete | `false` if drafted, `true` if skipped |
| J: email_1_sent_at | Empty (filled later — see note below) |
| K: email_1_thread_id | Superhuman `thread_id` (empty if skipped) |
| L: processed_at | Current ISO timestamp |

Update `Config!B2` to current ISO timestamp.

**Note on J (`email_1_sent_at`):** We leave this empty. The follow-up script (`scripts/follow_ups.py`) uses the Gmail thread itself (via `email_1_thread_id`) as the source of truth for whether the email was sent — no sheet bookkeeping required.

### Step 7: Report

```
DRAFTS CREATED — [X] in Superhuman. Review and send from your drafts folder.
Next run will only fetch entries added after [now].
Follow-ups will be handled automatically by the daily cron.
```

## Error handling

- **Attio MCP unavailable** → abort: "Attio MCP not connected."
- **Superhuman MCP unavailable** → run in `summary` mode automatically, report: "Superhuman MCP down — qualification only, no drafts."
- **Sheet MCP unavailable** → abort: "Cannot load state — sheet MCP down."
- **Missing email on person** → log as `missing_email`, skip drafting.
- **Rate limit** → pause 60s, retry once; if still failing, abort.

## Guidelines

- Never send. Drafts only.
- HTML emails using Gmail-native `<div>` formatting (per CLAUDE.md).
- No manual signature — Superhuman appends its own.
- Idempotent: running twice produces no duplicate drafts (entry_id dedup).
