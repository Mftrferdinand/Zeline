"""Contract tests for the LSP client and the `code_intel` tool.

Live-server tests are opt-in via ``ZELINE_LSP_TESTS=1``: which language servers
exist differs per machine and per CI runner, and a missing binary would look like
a Zeline bug. What is pinned without any server is everything that must hold
regardless — the JSON-RPC framing, the non-blocking reader, capability gating,
argument validation, workspace confinement, profile gating and server reuse.

The framing tests speak to a fake language server implemented in-process, so they
exercise the real client against real ``Content-Length`` framed bytes.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

LIVE = os.environ.get("ZELINE_LSP_TESTS") == "1"


def fresh(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    cfg = importlib.import_module("zeline.config")
    lsp = importlib.import_module("zeline.lsp")
    tools = importlib.import_module("zeline.tools")
    return cfg, lsp, tools


class FakePipe:
    """A byte stream fed from a script, standing in for a server's stdout."""

    def __init__(self, chunks: list[bytes]):
        self._data = b"".join(chunks)
        self._offset = 0
        self._closed = threading.Event()

    def readline(self) -> bytes:
        if self._offset >= len(self._data):
            self._closed.wait(0.05)
            return b""
        end = self._data.find(b"\n", self._offset)
        end = len(self._data) if end == -1 else end + 1
        line, self._offset = self._data[self._offset:end], end
        return line

    def read(self, count: int) -> bytes:
        chunk = self._data[self._offset:self._offset + count]
        self._offset += len(chunk)
        return chunk

    def close(self):
        self._closed.set()


class LspBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "zhome"
        self.home.mkdir(parents=True)
        self._saved = os.environ.get("ZELINE_HOME")
        self.config, self.lsp, self.tools = fresh(self.home)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        self._tmp.cleanup()

    @staticmethod
    def frame(payload: dict) -> bytes:
        body = json.dumps(payload).encode()
        return f"Content-Length: {len(body)}\r\n\r\n".encode() + body

    def server(self, outgoing: list[bytes], capabilities: dict | None = None):
        """A LanguageServer wired to a scripted stdout and a capturing stdin."""
        server = self.lsp.LanguageServer(
            language="python", argv=("fake-server",), root=self.home
        )
        written: list[dict] = []

        class FakeStdin:
            def write(self, data: bytes):
                text = data.decode("utf-8", errors="replace")
                _, _, body = text.partition("\r\n\r\n")
                if body:
                    written.append(json.loads(body))

            def flush(self):
                pass

            def close(self):
                pass

        process = mock.Mock()
        process.poll.return_value = None
        process.stdin = FakeStdin()
        process.stdout = FakePipe(outgoing)
        server._process = process
        server._capabilities = capabilities or {}
        server._reader = threading.Thread(target=server._pump, daemon=True)
        server._reader.start()
        return server, written


class FramingTests(LspBase):
    def test_a_request_reply_is_matched_by_id(self):
        server, written = self.server([
            self.frame({"jsonrpc": "2.0", "method": "window/logMessage", "params": {}}),
            self.frame({"jsonrpc": "2.0", "id": 1, "result": {"answer": 42}}),
        ])
        self.assertEqual(server._request("test/thing"), {"answer": 42})
        self.assertEqual(written[0]["method"], "test/thing")

    def test_an_out_of_order_reply_is_not_mistaken_for_ours(self):
        """Assuming the next frame is the answer returns another call's result."""
        server, _ = self.server([
            self.frame({"jsonrpc": "2.0", "id": 99, "result": "not yours"}),
            self.frame({"jsonrpc": "2.0", "id": 1, "result": "yours"}),
        ])
        self.assertEqual(server._request("test/thing"), "yours")

    def test_a_server_error_becomes_an_lsp_error(self):
        server, _ = self.server([
            self.frame({"jsonrpc": "2.0", "id": 1, "error": {"message": "Unknown request"}}),
        ])
        with self.assertRaises(self.lsp.LspError) as caught:
            server._request("test/thing")
        self.assertIn("Unknown request", str(caught.exception))

    def test_a_silent_server_times_out_instead_of_hanging(self):
        """A clean file produces no output at all, so this must not block."""
        server, _ = self.server([])
        with self.assertRaises(self.lsp.LspError) as caught:
            server._request("test/thing", timeout=0.5)
        self.assertIn("timed out", str(caught.exception))

    def test_a_server_to_client_request_is_answered(self):
        """Some servers stall forever waiting for a reply to their own request."""
        server, written = self.server([
            self.frame({"jsonrpc": "2.0", "id": 7, "method": "workspace/configuration", "params": {}}),
            self.frame({"jsonrpc": "2.0", "id": 1, "result": "done"}),
        ])
        self.assertEqual(server._request("test/thing"), "done")
        answers = [item for item in written if item.get("id") == 7]
        self.assertEqual(len(answers), 1)
        self.assertIn("result", answers[0])

    def test_notifications_seen_while_waiting_are_kept_for_drain(self):
        server, _ = self.server([
            self.frame({
                "jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                "params": {"uri": "file:///x.py", "diagnostics": []},
            }),
            self.frame({"jsonrpc": "2.0", "id": 1, "result": "ok"}),
        ])
        server._request("test/thing")
        collected = server.drain(settle=0.2)
        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0]["method"], "textDocument/publishDiagnostics")

    def test_drain_returns_empty_on_a_quiet_server(self):
        server, _ = self.server([])
        self.assertEqual(server.drain(settle=0.2), [])

    def test_a_large_message_is_reassembled(self):
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"blob": "z" * 80_000}}
        server, _ = self.server([self.frame(payload)])
        self.assertEqual(len(server._request("test/thing")["blob"]), 80_000)

    def test_writing_to_a_dead_server_is_reported(self):
        server, _ = self.server([])
        server._process.poll.return_value = 1
        with self.assertRaises(self.lsp.LspError):
            server._request("test/thing")


