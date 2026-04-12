---
name: process-inbound
description: Qualify inbound demo signups from Attio and draft outreach sequences in Superhuman
---

# /process-inbound — Inbound Lead Processing

## Description

Pull new demo signups from the Attio "Landing Page Inbound Sales" list, qualify by
country (US/CA/UK only), draft personalized outreach in Superhuman, and manage a
3-email follow-up sequence. Sequence ends when the recipient replies or books via Calendly.

## Arguments

- `(no argument)` — Full run: process new entries + check follow-ups for existing leads
- `summary` — Show new entries and qualification results, no drafts
- `follow-up` — Only check follow-up eligibility for existing leads, skip new entries
- `reset` — Clear the processed entries state file and start fresh

## Constants

```yaml
attio_list_id: "8fb5ba5a-0132-438f-aa77-0cadfec0e667"
state_sheet_id: "1GCrRrEM8uT-m40PTkMXhmqjLDYgt_b0qk7NSD0B4VUk"
state_config_tab: "Config" # Cell B2 = last_run timestamp
state_leads_tab: "Leads" # Tracks all processed entries
qualified_countries: ["US", "CA", "GB"]
email_1_subject: "Sentra Exploratory Call"
follow_up_delay_days: 3
calendly_link: "https://calendly.com/justin-sentra/intro"
trigger_id: "trig_01TUNMgiJPQEQyMMCfAkaMRs" # Runs hourly on the cloud
```

## Instructions

### Step 0: Load State and Verify

1. Read state from Google Sheets (spreadsheet ID: `1GCrRrEM8uT-m40PTkMXhmqjLDYgt_b0qk7NSD0B4VUk`):
   - Read `Config` tab, cell B2 for `last_run` timestamp
   - Read `Leads` tab for all previously processed entries
2. Extract the `last_run` timestamp. If B2 is empty, this is the first run.
3. Verify Attio MCP is accessible (call `get-lists` or similar lightweight check).
4. Verify Superhuman MCP is accessible (only needed if not in `summary` mode).
5. If `reset` argument: clear all rows in Leads tab (keep headers), clear Config B2, and report "State cleared." Then continue as a full run.

### Step 1: Fetch New Entries (Efficient)

**Do NOT fetch the entire list.** Use timestamp-based filtering to only get new entries.

If `last_run` exists:

- Call `filter-list-entries` with:
  - `listId`: `8fb5ba5a-0132-438f-aa77-0cadfec0e667`
  - `attributeSlug`: `created_at`
  - `condition`: `greater_than`
  - `value`: the `last_run` timestamp
  - `limit`: 50

If `last_run` is null (first run):

- Call `get-list-entries` with `limit: 50` and paginate if needed to get all entries.

**Fallback:** If the filter-list-entries call fails on `created_at`, fall back to `get-list-entries` with full fetch, then filter client-side by comparing entry `created_at` against `last_run`.

If `follow-up` argument: skip this step entirely, go to Step 7.

If zero new entries: report "No new inbound signups since [last_run]." Then proceed to Step 7 (follow-up checks).

### Step 2: Enrich Each New Entry

For each new entry:

1. Extract list entry field values:
   - `company_size` → option title (e.g., "1-30", "31-100")
   - `motivations` → text value
   - `country_6` → `country_code` (e.g., "US", "CA", "GB", "IN")
   - `hear` → text value (how they heard about Sentra)
   - `created_at` → timestamp

2. Get the parent person record:
   - Call `get_record_details` with `resource_type: "people"` and `record_id`: the entry's `parent_record_id`
   - Extract: first name, last name, email address(es), associated company name

3. Compile a clean record:
   ```
   {
     entry_id, person_name, first_name, email, company_name,
     company_size, country_code, motivations, hear, created_at
   }
   ```

**If email is missing:** Flag the entry as "MISSING EMAIL" and skip it for drafting.

### Step 3: Country Filter

Apply qualification:

- `country_code` in `["US", "CA", "GB"]` → **Qualified**
- All others → **Skipped** (log with reason: "outside target market — [country]")

### Step 4: Present Results

Display all leads grouped by disposition:

