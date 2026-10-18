import json
from pathlib import Path
import tempfile
import unittest

from zulip_hub.state import StateReducer, StateStore


class StateTests(unittest.TestCase):
    def test_message_updates_counts_and_recent_without_content(self):
        reducer = StateReducer(limit=2)
        reducer.apply({"type": "message", "message": {
            "id": 42, "type": "private", "sender_full_name": "Alice",
            "sender_id": 7,
            "content": "secret body", "timestamp": 10, "flags": ["mentioned"],
            "display_recipient": [{"id": 7}, {"id": 8}],
        }})
        self.assertEqual(reducer.state.unread, {"total": 1, "mentions": 1, "private": 1})
        self.assertNotIn("content", reducer.state.recent[0])
        self.assertEqual(reducer.state.recent[0]["recipient_ids"], [7, 8])
        self.assertEqual(reducer.state.recent[0]["sender_id"], 7)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            StateStore(path).write(reducer.state)
            self.assertEqual(json.loads(path.read_text())["recent"][0]["id"], 42)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_a_hostile_message_cannot_inflate_the_stored_state(self):
        from zulip_hub.limits import MAX_COLLECTION, MAX_TEXT
        reducer = StateReducer(limit=2)
        reducer.apply({"type": "message", "message": {
            "id": 42, "type": "stream",
            "sender_full_name": "A" * (MAX_TEXT * 3),
            "display_recipient": "B" * (MAX_TEXT * 3),
            "subject": "C" * (MAX_TEXT * 3),
            "sender_id": 7, "stream_id": 9, "timestamp": 10,
            "flags": ["mentioned"] * (MAX_COLLECTION * 2),
        }})
        row = reducer.state.recent[0]
        self.assertEqual(len(row["sender"]), MAX_TEXT)
        self.assertEqual(len(row["channel"]), MAX_TEXT)
        self.assertEqual(len(row["topic"]), MAX_TEXT)
        self.assertEqual(len(row["flags"]), MAX_COLLECTION)

    def test_a_private_message_bounds_its_recipient_list(self):
        from zulip_hub.limits import MAX_COLLECTION
        reducer = StateReducer(limit=2)
        reducer.apply({"type": "message", "message": {
            "id": 43, "type": "private", "sender_full_name": "Bob", "sender_id": 8,
            "timestamp": 11, "flags": [],
            "display_recipient": [{"id": index} for index in range(MAX_COLLECTION * 2)],
        }})
        self.assertEqual(len(reducer.state.recent[0]["recipient_ids"]), MAX_COLLECTION)

    def test_the_server_cannot_impose_an_absurd_message_length(self):
        from zulip_hub.limits import MAX_MESSAGE_LENGTH
        reducer = StateReducer()
        reducer.initialized({"unread_msgs": {}, "max_message_length": 10 ** 12})
        self.assertEqual(reducer.state.max_message_length, MAX_MESSAGE_LENGTH)
        reducer.initialized({"unread_msgs": {}, "max_message_length": float("nan")})
        self.assertEqual(reducer.state.max_message_length, 10000)

    def test_the_written_state_stays_within_an_aggregate_budget(self):
        from zulip_hub.limits import MAX_PAYLOAD_BYTES, MAX_TEXT
        reducer = StateReducer(limit=200)
        for index in range(200):
            reducer.apply({"type": "message", "message": {
                "id": index, "type": "stream",
                "sender_full_name": "A" * MAX_TEXT,
                "display_recipient": "B" * MAX_TEXT,
                "subject": "C" * MAX_TEXT,
                "sender_id": 7, "stream_id": 9, "timestamp": 10, "flags": [],
            }})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            StateStore(path).write(reducer.state)
            self.assertLessEqual(path.stat().st_size, MAX_PAYLOAD_BYTES)
            self.assertTrue(json.loads(path.read_text())["recent"])

    def test_read_flags_never_make_count_negative(self):
        reducer = StateReducer()
        reducer.apply({"type": "update_message_flags", "flag": "read", "operation": "add", "messages": [1, 2]})
        self.assertEqual(reducer.state.unread["total"], 0)

    def test_read_flags_update_all_categories(self):
        reducer = StateReducer()
        reducer.apply({"type": "message", "message": {
            "id": 42, "type": "private", "flags": ["mentioned"]
        }})
        reducer.apply({
            "type": "update_message_flags", "flag": "read",
            "operation": "add", "messages": [42],
        })
        self.assertEqual(reducer.state.unread, {"total": 0, "mentions": 0, "private": 0})

    def test_registration_counts_unique_unread_ids(self):
        reducer = StateReducer()
        reducer.initialized({"unread_msgs": {
            "mentions": [2], "pms": [1], "streams": [[7, [2, 3]]]
        }})
        self.assertEqual(reducer.state.unread, {"total": 3, "mentions": 1, "private": 1})

    def test_registration_records_server_message_limit(self):
        reducer = StateReducer()
        reducer.initialized({"unread_msgs": {}, "max_message_length": 12345})
        self.assertEqual(reducer.state.max_message_length, 12345)

    def test_mark_all_read_uses_modern_op_field(self):
        reducer = StateReducer()
        reducer.apply({"type": "message", "message": {
            "id": 42, "type": "private", "flags": ["topic_wildcard_mentioned"]
        }})
        reducer.apply({"type": "update_message_flags", "flag": "read", "op": "add", "all": True, "messages": []})
        self.assertEqual(reducer.state.unread, {"total": 0, "mentions": 0, "private": 0})


class ZulipUnreadFormatTests(unittest.TestCase):
    def test_register_unread_msgs_in_server_format(self):
        from zulip_hub.state import StateReducer
        reducer = StateReducer()
        reducer.initialized({"unread_msgs": {
            "count": 6,
            "pms": [{"other_user_id": 8, "sender_id": 8, "unread_message_ids": [1, 2]}],
            "huddles": [{"user_ids_string": "1,8,9", "unread_message_ids": [3]}],
            "streams": [
                {"stream_id": 7, "topic": "a", "unread_message_ids": [4, 5]},
                {"stream_id": 7, "topic": "b", "unread_message_ids": [6]},
            ],
            "mentions": [4],
        }})
        self.assertEqual(reducer.state.unread, {"total": 6, "mentions": 1, "private": 3})
