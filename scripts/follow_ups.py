#!/usr/bin/env python3
"""
Autonomous follow-up drafter.

For each lead in the Leads sheet with sequence_complete=false, read the Gmail
thread (via thread_id), decide the thread's state, and either:
  - mark sequence_complete=true (recipient replied, or sequence exhausted), or
  - create a Gmail draft as reply-in-thread (Email 2 or Email 3 body).

Drafts appear natively in Superhuman's drafts folder.

Runs daily on GitHub Actions. Stdlib only.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
GMAIL_REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]
SHEETS_REFRESH_TOKEN = os.environ["SHEETS_REFRESH_TOKEN"]
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "justin@sentra.app")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

FOLLOW_UP_DELAY_DAYS = 3
MAX_SEQUENCE_STEP = 3


def email_2_html(first_name: str) -> str:
    return (
        '<div dir="ltr">'
        f'<div>Hi {first_name},</div>'
        '<div><br></div>'
        "<div>Just checking in to see if you might have some time to chat soon. "
        "I know how busy things get so I'm happy to work around your availability. "
        "Let me know when works best and I can send over an invite.</div>"
        '<div><br></div>'
        '<div>Best,</div>'
        '<div>Justin</div>'
        '</div>'
    )


def email_3_html(first_name: str) -> str:
    return (
        '<div dir="ltr">'
        f'<div>Hi {first_name},</div>'
        '<div><br></div>'
        "<div>Just wanted to send one last note - I know things get busy, so I won't keep nudging.</div>"
        '<div><br></div>'
        '<div>If it makes sense to chat down the line, feel free to grab time '
        '<a href="https://calendly.com/justin-sentra/intro">here</a> anytime. '
        "Otherwise, wishing you all the best and appreciate you taking a look!</div>"
        '<div><br></div>'
        '<div>Best,</div>'
        '<div>Justin</div>'
        '</div>'
    )


TEMPLATES = {2: email_2_html, 3: email_3_html}


def api_request(url, data=None, headers=None, method=None):
    headers = headers or {}
    if isinstance(data, dict):
        data = json.dumps(data).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} error for {url}: {body[:500]}", file=sys.stderr)
        raise


def form_post(url, params):
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_access_token(refresh_token):
    return form_post(
        "https://oauth2.googleapis.com/token",
        {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )["access_token"]


def sheets_get(token, range_):
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
        f"{urllib.parse.quote(range_)}"
    )
    return api_request(url, headers={"Authorization": f"Bearer {token}"})


def sheets_update(token, range_, values):
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/"
        f"{urllib.parse.quote(range_)}?valueInputOption=RAW"
    )
    return api_request(
        url,
        data={"values": values},
        headers={"Authorization": f"Bearer {token}"},
        method="PUT",
    )


def gmail_get_thread(token, thread_id):
    url = (
        f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}"
        "?format=metadata&metadataHeaders=From&metadataHeaders=Date"
        "&metadataHeaders=Subject&metadataHeaders=Message-Id"
        "&metadataHeaders=Auto-Submitted&metadataHeaders=Precedence"
    )
    return api_request(url, headers={"Authorization": f"Bearer {token}"})


def gmail_create_draft_reply(
    token, to_email, subject, html_body, thread_id, in_reply_to_message_id
):
    """Create a Gmail draft as a reply in the given thread."""
    msg = MIMEMultipart("alternative")
    msg["to"] = to_email
    msg["from"] = SENDER_EMAIL
    msg["subject"] = subject
    if in_reply_to_message_id:
        msg["In-Reply-To"] = in_reply_to_message_id
        msg["References"] = in_reply_to_message_id
    msg.attach(MIMEText(html_body, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    url = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
    return api_request(
        url,
        data={"message": {"raw": raw, "threadId": thread_id}},
        headers={"Authorization": f"Bearer {token}"},
    )


def parse_thread_state(thread: dict) -> dict:
    """
    Given a Gmail thread response, return:
      {
        "sent_count":     int,             # # of SENT messages
        "last_sent_at":   datetime | None, # most recent SENT timestamp
        "inbox_messages": list[dict],      # all INBOX (inbound) messages
        "last_message_id": str,            # Message-Id of last message (for In-Reply-To)
        "original_subject": str,           # Subject of first message (for Re: prefix)
      }
    """
    messages = thread.get("messages", [])
    sent = []
    inbox = []
    for m in messages:
        labels = set(m.get("labelIds", []))
        if "SENT" in labels:
            sent.append(m)
        if "INBOX" in labels:
            inbox.append(m)

    last_sent_at = None
    if sent:
        ms = max(int(m.get("internalDate", "0")) for m in sent)
        last_sent_at = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

    last_message_id = ""
    if messages:
        for h in messages[-1].get("payload", {}).get("headers", []):
            if h["name"].lower() == "message-id":
                last_message_id = h["value"]
                break

    original_subject = ""
    if messages:
        for h in messages[0].get("payload", {}).get("headers", []):
            if h["name"].lower() == "subject":
                original_subject = h["value"]
                break

    return {
        "sent_count": len(sent),
        "last_sent_at": last_sent_at,
        "inbox_messages": inbox,
        "last_message_id": last_message_id,
        "original_subject": original_subject,
    }


def has_genuine_reply(inbox_messages: list) -> bool:
    """
    Return True if the thread has a genuine reply from the recipient.
    Return False if the only inbox messages are auto-responders / OOO / bounces.

    Uses the RFC-standard Auto-Submitted and Precedence headers (RFC 3834).
    """
    for m in inbox_messages:
        headers = {
            h["name"].lower(): h["value"].lower()
            for h in m.get("payload", {}).get("headers", [])
        }
        if headers.get("auto-submitted", "no") != "no":
            continue
        if headers.get("precedence", "") in ("auto_reply", "bulk", "list"):
            continue
        return True
    return False


def decide_action(state: dict, current_step: int, now: datetime) -> str:
    """
    Pure function: given thread state + current step + now, return one of:
      "skip_not_sent"       — no SENT message yet; Justin hasn't sent Email 1
      "mark_complete_reply" — recipient replied
      "mark_complete_done"  — all N emails sent, sequence exhausted
      "draft_next"          — ready to draft next email
      "wait"                — sent recently, not yet 3 days
    """
    if state["sent_count"] == 0:
        return "skip_not_sent"

    if has_genuine_reply(state["inbox_messages"]):
        return "mark_complete_reply"

    age_days = (now - state["last_sent_at"]).days

    if state["sent_count"] >= MAX_SEQUENCE_STEP:
        if age_days >= FOLLOW_UP_DELAY_DAYS:
            return "mark_complete_done"
        return "wait"

    if age_days >= FOLLOW_UP_DELAY_DAYS:
        return "draft_next"
    return "wait"


def main():
    now = datetime.now(timezone.utc)
    print(f"=== FOLLOW-UP RUN — {now.isoformat()} === (dry_run={DRY_RUN})")

    gmail_token = get_access_token(GMAIL_REFRESH_TOKEN)
    sheets_token = get_access_token(SHEETS_REFRESH_TOKEN)

    leads = sheets_get(sheets_token, "Leads!A:L").get("values", [])
    if not leads or len(leads) < 2:
        print("No leads in sheet. Done.")
        return

    header, rows = leads[0], leads[1:]
    col = {name: idx for idx, name in enumerate(header)}

    stats = {
        "checked": 0, "skipped_not_sent": 0, "replied": 0,
        "drafted": 0, "exhausted": 0, "waiting": 0, "errors": 0,
    }
    updated_rows = []

    for i, row in enumerate(rows, start=2):
        row = row + [""] * (len(header) - len(row))

        if row[col["disposition"]] != "qualified":
            continue
        if (row[col["sequence_complete"]] or "").lower() == "true":
            continue
        thread_id = row[col["email_1_thread_id"]]
        if not thread_id:
            continue

        stats["checked"] += 1
        try:
            thread = gmail_get_thread(gmail_token, thread_id)
        except Exception as e:
            print(f"  ERROR getting thread {thread_id}: {e}")
            stats["errors"] += 1
            continue

        state = parse_thread_state(thread)
        current_step = int(row[col["sequence_step"]] or "0")
        action = decide_action(state, current_step, now)

        first_name = row[col["first_name"]] or "there"
        email = row[col["email"]]
        print(
            f"  [{email}] step={current_step} sent={state['sent_count']} "
            f"inbox={len(state['inbox_messages'])} → {action}"
        )

        if action == "skip_not_sent":
            stats["skipped_not_sent"] += 1
        elif action == "mark_complete_reply":
            stats["replied"] += 1
            updated_rows.append((i, col["sequence_complete"], "true"))
        elif action == "mark_complete_done":
            stats["exhausted"] += 1
            updated_rows.append((i, col["sequence_complete"], "true"))
        elif action == "wait":
            stats["waiting"] += 1
        elif action == "draft_next":
            next_step = state["sent_count"] + 1
            if next_step not in TEMPLATES:
                print(f"  No template for step {next_step} — marking complete")
                updated_rows.append((i, col["sequence_complete"], "true"))
                stats["exhausted"] += 1
                continue
            html = TEMPLATES[next_step](first_name)
            orig_subj = state["original_subject"] or "Sentra Exploratory Call"
            reply_subject = (
                orig_subj if orig_subj.lower().startswith("re:") else f"Re: {orig_subj}"
            )
            if DRY_RUN:
                print(f"  DRY_RUN: would draft Email {next_step} → {email} (subject: {reply_subject!r})")
            else:
                try:
                    gmail_create_draft_reply(
                        gmail_token,
                        to_email=email,
                        subject=reply_subject,
                        html_body=html,
                        thread_id=thread_id,
                        in_reply_to_message_id=state["last_message_id"],
                    )
                    stats["drafted"] += 1
                    updated_rows.append((i, col["sequence_step"], str(next_step)))
                except Exception as e:
                    print(f"  ERROR drafting follow-up for {email}: {e}")
                    stats["errors"] += 1

    if updated_rows and not DRY_RUN:
        def col_letter(idx):
            return chr(ord("A") + idx)
        for row_idx, col_idx, value in updated_rows:
            rng = f"Leads!{col_letter(col_idx)}{row_idx}"
            sheets_update(sheets_token, rng, [[value]])

    print("\n=== SUMMARY ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