```
INBOUND PROCESSING — [X] new entries since [last_run or "first run"]

QUALIFIED ([count])
1. [Name] ([email]) — [Company] — [Size] — [Country]
   Motivation: "[motivations text]"
   Heard via: "[hear text]"

2. ...

SKIPPED ([count])
3. [Name] — [Country] — outside target market

SUMMARY: [X] qualified, [Y] skipped
```

If `summary` mode: stop here. Update `last_run` in state file and exit.

Otherwise, ask:

```
Draft + send [X] emails? (Y / draft-only / adjust / skip)
```

- **Y** — Draft and send Email 1 for all qualified leads
- **draft-only** — Create drafts only, don't send (review in Superhuman)
- **adjust** — Let Justin modify the list (remove leads, etc.)
- **skip** — Log as processed but don't email

### Step 5: Draft and Send Email 1

For each approved lead:

1. **Build the email body in HTML** (Gmail-native div formatting per CLAUDE.md):

```html
<div dir="ltr">
  <div>Hi [first_name],</div>
  <div><br /></div>
  <div>
    Thanks for reaching out. I'd love to learn more about how you're operating
    at [company_name] and explore a few potential use cases for Sentra.
  </div>
  <div><br /></div>
  <div>
    Would you be open to a quick meeting in the coming weeks? If it's easier for
    you, here's a link to my calendar:
    <a href="https://calendly.com/justin-sentra/intro"
      >https://calendly.com/justin-sentra/intro</a
    >
  </div>
  <div><br /></div>
  <div>Best,</div>
  <div>Justin</div>
</div>

**NOTE:** Do NOT append a full signature block. Superhuman automatically appends
Justin's default signature to all outgoing emails.
```

Replace `[first_name]` and `[company_name]` with actual values.

2. **Create the draft** via Superhuman MCP:
   - `create_or_update_draft` with:
     - `type`: "new"
     - `to`: ["[email]"]
     - `subject`: "Sentra Exploratory Call"
     - `body`: the HTML above

3. **If user chose "Y" (send):** immediately call `send_email` with the returned `draft_id` and `thread_id`.

4. **Store in state:** Save `draft_id`, `thread_id`, and `email_1_sent_at` for each lead.

5. **Report each email:**
   ```
   ✓ Email 1 [sent/drafted] → Jane Smith (jane@acme.com) — "Sentra Exploratory Call"
   ```

**IMPORTANT:** Always show the draft content and get approval before sending. Never send without explicit "Y" or "Send" from Justin.

### Step 6: Update State (Google Sheets)

Write state to Google Sheets (ID: `1GCrRrEM8uT-m40PTkMXhmqjLDYgt_b0qk7NSD0B4VUk`):

1. **Leads tab** — append one row per processed entry:

| Column               | Value                            |
| -------------------- | -------------------------------- |
| A: entry_id          | Attio entry ID                   |
| B: person_name       | Full name                        |
| C: first_name        | First name                       |
| D: email             | Email address                    |
| E: company_name      | Company name                     |
| F: country           | Country code                     |
| G: disposition       | "qualified" or "skipped"         |
| H: sequence_step     | 1 (or 0 if skipped)              |
| I: sequence_complete | false (or true if skipped)       |
| J: email_1_sent_at   | Timestamp or empty if draft-only |
| K: email_1_thread_id | Thread ID from Superhuman        |
| L: processed_at      | Current timestamp                |

2. **Config tab** — update cell B2 with current ISO timestamp.

**Deduplication:** Before appending, check if entry_id already exists in column A of Leads tab.

Report:

```
STATE SAVED — [X] leads processed, [Y] drafts/emails created.
Next run will only fetch entries added after [last_run].
Spreadsheet: https://docs.google.com/spreadsheets/d/1GCrRrEM8uT-m40PTkMXhmqjLDYgt_b0qk7NSD0B4VUk
```

### Step 7: Follow-Up Sequence Checks

**Run on every invocation** except `summary` mode.

For each lead in state where:

- `disposition` = "qualified"
- `sequence_complete` = false
- `email_1_sent_at` is not null (email was actually sent, not just drafted)
- Current time is 3+ days after the most recent sequence email sent

Do:

1. **Check for reply:**
   - Call Superhuman `get_thread` with the lead's `email_1_thread_id`
   - If the thread has messages beyond the original (i.e., recipient replied) → mark `sequence_complete: true`

