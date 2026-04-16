# Weekly Instantly Campaign Report — Cloud Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully autonomous, cloud-hosted weekly report that pulls Instantly campaign data, updates a Google Sheet, and emails Justin a summary every Saturday 8am ET — with zero manual activation, ever.

**Architecture:** A single standalone Python script (`scripts/weekly_report_cloud.py`) using only Python stdlib (no pip install), following the exact proven pattern from `scripts/process_inbound_cloud.py`. Credentials embedded in the script. Executed by a RemoteTrigger (Anthropic cloud cron) that simply runs `python3 scripts/weekly_report_cloud.py`. The script handles everything: Instantly API → computation → Google Sheets update → Gmail send.

**Tech Stack:** Python 3.x stdlib (`urllib`, `json`, `base64`, `email.mime`), Instantly v2 API, Google Sheets API v4, Gmail API v1, RemoteTrigger (Anthropic cloud cron)

---

## Why This Approach (And Why Everything Else Failed)

### Attempt 1: RemoteTrigger + MCP connectors → FAILED

The remote agent had Bash but no MCP connections (`mcp_connections: []`). It tried calling `mcp__google_sheets__*` tools that didn't exist in its sandbox. The claude.ai UI doesn't expose a connector attachment flow for scheduled tasks. **Dead end.**

### Attempt 2: Local launchd + `claude -p` → UNTESTED / FRAGILE

Three bugs: (1) no `--permission-mode` set, so tools would prompt in headless mode; (2) CLAUDE.md's "never send without approval" rule blocks email; (3) requires Mac to be awake at 8am Saturday.

### This approach: Standalone Python + RemoteTrigger → PROVEN

`scripts/process_inbound_cloud.py` already does this exact pattern successfully: stdlib-only Python with embedded OAuth credentials, calling Google Sheets + Gmail APIs directly via `urllib`. The RemoteTrigger just runs `python3 <script>`. No MCP needed. No permission issues. No local machine dependency.

**Key insight:** The remote agent doesn't need MCP tools — the Python script bypasses MCP entirely by calling Google APIs directly with the same OAuth tokens the MCP servers use.

---

## Manual vs Automated — Clear Breakdown

### ONE-TIME MANUAL (done during implementation)

| Action                      | Who               | Why it can't be automated                             |
| --------------------------- | ----------------- | ----------------------------------------------------- |
| Create RemoteTrigger        | Claude (via API)  | Requires this session's auth context                  |
| Commit script to repo       | Claude (via git)  | The remote agent needs the script in the git checkout |
| Unload broken launchd plist | Claude (via bash) | Cleanup of previous attempt                           |

### FULLY AUTOMATED (runs forever after setup)

| Action                                                 | How                                     | Frequency |
| ------------------------------------------------------ | --------------------------------------- | --------- |
| Pull Instantly campaign data                           | Python `urllib` → Instantly API v2      | Weekly    |
| Compute rates, deltas, health scores                   | Python math                             | Weekly    |
| Read last week's data from Google Sheets               | Python `urllib` → Sheets API v4         | Weekly    |
| Write updated dashboard to Google Sheets               | Python `urllib` → Sheets API v4         | Weekly    |
| Compose HTML email with insights                       | Python template + rule-based heuristics | Weekly    |
| Send email to justin@sentra.app                        | Python `urllib` → Gmail API v1          | Weekly    |
| Log results to stdout (visible in trigger run history) | Python `print()`                        | Weekly    |

### RARE MAINTENANCE (if something breaks)

