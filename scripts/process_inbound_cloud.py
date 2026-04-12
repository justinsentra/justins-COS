#!/usr/bin/env python3
"""
Inbound Lead Processor — stdlib only (no pip install needed).
Polls Attio for new demo signups, filters US/CA/GB, creates Gmail drafts.
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

ATTIO_API_KEY = "0cc8623d32a014cf0b8c074a0f8bf97e5ef8cfec843307b5ea70b3b4d44ed489"
GOOGLE_CLIENT_ID = "REDACTED_GOOGLE_CLIENT_ID"
GOOGLE_CLIENT_SECRET = "REDACTED_GOOGLE_CLIENT_SECRET"
GMAIL_REFRESH_TOKEN = "REDACTED_REFRESH_TOKEN"
SHEETS_REFRESH_TOKEN = "REDACTED_REFRESH_TOKEN"
SHEET_ID = "1GCrRrEM8uT-m40PTkMXhmqjLDYgt_b0qk7NSD0B4VUk"
ATTIO_LIST_ID = "8fb5ba5a-0132-438f-aa77-0cadfec0e667"
SENDER_EMAIL = "justin@sentra.app"
QUALIFIED_COUNTRIES = {"US", "CA", "GB"}

SPAM_KEYWORDS = [
    "i help businesses", "book meetings for", "targeted outreach",
    "freelance", "your project caught our attention", "schedule a time with me",
    "hack4brahma", "dealvora",
]

EMAIL_HTML = (
    '<div dir="ltr">'
    '<div>Hi {first_name},</div>'
    '<div><br></div>'
    '<div>Thanks for reaching out. I\'d love to learn more about how you\'re '
    'operating at {company_name} and explore a few potential use cases for Sentra.</div>'
    '<div><br></div>'
    '<div>Would you be open to a quick meeting in the coming weeks? If it\'s '
    'easier for you, here\'s a link to my calendar: '
    '<a href="https://calendly.com/justin-sentra/intro">'
    'https://calendly.com/justin-sentra/intro</a></div>'
    '<div><br></div>'
    '<div>Best,</div>'
    '<div>Justin</div>'
    '</div>'
)

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


# ── Attio API ─────────────────────────────────────────────────────────────────

def attio_headers():
    return {"Authorization": f"Bearer {ATTIO_API_KEY}", "Content-Type": "application/json"}


def fetch_all_entries():
    """Fetch all entries from the Attio list."""
    url = f"https://api.attio.com/v2/lists/{ATTIO_LIST_ID}/entries/query"
    entries = []
    offset = None
    while True:
        body = {"limit": 50}
        if offset:
            body["offset"] = offset
        result = api_request(url, data=body, headers=attio_headers())
        batch = result.get("data", [])
        entries.extend(batch)
        if len(batch) < 50:
            break
        offset = (offset or 0) + 50
    return entries


def get_person(record_id):
    """Get person record from Attio."""
    url = f"https://api.attio.com/v2/objects/people/records/{record_id}"
    return api_request(url, headers=attio_headers(), method="GET")


# ── Entry parsing ─────────────────────────────────────────────────────────────

def parse_entry(entry):
    """Extract useful fields from an Attio list entry."""
    vals = entry.get("entry_values", {})

    country = None
    cv = vals.get("country_6", [])
    if cv and cv[0].get("country_code"):
        country = cv[0]["country_code"]

    motivation = ""
    mv = vals.get("motivations", [])
    if mv and mv[0].get("value"):
        motivation = mv[0]["value"]

    size = ""
    sv = vals.get("company_size", [])
    if sv and sv[0].get("option", {}).get("title"):
        size = sv[0]["option"]["title"]

    return {
        "entry_id": entry["id"]["entry_id"],
        "parent_record_id": entry.get("parent_record_id"),
        "created_at": entry.get("created_at", ""),
        "country": country,
        "motivation": motivation,
        "size": size,
    }


def parse_person(data):
    """Extract name/email/company from person record."""
    vals = data.get("data", {}).get("values", {})

    first = ""
    nv = vals.get("name", [])
    if nv:
        first = nv[0].get("first_name", "")

    last = ""
    if nv:
        last = nv[0].get("last_name", "")

    email = ""
    ev = vals.get("email_addresses", [])
    if ev:
        email = ev[0].get("email_address", "")

    company_id = ""
    cv = vals.get("company", [])
    if cv:
        company_id = cv[0].get("target_record_id", "")

    return {
        "first_name": first or "there",
        "last_name": last or "",
        "full_name": f"{first} {last}".strip() or "Unknown",
        "email": email,
        "company_id": company_id,
    }


def get_company_name(company_id):
    """Get company name from Attio."""
    if not company_id:
        return "your company"
    try:
        url = f"https://api.attio.com/v2/objects/companies/records/{company_id}"
        data = api_request(url, headers=attio_headers(), method="GET")
        vals = data.get("data", {}).get("values", {})
        nv = vals.get("name", [])
        if nv:
            return nv[0].get("value", "your company") or "your company"
    except Exception:
        pass
    return "your company"


def is_spam(motivation):
    """Check if motivation text is spam."""
    if len(motivation.strip()) < 10:
        return True
    lower = motivation.lower()
    for kw in SPAM_KEYWORDS:
        if kw in lower:
            return True
    if "calendly.com" in lower and "calendly.com/justin-sentra" not in lower:
        return True
    return False


# ── Gmail draft ───────────────────────────────────────────────────────────────

def create_draft(gmail_token, to_email, subject, html_body):
    """Create a Gmail draft."""
    msg = MIMEMultipart("alternative")
    msg["to"] = to_email
    msg["from"] = SENDER_EMAIL
    msg["subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    url = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
    result = api_request(url, data={"message": {"raw": raw}},
                         headers={"Authorization": f"Bearer {gmail_token}"})
    return result.get("id", ""), result.get("message", {}).get("threadId", "")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    print(f"=== INBOUND PROCESSOR RUN — {now} ===")

    # Get access tokens
    print("Getting Google access tokens...")
    gmail_token = get_access_token(GMAIL_REFRESH_TOKEN)
    sheets_token = get_access_token(SHEETS_REFRESH_TOKEN)
    print("  Gmail token: OK")
    print("  Sheets token: OK")

    # Read last_run
    result = sheets_get(sheets_token, "Config!B2")
    last_run = ""
    if "values" in result and result["values"] and result["values"][0]:
        last_run = result["values"][0][0]
    print(f"Last run: {last_run or 'FIRST RUN'}")

    # Read existing leads (for dedup)
    leads_result = sheets_get(sheets_token, "Leads!A:A")
    existing_ids = set()
    if "values" in leads_result:
        for row in leads_result["values"][1:]:  # skip header
            if row:
                existing_ids.add(row[0])
    print(f"Existing leads: {len(existing_ids)}")

    # Fetch all Attio entries
    print("Fetching Attio entries...")
    entries = fetch_all_entries()
    print(f"  Total entries in list: {len(entries)}")

    # Filter new entries
    new_entries = []
    for entry in entries:
        parsed = parse_entry(entry)
        if parsed["entry_id"] in existing_ids:
            continue
        if last_run and parsed["created_at"] <= last_run:
            continue
        new_entries.append(parsed)

    print(f"  New entries since last_run: {len(new_entries)}")

    # Process
    stats = {"qualified": 0, "skipped_country": 0, "skipped_spam": 0, "drafted": 0, "errors": 0}

    for entry in new_entries:
        # Country filter
        if entry["country"] not in QUALIFIED_COUNTRIES:
            stats["skipped_country"] += 1
            print(f"  SKIP (country={entry['country']}): {entry['entry_id'][:8]}...")
            sheets_append(sheets_token, "Leads!A:L", [[
                entry["entry_id"], "", "", "", "", entry["country"] or "unknown",
                "skipped_country", "0", "true", "", "", now,
            ]])
            continue

        # Spam filter
        if is_spam(entry["motivation"]):
            stats["skipped_spam"] += 1
            print(f"  SKIP (spam): {entry['entry_id'][:8]}... motivation='{entry['motivation'][:50]}'")
            sheets_append(sheets_token, "Leads!A:L", [[
                entry["entry_id"], "", "", "", "", entry["country"],
                "skipped_spam", "0", "true", "", "", now,
            ]])
            continue

        # Get person details
        try:
            person_data = get_person(entry["parent_record_id"])
            person = parse_person(person_data)
        except Exception as e:
            print(f"  ERROR getting person {entry['parent_record_id']}: {e}")
            stats["errors"] += 1
            continue

        if not person["email"]:
            print(f"  SKIP (no email): {person['full_name']}")
            stats["errors"] += 1
            continue

        # Get company name
        company_name = get_company_name(person["company_id"])

        # Create Gmail draft
        try:
            html = EMAIL_HTML.format(
                first_name=person["first_name"],
                company_name=company_name,
            )
            draft_id, thread_id = create_draft(
                gmail_token, person["email"], "Sentra Exploratory Call", html
            )
            stats["drafted"] += 1
            print(f"  DRAFT: {person['full_name']} ({person['email']}) — {company_name} — {entry['country']}")
        except Exception as e:
            print(f"  ERROR creating draft for {person['email']}: {e}")
            stats["errors"] += 1
            continue

        # Log to sheet
        sheets_append(sheets_token, "Leads!A:L", [[
            entry["entry_id"], person["full_name"], person["first_name"],
            person["email"], company_name, entry["country"],
            "qualified", "1", "false", now, thread_id, now,
        ]])
        stats["qualified"] += 1

    # Update last_run
    sheets_update(sheets_token, "Config!B2", [[now]])

    # Summary
    print(f"\n=== SUMMARY ===")
    print(f"New entries: {len(new_entries)}")
    print(f"Qualified + drafted: {stats['qualified']}")
    print(f"Skipped (country): {stats['skipped_country']}")
    print(f"Skipped (spam): {stats['skipped_spam']}")
    print(f"Errors: {stats['errors']}")
    print(f"last_run updated to: {now}")


if __name__ == "__main__":
    main()