2. **Check for Calendly booking:**
   - Call Google Calendar `list_events` for the next 30 days
   - Search event attendees for the lead's email address
   - If found → mark `sequence_complete: true`

3. **If neither reply nor booking, and 3+ days since last email:**
   - If `sequence_step` = 1 and Email 2 template exists:
     - Draft Email 2 as a reply in the same thread:
       - `create_or_update_draft` with `type: "reply"`, `thread_id`: email_1_thread_id
       - Use Email 2 template (see below)
     - Present for approval before sending
     - Update `sequence_step: 2`, `email_2_sent_at`
   - If `sequence_step` = 2 and Email 3 template exists:
     - Same pattern, Email 3 template
     - Update `sequence_step: 3`, `email_3_sent_at`
   - If `sequence_step` = 3:
     - Sequence exhausted. Mark `sequence_complete: true`.
     - Report: "[Name] — 3 emails sent, no response. Sequence complete."

4. **Report follow-up status:**

   ```
   FOLLOW-UP CHECK — [X] leads in active sequences

   REPLIED / BOOKED ([count])
   - Jane Smith — replied on 04/07
   - Bob Jones — booked meeting for 04/10

   FOLLOW-UP DUE ([count])
   - Sarah Lee — Email 1 sent 04/02, no reply, no booking → Draft Email 2?

   WAITING ([count])
   - Tom Park — Email 1 sent 04/04, 1 day remaining before follow-up

   NO ACTION NEEDED ([count])
   - Mike Chen — Sequence complete (3 emails, no response)
   ```

### Email 2 Template

**Subject:** Same thread as Email 1 (reply in thread)
**Type:** `reply` using `thread_id` from Email 1

```html
<div dir="ltr">
  <div>Hi [first_name],</div>
  <div><br /></div>
  <div>
    Just checking in to see if you might have some time to chat soon. I know how
    busy things get so I'm happy to work around your availability. Let me know
    when works best and I can send over an invite.
  </div>
  <div><br /></div>
  <div>Best,</div>
  <div>Justin</div>
</div>
```

### Email 3 Template

**Subject:** Same thread as Email 1 (reply in thread)
**Type:** `reply` using `thread_id` from Email 1
**Note:** This is the final email in the sequence. After this, mark `sequence_complete: true`.

```html
<div dir="ltr">
  <div>Hi [first_name],</div>
  <div><br /></div>
  <div>
    Just wanted to send one last note - I know things get busy, so I won't keep
    nudging.
  </div>
  <div><br /></div>
  <div>
    If it makes sense to chat down the line, feel free to grab time
    <a href="https://calendly.com/justin-sentra/intro">here</a> anytime.
    Otherwise, wishing you all the best and appreciate you taking a look!
  </div>
  <div><br /></div>
  <div>Best,</div>
  <div>Justin</div>
</div>
```

## Error Handling

- **Attio MCP unavailable:** "Attio MCP is not connected. Cannot fetch inbound entries." → abort
- **Superhuman MCP unavailable:** "Superhuman MCP is not connected. Showing qualification results only." → run in `summary` mode automatically
- **Missing email on person record:** Flag: "[Name] — NO EMAIL on Attio record, cannot draft" → skip, log as skipped
- **Calendar MCP unavailable:** "Calendar MCP down — skipping booking check. Reply check still active." → proceed with reply check only
- **filter-list-entries fails on created_at:** Fall back to `get-list-entries` full fetch + client-side timestamp filter. Log: "Note: Used full list fetch (filter fallback)."
- **Rate limits (Attio or Superhuman):** Pause 60 seconds, retry once. If still failing, report and stop.
- **State file corrupted:** Recreate empty state, warn "State file was corrupted — treating all entries as new."

## Guidelines

- **Speed matters.** One targeted Attio query, not multiple exploratory calls.
- **Never send without approval.** Always show drafts and wait for explicit "Y" or "Send."
- **HTML emails only.** Use Gmail-native `<div>` formatting per CLAUDE.md.
- **Do NOT append a signature block.** Superhuman automatically adds Justin's default signature to every outgoing email — adding one manually would duplicate it.
- **Track everything.** Every entry gets logged to state — qualified or skipped.
- **Idempotent runs.** Running the command twice should not create duplicate emails or state entries.
- **Batch operations.** When processing multiple leads, present all at once for batch approval rather than one-by-one.