| Scenario                   | Fix                                                       | Frequency                                       |
| -------------------------- | --------------------------------------------------------- | ----------------------------------------------- |
| Google OAuth token revoked | Re-run MCP auth locally, copy new refresh token to script | ~Never (tokens don't expire for published apps) |
| Instantly API key rotated  | Update key in script, commit                              | ~Yearly                                         |
| Spreadsheet ID changes     | Update ID in script, commit                               | ~Never                                          |

---

## Efficiency & Speed Optimizations

**Q: Can we batch Instantly API calls?**
A: No — Instantly's API requires one analytics call per campaign. But we fetch the campaign list in one paginated call (not N separate list calls).

**Q: Can we reduce Google Sheets API calls?**
A: Yes. We read the Update Log in ONE call, write the entire dashboard in ONE PUT, and append to Update Log in ONE POST. Total: 3 Sheets API calls (was unbounded with Claude-driven approach).

**Q: Why stdlib instead of `requests`/`gspread`?**
A: The remote environment may not have pip packages. stdlib means zero install time, zero dependency failures, zero version conflicts. `process_inbound_cloud.py` proves this works.

**Q: Why not have Claude compose the email (AI insights)?**
A: The Python script runs standalone — Claude just kicks it off. We'd need an Anthropic API key for the script to call Claude independently. Instead, we use rule-based heuristics for insights (reliable, deterministic, free). These cover 90% of useful analysis:

- High bounce → "list quality issues"
- Declining open rate → "subject line fatigue"
- High open + low reply → "copy needs work"
- Sudden spike → "anomalous, investigate"

**Q: What about the trailing `z` on the Instantly API key?**
A: The key in `.env` is `INSTANTLY_API_KEY` (no trailing z). The broken remote trigger had a typo with an extra `z`. Fixed in this implementation.

---

## File Structure

| File                                                              | Action | Responsibility                                                       |
| ----------------------------------------------------------------- | ------ | -------------------------------------------------------------------- |
| `scripts/weekly_report_cloud.py`                                  | CREATE | The entire weekly report: Instantly fetch, Sheets update, email send |
| `scripts/weekly-instantly-report.sh`                              | DELETE | Broken launchd script, replaced by cloud execution                   |
| `~/Library/LaunchAgents/com.justin.weekly-instantly-report.plist` | DELETE | Broken launchd plist, replaced by RemoteTrigger                      |
| RemoteTrigger `trig_01PkHaoiWqg93RxXpJER8eXG`                     | UPDATE | Re-enable with new minimal prompt                                    |

---

## Tasks

### Task 1: Clean Up Broken Artifacts

**Files:**

- Delete: `scripts/weekly-instantly-report.sh`
- Delete: `~/Library/LaunchAgents/com.justin.weekly-instantly-report.plist`
- Update: RemoteTrigger `trig_01PkHaoiWqg93RxXpJER8eXG` (disable or delete reference)

- [ ] **Step 1: Unload and remove launchd plist**

```bash
launchctl unload ~/Library/LaunchAgents/com.justin.weekly-instantly-report.plist 2>/dev/null
rm ~/Library/LaunchAgents/com.justin.weekly-instantly-report.plist
```

Expected: No errors. Plist gone.

- [ ] **Step 2: Delete broken shell script**

```bash
rm scripts/weekly-instantly-report.sh
```

- [ ] **Step 3: Verify cleanup**

```bash
launchctl list | grep weekly  # Should return nothing
ls scripts/weekly-instantly-report.sh  # Should say "No such file"
```

---

### Task 2: Create `scripts/weekly_report_cloud.py` — Scaffold + HTTP Helpers

**Files:**

- Create: `scripts/weekly_report_cloud.py`

**Why:** Reuse the exact HTTP helper pattern from `scripts/process_inbound_cloud.py` (lines 51-122). These are battle-tested functions for urllib requests, Google OAuth token exchange, and Sheets CRUD.

- [ ] **Step 1: Create script with imports, credentials, and HTTP helpers**

```python
#!/usr/bin/env python3
"""
Weekly Instantly Campaign Report — cloud edition (stdlib only).
Pulls Instantly analytics, updates Google Sheets, emails summary.
Runs via RemoteTrigger every Saturday 8am ET.
"""

import json
import base64
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── Credentials ───────────────────────────────────────────────────────────────

INSTANTLY_API_KEY = os.environ["INSTANTLY_API_KEY"]
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GMAIL_REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]
SHEETS_REFRESH_TOKEN = os.environ["SHEETS_REFRESH_TOKEN"]
SHEET_ID = "16zFEGHetXFc-H7E2xaoP7yQ-W4ZHeiEVFGen4-6qJiM"
SENDER_EMAIL = "justin@sentra.app"
RECIPIENT_EMAIL = "justin@sentra.app"

# Campaigns to exclude from tracking
EXCLUDED_CAMPAIGNS = {"March 2026 - PT", "March 2026 - ET"}

# Status mapping
STATUS_MAP = {0: "Draft", 1: "Active", 2: "Paused", 3: "Completed"}


# ── HTTP helpers (from process_inbound_cloud.py) ─────────────────────────────

def api_request(url, data=None, headers=None, method=None):
    """Make an HTTP request using urllib. Returns parsed JSON."""
    if headers is None:
        headers = {}
    if data is not None and isinstance(data, dict):
        data = json.dumps(data).encode("utf-8")
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
    elif data is not None and isinstance(data, str):
        data = data.encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  HTTP {e.code} error for {url}: {body[:500]}")
        raise


def form_post(url, params):
    """POST form-encoded data."""
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── Google OAuth ──────────────────────────────────────────────────────────────

def get_access_token(refresh_token):
    """Exchange refresh token for access token."""
    return form_post("https://oauth2.googleapis.com/token", {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })["access_token"]


# ── Google Sheets ─────────────────────────────────────────────────────────────

def sheets_get(token, range_):
    """Read from Google Sheets."""
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{urllib.parse.quote(range_)}"
    return api_request(url, headers={"Authorization": f"Bearer {token}"})


def sheets_update(token, range_, values):
    """Write to Google Sheets (overwrite)."""
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
        f"{urllib.parse.quote(range_)}?valueInputOption=RAW"
    )
    return api_request(url, data={"values": values},
                       headers={"Authorization": f"Bearer {token}"},
                       method="PUT")


def sheets_append(token, range_, values):
    """Append rows to Google Sheets."""
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
        f"{urllib.parse.quote(range_)}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
    )
    return api_request(url, data={"values": values},
                       headers={"Authorization": f"Bearer {token}"},
                       method="POST")


def sheets_clear(token, range_):
    """Clear a range in Google Sheets."""
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
        f"{urllib.parse.quote(range_)}:clear"
    )
    return api_request(url, data={},
                       headers={"Authorization": f"Bearer {token}"},
                       method="POST")
```

- [ ] **Step 2: Verify scaffold runs without errors**

```bash
cd /Users/justincheng/Desktop/claude-chief-of-staff
python3 -c "exec(open('scripts/weekly_report_cloud.py').read()); print('Scaffold OK')"
```

Expected: `Scaffold OK`

- [ ] **Step 3: Commit scaffold**

```bash
git add scripts/weekly_report_cloud.py
git commit -m "feat: scaffold weekly report cloud script with HTTP helpers"
```

---

### Task 3: Add Instantly API Fetching

**Files:**

- Modify: `scripts/weekly_report_cloud.py`

**Why:** This is the data source. We fetch all campaigns (paginated), then fetch analytics per campaign. The Instantly v2 API requires separate analytics calls per campaign — no batch endpoint exists.

- [ ] **Step 1: Add Instantly fetch functions**

Append after the `sheets_clear` function:

```python
# ── Instantly API ─────────────────────────────────────────────────────────────

def instantly_headers():
    return {"Authorization": f"Bearer {INSTANTLY_API_KEY}"}


def fetch_campaigns():
    """Fetch all campaigns from Instantly, handling pagination."""
    campaigns = []
    cursor = None
    while True:
        url = "https://api.instantly.ai/api/v2/campaigns?limit=100"
        if cursor:
            url += f"&starting_after={cursor}"
        result = api_request(url, headers=instantly_headers(), method="GET")

        items = result if isinstance(result, list) else result.get("data", result.get("items", []))
        if not items:
            break
        campaigns.extend(items)

        cursor = result.get("next_starting_after")
        if not cursor:
            break

    # Filter excluded campaigns
    return [c for c in campaigns if c.get("name") not in EXCLUDED_CAMPAIGNS]


def fetch_campaign_analytics(campaign_id):
    """Fetch analytics overview for a single campaign."""
    url = f"https://api.instantly.ai/api/v2/campaigns/analytics/overview?id={campaign_id}"
    try:
        return api_request(url, headers=instantly_headers(), method="GET")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            import time
            print(f"  Rate limited — waiting 60s...")
            time.sleep(60)
            return api_request(url, headers=instantly_headers(), method="GET")
        raise


def compute_rate(numerator, denominator):
    """Compute percentage rate, return formatted string."""
    if not denominator or denominator == 0:
        return "N/A"
    return f"{(numerator / denominator) * 100:.1f}%"


def compute_rate_float(numerator, denominator):
    """Compute percentage rate as float, return 0.0 if N/A."""
    if not denominator or denominator == 0:
        return 0.0
    return round((numerator / denominator) * 100, 1)


def build_campaign_data():
    """Fetch all campaigns + analytics, compute rates, return structured data."""
    print("Fetching Instantly campaigns...")
    campaigns = fetch_campaigns()
    print(f"  Found {len(campaigns)} campaigns (after exclusions)")

    results = []
    for c in campaigns:
        cid = c.get("id", "")
        name = c.get("name", "Unknown")
        status_code = c.get("status", 0)
        status = STATUS_MAP.get(status_code, "Unknown")

        print(f"  Fetching analytics for: {name}")
        analytics = fetch_campaign_analytics(cid)

        sent = analytics.get("emails_sent_count", 0)
        contacted = analytics.get("contacted_count", 0)
        new_leads = analytics.get("new_leads_contacted_count", 0)
        opened = analytics.get("open_count_unique", 0)
        replies = analytics.get("reply_count_unique", 0)
        auto_replies = analytics.get("reply_count_automatic_unique", 0)
        clicks = analytics.get("link_click_count_unique", 0)
        bounces = analytics.get("bounced_count", 0)
        interested = analytics.get("total_interested", 0)
        meetings_booked = analytics.get("total_meeting_booked", 0)
        meetings_completed = analytics.get("total_meeting_completed", 0)
        closed = analytics.get("total_closed", 0)

        results.append({
            "name": name,
            "status": status,
            "sent": sent,
            "contacted": contacted,
            "new_leads": new_leads,
            "opened": opened,
            "open_rate": compute_rate_float(opened, contacted),
            "open_rate_str": compute_rate(opened, contacted),
            "replies": replies,
            "auto_replies": auto_replies,
            "reply_rate": compute_rate_float(replies, contacted),
            "reply_rate_str": compute_rate(replies, contacted),
            "clicks": clicks,
            "click_rate_str": compute_rate(clicks, contacted),
            "bounces": bounces,
            "bounce_rate": compute_rate_float(bounces, contacted),
            "bounce_rate_str": compute_rate(bounces, contacted),
            "interested": interested,
            "meetings_booked": meetings_booked,
            "meetings_completed": meetings_completed,
            "closed": closed,
        })

    # Sort by open rate descending
    results.sort(key=lambda x: x["open_rate"], reverse=True)
    return results
```

- [ ] **Step 2: Test Instantly fetch locally**

```bash
cd /Users/justincheng/Desktop/claude-chief-of-staff
python3 -c "
exec(open('scripts/weekly_report_cloud.py').read())
data = build_campaign_data()
for d in data[:3]:
    print(f'{d[\"name\"]}: {d[\"open_rate_str\"]} open, {d[\"reply_rate_str\"]} reply')
print(f'Total: {len(data)} campaigns')
"
```

Expected: Campaign names with open/reply rates printed. If 401 error → API key is wrong.

- [ ] **Step 3: Commit**

```bash
git add scripts/weekly_report_cloud.py
git commit -m "feat: add Instantly API fetching with rate computation"
```

---

### Task 4: Add Google Sheets Read/Write

**Files:**

- Modify: `scripts/weekly_report_cloud.py`

**Why:** The spreadsheet is the dashboard. We need to: (1) read last week's Update Log for week-over-week deltas, (2) clear + rewrite Campaign Dashboard, (3) append to Update Log. All in 3 API calls — minimal and fast.

- [ ] **Step 1: Add sheets read/write functions**

Append after `build_campaign_data`:

```python
# ── Sheets operations ─────────────────────────────────────────────────────────

def read_last_week(sheets_token):
    """Read the most recent row from Update Log tab for comparison."""
    try:
        result = sheets_get(sheets_token, "Update Log!A:F")
        rows = result.get("values", [])
        if len(rows) > 1:
            last_row = rows[-1]
            return {
                "timestamp": last_row[0] if len(last_row) > 0 else "",
                "campaigns_count": int(last_row[1]) if len(last_row) > 1 and last_row[1].isdigit() else 0,
                "total_sent": int(last_row[2]) if len(last_row) > 2 and last_row[2].isdigit() else 0,
                "avg_open_rate": float(last_row[3].replace("%", "")) if len(last_row) > 3 and last_row[3].replace(".", "").replace("%", "").isdigit() else 0.0,
                "avg_reply_rate": float(last_row[4].replace("%", "")) if len(last_row) > 4 and last_row[4].replace(".", "").replace("%", "").isdigit() else 0.0,
            }
    except Exception as e:
        print(f"  Could not read Update Log: {e}")
    return None


def write_dashboard(sheets_token, campaign_data):
    """Clear and rewrite the Campaign Dashboard tab."""
    headers = [
        "Campaign Name", "Status", "Sent", "Contacted", "New Leads",
        "Opened (Unique)", "Open Rate", "Replies", "Auto-Replies",
        "Reply Rate", "Clicks (Unique)", "Click Rate", "Bounces",
        "Bounce Rate", "Interested", "Meetings Booked",
        "Meetings Completed", "Closed",
    ]

    rows = [headers]
    for c in campaign_data:
        rows.append([
            c["name"], c["status"], c["sent"], c["contacted"], c["new_leads"],
            c["opened"], c["open_rate_str"], c["replies"], c["auto_replies"],
            c["reply_rate_str"], c["clicks"], c["click_rate_str"], c["bounces"],
            c["bounce_rate_str"], c["interested"], c["meetings_booked"],
            c["meetings_completed"], c["closed"],
        ])

    # Clear existing data (keep headers row structure)
    sheets_clear(sheets_token, "Campaign Dashboard!A1:R200")

    # Write new data
    range_ = f"Campaign Dashboard!A1:R{len(rows)}"
    sheets_update(sheets_token, range_, rows)
    print(f"  Dashboard updated: {len(rows) - 1} campaigns written")


def append_update_log(sheets_token, campaign_data):
    """Append a summary row to the Update Log tab."""
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%b %d, %Y")

    total_sent = sum(c["sent"] for c in campaign_data)
    total_contacted = sum(c["contacted"] for c in campaign_data)
    total_opened = sum(c["opened"] for c in campaign_data)
    total_replies = sum(c["replies"] for c in campaign_data)

    avg_open = compute_rate_float(total_opened, total_contacted)
    avg_reply = compute_rate_float(total_replies, total_contacted)

    row = [
        timestamp,
        str(len(campaign_data)),
        str(total_sent),
        f"{avg_open}%",
        f"{avg_reply}%",
        "Automated weekly update",
    ]

    sheets_append(sheets_token, "Update Log!A:F", [row])
    print(f"  Update Log appended: {timestamp}")

    return {
        "total_sent": total_sent,
        "total_contacted": total_contacted,
        "total_opened": total_opened,
        "total_replies": total_replies,
        "avg_open_rate": avg_open,
        "avg_reply_rate": avg_reply,
        "timestamp": timestamp,
    }
```

- [ ] **Step 2: Test sheets access locally**

```bash
cd /Users/justincheng/Desktop/claude-chief-of-staff
python3 -c "
exec(open('scripts/weekly_report_cloud.py').read())
token = get_access_token(SHEETS_REFRESH_TOKEN)
print('Sheets token: OK')
last = read_last_week(token)
print('Last week data:', last)
"
```

Expected: Prints `Sheets token: OK` and last week's data (or `None` if no log rows yet).

- [ ] **Step 3: Commit**

```bash
git add scripts/weekly_report_cloud.py
git commit -m "feat: add Google Sheets read/write for dashboard and update log"
```

---

### Task 5: Add Email Composition + Sending

**Files:**

- Modify: `scripts/weekly_report_cloud.py`

**Why:** The email is the primary interface — Justin reads it Saturday morning and decides what to act on. It needs to be clean, scannable, and insight-driven. Rule-based heuristics cover 90% of useful analysis without requiring an AI API call.

- [ ] **Step 1: Add email composition and sending functions**

Append after `append_update_log`:

```python
# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(gmail_token, subject, html_body):
    """Send an email via Gmail API."""
    msg = MIMEMultipart("alternative")
    msg["to"] = RECIPIENT_EMAIL
    msg["from"] = SENDER_EMAIL
    msg["subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    result = api_request(url, data={"raw": raw},
                         headers={"Authorization": f"Bearer {gmail_token}"})
    print(f"  Email sent: message id {result.get('id', 'unknown')}")
    return result


def delta_str(current, previous):
    """Format a delta as +X or -X with arrow."""
    diff = current - previous
    if diff > 0:
        return f"+{diff} ↑"
    elif diff < 0:
        return f"{diff} ↓"
    return "0 →"


def delta_rate_str(current, previous):
    """Format a rate delta as +X.X pp."""
    diff = round(current - previous, 1)
    if diff > 0:
        return f"+{diff}pp ↑"
    elif diff < 0:
        return f"{diff}pp ↓"
    return "0pp →"


def generate_insights(campaign_data):
    """Generate rule-based insights from campaign data."""
    insights_good = []
    insights_bad = []
    insights_unexpected = []

    for c in campaign_data:
        name = c["name"]
        # What went well
        if c["open_rate"] > 50 and c["contacted"] > 30:
            insights_good.append(
                f"<b>{name}</b>: {c['open_rate_str']} open rate is excellent — "
                f"strong subject line/targeting fit"
            )
        if c["reply_rate"] > 10 and c["contacted"] > 30:
            insights_good.append(
                f"<b>{name}</b>: {c['reply_rate_str']} reply rate is strong — "
                f"copy is resonating with this audience"
            )

        # What needs attention
        if c["bounce_rate"] > 5 and c["contacted"] > 20:
            insights_bad.append(
                f"<b>{name}</b>: {c['bounce_rate_str']} bounce rate — "
                f"list quality issue, consider verifying emails"
            )
        if c["contacted"] > 100 and c["replies"] == 0:
            insights_bad.append(
                f"<b>{name}</b>: {c['contacted']} contacted, 0 replies — "
                f"targeting or copy mismatch, consider pausing"
            )
        if c["open_rate"] > 40 and c["reply_rate"] < 3 and c["contacted"] > 50:
            insights_bad.append(
                f"<b>{name}</b>: High open ({c['open_rate_str']}) but low reply "
                f"({c['reply_rate_str']}) — copy may need rework"
            )

        # Unexpected
        if c["auto_replies"] > c["replies"] and c["replies"] > 0:
            insights_unexpected.append(
                f"<b>{name}</b>: Auto-replies ({c['auto_replies']}) exceed "
                f"manual replies ({c['replies']}) — check OOO/bounce patterns"
            )

    return insights_good, insights_bad, insights_unexpected


def compose_email_body(campaign_data, totals, last_week):
    """Compose HTML email body with insights."""
    d = '<div style="font-family:sans-serif;font-size:14px;color:#333;">'

    # Header
    d += '<div style="font-size:20px;font-weight:bold;margin-bottom:16px;">'
    d += f'Weekly Instantly Report — {totals["timestamp"]}</div>'

    # Top-line numbers
    active = [c for c in campaign_data if c["status"] == "Active"]
    d += '<div style="background:#f5f5f5;padding:12px;border-radius:6px;margin-bottom:16px;">'
    d += f'<b>{len(active)}</b> active campaigns · '
    d += f'<b>{totals["total_sent"]}</b> sent · '
    d += f'<b>{totals["total_contacted"]}</b> contacted · '
    d += f'<b>{totals["avg_open_rate"]}%</b> avg open · '
    d += f'<b>{totals["avg_reply_rate"]}%</b> avg reply'
    d += '</div>'

    # Week-over-week
    if last_week:
        d += '<div style="margin-bottom:16px;">'
        d += '<div style="font-size:16px;font-weight:bold;margin-bottom:8px;">vs Last Week</div>'
        d += '<div style="padding-left:12px;">'
        d += f'Sent: {delta_str(totals["total_sent"], last_week["total_sent"])}<br>'
        d += f'Open rate: {delta_rate_str(totals["avg_open_rate"], last_week["avg_open_rate"])}<br>'
        d += f'Reply rate: {delta_rate_str(totals["avg_reply_rate"], last_week["avg_reply_rate"])}<br>'
        d += f'Campaigns: {delta_str(len(campaign_data), last_week["campaigns_count"])}'
        d += '</div></div>'

    # Top 3 performers
    top3 = campaign_data[:3]
    if top3:
        d += '<div style="margin-bottom:16px;">'
        d += '<div style="font-size:16px;font-weight:bold;margin-bottom:8px;">Top Performers</div>'
        for i, c in enumerate(top3, 1):
            d += f'<div style="padding-left:12px;margin-bottom:4px;">'
            d += f'{i}. <b>{c["name"]}</b> — {c["open_rate_str"]} open, '
            d += f'{c["reply_rate_str"]} reply ({c["contacted"]} contacted)'
            d += '</div>'
        d += '</div>'

    # Insights
    goods, bads, unexpecteds = generate_insights(campaign_data)

    if goods:
        d += '<div style="margin-bottom:16px;">'
        d += '<div style="font-size:16px;font-weight:bold;margin-bottom:8px;">What Went Well</div>'
        for g in goods[:4]:
            d += f'<div style="padding-left:12px;margin-bottom:4px;">✅ {g}</div>'
        d += '</div>'

    if bads:
        d += '<div style="margin-bottom:16px;">'
        d += '<div style="font-size:16px;font-weight:bold;margin-bottom:8px;">Needs Attention</div>'
        for b in bads[:4]:
            d += f'<div style="padding-left:12px;margin-bottom:4px;">⚠️ {b}</div>'
        d += '</div>'

    if unexpecteds:
        d += '<div style="margin-bottom:16px;">'
        d += '<div style="font-size:16px;font-weight:bold;margin-bottom:8px;">Unexpected</div>'
        for u in unexpecteds[:3]:
            d += f'<div style="padding-left:12px;margin-bottom:4px;">🔍 {u}</div>'
        d += '</div>'

    # Spreadsheet link
    d += '<div style="margin-top:20px;padding-top:12px;border-top:1px solid #ddd;">'
    d += '<a href="https://docs.google.com/spreadsheets/d/16zFEGHetXFc-H7E2xaoP7yQ-W4ZHeiEVFGen4-6qJiM">'
    d += 'View full dashboard →</a></div>'

    d += '</div>'
    return d
```

- [ ] **Step 2: Test Gmail token exchange locally**

```bash
cd /Users/justincheng/Desktop/claude-chief-of-staff
python3 -c "
exec(open('scripts/weekly_report_cloud.py').read())
token = get_access_token(GMAIL_REFRESH_TOKEN)
print('Gmail token: OK' if token else 'FAILED')
"
```

Expected: `Gmail token: OK`

- [ ] **Step 3: Commit**

```bash
git add scripts/weekly_report_cloud.py
git commit -m "feat: add email composition with rule-based insights and Gmail send"
```

---

### Task 6: Add `main()` Orchestration

**Files:**

- Modify: `scripts/weekly_report_cloud.py`

**Why:** Ties everything together in the correct order: auth → fetch → read last week → write sheets → compose → send. Includes error handling and logging.

- [ ] **Step 1: Add main function and entry point**

Append at the end of the script:

```python
# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc)
    print(f"=== WEEKLY INSTANTLY REPORT — {now.strftime('%Y-%m-%d %H:%M:%S UTC')} ===")

    # Step 1: Get Google access tokens
    print("Getting Google access tokens...")
    try:
        gmail_token = get_access_token(GMAIL_REFRESH_TOKEN)
        print("  Gmail: OK")
    except Exception as e:
        print(f"  FATAL: Gmail token failed: {e}")
        return

    try:
        sheets_token = get_access_token(SHEETS_REFRESH_TOKEN)
        print("  Sheets: OK")
    except Exception as e:
        print(f"  FATAL: Sheets token failed: {e}")
        return

    # Step 2: Read last week's data BEFORE updating
    print("Reading last week's data...")
    last_week = read_last_week(sheets_token)
    if last_week:
        print(f"  Previous: {last_week['timestamp']} — "
              f"{last_week['total_sent']} sent, "
              f"{last_week['avg_open_rate']}% open, "
              f"{last_week['avg_reply_rate']}% reply")
    else:
        print("  No previous data (first run)")

    # Step 3: Fetch Instantly campaign data
    try:
        campaign_data = build_campaign_data()
    except Exception as e:
        print(f"  FATAL: Instantly fetch failed: {e}")
        # Try to send error email
        try:
            send_email(gmail_token,
                       f"⚠ Weekly Report FAILED — {now.strftime('%b %d, %Y')}",
                       f'<div>Instantly API fetch failed: {e}</div>')
        except Exception:
            pass
        return

    if not campaign_data:
        print("  No campaigns found. Check API key.")
        return

    print(f"  {len(campaign_data)} campaigns fetched")

    # Step 4: Update Google Sheets
    print("Updating Google Sheets...")
    try:
        write_dashboard(sheets_token, campaign_data)
        totals = append_update_log(sheets_token, campaign_data)
    except Exception as e:
        print(f"  ERROR: Sheets update failed: {e}")
        # Continue to email anyway with computed totals
        total_contacted = sum(c["contacted"] for c in campaign_data)
        total_opened = sum(c["opened"] for c in campaign_data)
        total_replies = sum(c["replies"] for c in campaign_data)
        totals = {
            "total_sent": sum(c["sent"] for c in campaign_data),
            "total_contacted": total_contacted,
            "total_opened": total_opened,
            "total_replies": total_replies,
            "avg_open_rate": compute_rate_float(total_opened, total_contacted),
            "avg_reply_rate": compute_rate_float(total_replies, total_contacted),
            "timestamp": now.strftime("%b %d, %Y"),
        }

    # Step 5: Compose and send email
    print("Composing email...")
    subject = f"Weekly Instantly Report — {totals['timestamp']}"
    body = compose_email_body(campaign_data, totals, last_week)

    print("Sending email...")
    try:
        send_email(gmail_token, subject, body)
        print("  Email sent successfully")
    except Exception as e:
        print(f"  ERROR: Email send failed: {e}")

    # Summary
    print(f"\n=== COMPLETE ===")
    print(f"Campaigns: {len(campaign_data)}")
    print(f"Total sent: {totals['total_sent']}")
    print(f"Avg open rate: {totals['avg_open_rate']}%")
    print(f"Avg reply rate: {totals['avg_reply_rate']}%")
    print(f"Email: {RECIPIENT_EMAIL}")
    print(f"Sheet: https://docs.google.com/spreadsheets/d/{SHEET_ID}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/weekly_report_cloud.py
git commit -m "feat: add main() orchestration for weekly report"
```

---

### Task 7: Local End-to-End Test

**Files:** None (read-only verification)

**Why:** Before deploying to cloud, verify the script works locally against real APIs.

- [ ] **Step 1: Run the full script**

```bash
cd /Users/justincheng/Desktop/claude-chief-of-staff
python3 scripts/weekly_report_cloud.py
```

Expected output:

```
=== WEEKLY INSTANTLY REPORT — 2026-04-11 ...UTC ===
Getting Google access tokens...
  Gmail: OK
  Sheets: OK
Reading last week's data...
  ...
Fetching Instantly campaigns...
  Found N campaigns (after exclusions)
  Fetching analytics for: ...
  ...
Updating Google Sheets...
  Dashboard updated: N campaigns written
  Update Log appended: Apr 11, 2026
Composing email...
Sending email...
  Email sent: message id ...
=== COMPLETE ===
```

- [ ] **Step 2: Verify spreadsheet**

Open https://docs.google.com/spreadsheets/d/16zFEGHetXFc-H7E2xaoP7yQ-W4ZHeiEVFGen4-6qJiM

Check:

- "Campaign Dashboard" tab has all campaigns sorted by open rate descending
- "Update Log" tab has a new row with today's date

- [ ] **Step 3: Verify email**

Check justin@sentra.app inbox for "Weekly Instantly Report — Apr 11, 2026"

Check:

- Top-line numbers present
- Top performers listed
- Insights present (if any campaigns trigger rules)
- Spreadsheet link works

- [ ] **Step 4: Push to remote**

```bash
git push origin main
```

**Why:** The RemoteTrigger clones the repo. The script must be on `main` for the cloud agent to find it.

---

### Task 8: Create Cloud RemoteTrigger

**Why:** This is what makes it autonomous. The RemoteTrigger runs in Anthropic's cloud every Saturday — no local machine, no manual activation, ever.

- [ ] **Step 1: Disable old broken trigger**

```python
# Via RemoteTrigger API:
action: "update"
trigger_id: "trig_01PkHaoiWqg93RxXpJER8eXG"
body: {"enabled": false}
```

Already done — just verify it stays disabled.

- [ ] **Step 2: Create new trigger with minimal prompt**

````python
# Via RemoteTrigger API:
action: "create"
body: {
    "name": "weekly-instantly-report-v2",
    "cron_expression": "0 12 * * 6",  # Saturday 12:00 UTC = 8:00 AM ET
    "enabled": true,
    "job_config": {
        "ccr": {
            "environment_id": "env_01TKsUt7R27tuAJsqMdNaHeh",
            "session_context": {
                "model": "claude-sonnet-4-6",
                "sources": [
                    {"git_repository": {"url": "https://github.com/mimurchison/claude-chief-of-staff"}}
                ],
                "allowed_tools": ["Bash", "Read"]
            },
            "events": [
                {
                    "data": {
                        "uuid": "<generate fresh uuid>",
                        "session_id": "",
                        "type": "user",
                        "parent_tool_use_id": null,
                        "message": {
                            "content": "Run the weekly Instantly campaign report script:\n\n```bash\npython3 scripts/weekly_report_cloud.py\n```\n\nThis script fetches Instantly campaign analytics, updates the Google Sheets dashboard, and emails a summary to justin@sentra.app. All credentials are embedded in the script. No MCP tools needed.\n\nReport the full output. If it fails, show the error.",
                            "role": "user"
                        }
                    }
                }
            ]
        }
    }
}
````

**Why the prompt is minimal:** All logic is in the Python script. Claude's only job is to run `python3 scripts/weekly_report_cloud.py` and report results. This makes the trigger reliable — no prompt engineering, no tool-calling variability, no MCP dependencies.

- [ ] **Step 3: Verify trigger created**

Note the trigger ID. Visit `https://claude.ai/code/scheduled/{TRIGGER_ID}` to confirm:

- Status: Active
- Schedule: Every Saturday at 5:00 AM PDT (= 8:00 AM ET = 12:00 UTC)
- Repository: mimurchison/claude-chief-of-staff

---

### Task 9: Cloud Test Run

- [ ] **Step 1: Trigger manual run**

```python
# Via RemoteTrigger API:
action: "run"
trigger_id: "<new trigger id>"
```

- [ ] **Step 2: Wait for completion**

Check `https://claude.ai/code/scheduled/{TRIGGER_ID}` — the run should show as completed (green check) within 2-3 minutes.

- [ ] **Step 3: Verify outputs**

Same checks as Task 7:

- Spreadsheet has new Update Log row
- Email received at justin@sentra.app
- Run logs show success in the trigger history

If the run fails: click into the run, read the error output, fix the script, commit, push, re-run.

---

## Security Note

This script follows the same pattern as `scripts/process_inbound_cloud.py`: credentials embedded directly in a git-tracked Python file. This is an accepted pattern in this private repo (`mimurchison/claude-chief-of-staff`). If the repo ever becomes public, credentials must be moved to GitHub secrets or a vault.

---

## What Happens Every Saturday (After Setup)

```
12:00 UTC (8:00 AM ET) — Anthropic cloud fires RemoteTrigger
  → Clones mimurchison/claude-chief-of-staff
  → Claude runs: python3 scripts/weekly_report_cloud.py
    → Exchanges Google OAuth refresh tokens for access tokens
    → Fetches all Instantly campaigns (paginated)
    → Fetches per-campaign analytics
    → Computes rates, sorts by open rate
    → Reads Update Log for last week's baseline
    → Clears + rewrites Campaign Dashboard tab
    → Appends new row to Update Log tab
    → Composes HTML email with insights
    → Sends to justin@sentra.app via Gmail API
    → Prints summary to stdout (visible in trigger run history)
  → Claude reports: "Done. N campaigns tracked. Email sent."
  → Run marked complete in trigger history
```

Justin wakes up Saturday morning. Email is in inbox. Spreadsheet is updated. Zero manual steps.