class CapabilityTests(LspBase):
    def test_a_feature_the_server_lacks_is_refused_with_advice(self):
        """ruff's server does diagnostics only; asking it for symbols must explain."""
        server, _ = self.server([], capabilities={"hoverProvider": True})
        with self.assertRaises(self.lsp.LspError) as caught:
            server.require("symbols")
        message = str(caught.exception)
        self.assertIn("does not provide 'symbols'", message)
        self.assertIn("hover", message)
        self.assertIn("basedpyright", message)

    def test_a_supported_feature_passes(self):
        server, _ = self.server([], capabilities={"documentSymbolProvider": True})
        server.require("symbols")

    def test_diagnostics_needs_no_capability(self):
        """Every server publishes diagnostics; none advertises a provider for it."""
        server, _ = self.server([], capabilities={})
        self.assertTrue(server.supports("diagnostics"))


class DiscoveryTests(LspBase):
    def test_language_is_detected_from_the_extension(self):
        cases = {
            "a.py": "python", "a.pyi": "python", "a.ts": "typescript",
            "a.tsx": "typescript", "a.js": "javascript", "a.go": "go",
            "a.rs": "rust", "a.c": "c", "a.cpp": "cpp", "a.txt": None,
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.lsp.language_for(name), expected)

    def test_a_configured_override_wins(self):
        saved = self.config.config_copy()
        saved["tools"]["lsp_servers"] = {"python": "my-server --stdio"}
        self.config.save_config(saved)
        with mock.patch.object(self.lsp.shutil, "which", return_value="/usr/bin/my-server"):
            self.assertEqual(self.lsp.find_server("python"), ("my-server", "--stdio"))

    def test_a_configured_override_that_is_missing_does_not_fall_back(self):
        """Silently using a different server than asked for would be confusing."""
        saved = self.config.config_copy()
        saved["tools"]["lsp_servers"] = {"python": "not-installed-server"}
        self.config.save_config(saved)
        with mock.patch.object(self.lsp.shutil, "which", return_value=None):
            self.assertIsNone(self.lsp.find_server("python"))

    def test_no_server_installed_returns_none(self):
        with mock.patch.object(self.lsp.shutil, "which", return_value=None):
            self.assertIsNone(self.lsp.find_server("python"))
            self.assertFalse(any(self.lsp.available().values()))

    def test_disabled_by_config(self):
        saved = self.config.config_copy()
        saved["tools"]["lsp"] = False
        self.config.save_config(saved)
        self.assertFalse(self.lsp.enabled())

    def test_an_unsupported_file_type_says_what_is_supported(self):
        registry = self.lsp.LspRegistry(self.home)
        with self.assertRaises(self.lsp.LspError) as caught:
            registry.server_for(self.home / "notes.txt")
        self.assertIn("python", str(caught.exception))

    def test_a_missing_server_says_what_to_install(self):
        registry = self.lsp.LspRegistry(self.home)
        with (
            mock.patch.object(self.lsp.shutil, "which", return_value=None),
            self.assertRaises(self.lsp.LspError) as caught,
        ):
            registry.server_for(self.home / "a.py")
        self.assertIn("no python language server found", str(caught.exception))

    def test_a_crashed_server_is_replaced_rather_than_reused(self):
        registry = self.lsp.LspRegistry(self.home)
        dead = self.lsp.LanguageServer(language="python", argv=("x",), root=self.home)
        dead._process = mock.Mock(poll=mock.Mock(return_value=1))
        registry.servers["python"] = dead
        started = []

        class Replacement:
            running = True

            def __init__(self, **kwargs):
                started.append(self)

            def start(self):
                pass

        with (
            mock.patch.object(self.lsp, "find_server", return_value=("x",)),
            mock.patch.object(self.lsp, "LanguageServer", Replacement),
        ):
            registry.server_for(self.home / "a.py")
        self.assertEqual(len(started), 1)


