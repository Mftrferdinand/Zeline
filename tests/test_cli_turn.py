"""Tests for the chat REPL turn loop in ``zeline.cli.cmd_chat``.

Covers streaming (``on_stream_delta`` wired to ``tui.StreamRenderer``),
tool result progress lines, Ctrl+C turn cancellation, and the per-turn
footer. ``sessions.send`` is faked — no provider or model is ever touched.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from zeline import cli  # noqa: E402
from zeline import tui  # noqa: E402


class FakeRenderer:
    """Stand-in for ``tui.StreamRenderer`` recording feed/done calls."""

    def __init__(self, *args, **kwargs):
        self.fed: list[str] = []
        self.done_calls = 0
        self.feed_error: Exception | None = None
        self.done_error: Exception | None = None

    def feed(self, delta: str) -> None:
        if self.feed_error is not None:
            raise self.feed_error
        self.fed.append(delta)

    def done(self) -> str:
        self.done_calls += 1
        if self.done_error is not None:
            raise self.done_error
        return "".join(self.fed)


class FakeSessionStore:
    """Fake ``SessionStore``: ``send`` is driven by ``script``, ``stop`` is recorded."""

    def __init__(self, *args, **kwargs):
        self.send_kwargs: dict | None = None
        self.send_calls = 0
        self.stop_calls: list[str] = []
        self.script = None  # callable(send_kwargs) -> str

    def send(self, **kwargs):
        self.send_calls += 1
        self.send_kwargs = kwargs
        return self.script(kwargs)

    def stop(self, identity: str) -> bool:
        self.stop_calls.append(identity)
        return True


class ChatTurnTestBase(unittest.TestCase):
    def setUp(self):
        self.store = FakeSessionStore()
        self.renderers: list[FakeRenderer] = []

        def make_renderer(*args, **kwargs):
            renderer = FakeRenderer(*args, **kwargs)
            self.renderers.append(renderer)
            return renderer

        self.patches = [
            mock.patch.object(cli, "SessionStore", lambda *a, **k: self.store),
            mock.patch.object(tui, "StreamRenderer", make_renderer),
            # Pretend we are on an interactive TTY unless a test says otherwise.
            mock.patch.object(tui, "supports_stream", lambda: True),
            mock.patch.object(cli.config, "GATEWAY_SETUP_COMPLETE", True),
            mock.patch.object(cli.config, "SETUP_COMPLETE", True),
            mock.patch.object(cli.config, "PROVIDER", {"model_verified": True}),
            mock.patch.object(cli.config, "API_KEY", "test-key"),
            mock.patch.object(
                cli.config, "stored_config_copy", lambda: {"provider": {"name": "test"}}
            ),
            mock.patch.object(cli, "_run_reflection", lambda sessions: None),
        ]
        for patcher in self.patches:
            patcher.start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for patcher in reversed(self.patches):
            patcher.stop()

    def run_repl(self, inputs, script):
        """Drive cmd_chat with fake input lines; script drives send. Returns (status, output)."""
        self.store.script = script
        with mock.patch.object(cli, "_read_user_input", side_effect=list(inputs)):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                status = cli.cmd_chat(None)
        return status, buffer.getvalue()


class StreamingTests(ChatTurnTestBase):
    def test_deltas_forwarded_to_renderer_and_done_called(self):
        """(a) on_stream_delta forwards deltas to the renderer; done() runs."""

        def script(kwargs):
            self.assertIsNotNone(kwargs["on_stream_delta"])
            kwargs["on_stream_delta"]("hello ")
            kwargs["on_stream_delta"]("world")
            return "hello world"

        status, output = self.run_repl(["hi", "exit"], script)
        self.assertEqual(status, 0)
        self.assertEqual(len(self.renderers), 1)
        self.assertEqual(self.renderers[0].fed, ["hello ", "world"])
        self.assertEqual(self.renderers[0].done_calls, 1)
        # Final render still goes through print_markdown with the full reply.
        self.assertIn("hello world", output)

    def test_renderer_exception_does_not_fail_turn(self):
        """(b) renderer feed/done raising -> the turn still succeeds."""
        failing = []

        def make_failing(*args, **kwargs):
            renderer = FakeRenderer(*args, **kwargs)
            renderer.feed_error = RuntimeError("boom")
            renderer.done_error = RuntimeError("kaboom")
            failing.append(renderer)
            return renderer

        with mock.patch.object(tui, "StreamRenderer", make_failing):

            def script(kwargs):
                kwargs["on_stream_delta"]("partial")
                return "full answer"

            status, output = self.run_repl(["hi", "exit"], script)
        self.assertEqual(status, 0)
        self.assertEqual(failing[0].done_calls, 1)
        self.assertIn("full answer", output)

    def test_non_tty_no_streaming(self):
        """(c) non-TTY -> on_stream_delta=None, no renderer created."""

        def script(kwargs):
            self.assertIsNone(kwargs["on_stream_delta"])
            return "plain"

        with mock.patch.object(tui, "supports_stream", lambda: False):
            status, output = self.run_repl(["hi", "exit"], script)
        self.assertEqual(status, 0)
        self.assertEqual(self.renderers, [])
        self.assertIn("plain", output)

    def test_done_called_when_send_raises(self):
        """done() runs even when the turn itself errors, so Live never hangs."""

        def script(kwargs):
            raise cli.ZelineError("provider down")

        status, output = self.run_repl(["hi", "exit"], script)
        self.assertEqual(status, 0)
        self.assertEqual(self.renderers[0].done_calls, 1)
        self.assertIn("[error] provider down", output)


class ToolProgressTests(ChatTurnTestBase):
    def test_tool_result_lines(self):
        """(d) on_tool_result prints one dim line per tool: success and failure."""

        def script(kwargs):
            kwargs["on_tool"]("read_file", {"path": "notes.txt"})
            kwargs["on_tool_result"]("read_file", {"path": "notes.txt"}, "file contents here")
            kwargs["on_tool"]("run_shell", {"command": "false"})
            kwargs["on_tool_result"](
                "run_shell", {"command": "false"}, "ERROR run shell: exit 1\nsecond line"
            )
            return "done"

        status, output = self.run_repl(["hi", "exit"], script)
        self.assertEqual(status, 0)
        # Existing on_tool line is preserved.
        self.assertIn("⚙ read_file(path=notes.txt)", output)
        # Success line: one tick + name + elapsed.
        self.assertRegex(output, r"✓ read_file \(\d+\.\ds\)")
        # Failure line: cross + name + elapsed + short error summary, ≤80 chars.
        # The multi-line result is collapsed to one line, then truncated.
        match = re.search(r"✗ run_shell \(\d+\.\ds\) • ([^\x1b]+)", output)
        self.assertIsNotNone(match, "failure line missing")
        summary = match.group(1)
        self.assertEqual(summary, "ERROR run shell: exit 1 second line")
        self.assertLessEqual(len(summary), 80)

    def test_long_error_summary_truncated(self):
        """Error summaries longer than 80 chars are cut."""

        def script(kwargs):
            kwargs["on_tool_result"]("x", {}, "ERROR " + "y" * 200)
            return "done"

        status, output = self.run_repl(["hi", "exit"], script)
        self.assertEqual(status, 0)
        match = re.search(r"✗ x \(\d+\.\ds\) • ([^\x1b]+)", output)
        self.assertIsNotNone(match)
        self.assertLessEqual(len(match.group(1)), 80)


class CancelTests(ChatTurnTestBase):
    def test_ctrl_c_cancels_turn_and_repl_survives(self):
        """(e) KeyboardInterrupt mid-turn -> stop('cli:local'), dim notice,
        footer, and the REPL keeps running for the next turn."""
        calls = {"n": 0}

        def script(kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyboardInterrupt
            return "second turn answer"

        footer_calls: list[tuple] = []
        with mock.patch.object(
            tui,
            "format_turn_footer",
            lambda elapsed, tokens: footer_calls.append((elapsed, tokens)) or "FOOT",
        ):
            status, output = self.run_repl(["first", "second", "exit"], script)
        self.assertEqual(status, 0)
        # The cancelled turn signalled stop on the CLI identity.
        self.assertEqual(self.store.stop_calls, ["cli:local"])
        # Dim "turn dibatalkan" notice was printed.
        self.assertIn("turn dibatalkan", output)
        # The renderer was torn down even on interruption.
        self.assertEqual(self.renderers[0].done_calls, 1)
        # The next turn runs normally after the cancellation.
        self.assertIn("second turn answer", output)
        # Footer printed for both the cancelled and the successful turn.
        self.assertEqual(len(footer_calls), 2)
        for elapsed, tokens in footer_calls:
            self.assertIsInstance(elapsed, float)
            self.assertGreaterEqual(elapsed, 0.0)
            self.assertIsNone(tokens)

    def test_ctrl_c_at_empty_prompt_still_exits(self):
        """Ctrl+C while the input prompt is empty still exits the REPL."""
        with mock.patch.object(cli, "_read_user_input", side_effect=KeyboardInterrupt):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                status = cli.cmd_chat(None)
        self.assertEqual(status, 0)
        self.assertIn("Goodbye!", buffer.getvalue())
        self.assertEqual(self.store.send_calls, 0)
        self.assertEqual(self.store.stop_calls, [])


class FooterTests(ChatTurnTestBase):
    def test_footer_format_called_with_elapsed(self):
        """(f) footer is rendered after a successful turn with the turn's elapsed time."""
        seen: list[tuple] = []

        def fake_footer(elapsed_s, tokens):
            seen.append((elapsed_s, tokens))
            return "FOOTER-LINE"

        with mock.patch.object(tui, "format_turn_footer", fake_footer):
            status, output = self.run_repl(["hi", "exit"], lambda kwargs: "answer")
        self.assertEqual(status, 0)
        self.assertEqual(len(seen), 1)
        elapsed, tokens = seen[0]
        self.assertIsInstance(elapsed, float)
        self.assertGreaterEqual(elapsed, 0.0)
        self.assertIsNone(tokens)
        self.assertIn("FOOTER-LINE", output)

    def test_footer_skipped_when_not_tty(self):
        """No footer (or streaming) in piped/non-TTY mode."""
        with (
            mock.patch.object(tui, "supports_stream", lambda: False),
            mock.patch.object(
                tui, "format_turn_footer", side_effect=AssertionError("must not be called")
            ),
        ):
            status, output = self.run_repl(["hi", "exit"], lambda kwargs: "answer")
        self.assertEqual(status, 0)
        self.assertIn("answer", output)


