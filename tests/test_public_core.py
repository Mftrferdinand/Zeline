"""Regression tests untuk fondasi Aesora publik.

Jalankan tanpa provider/API key sungguhan:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import http.client
import importlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


def fresh_aesora(home: Path):
    """Reload package with a fully-isolated AESORA_HOME."""
    os.environ["AESORA_HOME"] = str(home)
    for module_name in list(sys.modules):
        if module_name == "aesora" or module_name.startswith("aesora."):
            sys.modules.pop(module_name, None)
    cfg = importlib.import_module("aesora.config")
    memory = importlib.import_module("aesora.memory")
    tools = importlib.import_module("aesora.tools")
    return cfg, memory, tools


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class AesoraPublicCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "aesora-home"
        self.old_home = os.environ.get("AESORA_HOME")
        self.old_key = os.environ.pop("AESORA_API_KEY", None)
        self.old_base = os.environ.pop("AESORA_BASE_URL", None)
        self.old_model = os.environ.pop("AESORA_MODEL", None)
        self.config, self.memory, self.tools = fresh_aesora(self.home)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("AESORA_HOME", None)
        else:
            os.environ["AESORA_HOME"] = self.old_home
        for key, value in (("AESORA_API_KEY", self.old_key), ("AESORA_BASE_URL", self.old_base), ("AESORA_MODEL", self.old_model)):
            if value is not None:
                os.environ[key] = value
        self.temp.cleanup()

    def test_fresh_public_install_has_no_implicit_external_secret_import(self):
        """A public install cannot silently bind itself to another app's key."""
        self.assertEqual(self.config.API_KEY, "")
        self.assertEqual(self.config.BASE_URL, "https://api.openai.com/v1")
        self.assertEqual(self.config.MODEL, "gpt-4o-mini")

    def test_default_runtime_uses_aesora_agent_v1_persona(self):
        self.assertIn("Aesora-Agent-V1", self.config.SYSTEM_PROMPT)
        self.assertIn("eksekusi", self.config.SYSTEM_PROMPT.lower())

    def test_seeded_superagent_skill_corpus_is_available_to_public_gateway(self):
        skill_system = importlib.import_module("aesora.skills")
        skill_system.seed_skills()
        content = skill_system.load_skill("superagent-v7-sk0")
        self.assertIn("Skill Registry", content)

    def test_memory_isolated_between_platform_users(self):
        one = self.memory.MemoryStore("telegram:100")
        two = self.memory.MemoryStore("telegram:200")
        one.add("Suka kopi tanpa gula")
        self.assertIn("kopi", one.formatted())
        self.assertEqual(two.formatted(), "(memory kosong)")

    def test_memory_per_identity_has_bounded_fact_count(self):
        store = self.memory.MemoryStore("telegram:bounded")
        with mock.patch.object(self.memory, "MAX_FACTS_PER_IDENTITY", 2):
            self.assertIn("disimpan", store.add("fakta satu"))
            self.assertIn("disimpan", store.add("fakta dua"))
            self.assertIn("batas", store.add("fakta tiga").lower())
        self.assertEqual(store.list(), ["fakta satu", "fakta dua"])

    def test_memory_rejects_new_identity_after_global_file_limit(self):
        self.memory.MemoryStore("telegram:first").add("fakta pertama")
        with mock.patch.object(self.memory, "MAX_IDENTITIES", 1):
            result = self.memory.MemoryStore("telegram:second").add("fakta kedua")
        self.assertIn("batas", result.lower())

    def test_safe_profile_cannot_access_file_or_shell(self):
        executor = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        names = {item["function"]["name"] for item in executor.schemas}
        self.assertIn("add_memory", names)
        self.assertNotIn("read_file", names)
        self.assertNotIn("run_shell", names)
        self.assertIn("tidak diizinkan", executor.run("run_shell", {"command": "id"}))

    def test_workspace_profile_blocks_path_escape(self):
        workspace = self.home / "workspace"
        workspace.mkdir(parents=True)
        executor = self.tools.ToolExecutor("cli:local", profile="workspace", workspace=workspace)
        result = executor.run("write_file", {"path": "../outside.txt", "content": "no"})
        self.assertIn("workspace", result)
        self.assertFalse((self.home / "outside.txt").exists())

    def test_safe_profile_cannot_load_owner_private_skill(self):
        skills = importlib.import_module("aesora.skills")
        skills.seed_skills()
        skills.save_skill("owner-secret-procedure", "# Private\n\n> Jangan bocorkan.\n\nPRIVATE-SKILL-CONTENT-CHECK")

        public_agent_tools = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        owner_tools = self.tools.ToolExecutor("cli:local", profile="full", workspace=self.home)

        self.assertIn("tidak ditemukan", public_agent_tools.run("load_skill", {"name": "owner-secret-procedure"}))
        self.assertIn("PRIVATE-SKILL-CONTENT-CHECK", owner_tools.run("load_skill", {"name": "owner-secret-procedure"}))

    def test_webhook_requires_token_and_keeps_identity_namespaced(self):
        webhook = importlib.import_module("aesora.gateways.webhook")
        port = free_port()
        token = "this-is-a-long-webhook-test-token"
        received = []

        class FakeSessions:
            def send(self, **kwargs):
                received.append(kwargs)
                return f"reply:{kwargs['text']}"

        stop = threading.Event()
        thread = threading.Thread(
            target=webhook.start,
            args=(
                FakeSessions(),
                {"host": "127.0.0.1", "port": port, "token": token, "tool_profile": "safe"},
                stop,
            ),
            daemon=True,
        )
        thread.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=0.3)
                conn.request("GET", "/health")
                response = conn.getresponse()
                response.read()
                conn.close()
                if response.status == 200:
                    break
            except OSError:
                time.sleep(0.03)
        else:
            self.fail("webhook server tidak siap")

        body = json.dumps({"chat_id": "alice", "text": "halo"})
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request("POST", "/message", body=body, headers={"Content-Type": "application/json"})
        unauthorized = conn.getresponse()
        self.assertEqual(unauthorized.status, 401)
        unauthorized.read()
        conn.close()

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        conn.request(
            "POST",
            "/message",
            body=body,
            headers={"Content-Type": "application/json", "X-Aesora-Token": token},
        )
        authorized = conn.getresponse()
        payload = json.loads(authorized.read())
        conn.close()
        self.assertEqual(authorized.status, 200)
        self.assertEqual(payload["reply"], "reply:halo")
        self.assertEqual(received[0]["identity"], "webhook:alice")
        self.assertEqual(received[0]["tool_profile"], "safe")

        stop.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_telegram_config_and_message_split_helpers(self):
        telegram = importlib.import_module("aesora.gateways.telegram")
        self.assertTrue(telegram.validate_config({"token": "x", "tool_profile": "safe", "allowed": []}))
        self.assertEqual(telegram.validate_config({"token": "123:abc", "tool_profile": "safe", "allowed": []}), [])
        self.assertTrue(telegram._allowed(123, []))
        self.assertFalse(telegram._allowed(123, [456]))
        parts = telegram._split_message("a" * 8_010)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 4_000 for part in parts))

    def test_telegram_model_command_persists_model_and_resets_session(self):
        telegram = importlib.import_module("aesora.gateways.telegram")
        cfg = self.config.config_copy()
        cfg["provider"]["api_key"] = "test-key"
        self.config.save_config(cfg)

        class Sessions:
            def __init__(self): self.reset_id = None
            def reset(self, identity): self.reset_id = identity; return True

        sessions = Sessions()
        reply = telegram._handle_command("/model vendor/new-model", sessions, "telegram:42", stop_event=threading.Event())
        self.assertIn("vendor/new-model", reply)
        self.assertEqual(sessions.reset_id, "telegram:42")
        self.assertEqual(self.config.config_copy()["provider"]["model"], "vendor/new-model")

    def test_telegram_accepts_zip_larger_than_legacy_256_kb_limit(self):
        telegram = importlib.import_module("aesora.gateways.telegram")
        payload = b"z" * (729 * 1024)
        document = {"file_name": "SUPERAGENT-V7-FORSALE-FINAL.zip", "file_size": len(payload), "file_id": "zip-file"}
        response = mock.Mock(ok=True, content=payload)
        with mock.patch.object(telegram, "_api_call", return_value={"result": {"file_path": "documents/zip-file"}}), \
             mock.patch.object(telegram.requests, "get", return_value=response):
            content, error = telegram._download_document("https://api.telegram.org/bottoken", "token", document)
        self.assertIsNone(error)
        self.assertEqual(content, payload)

    def test_telegram_extracts_zip_with_more_than_legacy_64_text_files(self):
        telegram = importlib.import_module("aesora.gateways.telegram")
        import io, zipfile
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for index in range(65):
                archive.writestr(f"skills/skill-{index}.md", f"skill {index}")
        text, error = telegram._extract_document_text("skills.zip", data.getvalue(), "application/zip")
        self.assertIsNone(error)
        self.assertIn("skill 64", text)

    def test_telegram_truncates_extracted_zip_text_to_message_limit(self):
        telegram = importlib.import_module("aesora.gateways.telegram")
        import io, zipfile
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("skills/large.md", "x" * 20_000)
        text, error = telegram._extract_document_text("skills.zip", data.getvalue(), "application/zip")
        self.assertIsNone(error)
        prompt = telegram._build_document_prompt("skills.zip", text)
        self.assertLessEqual(len(prompt), 16_000)

    def test_telegram_extracts_text_and_safe_zip_entries(self):
        telegram = importlib.import_module("aesora.gateways.telegram")
        import io, zipfile
        text, error = telegram._extract_document_text("notes.md", b"# Hello\nAesora", "text/markdown")
        self.assertIsNone(error)
        self.assertIn("Hello", text)
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("notes.txt", "inside archive")
            archive.writestr("../escape.txt", "must not appear")
        text, error = telegram._extract_document_text("notes.zip", data.getvalue(), "application/zip")
        self.assertIsNone(error)
        self.assertIn("inside archive", text)
        self.assertNotIn("must not appear", text)

    def test_telegram_identifies_images_without_claiming_visual_analysis(self):
        telegram = importlib.import_module("aesora.gateways.telegram")
        text, error = telegram._extract_document_text("image.png", b"not-a-real-image", "image/png")
        self.assertIsNone(error)
        self.assertIn("image", text.lower())
        self.assertIn("vision-capable", text.lower())

    def test_whatsapp_adapter_declares_validation_and_safe_defaults(self):
        whatsapp = importlib.import_module("aesora.gateways.whatsapp")
        self.assertEqual(whatsapp.validate_config({"enabled": True, "allowed": [], "tool_profile": "safe"}), [])
        self.assertTrue(whatsapp.validate_config({"enabled": True, "allowed": "bad", "tool_profile": "safe"}))
        self.assertTrue(whatsapp.validate_config({"enabled": True, "allowed": [], "tool_profile": "safe", "callback_port": "bad"}))
        bridge = whatsapp.render_bridge("bridge-test-token")
        self.assertIn("fromMe", bridge)
        self.assertIn("x-aesora-bridge", bridge.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