class RenderingTests(LspBase):
    def test_diagnostics_are_rendered_one_per_line_with_1_based_lines(self):
        item = self.lsp.Diagnostic(line=4, character=2, severity="error", message="bad", source="Ruff")
        self.assertEqual(item.render(Path("f.py")), "f.py:5:3: error [Ruff]: bad")

    def test_locations_are_rendered_readably(self):
        rendered = self.lsp._as_locations([
            {"uri": "file:///project/mod.py", "range": {"start": {"line": 9, "character": 4}}},
        ])
        self.assertEqual(rendered, ["  mod.py:10:5"])

    def test_location_links_are_understood_too(self):
        """Servers may reply with LocationLink instead of Location."""
        rendered = self.lsp._as_locations([
            {"targetUri": "file:///p/a.py", "targetSelectionRange": {"start": {"line": 0, "character": 0}}},
        ])
        self.assertEqual(rendered, ["  a.py:1:1"])

    def test_hover_handles_all_three_content_shapes(self):
        self.assertEqual(self.lsp._hover_text({"contents": "plain"}), "plain")
        self.assertEqual(self.lsp._hover_text({"contents": {"value": "marked"}}), "marked")
        self.assertEqual(
            self.lsp._hover_text({"contents": ["a", {"value": "b"}]}), "a\nb"
        )
        self.assertEqual(self.lsp._hover_text(None), "")

    def test_nested_symbols_are_indented(self):
        lines = self.lsp._flatten_symbols([{
            "name": "Thing", "kind": 5, "range": {"start": {"line": 0}},
            "children": [{"name": "go", "kind": 6, "range": {"start": {"line": 1}}}],
        }])
        self.assertEqual(lines, ["class Thing (line 1)", "  method go (line 2)"])

    def test_flat_symbol_information_is_also_handled(self):
        lines = self.lsp._flatten_symbols([{
            "name": "helper", "kind": 12,
            "location": {"range": {"start": {"line": 3}}},
        }])
        self.assertEqual(lines, ["function helper (line 4)"])