class ToolErrorDetectionTests(ChatTurnTestBase):
    """MINOR-1: precise framework-error detection (no more 'ERRORS: 0' false ✗)."""

    def test_errors_plural_success_not_flagged(self):
        self.assertFalse(cli._tool_failed("ERRORS: 0, warnings: 2"))

    def test_framework_error_formats_flagged(self):
        for result in (
            "ERROR read file: not found",
            "ERROR: boom",
            "ERROR write file: too large",
        ):
            self.assertTrue(cli._tool_failed(result), result)

    def test_lowercase_and_empty_not_flagged(self):
        # Lowercase is not the framework convention (all emitters uppercase).
        self.assertFalse(cli._tool_failed("error: connection refused"))
        self.assertFalse(cli._tool_failed(""))
        self.assertFalse(cli._tool_failed(None))

    def test_errors_plural_renders_check_mark(self):
        def script(kwargs):
            kwargs["on_tool_result"]("x", {}, "ERRORS: 0, warnings: 2")
            return "done"

        status, output = self.run_repl(["hi", "exit"], script)
        self.assertEqual(status, 0)
        self.assertRegex(output, r"✓ x \(\d+\.\ds\)")
        self.assertNotIn("✗ x", output)


class RenderPhaseRobustnessTests(ChatTurnTestBase):
    """MINOR-2: Ctrl+C during final render + non-ZelineError must not kill the REPL."""

    def test_ctrl_c_during_render_keeps_repl_alive(self):
        # #9: KI saat render akhir = turn SUDAH sukses -> pesan netral
        # "turn selesai", BUKAN "turn dibatalkan" yang menyesatkan.
        calls = {"send": 0, "render": 0}
        real_print_markdown = tui.print_markdown

        def script(kwargs):
            calls["send"] += 1
            return f"answer {calls['send']}"

        def flaky_render(answer):
            calls["render"] += 1
            if calls["render"] == 1:
                raise KeyboardInterrupt
            return real_print_markdown(answer)

        with mock.patch.object(tui, "print_markdown", side_effect=flaky_render):
            status, output = self.run_repl(["first", "second", "exit"], script)
        self.assertEqual(status, 0)
        self.assertIn("turn selesai", output)
        self.assertNotIn("turn dibatalkan", output)
        # Second turn renders normally through the real print_markdown.
        self.assertIn("answer 2", output)
        self.assertEqual(calls["send"], 2)
        # stop() is best-effort even though no turn was running anymore.
        self.assertEqual(self.store.stop_calls, ["cli:local"])

    def test_non_zeline_error_does_not_kill_repl(self):
        calls = {"n": 0}

        def script(kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("provider exploded")
            return "recovered"

        status, output = self.run_repl(["first", "second", "exit"], script)
        self.assertEqual(status, 0)
        self.assertIn("RuntimeError: provider exploded", output)
        self.assertIn("recovered", output)
        self.assertEqual(calls["n"], 2)


class IncrementalRenderSkipTests(ChatTurnTestBase):
    """#8: no double output — skip the final render when the no-rich path
    already wrote incrementally (``renderer.rendered_incrementally``)."""

    def _make_renderer(self, incremental: bool):
        def make(*args, **kwargs):
            renderer = FakeRenderer(*args, **kwargs)
            renderer.rendered_incrementally = incremental
            return renderer

        return make

    def test_final_render_skipped_when_incremental(self):
        def script(kwargs):
            return "streamed answer"

        with (
            mock.patch.object(tui, "StreamRenderer", self._make_renderer(True)),
            mock.patch.object(tui, "print_markdown") as final_render,
        ):
            status, output = self.run_repl(["hi", "exit"], script)
        self.assertEqual(status, 0)
        final_render.assert_not_called()
        self.assertNotIn("streamed answer", output)

    def test_final_render_kept_when_not_incremental(self):
        """Rich Live di-wipe saat turn berakhir -> render final tetap jalan."""

        def script(kwargs):
            return "full answer"

        with (
            mock.patch.object(tui, "StreamRenderer", self._make_renderer(False)),
            mock.patch.object(tui, "print_markdown") as final_render,
        ):
            status, output = self.run_repl(["hi", "exit"], script)
        self.assertEqual(status, 0)
        final_render.assert_called_once_with("full answer")

    def test_renderer_without_property_still_renders_final(self):
        """Mock/fake lama tanpa properti ``rendered_incrementally`` ->
        getattr default False -> render final tetap (tidak ada output hilang)."""

        def script(kwargs):
            return "legacy answer"

        with mock.patch.object(tui, "print_markdown") as final_render:
            status, output = self.run_repl(["hi", "exit"], script)
        self.assertEqual(status, 0)
        final_render.assert_called_once_with("legacy answer")


class DoubleInterruptTests(ChatTurnTestBase):
    """#10: KeyboardInterrupt KEDUA di dalam body handler -> keluar bersih."""

    def test_double_ctrl_c_exits_cleanly(self):
        def script(kwargs):
            raise KeyboardInterrupt  # KI pertama: mid-turn

        def stop_raises(identity):
            self.store.stop_calls.append(identity)
            raise KeyboardInterrupt  # KI kedua: di dalam body handler

        self.store.stop = stop_raises
        status, output = self.run_repl(["hi", "exit"], script)
        self.assertEqual(status, 0)
        self.assertIn("Goodbye!", output)
        # Tidak ada traceback; "exit" tidak pernah diproses.
        self.assertEqual(self.store.send_calls, 1)


class SharedErrorDetectionTests(ChatTurnTestBase):
    """#13: ``_tool_failed`` memakai helper bersama ``zeline.agent.is_error_text``."""

    def test_tool_failed_delegates_to_agent_helper(self):
        """Wiring: _tool_failed mendelegasi ke helper milik agent.py."""
        with mock.patch(
            "zeline.agent.is_error_text", return_value=True, create=True
        ) as helper:
            self.assertTrue(cli._tool_failed("anything at all"))
        helper.assert_called_once_with("anything at all")

    def test_is_error_text_patterns(self):
        """Pola audit #13 via _tool_failed (butuh helper A4 di agent.py)."""
        self.assertTrue(cli._tool_failed("ERROR: boom"))
        self.assertTrue(cli._tool_failed("ERROR read file: not found"))
        self.assertFalse(cli._tool_failed("ERRORS: 0"))
        self.assertFalse(cli._tool_failed("all systems nominal"))


if __name__ == "__main__":
    unittest.main()
