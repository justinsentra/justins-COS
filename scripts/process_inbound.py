"""
Inbound Lead Processor — Automated Email Drafting

Pulls new demo signups from Attio, qualifies by country (US/CA/GB),
and creates Gmail drafts (visible in Superhuman) for qualified leads.
Manages a 3-email follow-up sequence with reply/booking detection.

State tracked in Google Sheets. Runs on GitHub Actions cron.
"""

import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ── Constants ──────────────────────────────────────────────────────────────────

ATTIO_LIST_ID = "8fb5ba5a-0132-438f-aa77-0cadfec0e667"
SHEET_ID = os.environ.get("SHEET_ID", "1GCrRrEM8uT-m40PTkMXhmqjLDYgt_b0qk7NSD0B4VUk")
QUALIFIED_COUNTRIES = {"US", "CA", "GB"}
EMAIL_SUBJECT = "Sentra Exploratory Call"
FOLLOW_UP_DELAY_DAYS = 3
SENDER_EMAIL = "justin@sentra.app"

# ── Email Templates ───────────────────────────────────────────────────────────

SIGNATURE = (
    '<div>Justin Cheng</div>'
    '<div>Head of Strategy &amp; Operations, '
    '<a href="https://www.sentra.app/">Sentra</a></div>'
    '<div><a href="https://calendly.com/justin-sentra/intro">Book a meeting</a></div>'
)

EMAIL_TEMPLATES = {
    1: (
        '<div dir="ltr">'
        '<div>Hi {first_name},</div>'
        '<div><br></div>'
        "<div>Thanks for reaching out. I'd love to learn more about how you're "
        "operating at {company_name} and explore a few potential use cases for Sentra.</div>"
        '<div><br></div>'
        "<div>Would you be open to a quick meeting in the coming weeks? If it's "
        'easier for you, here\'s a link to my calendar: '
        '<a href="https://calendly.com/justin-sentra/intro">'
        "https://calendly.com/justin-sentra/intro</a></div>"
        '<div><br></div>'
        '<div>Best,</div><div><br></div><div><br></div>'
        f'{SIGNATURE}'
        '</div>'
    ),
    2: (
        '<div dir="ltr">'
        '<div>Hi {first_name},</div>'
        '<div><br></div>'
        "<div>Just checking in to see if you might have some time to chat soon. "
        "I know how busy things get so I'm happy to work around your availability. "
        "Let me know when works best and I can send over an invite.</div>"
        '<div><br></div>'
        '<div>Best,</div><div><br></div><div><br></div>'
        f'{SIGNATURE}'
        '</div>'
    ),
    3: (
        '<div dir="ltr">'
        '<div>Hi {first_name},</div>'
        '<div><br></div>'
        "<div>Just wanted to send one last note - I know things get busy, so I "
        "won't keep nudging.</div>"
        '<div><br></div>'
        '<div>If it makes sense to chat down the line, feel free to grab time '
        '<a href="https://calendly.com/justin-sentra/intro">here</a> anytime. '
        "Otherwise, wishing you all the best and appreciate you taking a look!</div>"
        '<div><br></div>'
        '<div>Best,</div><div><br></div><div><br></div>'
        f'{SIGNATURE}'
        '</div>'
    ),
}

# ── Auth & API Clients ────────────────────────────────────────────────────────