class ToolRoutingTests(LspBase):
    ARG_CASES: ClassVar[dict[str, str]] = {
        "diagnostics": "needs a path",
        "symbols": "needs a path",
        "definition": "needs a path",
        "references": "needs a path",
        "hover": "needs a path",
    }

    def executor(self, profile: str = "full"):
        return self.tools.ToolExecutor(
            identity="cli:local", profile=profile, workspace=str(self.home)
        )

    def test_safe_profile_cannot_use_it(self):
        self.assertIn("not allowed", self.executor("safe").run("code_intel", {"action": "servers"}))

    def test_it_is_advertised_only_to_operator_profiles(self):
        # all_schemas, not schemas: the profile boundary is what is under test,
        # and `schemas` may withhold detail behind tool_search.
        self.assertIn("code_intel", [s["function"]["name"] for s in self.executor("full").all_schemas])
        self.assertNotIn("code_intel", [s["function"]["name"] for s in self.executor("safe").all_schemas])

    def test_disabled_config_is_reported(self):
        saved = self.config.config_copy()
        saved["tools"]["lsp"] = False
        self.config.save_config(saved)
        self.assertIn("disabled", self.executor().run("code_intel", {"action": "diagnostics", "path": "a.py"}))

    def test_unknown_action_lists_the_valid_ones(self):
        result = self.executor().run("code_intel", {"action": "teleport"})
        for verb in ("diagnostics", "definition", "references", "hover", "symbols", "servers"):
            self.assertIn(verb, result)

    def test_arguments_are_validated_before_any_server_starts(self):
        """A malformed call must not be reported as a missing language server."""
        executor = self.executor()
        with mock.patch.object(self.lsp, "find_server", return_value=None):
            for action, expected in self.ARG_CASES.items():
                with self.subTest(action=action):
                    self.assertIn(expected, executor.run("code_intel", {"action": action}))
            self.assertIn(
                "1-based line",
                executor.run("code_intel", {"action": "hover", "path": "a.py"}),
            )

    def test_servers_action_works_with_nothing_installed(self):
        with mock.patch.object(self.lsp.shutil, "which", return_value=None):
            result = self.executor().run("code_intel", {"action": "servers"})
        self.assertIn("not installed", result)
        self.assertIn("basedpyright", result)

    def test_a_path_outside_the_workspace_is_refused(self):
        result = self.executor().run("code_intel", {"action": "diagnostics", "path": "../../etc/passwd"})
        self.assertIn("workspace", result)

    def test_a_missing_file_is_reported_before_starting_a_server(self):
        with mock.patch.object(self.lsp, "find_server", return_value=None):
            result = self.executor().run("code_intel", {"action": "diagnostics", "path": "ghost.py"})
        self.assertIn("not found", result)

    def test_an_lsp_error_is_returned_as_text_not_raised(self):
        (self.home / "a.py").write_text("x = 1\n", encoding="utf-8")
        executor = self.executor()
        with mock.patch.object(self.lsp, "find_server", return_value=None):
            result = executor.run("code_intel", {"action": "diagnostics", "path": "a.py"})
        self.assertTrue(result.startswith("ERROR code_intel:"))
        self.assertIn("no python language server", result)

    def test_the_registry_is_reused_across_calls(self):
        """Initialization is the expensive part, so it must happen once."""
        (self.home / "a.py").write_text("x = 1\n", encoding="utf-8")
        executor = self.executor()
        created = []

        class FakeRegistry:
            def __init__(self, root):
                created.append(root)

            def diagnostics(self, path):
                return "clean"

            def symbols(self, path):
                return "none"

        with mock.patch.object(self.lsp, "LspRegistry", FakeRegistry):
            executor.run("code_intel", {"action": "diagnostics", "path": "a.py"})
            executor.run("code_intel", {"action": "symbols", "path": "a.py"})
        self.assertEqual(len(created), 1)

    def test_one_tool_covers_every_action(self):
        schema = next(
            s for s in self.executor().all_schemas if s["function"]["name"] == "code_intel"
        )
        self.assertEqual(schema["function"]["parameters"]["required"], ["action"])


@unittest.skipUnless(LIVE, "set ZELINE_LSP_TESTS=1 to run against installed language servers")
class LiveServerTests(LspBase):
    def test_diagnostics_find_a_real_problem_and_clean_files_report_none(self):
        if not self.lsp.find_server("python"):
            self.skipTest("no python language server installed")
        broken = self.home / "broken.py"
        broken.write_text("import os\nundefined_thing()\n", encoding="utf-8")
        clean = self.home / "clean.py"
        clean.write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
        registry = self.lsp.LspRegistry(self.home)
        try:
            report = registry.diagnostics(broken)
            self.assertIn("undefined_thing", report)
            self.assertIn("No diagnostics", registry.diagnostics(clean))
        finally:
            registry.shutdown()

    def test_symbols_definition_references_and_hover_on_a_real_project(self):
        argv = self.lsp.find_server("c")
        if not argv:
            self.skipTest("no c language server installed")
        source = self.home / "m.c"
        source.write_text(
            "int helper(int x){return x*2;}\nint main(){return helper(21);}\n",
            encoding="utf-8",
        )
        registry = self.lsp.LspRegistry(self.home)
        try:
            self.assertIn("helper", registry.symbols(source))
            self.assertIn("m.c:", registry.definition(source, 2, 19))
            references = registry.references(source, 1, 4)
            self.assertIn("m.c:1:5", references)
            self.assertIn("helper", registry.hover(source, 1, 4))
        finally:
            registry.shutdown()
        self.assertFalse(any(s.running for s in registry.servers.values()))


if __name__ == "__main__":
    unittest.main()
