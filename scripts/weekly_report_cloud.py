#!/usr/bin/env python3
"""
Weekly Instantly Campaign Report — stdlib only (no pip install needed).
Fetches campaign data from Instantly.ai, updates Google Sheets, sends email report.
"""

import json
import base64
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── Credentials ───────────────────────────────────────────────────────────────

INSTANTLY_API_KEY = "REDACTED_INSTANTLY_API_KEY"
GOOGLE_CLIENT_ID = "REDACTED_GOOGLE_CLIENT_ID"
GOOGLE_CLIENT_SECRET = "REDACTED_GOOGLE_CLIENT_SECRET"
GMAIL_REFRESH_TOKEN = "REDACTED_REFRESH_TOKEN"
SHEETS_REFRESH_TOKEN = "REDACTED_REFRESH_TOKEN"
SHEET_ID = "16zFEGHetXFc-H7E2xaoP7yQ-W4ZHeiEVFGen4-6qJiM"
SENDER_EMAIL = "justin@sentra.app"
RECIPIENT_EMAIL = "justin@sentra.app"

EXCLUDED_CAMPAIGNS = {"March 2026 - PT", "March 2026 - ET"}

STATUS_MAP = {0: "Draft", 1: "Active", 2: "Paused", 3: "Completed"}

DASHBOARD_HEADERS = [
    "Campaign Name", "Status", "Sent", "Contacted", "New Leads",
    "Opened (Unique)", "Open Rate", "Replies", "Auto-Replies", "Reply Rate",
    "Clicks (Unique)", "Click Rate", "Bounces", "Bounce Rate",
    "Interested", "Meetings Booked", "Meetings Completed", "Closed",
]

# ── HTTP helpers ──────────────────────────────────────────────────────────────

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
        print(f"HTTP {e.code} error for {url}: {body[:500]}")
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

def sheets_get(sheets_token, range_):
    """Read from Google Sheets."""
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{urllib.parse.quote(range_)}"
    return api_request(url, headers={"Authorization": f"Bearer {sheets_token}"})


def sheets_update(sheets_token, range_, values):
    """Write to Google Sheets."""
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
        f"{urllib.parse.quote(range_)}?valueInputOption=RAW"
    )
    return api_request(url, data={"values": values},
                       headers={"Authorization": f"Bearer {sheets_token}"},
                       method="PUT")


def sheets_append(sheets_token, range_, values):
    """Append rows to Google Sheets."""
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
        f"{urllib.parse.quote(range_)}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
    )
    return api_request(url, data={"values": values},
                       headers={"Authorization": f"Bearer {sheets_token}"},
                       method="POST")


def sheets_clear(sheets_token, range_):
    """Clear a range in Google Sheets."""
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
        f"{urllib.parse.quote(range_)}:clear"
    )
    return api_request(url, data={},
                       headers={"Authorization": f"Bearer {sheets_token}"},
                       method="POST")


# ── Gmail ─────────────────────────────────────────────────────────────────────

def send_email(gmail_token, to_email, subject, html_body):
    """Send an email via Gmail API."""
    msg = MIMEMultipart("alternative")
    msg["to"] = to_email
    msg["from"] = SENDER_EMAIL
    msg["subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    result = api_request(url, data={"raw": raw},
                         headers={"Authorization": f"Bearer {gmail_token}"})
    return result.get("id", "")


# ── Instantly API ─────────────────────────────────────────────────────────────

def instantly_request(url):
    """Make an Instantly API request with rate-limit retry."""
    headers = {"Authorization": f"Bearer {INSTANTLY_API_KEY}"}
    try:
        return api_request(url, headers=headers, method="GET")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print("  Rate limited by Instantly, waiting 60s...")
            time.sleep(60)
            return api_request(url, headers=headers, method="GET")
        raise


def fetch_campaigns():
    """Fetch all campaigns from Instantly v2 API with pagination."""
    campaigns = []
    starting_after = None

    while True:
        url = "https://api.instantly.ai/api/v2/campaigns?limit=100"
        if starting_after:
            url += f"&starting_after={urllib.parse.quote(starting_after)}"

        result = instantly_request(url)
        batch = result.get("data", result) if isinstance(result, dict) else result

        if isinstance(batch, dict):
            items = batch.get("items", batch.get("data", []))
        else:
            items = batch

        if not items:
            break

        campaigns.extend(items)

        # Handle pagination
        next_cursor = None
        if isinstance(result, dict):
            next_cursor = result.get("next_starting_after")
        if not next_cursor:
            break
        starting_after = next_cursor

    return campaigns