def get_attio_headers():
    api_key = os.environ["ATTIO_API_KEY"]
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def get_google_creds():
    """Build Google credentials from environment variables."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise RuntimeError("GOOGLE_CREDENTIALS env var not set")

    creds_data = json.loads(creds_json)

    # Support both service account and OAuth2 refresh token
    if "type" in creds_data and creds_data["type"] == "authorized_user":
        creds = Credentials.from_authorized_user_info(
            creds_data,
            scopes=[
                "https://www.googleapis.com/auth/gmail.compose",
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/calendar.readonly",
            ],
        )
    else:
        # OAuth2 refresh token format
        creds = Credentials(
            token=None,
            refresh_token=creds_data["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=creds_data["client_id"],
            client_secret=creds_data["client_secret"],
            scopes=[
                "https://www.googleapis.com/auth/gmail.compose",
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/calendar.readonly",
            ],
        )

    if not creds.valid:
        creds.refresh(Request())

    return creds


# ── Google Sheets State ───────────────────────────────────────────────────────


def read_last_run(sheets):
    result = sheets.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="Config!B2"
    ).execute()
    values = result.get("values", [])
    if values and values[0] and values[0][0]:
        return values[0][0]
    return None


def write_last_run(sheets, timestamp):
    sheets.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range="Config!B2",
        valueInputOption="RAW",
        body={"values": [[timestamp]]},
    ).execute()


def read_processed_entries(sheets):
    result = sheets.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="Leads!A:L"
    ).execute()
    rows = result.get("values", [])
    if len(rows) <= 1:
        return []
    return rows[1:]  # skip header


def append_lead_row(sheets, row):
    sheets.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="Leads!A:L",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def update_lead_row(sheets, row_index, updates):
    """Update specific cells in an existing lead row (1-indexed, header is row 1)."""
    for col, value in updates.items():
        sheets.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=f"Leads!{col}{row_index + 2}",
            valueInputOption="RAW",
            body={"values": [[value]]},
        ).execute()


# ── Attio API ─────────────────────────────────────────────────────────────────


def fetch_list_entries(headers, last_run=None):
    """Fetch entries from Attio list, optionally filtered by created_at."""
    url = f"https://api.attio.com/v2/lists/{ATTIO_LIST_ID}/entries/query"
    body = {"limit": 50}

    if last_run:
        body["filter"] = {
            "created_at": {"gt": last_run}
        }

    entries = []
    while True:
        resp = requests.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        entries.extend(data.get("data", []))

        next_cursor = data.get("next_page_cursor")
        if not next_cursor:
            break
        body["page_cursor"] = next_cursor

    return entries


def get_person_details(headers, record_id):
    """Get person record details from Attio."""
    url = f"https://api.attio.com/v2/objects/people/records/{record_id}"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json().get("data", {})


def extract_entry_fields(entry):
    """Extract relevant fields from an Attio list entry."""
    values = entry.get("entry_values", {})

    country_code = None
    country_vals = values.get("country_6", [])
    if country_vals and country_vals[0].get("country_code"):
        country_code = country_vals[0]["country_code"]

    company_size = None
    size_vals = values.get("company_size", [])
    if size_vals and size_vals[0].get("option", {}).get("title"):
        company_size = size_vals[0]["option"]["title"]

    motivations = None
    motiv_vals = values.get("motivations", [])
    if motiv_vals and motiv_vals[0].get("value"):
        motivations = motiv_vals[0]["value"]

    return {
        "entry_id": entry["id"]["entry_id"],
        "parent_record_id": entry.get("parent_record_id"),
        "country_code": country_code,
        "company_size": company_size,
        "motivations": motivations,
        "created_at": entry.get("created_at"),
    }


def extract_person_fields(person):
    """Extract name, email, company from a person record."""
    values = person.get("values", {})

    first_name = None
    name_vals = values.get("first_name", [])
    if name_vals:
        first_name = name_vals[0].get("first_name") or name_vals[0].get("value")

    last_name = None
    last_vals = values.get("last_name", [])
    if last_vals:
        last_name = last_vals[0].get("last_name") or last_vals[0].get("value")

    email = None
    email_vals = values.get("email_addresses", [])
    if email_vals:
        email = email_vals[0].get("email_address") or email_vals[0].get("value")

    company_name = None
    company_vals = values.get("company", values.get("primary_company", []))
    if company_vals:
        company_name = company_vals[0].get("value")

    person_name = " ".join(filter(None, [first_name, last_name])) or "Unknown"

    return {
        "first_name": first_name or "there",
        "person_name": person_name,
        "email": email,
        "company_name": company_name or "your company",
    }


# ── Gmail Draft Creation ─────────────────────────────────────────────────────


def create_gmail_draft(gmail, to_email, subject, html_body, thread_id=None):
    """Create a Gmail draft. Returns draft_id and thread_id."""
    message = MIMEMultipart("alternative")
    message["to"] = to_email
    message["from"] = SENDER_EMAIL
    message["subject"] = subject

    html_part = MIMEText(html_body, "html")
    message.attach(html_part)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    body = {"message": {"raw": raw}}
    if thread_id:
        body["message"]["threadId"] = thread_id

    draft = gmail.users().drafts().create(userId="me", body=body).execute()

    return {
        "draft_id": draft["id"],
        "thread_id": draft["message"]["threadId"],
    }


# ── Follow-Up Logic ──────────────────────────────────────────────────────────


def check_for_reply(gmail, thread_id):
    """Check if a Gmail thread has replies beyond the original message."""
    thread = gmail.users().threads().get(userId="me", id=thread_id).execute()
    messages = thread.get("messages", [])
    if len(messages) <= 1:
        return False
    # Check if any message is from someone other than us
    for msg in messages[1:]:
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        from_addr = headers.get("from", "")
        if SENDER_EMAIL not in from_addr:
            return True
    return False


def check_for_booking(calendar, email):
    """Check Google Calendar for upcoming events with this attendee."""
    now = datetime.now(timezone.utc).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

    events_result = calendar.events().list(
        calendarId="primary",
        timeMin=now,
        timeMax=future,
        singleEvents=True,
        maxResults=100,
    ).execute()

    for event in events_result.get("items", []):
        attendees = event.get("attendees", [])
        for attendee in attendees:
            if attendee.get("email", "").lower() == email.lower():
                return True
    return False


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    now = datetime.now(timezone.utc).isoformat()
    print(f"INBOUND PROCESSOR RUN — {now}")

    # Init clients
    attio_headers = get_attio_headers()
    creds = get_google_creds()
    sheets = build("sheets", "v4", credentials=creds)
    gmail = build("gmail", "v1", credentials=creds)
    calendar = build("calendar", "v3", credentials=creds)

    # Read state
    last_run = read_last_run(sheets)
    processed = read_processed_entries(sheets)
    processed_ids = {row[0] for row in processed if row}

    print(f"Last run: {last_run or 'first run'}")
    print(f"Previously processed: {len(processed_ids)} entries")

    # ── Process New Entries ────────────────────────────────────────────────
    entries = fetch_list_entries(attio_headers, last_run)
    new_entries = [e for e in entries if e["id"]["entry_id"] not in processed_ids]

    stats = {"new": len(new_entries), "qualified": 0, "skipped": 0, "drafted": 0}
    print(f"New entries found: {len(new_entries)}")

    for entry in new_entries:
        fields = extract_entry_fields(entry)
        entry_id = fields["entry_id"]

        # Country filter
        if fields["country_code"] not in QUALIFIED_COUNTRIES:
            print(f"  SKIP: {entry_id} — country {fields['country_code']}")
            append_lead_row(sheets, [
                entry_id, "", "", "", "", fields["country_code"] or "unknown",
                "skipped", "0", "true", "", "", now,
            ])
            stats["skipped"] += 1
            continue

        # Get person details
        person = get_person_details(attio_headers, fields["parent_record_id"])
        person_fields = extract_person_fields(person)

        if not person_fields["email"]:
            print(f"  SKIP: {entry_id} — no email")
            append_lead_row(sheets, [
                entry_id, person_fields["person_name"], person_fields["first_name"],
                "", "", fields["country_code"], "skipped_no_email", "0", "true", "", "", now,
            ])
            stats["skipped"] += 1
            continue

        # Build and create draft
        html = EMAIL_TEMPLATES[1].format(
            first_name=person_fields["first_name"],
            company_name=person_fields["company_name"],
        )
        draft = create_gmail_draft(gmail, person_fields["email"], EMAIL_SUBJECT, html)

        print(f"  DRAFT: {person_fields['person_name']} ({person_fields['email']}) — {fields['country_code']}")

        append_lead_row(sheets, [
            entry_id, person_fields["person_name"], person_fields["first_name"],
            person_fields["email"], person_fields["company_name"],
            fields["country_code"], "qualified", "1", "false",
            now, draft["thread_id"], now,
        ])
        stats["qualified"] += 1
        stats["drafted"] += 1

    # ── Follow-Up Sequence ────────────────────────────────────────────────
    followup_stats = {"checked": 0, "replied": 0, "booked": 0, "followup_drafted": 0}

    # Re-read to get latest state including newly added rows
    processed = read_processed_entries(sheets)

    for idx, row in enumerate(processed):
        if len(row) < 12:
            continue

        disposition = row[6] if len(row) > 6 else ""
        sequence_step = int(row[7]) if len(row) > 7 and row[7].isdigit() else 0
        sequence_complete = row[8].lower() == "true" if len(row) > 8 else False
        email_sent_at = row[9] if len(row) > 9 else ""
        thread_id = row[10] if len(row) > 10 else ""

        if disposition != "qualified" or sequence_complete or not email_sent_at or not thread_id:
            continue

        # Check if enough time has passed
        try:
            sent_time = datetime.fromisoformat(email_sent_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue

        if datetime.now(timezone.utc) - sent_time < timedelta(days=FOLLOW_UP_DELAY_DAYS):
            continue

        followup_stats["checked"] += 1
        email = row[3]
        first_name = row[2]

        # Check for reply
        try:
            if check_for_reply(gmail, thread_id):
                print(f"  REPLIED: {row[1]} ({email})")
                update_lead_row(sheets, idx, {"I": "true"})
                followup_stats["replied"] += 1
                continue
        except Exception as e:
            print(f"  WARN: Could not check reply for {email}: {e}")

        # Check for booking
        try:
            if check_for_booking(calendar, email):
                print(f"  BOOKED: {row[1]} ({email})")
                update_lead_row(sheets, idx, {"I": "true"})
                followup_stats["booked"] += 1
                continue
        except Exception as e:
            print(f"  WARN: Could not check booking for {email}: {e}")

        # Draft next email in sequence
        next_step = sequence_step + 1
        if next_step > 3:
            update_lead_row(sheets, idx, {"I": "true"})
            continue

        html = EMAIL_TEMPLATES[next_step].format(
            first_name=first_name,
            company_name=row[4] if len(row) > 4 else "your company",
        )
        draft = create_gmail_draft(gmail, email, f"Re: {EMAIL_SUBJECT}", html, thread_id)

        print(f"  FOLLOW-UP {next_step}: {row[1]} ({email})")

        updates = {"H": str(next_step), "J": now}
        if next_step >= 3:
            updates["I"] = "true"
        update_lead_row(sheets, idx, updates)
        followup_stats["followup_drafted"] += 1

    # ── Update last_run ───────────────────────────────────────────────────
    write_last_run(sheets, now)

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\nSUMMARY")
    print(f"New entries: {stats['new']} (qualified: {stats['qualified']}, skipped: {stats['skipped']})")
    print(f"Drafts created: {stats['drafted']}")
    print(f"Follow-ups checked: {followup_stats['checked']}")
    print(f"  Replied: {followup_stats['replied']}")
    print(f"  Booked: {followup_stats['booked']}")
    print(f"  Follow-up drafts: {followup_stats['followup_drafted']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
