"""A dangling tool call must be repaired, never amputated.

The provider contract is narrow: every ``tool_calls`` entry on an ``assistant``
message needs a matching ``tool`` result. These tests pin that Zeline now
satisfies it by adding the missing results instead of deleting the messages that
already carry real work.
"""

import unittest

from zeline import tool_protocol


def _assistant(*calls, content=""):
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}
            for call_id, name in calls
        ],
    }


def _result(call_id, content="ok"):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _answered(messages):
    return [m["tool_call_id"] for m in messages if m.get("role") == "tool"]


class UnansweredCallTests(unittest.TestCase):
    def test_history_without_tool_calls_reports_nothing_missing(self):
        messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        self.assertEqual(tool_protocol.unanswered_call_ids(messages), [])

    def test_fully_answered_batch_reports_nothing_missing(self):
        messages = [_assistant(("a", "read_file"), ("b", "web_search")), _result("a"), _result("b")]
        self.assertEqual(tool_protocol.unanswered_call_ids(messages), [])

    def test_partially_answered_batch_reports_only_the_gap(self):
        messages = [_assistant(("a", "read_file"), ("b", "web_search")), _result("a")]
        self.assertEqual(tool_protocol.unanswered_call_ids(messages), ["b"])


class RepairTests(unittest.TestCase):
    def test_clean_history_is_returned_untouched(self):
        messages = [_assistant(("a", "read_file")), _result("a")]
        self.assertIs(tool_protocol.repair(messages), messages)

    def test_repair_does_not_mutate_the_input(self):
        messages = [_assistant(("a", "read_file"))]
        before = len(messages)
        tool_protocol.repair(messages)
        self.assertEqual(len(messages), before)

    def test_every_dangling_call_gets_exactly_one_result(self):
        messages = [_assistant(("a", "read_file"), ("b", "web_search"), ("c", "run_shell"))]
        repaired = tool_protocol.repair(messages)
        self.assertEqual(_answered(repaired), ["a", "b", "c"])

    def test_completed_results_and_narration_survive_the_repair(self):
        messages = [
            {"role": "user", "content": "cek dua sumber"},
            _assistant(("a", "web_search"), ("b", "web_fetch"), content="aku cek dua sumber"),
            _result("a", "hasil pencarian nyata"),
        ]
        repaired = tool_protocol.repair(messages)
        contents = [m.get("content") for m in repaired]
        self.assertIn("hasil pencarian nyata", contents)
        self.assertIn("aku cek dua sumber", contents)
        self.assertEqual(_answered(repaired), ["a", "b"])

    def test_placeholder_states_the_call_did_not_run_without_inventing_output(self):
        repaired = tool_protocol.repair([_assistant(("a", "run_shell"))])
        placeholder = repaired[-1]
        self.assertEqual(placeholder["tool_call_id"], "a")
        self.assertEqual(placeholder["name"], "run_shell")
        self.assertIn("did not complete", placeholder["content"])
        self.assertIn("No output is available", placeholder["content"])

    def test_malformed_arguments_get_a_distinct_reason(self):
        messages = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "a", "type": "function", "function": {"name": "read_file"}}],
        }]
        repaired = tool_protocol.repair(messages)
        self.assertIn("malformed or truncated", repaired[-1]["content"])

    def test_dangling_call_in_the_middle_of_history_is_repaired_in_place(self):
        """The old tail-only amputation left these sessions permanently broken."""
        messages = [
            _assistant(("a", "read_file")),
            # no result for "a" -- provider rejected every later turn
            {"role": "user", "content": "lanjut"},
            _assistant(("b", "web_search")),
            _result("b"),
            {"role": "assistant", "content": "selesai"},
        ]
        repaired = tool_protocol.repair(messages)
        self.assertEqual(_answered(repaired), ["a", "b"])
        # The placeholder sits with its own call, not appended at the end.
        self.assertEqual(repaired[1]["tool_call_id"], "a")
        self.assertEqual(repaired[-1]["content"], "selesai")

    def test_repair_is_idempotent(self):
        once = tool_protocol.repair([_assistant(("a", "read_file"))])
        twice = tool_protocol.repair(once)
        self.assertIs(twice, once)

    def test_calls_without_an_id_are_skipped_rather_than_crashing(self):
        messages = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [{"type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
        }]
        self.assertEqual(tool_protocol.repair(messages), messages)


if __name__ == "__main__":
    unittest.main()