def fetch_campaign_analytics(campaign_id):
    """Fetch analytics overview for a single campaign."""
    url = f"https://api.instantly.ai/api/v2/campaigns/analytics/overview?id={urllib.parse.quote(campaign_id)}"
    return instantly_request(url)


def compute_rate(numerator, denominator):
    """Compute a percentage rate. Returns 'N/A' if denominator is 0."""
    if not denominator:
        return "N/A"
    return f"{(numerator / denominator) * 100:.1f}%"


def rate_value(rate_str):
    """Parse a rate string like '12.3%' to a float. Returns 0.0 for 'N/A'."""
    if rate_str == "N/A":
        return 0.0
    return float(rate_str.replace("%", ""))


def build_campaign_rows(campaigns):
    """Fetch analytics for each campaign and build row data."""
    rows = []

    for campaign in campaigns:
        name = campaign.get("name", "Unknown")
        if name in EXCLUDED_CAMPAIGNS:
            print(f"  SKIP (excluded): {name}")
            continue

        campaign_id = campaign.get("id", "")
        status_code = campaign.get("status", 0)
        status = STATUS_MAP.get(status_code, f"Unknown({status_code})")

        try:
            analytics = fetch_campaign_analytics(campaign_id)
        except Exception as e:
            print(f"  ERROR fetching analytics for '{name}': {e}")
            continue

        # Handle both direct response and nested data
        data = analytics
        if isinstance(analytics, dict) and "data" in analytics:
            data_list = analytics["data"]
            data = data_list[0] if isinstance(data_list, list) and data_list else analytics

        sent = data.get("emails_sent_count", 0)
        contacted = data.get("contacted_count", 0)
        new_leads = data.get("new_leads_contacted_count", 0)
        opened = data.get("open_count_unique", 0)
        replies = data.get("reply_count_unique", 0)
        auto_replies = data.get("reply_count_automatic_unique", 0)
        clicks = data.get("link_click_count_unique", 0)
        bounces = data.get("bounced_count", 0)
        interested = data.get("total_interested", 0)
        meetings_booked = data.get("total_meeting_booked", 0)
        meetings_completed = data.get("total_meeting_completed", 0)
        closed = data.get("total_closed", 0)

        open_rate = compute_rate(opened, contacted)
        reply_rate = compute_rate(replies, contacted)
        click_rate = compute_rate(clicks, contacted)
        bounce_rate = compute_rate(bounces, contacted)

        rows.append({
            "name": name,
            "status": status,
            "sent": sent,
            "contacted": contacted,
            "new_leads": new_leads,
            "opened": opened,
            "open_rate": open_rate,
            "replies": replies,
            "auto_replies": auto_replies,
            "reply_rate": reply_rate,
            "clicks": clicks,
            "click_rate": click_rate,
            "bounces": bounces,
            "bounce_rate": bounce_rate,
            "interested": interested,
            "meetings_booked": meetings_booked,
            "meetings_completed": meetings_completed,
            "closed": closed,
        })

        print(f"  {name}: sent={sent}, contacted={contacted}, open={open_rate}, reply={reply_rate}")

    # Sort by open rate descending
    rows.sort(key=lambda r: rate_value(r["open_rate"]), reverse=True)
    return rows


def row_to_sheet_values(row):
    """Convert a campaign row dict to a list of values for Sheets."""
    return [
        row["name"], row["status"], row["sent"], row["contacted"],
        row["new_leads"], row["opened"], row["open_rate"],
        row["replies"], row["auto_replies"], row["reply_rate"],
        row["clicks"], row["click_rate"], row["bounces"], row["bounce_rate"],
        row["interested"], row["meetings_booked"], row["meetings_completed"],
        row["closed"],
    ]


# ── Update Log ────────────────────────────────────────────────────────────────

def read_last_week(sheets_token):
    """Read the most recent row from the Update Log tab."""
    try:
        result = sheets_get(sheets_token, "'Update Log'!A:F")
        rows = result.get("values", [])
        if len(rows) > 1:
            return rows[-1]  # Last data row
    except Exception as e:
        print(f"  WARNING: Could not read Update Log: {e}")
    return None


def build_update_log_row(rows, timestamp):
    """Build a single row for the Update Log."""
    total_sent = sum(r["sent"] for r in rows)
    total_contacted = sum(r["contacted"] for r in rows)
    total_opened = sum(r["opened"] for r in rows)
    total_replies = sum(r["replies"] for r in rows)

    avg_open = compute_rate(total_opened, total_contacted)
    avg_reply = compute_rate(total_replies, total_contacted)

    return [
        timestamp,
        len(rows),
        total_sent,
        avg_open,
        avg_reply,
        "Automated weekly report",
    ]


