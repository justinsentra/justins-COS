"""Tests for follow_ups.py pure logic (no network)."""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest import TestCase, main

# Set required env vars so the module imports cleanly
for var in ("SHEET_ID", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
            "GMAIL_REFRESH_TOKEN", "SHEETS_REFRESH_TOKEN"):
    os.environ.setdefault(var, "test")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import follow_ups as fu


def make_msg(label, internal_date_ms, headers=None, snippet="hello"):
    return {
        "id": "m1",
        "internalDate": str(internal_date_ms),
        "labelIds": [label],
        "payload": {"headers": headers or []},
        "snippet": snippet,
    }


def make_headers(**kwargs):
    """Build a list of Gmail header dicts from kwargs."""
    return [{"name": k.replace("_", "-"), "value": v} for k, v in kwargs.items()]


class TestParseThreadState(TestCase):
    def test_empty_thread(self):
        state = fu.parse_thread_state({"messages": []})
        self.assertEqual(state["sent_count"], 0)
        self.assertIsNone(state["last_sent_at"])
        self.assertEqual(state["inbox_messages"], [])
        self.assertEqual(state["original_subject"], "")
        self.assertEqual(state["last_message_id"], "")

    def test_one_sent_no_inbox(self):
        ms = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)
        state = fu.parse_thread_state({"messages": [make_msg("SENT", ms)]})
        self.assertEqual(state["sent_count"], 1)
        self.assertEqual(state["last_sent_at"].year, 2026)
        self.assertEqual(state["inbox_messages"], [])

    def test_sent_and_inbox(self):
        ms1 = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)
        ms2 = int(datetime(2026, 4, 2, tzinfo=timezone.utc).timestamp() * 1000)
        state = fu.parse_thread_state({
            "messages": [make_msg("SENT", ms1), make_msg("INBOX", ms2)]
        })
        self.assertEqual(state["sent_count"], 1)
        self.assertEqual(len(state["inbox_messages"]), 1)

    def test_subject_and_message_id_extracted(self):
        ms = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)
        msg = make_msg("SENT", ms, headers=make_headers(
            Subject="Sentra Exploratory Call",
            Message_Id="<abc123@gmail.com>",
        ))
        state = fu.parse_thread_state({"messages": [msg]})
        self.assertEqual(state["original_subject"], "Sentra Exploratory Call")
        self.assertEqual(state["last_message_id"], "<abc123@gmail.com>")


class TestHasGenuineReply(TestCase):
    def test_empty_list_is_not_reply(self):
        self.assertFalse(fu.has_genuine_reply([]))

    def test_plain_inbox_message_is_reply(self):
        msg = make_msg("INBOX", 0, headers=make_headers(From="jane@acme.com"))
        self.assertTrue(fu.has_genuine_reply([msg]))

    def test_auto_submitted_is_not_reply(self):
        msg = make_msg("INBOX", 0, headers=make_headers(
            From="jane@acme.com",
            Auto_Submitted="auto-replied",
        ))
        self.assertFalse(fu.has_genuine_reply([msg]))

    def test_bulk_precedence_is_not_reply(self):
        msg = make_msg("INBOX", 0, headers=make_headers(
            From="newsletter@acme.com",
            Precedence="bulk",
        ))
        self.assertFalse(fu.has_genuine_reply([msg]))

    def test_genuine_reply_among_auto_replies_counts(self):
        auto = make_msg("INBOX", 0, headers=make_headers(Auto_Submitted="auto-replied"))
        real = make_msg("INBOX", 1, headers=make_headers(From="jane@acme.com"))
        self.assertTrue(fu.has_genuine_reply([auto, real]))

    def test_auto_submitted_no_still_counts_as_reply(self):
        # "Auto-Submitted: no" is the RFC 3834 way to explicitly mark NOT auto
        msg = make_msg("INBOX", 0, headers=make_headers(Auto_Submitted="no"))
        self.assertTrue(fu.has_genuine_reply([msg]))


class TestDecideAction(TestCase):
    def setUp(self):
        self.now = datetime(2026, 4, 15, tzinfo=timezone.utc)

    def _state(self, sent_count=0, days_ago=None, inbox_messages=None):
        return {
            "sent_count": sent_count,
            "last_sent_at": self.now - timedelta(days=days_ago) if days_ago is not None else None,
            "inbox_messages": inbox_messages or [],
            "last_message_id": "",
            "original_subject": "",
        }

    def test_skip_when_not_sent(self):
        self.assertEqual(fu.decide_action(self._state(), 1, self.now), "skip_not_sent")

    def test_wait_when_recently_sent(self):
        self.assertEqual(
            fu.decide_action(self._state(sent_count=1, days_ago=1), 1, self.now),
            "wait",
        )

    def test_draft_next_when_stale(self):
        self.assertEqual(
            fu.decide_action(self._state(sent_count=1, days_ago=4), 1, self.now),
            "draft_next",
        )

    def test_mark_complete_reply_even_when_stale(self):
        genuine = make_msg("INBOX", 0, headers=make_headers(From="jane@acme.com"))
        self.assertEqual(
            fu.decide_action(
                self._state(sent_count=1, days_ago=4, inbox_messages=[genuine]),
                1,
                self.now,
            ),
            "mark_complete_reply",
        )

    def test_auto_reply_does_not_mark_complete(self):
        auto = make_msg("INBOX", 0, headers=make_headers(Auto_Submitted="auto-replied"))
        self.assertEqual(
            fu.decide_action(
                self._state(sent_count=1, days_ago=4, inbox_messages=[auto]),
                1,
                self.now,
            ),
            "draft_next",
        )

    def test_exhausted_after_email_3(self):
        self.assertEqual(
            fu.decide_action(self._state(sent_count=3, days_ago=4), 3, self.now),
            "mark_complete_done",
        )

    def test_wait_when_email_3_recent(self):
        self.assertEqual(
            fu.decide_action(self._state(sent_count=3, days_ago=1), 3, self.now),
            "wait",
        )


if __name__ == "__main__":
    main()