# ── Email Composition ────────────────────────────────────────────────────────

def compose_email_html(rows, last_week_row, timestamp):
    """Compose the HTML email body with insights."""
    # Aggregate stats
    active_campaigns = sum(1 for r in rows if r["status"] == "Active")
    total_sent = sum(r["sent"] for r in rows)
    total_contacted = sum(r["contacted"] for r in rows)
    total_opened = sum(r["opened"] for r in rows)
    total_replies = sum(r["replies"] for r in rows)

    avg_open = compute_rate(total_opened, total_contacted)
    avg_reply = compute_rate(total_replies, total_contacted)

    # Style constants
    style_body = 'font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; color: #333; line-height: 1.6; max-width: 680px; margin: 0 auto; padding: 20px;'
    style_h1 = 'font-size: 22px; color: #1a1a1a; margin-bottom: 4px;'
    style_h2 = 'font-size: 17px; color: #1a1a1a; margin-top: 28px; margin-bottom: 12px; border-bottom: 1px solid #e0e0e0; padding-bottom: 6px;'
    style_stat_box = 'display: inline-block; background: #f8f9fa; border-radius: 8px; padding: 12px 18px; margin: 4px 8px 4px 0; text-align: center; min-width: 100px;'
    style_stat_num = 'font-size: 24px; font-weight: 700; color: #1a1a1a; display: block;'
    style_stat_label = 'font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 0.5px;'
    style_table = 'border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 8px;'
    style_th = 'text-align: left; padding: 8px 10px; background: #f1f3f5; border-bottom: 2px solid #dee2e6; font-weight: 600; color: #495057;'
    style_td = 'padding: 8px 10px; border-bottom: 1px solid #eee;'
    style_good = 'color: #2b8a3e;'
    style_bad = 'color: #e03131;'
    style_neutral = 'color: #868e96;'

    html_parts = []
    html_parts.append(f'<div style="{style_body}">')
    html_parts.append(f'<h1 style="{style_h1}">Weekly Instantly Report</h1>')
    html_parts.append(f'<p style="color: #888; margin-top: 0;">{timestamp}</p>')

    # ── Top-line numbers ──
    html_parts.append(f'<h2 style="{style_h2}">Top-Line Numbers</h2>')
    html_parts.append('<div>')
    for label, value in [
        ("Active Campaigns", active_campaigns),
        ("Total Sent", f"{total_sent:,}"),
        ("Total Contacted", f"{total_contacted:,}"),
        ("Avg Open Rate", avg_open),
        ("Avg Reply Rate", avg_reply),
    ]:
        html_parts.append(
            f'<div style="{style_stat_box}">'
            f'<span style="{style_stat_num}">{value}</span>'
            f'<span style="{style_stat_label}">{label}</span>'
            f'</div>'
        )
    html_parts.append('</div>')

    # ── vs Last Week ──
    html_parts.append(f'<h2 style="{style_h2}">vs Last Week</h2>')
    if last_week_row and len(last_week_row) >= 5:
        try:
            lw_sent = int(last_week_row[2])
            lw_open = float(last_week_row[3].replace("%", ""))
            lw_reply = float(last_week_row[4].replace("%", ""))

            def delta_arrow(current, previous):
                diff = current - previous
                if abs(diff) < 0.1:
                    return f'<span style="{style_neutral}">→ no change</span>'
                arrow = "↑" if diff > 0 else "↓"
                color = style_good if diff > 0 else style_bad
                return f'<span style="{color}">{arrow} {abs(diff):.1f}</span>'

            def delta_arrow_int(current, previous):
                diff = current - previous
                if diff == 0:
                    return f'<span style="{style_neutral}">→ no change</span>'
                arrow = "↑" if diff > 0 else "↓"
                color = style_good if diff > 0 else style_bad
                return f'<span style="{color}">{arrow} {abs(diff):,}</span>'

            html_parts.append('<table style="font-size: 14px; margin-top: 4px;">')
            html_parts.append(f'<tr><td style="padding: 4px 12px 4px 0;">Sent</td><td><strong>{total_sent:,}</strong> {delta_arrow_int(total_sent, lw_sent)}</td></tr>')
            html_parts.append(f'<tr><td style="padding: 4px 12px 4px 0;">Avg Open Rate</td><td><strong>{avg_open}</strong> {delta_arrow(rate_value(avg_open), lw_open)}</td></tr>')
            html_parts.append(f'<tr><td style="padding: 4px 12px 4px 0;">Avg Reply Rate</td><td><strong>{avg_reply}</strong> {delta_arrow(rate_value(avg_reply), lw_reply)}</td></tr>')
            html_parts.append('</table>')
        except (ValueError, IndexError):
            html_parts.append('<p style="color: #888;">Could not parse last week\'s data for comparison.</p>')
    else:
        html_parts.append('<p style="color: #888;">No previous week data available for comparison.</p>')

    # ── Top 3 Performers ──
    html_parts.append(f'<h2 style="{style_h2}">Top 3 Performers (by Open Rate)</h2>')
    top3 = [r for r in rows if rate_value(r["open_rate"]) > 0][:3]
    if top3:
        html_parts.append(f'<table style="{style_table}">')
        html_parts.append(f'<tr><th style="{style_th}">Campaign</th><th style="{style_th}">Open Rate</th><th style="{style_th}">Reply Rate</th><th style="{style_th}">Contacted</th></tr>')
        for r in top3:
            html_parts.append(
                f'<tr><td style="{style_td}">{r["name"]}</td>'
                f'<td style="{style_td}"><strong>{r["open_rate"]}</strong></td>'
                f'<td style="{style_td}">{r["reply_rate"]}</td>'
                f'<td style="{style_td}">{r["contacted"]:,}</td></tr>'
            )
        html_parts.append('</table>')
    else:
        html_parts.append('<p style="color: #888;">No campaigns with opens yet.</p>')

    # ── What Went Well ──
    html_parts.append(f'<h2 style="{style_h2}">What Went Well</h2>')
    well_items = []
    for r in rows:
        if r["contacted"] > 30 and rate_value(r["open_rate"]) > 50:
            well_items.append(f'<strong>{r["name"]}</strong> — {r["open_rate"]} open rate ({r["contacted"]:,} contacted)')
        if r["contacted"] > 30 and rate_value(r["reply_rate"]) > 10:
            well_items.append(f'<strong>{r["name"]}</strong> — {r["reply_rate"]} reply rate ({r["replies"]} replies)')
    if well_items:
        html_parts.append('<ul style="margin: 4px 0; padding-left: 20px;">')
        for item in well_items:
            html_parts.append(f'<li style="margin-bottom: 4px;">{item}</li>')
        html_parts.append('</ul>')
    else:
        html_parts.append('<p style="color: #888;">No standout performers this week (thresholds: >50% open or >10% reply with 30+ contacted).</p>')

    # ── Needs Attention ──
    html_parts.append(f'<h2 style="{style_h2}">Needs Attention</h2>')
    attention_items = []
    for r in rows:
        if rate_value(r["bounce_rate"]) > 5:
            attention_items.append(f'<span style="{style_bad}">⚠</span> <strong>{r["name"]}</strong> — {r["bounce_rate"]} bounce rate (check list quality)')
        if r["contacted"] >= 100 and r["replies"] == 0:
            attention_items.append(f'<span style="{style_bad}">⚠</span> <strong>{r["name"]}</strong> — {r["contacted"]:,} contacted, 0 replies (review copy)')
        if r["contacted"] >= 30 and rate_value(r["open_rate"]) > 30 and rate_value(r["reply_rate"]) < 3:
            attention_items.append(f'<span style="{style_bad}">⚠</span> <strong>{r["name"]}</strong> — {r["open_rate"]} opens but only {r["reply_rate"]} replies (CTA may need work)')
    if attention_items:
        html_parts.append('<ul style="margin: 4px 0; padding-left: 20px;">')
        for item in attention_items:
            html_parts.append(f'<li style="margin-bottom: 4px;">{item}</li>')
        html_parts.append('</ul>')
    else:
        html_parts.append('<p style="color: #2b8a3e;">Nothing flagged this week.</p>')

    # ── Unexpected ──
    unexpected_items = []
    for r in rows:
        if r["auto_replies"] > r["replies"] and r["auto_replies"] > 0:
            unexpected_items.append(
                f'<strong>{r["name"]}</strong> — {r["auto_replies"]} auto-replies vs {r["replies"]} manual replies '
                f'(may indicate OOO-heavy list or wrong audience)'
            )
    if unexpected_items:
        html_parts.append(f'<h2 style="{style_h2}">Unexpected</h2>')
        html_parts.append('<ul style="margin: 4px 0; padding-left: 20px;">')
        for item in unexpected_items:
            html_parts.append(f'<li style="margin-bottom: 4px;">{item}</li>')
        html_parts.append('</ul>')

    # ── Spreadsheet link ──
    html_parts.append(f'<h2 style="{style_h2}">Full Data</h2>')
    sheet_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    html_parts.append(f'<p><a href="{sheet_url}" style="color: #1971c2;">Open Campaign Dashboard in Google Sheets</a></p>')

    html_parts.append('</div>')
    return "\n".join(html_parts)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%b %d, %Y")
    print(f"=== WEEKLY INSTANTLY REPORT — {timestamp} ===")

    # 1. Get Google access tokens
    print("Getting Google access tokens...")
    try:
        gmail_token = get_access_token(GMAIL_REFRESH_TOKEN)
        sheets_token = get_access_token(SHEETS_REFRESH_TOKEN)
        print("  Gmail token: OK")
        print("  Sheets token: OK")
    except Exception as e:
        print(f"FATAL: Could not get Google access tokens: {e}")
        return

    # 2. Read last week's data from Update Log
    print("Reading last week's data from Update Log...")
    last_week_row = read_last_week(sheets_token)
    if last_week_row:
        print(f"  Last update: {last_week_row[0] if last_week_row else 'N/A'}")
    else:
        print("  No previous data found (first run)")

    # 3. Fetch Instantly campaign data
    print("Fetching Instantly campaigns...")
    try:
        campaigns = fetch_campaigns()
        print(f"  Found {len(campaigns)} campaigns")
    except Exception as e:
        print(f"ERROR: Failed to fetch Instantly campaigns: {e}")
        # Try to send error notification email
        try:
            error_html = (
                '<div style="font-family: sans-serif; font-size: 14px; color: #333;">'
                f'<h2 style="color: #e03131;">Weekly Instantly Report — Error</h2>'
                f'<p>Failed to fetch campaign data from Instantly API.</p>'
                f'<p>Error: {str(e)[:200]}</p>'
                f'<p>Time: {timestamp}</p>'
                '</div>'
            )
            send_email(gmail_token, RECIPIENT_EMAIL, f"Weekly Instantly Report — ERROR — {timestamp}", error_html)
            print("  Error notification email sent")
        except Exception as email_err:
            print(f"  Could not send error email: {email_err}")
        return

    # Build campaign data rows
    print("Fetching per-campaign analytics...")
    rows = build_campaign_rows(campaigns)
    print(f"  Processed {len(rows)} campaigns (after exclusions)")

    if not rows:
        print("WARNING: No campaign data to report")

    # 4. Update Google Sheets
    print("Updating Google Sheets...")

    # Clear and rewrite Campaign Dashboard
    try:
        sheets_clear(sheets_token, "'Campaign Dashboard'!A1:R200")
        sheet_values = [DASHBOARD_HEADERS] + [row_to_sheet_values(r) for r in rows]
        sheets_update(sheets_token, f"'Campaign Dashboard'!A1:R{len(sheet_values)}", sheet_values)
        print(f"  Campaign Dashboard: {len(rows)} rows written")
    except Exception as e:
        print(f"  WARNING: Failed to update Campaign Dashboard: {e}")

    # Append to Update Log
    try:
        log_row = build_update_log_row(rows, timestamp)
        sheets_append(sheets_token, "'Update Log'!A:F", [log_row])
        print(f"  Update Log: row appended")
    except Exception as e:
        print(f"  WARNING: Failed to append to Update Log: {e}")

    # 5. Compose email
    print("Composing email...")
    subject = f"Weekly Instantly Report — {timestamp}"
    html_body = compose_email_html(rows, last_week_row, timestamp)

    # 6. Send email
    print("Sending email...")
    try:
        msg_id = send_email(gmail_token, RECIPIENT_EMAIL, subject, html_body)
        print(f"  Email sent: {msg_id}")
    except Exception as e:
        print(f"  ERROR: Failed to send email: {e}")

    # 7. Summary
    total_sent = sum(r["sent"] for r in rows)
    total_contacted = sum(r["contacted"] for r in rows)
    total_opened = sum(r["opened"] for r in rows)
    total_replies = sum(r["replies"] for r in rows)
    print(f"\n=== SUMMARY ===")
    print(f"Campaigns tracked: {len(rows)}")
    print(f"Total sent: {total_sent:,}")
    print(f"Total contacted: {total_contacted:,}")
    print(f"Avg open rate: {compute_rate(total_opened, total_contacted)}")
    print(f"Avg reply rate: {compute_rate(total_replies, total_contacted)}")
    print(f"Report sent to: {RECIPIENT_EMAIL}")


if __name__ == "__main__":
    main()
