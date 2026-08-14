"""Regression tests untuk fondasi Zeline publik.

Jalankan tanpa provider/API key sungguhan:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import html
import http.client
import importlib
import json
import re
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


def fresh_zeline(home: Path):
    """Reload package with a fully-isolated ZELINE_HOME."""
    os.environ["ZELINE_HOME"] = str(home)
    for module_name in list(sys.modules):
        if module_name == "zeline" or module_name.startswith("zeline."):
            sys.modules.pop(module_name, None)
    cfg = importlib.import_module("zeline.config")
    memory = importlib.import_module("zeline.memory")
    tools = importlib.import_module("zeline.tools")
    return cfg, memory, tools


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ZelinePublicCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "zeline-home"
        self.old_zeline_home = os.environ.get("ZELINE_HOME")
        self.old_key = os.environ.pop("ZELINE_API_KEY", None)
        self.old_base = os.environ.pop("ZELINE_BASE_URL", None)
        self.old_model = os.environ.pop("ZELINE_MODEL", None)
        self.config, self.memory, self.tools = fresh_zeline(self.home)

    def tearDown(self):
        if self.old_zeline_home is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self.old_zeline_home

        for key, value in (("ZELINE_API_KEY", self.old_key), ("ZELINE_BASE_URL", self.old_base), ("ZELINE_MODEL", self.old_model)):
            if value is not None:
                os.environ[key] = value
        self.temp.cleanup()

    def test_fresh_public_install_has_no_implicit_external_secret_import(self):
        """A public install cannot silently bind itself to another app's key."""
        self.assertEqual(self.config.API_KEY, "")
        self.assertEqual(self.config.BASE_URL, "https://api.openai.com/v1")
        self.assertEqual(self.config.MODEL, "gpt-4o-mini")

    def test_default_runtime_uses_zeline_identity(self):
        self.assertIn("Zeline", self.config.SYSTEM_PROMPT)
        self.assertIn("Zerolinear", self.config.SYSTEM_PROMPT)
        self.assertEqual(self.config.NAME, "Zeline")
        self.assertIn("execute", self.config.SYSTEM_PROMPT.lower())

    def test_existing_zeline_config_keeps_agent_name_and_model(self):
        saved = self.config.config_copy()
        saved["name"] = "Lucian"
        saved["provider"]["model"] = "keep-this-model"
        self.config.save_config(saved)

        normalized = self.config.stored_config_copy()

        self.assertEqual(normalized["name"], "Lucian")
        self.assertEqual(normalized["provider"]["model"], "keep-this-model")

    def test_seeded_superagent_skill_corpus_is_available_to_public_gateway(self):
        skill_system = importlib.import_module("zeline.skills")
        skill_system.seed_skills()
        content = skill_system.load_skill("superagent-v7-sk0")
        self.assertIn("Skill Registry", content)

    def test_bundled_skills_do_not_expose_upstream_branding(self):
        skill_root = Path(__file__).resolve().parents[1] / "zeline" / "skills"
        source_suffixes = {".md", ".txt", ".py", ".sh", ".ts", ".json", ".yml", ".yaml"}
        sources = sorted(
            path for path in skill_root.rglob("*")
            if path.is_file() and path.suffix.lower() in source_suffixes
        )
        self.assertTrue(sources)
        unknown = sorted(
            str(path.relative_to(skill_root)) for path in skill_root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
            and path.suffix.lower() not in source_suffixes
        )
        self.assertEqual(unknown, [])
        leaked = []
        for path in sources:
            text = path.read_text(encoding="utf-8")
            if "hermes" in text.casefold():
                leaked.append(str(path.relative_to(skill_root)))
        self.assertEqual(leaked, [])

    def test_bundled_skills_do_not_invent_renamed_runtime_contracts(self):
        skill_root = Path(__file__).resolve().parents[1] / "zeline" / "skills"
        forbidden = (
            "skills/zeline/", "zeline/references/", "zeline/scripts/governor.py",
            "ZELINE_MASTER_PW", "ZELINE_SIGNING_KEY", "ZELINE_ALERTS_DB",
            "ZELINE_BRIEFING_STATE", "ZELINE_BOT_HEARTBEAT", "ZELINE_VAULT_DB",
            "ZELINE_WHISPER_MODEL", "tools/skill_integrity.py",
        )
        findings = []
        for path in skill_root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".py", ".sh", ".ts"}:
                text = path.read_text(encoding="utf-8")
                for marker in forbidden:
                    if marker in text:
                        findings.append(f"{path.relative_to(skill_root)}: {marker}")
        self.assertEqual(findings, [])

    def test_mem0_skill_uses_supported_manual_auth_configuration(self):
        path = Path(__file__).resolve().parents[1] / "zeline" / "skills" / "mem0-memory-mcp" / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("zeline mcp add", text)
        self.assertIn("config.json", text)
        self.assertIn("Authorization: Bearer <key>", text)

    def test_folder_based_skill_is_seeded_listed_and_loaded(self):
        """Folder skill is tested from an isolated source, never package data."""
        skill_system = importlib.import_module("zeline.skills")
        source_root = self.home / "bundled-test-skills"
        folder = source_root / "folder-demo-skill"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(
            "---\nname: folder-demo-skill\ndescription: Demo folder skill.\n---\n\n# Folder Demo\n\nLangkah utama di sini.\n",
            encoding="utf-8",
        )
        refs = folder / "references"
        refs.mkdir(exist_ok=True)
        (refs / "extra.md").write_text("Detail tambahan.\n", encoding="utf-8")

        skill_system.seed_skills(source=source_root)
        names = [name for _scope, name, _title, _desc in skill_system.list_skill_entries()]
        self.assertIn("folder-demo-skill", names)
        content = skill_system.load_skill("folder-demo-skill")
        self.assertIn("Langkah utama di sini", content)
        self.assertIn("references/extra.md", content)

    def test_memory_isolated_between_platform_users(self):
        one = self.memory.MemoryStore("telegram:100")
        two = self.memory.MemoryStore("telegram:200")
        one.add("Suka kopi tanpa gula")
        self.assertIn("kopi", one.formatted())
        self.assertEqual(two.formatted(), "(memory empty)")

    def test_memory_per_identity_has_bounded_fact_count(self):
        store = self.memory.MemoryStore("telegram:bounded")
        with mock.patch.object(self.memory, "MAX_FACTS_PER_IDENTITY", 2):
            self.assertIn("saved", store.add("fakta satu"))
            self.assertIn("saved", store.add("fakta dua"))
            self.assertIn("limit", store.add("fakta tiga").lower())
        self.assertEqual(store.list(), ["fakta satu", "fakta dua"])

    def test_memory_rejects_new_identity_after_global_file_limit(self):
        self.memory.MemoryStore("telegram:first").add("fakta pertama")
        with mock.patch.object(self.memory, "MAX_IDENTITIES", 1):
            result = self.memory.MemoryStore("telegram:second").add("fakta kedua")
        self.assertIn("limit", result.lower())

    def test_session_history_survives_restart(self):
        # Simulasikan restart: buat store, simpan history, buang store, buat lagi.
        store_mod = importlib.import_module("zeline.session_store")
        persistence = store_mod.SessionPersistence(self.home / "sessions.db")
        persistence.save(
            "telegram:100",
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "budget gua 20k FundingPips"},
                {"role": "assistant", "content": "noted 20k"},
            ],
            title="FundingPips 20k",
        )
        # 'Restart' → instance baru membaca DB yang sama.
        reopened = store_mod.SessionPersistence(self.home / "sessions.db")
        messages, title = reopened.load("telegram:100")
        self.assertEqual(title, "FundingPips 20k")
        self.assertTrue(any("FundingPips" in str(m.get("content")) for m in messages))
        # Isolasi antar identity.
        other, _ = reopened.load("telegram:999")
        self.assertEqual(other, [])
        # reset menghapus history disk.
        self.assertTrue(reopened.reset("telegram:100"))
        self.assertEqual(reopened.load("telegram:100"), ([], None))

    def test_session_store_hydrates_agent_from_disk(self):
        sessions_mod = importlib.import_module("zeline.sessions")
        store_mod = importlib.import_module("zeline.session_store")
        persistence = store_mod.SessionPersistence(self.home / "sessions.db")
        persistence.save(
            "telegram:100",
            [
                {"role": "system", "content": "sys lama"},
                {"role": "user", "content": "inget budget 20k"},
                {"role": "assistant", "content": "oke"},
            ],
            title="Budget 20k",
        )
        store = sessions_mod.SessionStore(persistence=persistence)
        session = store.get_or_create("telegram:100", tool_profile="safe")
        # Agent baru harus sudah berisi history lama, dengan system prompt FRESH.
        self.assertEqual(session.title, "Budget 20k")
        self.assertEqual(session.agent.messages[0]["role"], "system")
        self.assertNotEqual(session.agent.messages[0]["content"], "sys lama")
        self.assertTrue(any("budget 20k" in str(m.get("content")).lower() for m in session.agent.messages))

    def test_safe_profile_cannot_access_file_or_shell(self):
        executor = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        names = {item["function"]["name"] for item in executor.schemas}
        self.assertIn("add_memory", names)
        self.assertNotIn("read_file", names)
        self.assertNotIn("run_shell", names)
        self.assertIn("not allowed", executor.run("run_shell", {"command": "id"}))

    def test_safe_profile_has_web_tools(self):
        executor = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        names = {item["function"]["name"] for item in executor.schemas}
        self.assertIn("web_search", names)
        self.assertIn("web_fetch", names)

    def test_runtime_info_reports_non_secret_self_configuration(self):
        cfg = self.config.config_copy()
        cfg["provider"].update({"base_url": "https://provider.example/v1", "api_key": "never-print-this", "model": "model-x", "protocol": "openai"})
        self.config.save_config(cfg)
        executor = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)

        result = executor.run("runtime_info", {})

        self.assertIn("model-x", result)
        self.assertIn("provider.example", result)
        self.assertIn("openai", result)
        self.assertNotIn("never-print-this", result)
        self.assertIn("runtime_info", {item["function"]["name"] for item in executor.schemas})

    def test_seeded_self_analysis_skill_is_available(self):
        skills = importlib.import_module("zeline.skills")
        skills.seed_skills()
        content = skills.load_skill("self-analysis")
        self.assertIn("runtime_info", content)
        self.assertIn("API key", content)

    def test_seeded_response_formatting_skill_is_available(self):
        skills = importlib.import_module("zeline.skills")
        skills.seed_skills()
        content = skills.load_skill("response-formatting")
        self.assertIn("**bold**", content)
        self.assertIn("```bash", content)
        self.assertIn("```html", content)
        self.assertIn("jangan mengarang", content.lower())

    def test_seeded_tmdb_media_maintenance_skill_preserves_existing_player_scope(self):
        skills = importlib.import_module("zeline.skills")
        skills.seed_skills()
        content = skills.load_skill("tmdb-media-web-maintenance")
        self.assertIn("existing player", content.lower())
        self.assertIn("do not automatically remove", content.lower())
        self.assertIn("license", content.lower())
        self.assertIn("DRM", content)

    def test_system_prompt_contains_default_response_formatting_rules(self):
        self.assertIn("**bold**", self.config.SYSTEM_PROMPT)
        self.assertIn("fenced code block", self.config.SYSTEM_PROMPT)
        self.assertIn("terminal", self.config.SYSTEM_PROMPT.lower())

    def test_web_fetch_blocks_internal_addresses(self):
        executor = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        for url in ("http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/", "http://192.168.1.1/"):
            self.assertIn("blocked", executor.run("web_fetch", {"url": url}))
        self.assertIn("blocked", executor.run("web_fetch", {"url": "http://localhost.localdomain/"}))
        self.assertIn("ERROR", executor.run("web_fetch", {"url": "ftp://example.com/file"}))

    def test_detects_cloudflare_challenge_page(self):
        # Halaman 'Just a moment…' / _cf_chl_opt bukan konten asli — harus
        # dikenali supaya web_fetch tidak balikin sampah challenge.
        tools = importlib.import_module("zeline.tools")
        challenge = '<html><head><title>Just a moment...</title></head><body><script>window._cf_chl_opt={cRay:"x"}</script></body></html>'
        self.assertTrue(tools._looks_like_cf_challenge(challenge))
        self.assertFalse(tools._looks_like_cf_challenge("<html><body>Konten normal soal FTMO challenge 10% profit target.</body></html>"))

    def test_web_fetch_falls_back_to_wayback_on_cloudflare(self):
        # Kalau reader proxy & fetch langsung balik halaman challenge CF,
        # web_fetch harus jatuh ke snapshot archive.org (bukan return sampah).
        tools = importlib.import_module("zeline.tools")
        cf_page = '<html><title>Just a moment...</title><script>window._cf_chl_opt={}</script></html>'

        class Resp:
            ok = True
            text = cf_page
            def iter_content(self, n):
                yield cf_page.encode()

        with mock.patch.object(tools.requests, "get", return_value=Resp()), \
             mock.patch.object(tools, "_fetch_via_wayback", return_value="[via arsip web] Isi FTMO asli.") as wb:
            out = tools._web_fetch("https://ftmo.com/en/how-it-works/")
        wb.assert_called_once()
        self.assertIn("arsip web", out)
        self.assertIn("FTMO", out)

    def test_web_fetch_reports_error_when_blocked_and_no_archive(self):
        tools = importlib.import_module("zeline.tools")
        cf_page = '<html><title>Just a moment...</title></html>'

        class Resp:
            ok = True
            text = cf_page
            def iter_content(self, n):
                yield cf_page.encode()

        with mock.patch.object(tools.requests, "get", return_value=Resp()), \
             mock.patch.object(tools, "_fetch_via_wayback", return_value=None):
            out = tools._web_fetch("https://ftmo.com/en/how-it-works/")
        self.assertIn("ERROR", out)
        self.assertIn("Cloudflare", out)

    def test_http_request_blocks_internal_and_bad_scheme(self):
        executor = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        # http_request harus tersedia bahkan di profile safe (SSRF-protected)
        names = {item["function"]["name"] for item in executor.schemas}
        self.assertIn("http_request", names)
        self.assertIn("system_env", names)
        self.assertIn("blocked", executor.run("http_request", {"method": "POST", "url": "http://127.0.0.1:20128/v1"}))
        self.assertIn("blocked", executor.run("http_request", {"method": "GET", "url": "http://169.254.169.254/latest/"}))
        self.assertIn("ERROR", executor.run("http_request", {"method": "GET", "url": "ftp://example.com/x"}))
        self.assertIn("unsupported", executor.run("http_request", {"method": "TRACE", "url": "https://example.com"}))

    def test_http_request_rejects_bad_headers_json(self):
        executor = self.tools.ToolExecutor("cli:local", profile="safe", workspace=self.home)
        self.assertIn("ERROR", executor.run("http_request", {"method": "GET", "url": "https://example.com", "headers": "{not json"}))

    def test_system_env_reports_environment_without_secrets(self):
        executor = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        result = executor.run("system_env", {})
        self.assertIn("OS", result)
        self.assertIn("Python", result)
        self.assertIn("Installed tools", result)

    def test_analyze_media_is_owner_gated_and_validates_input(self):
        # safe profile (gateway publik) TIDAK boleh punya analyze_media.
        safe = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        self.assertNotIn("analyze_media", {item["function"]["name"] for item in safe.schemas})
        # workspace/full punya tool-nya.
        ws = self.home / "media-ws"
        ws.mkdir(parents=True, exist_ok=True)
        executor = self.tools.ToolExecutor("cli:local", profile="workspace", workspace=ws)
        self.assertIn("analyze_media", {item["function"]["name"] for item in executor.schemas})
        # URL internal diblokir.
        self.assertIn("blocked", executor.run("analyze_media", {"path_or_url": "http://169.254.169.254/x.png"}))
        # File audio → diarahkan ke transkrip, bukan mengarang isi.
        audio = ws / "clip.ogg"
        audio.write_bytes(b"fakeaudio")
        self.assertIn("audio", executor.run("analyze_media", {"path_or_url": "clip.ogg"}).lower())
        # File video → diarahkan ke ekstraksi frame.
        vid = ws / "clip.mp4"
        vid.write_bytes(b"fakevideo")
        self.assertIn("video", executor.run("analyze_media", {"path_or_url": "clip.mp4"}).lower())

    def test_download_file_is_workspace_gated_and_ssrf_protected(self):
        # safe profile TIDAK boleh punya download_file (nulis ke disk)
        safe = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        self.assertNotIn("download_file", {item["function"]["name"] for item in safe.schemas})
        # workspace profile punya, tapi SSRF + path escape diblokir
        workspace = self.home / "dl-ws"
        workspace.mkdir(parents=True)
        executor = self.tools.ToolExecutor("cli:local", profile="workspace", workspace=workspace)
        self.assertIn("blocked", executor.run("download_file", {"url": "http://169.254.169.254/x", "path": "meta.txt"}))
        self.assertIn("workspace", executor.run("download_file", {"url": "https://example.com/x", "path": "../escape.txt"}))
        self.assertFalse((self.home / "escape.txt").exists())

    def _write_fake_mcp_server(self) -> Path:
        self.home.mkdir(parents=True, exist_ok=True)
        script = self.home / "fake_mcp.py"
        script.write_text(
            "import json, sys\n"
            "def send(o):\n"
            "    sys.stdout.write(json.dumps(o)+'\\n'); sys.stdout.flush()\n"
            "for line in sys.stdin:\n"
            "    line=line.strip()\n"
            "    if not line: continue\n"
            "    req=json.loads(line); m=req.get('method'); rid=req.get('id')\n"
            "    if m=='initialize': send({'jsonrpc':'2.0','id':rid,'result':{'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'fake','version':'1'}}})\n"
            "    elif m=='notifications/initialized': pass\n"
            "    elif m=='tools/list': send({'jsonrpc':'2.0','id':rid,'result':{'tools':[{'name':'add','description':'sum','inputSchema':{'type':'object','properties':{'a':{'type':'number'},'b':{'type':'number'}}}}]}})\n"
            "    elif m=='tools/call':\n"
            "        a=req['params']['arguments']; t=float(a.get('a',0))+float(a.get('b',0))\n"
            "        send({'jsonrpc':'2.0','id':rid,'result':{'content':[{'type':'text','text':'sum='+str(t)}]}})\n"
            "    elif rid is not None: send({'jsonrpc':'2.0','id':rid,'error':{'code':-32601,'message':'nf'}})\n",
            encoding="utf-8",
        )
        return script

    def test_mcp_stdio_discovery_and_call(self):
        mcp = importlib.import_module("zeline.mcp")
        script = self._write_fake_mcp_server()
        server = mcp.MCPServer(name="fake", transport="stdio", command=f"{sys.executable} {script}")
        try:
            tools = server.list_tools()
            names = [t["function"]["name"] for t in tools]
            self.assertIn("mcp__fake__add", names)
            self.assertEqual(server.call_tool("add", {"a": 2, "b": 3}), "sum=5.0")
        finally:
            server.close()

    def test_mcp_only_available_to_operator_profiles(self):
        mcp = importlib.import_module("zeline.mcp")
        script = self._write_fake_mcp_server()
        self.config.MCP_SERVERS = {"fake": {"transport": "stdio", "command": f"{sys.executable} {script}"}}
        try:
            # safe (gateway publik) TIDAK boleh dapat tool MCP (server = perintah lokal)
            safe = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
            self.assertFalse(any(n["function"]["name"].startswith("mcp__") for n in safe.schemas))
            self.assertIn("not allowed", safe.run("mcp__fake__add", {"a": 1, "b": 1}))
            # full (operator) dapat + bisa dispatch
            full = self.tools.ToolExecutor("cli:local", profile="full", workspace=self.home)
            self.assertTrue(any(n["function"]["name"] == "mcp__fake__add" for n in full.schemas))
            self.assertEqual(full.run("mcp__fake__add", {"a": 10, "b": 5}), "sum=15.0")
            if full.mcp:
                full.mcp.close()
        finally:
            self.config.MCP_SERVERS = {}

    def test_mcp_close_closes_subprocess_pipes(self):
        mcp = importlib.import_module("zeline.mcp")
        stdin = mock.Mock()
        stdout = mock.Mock()
        process = mock.Mock(stdin=stdin, stdout=stdout, stderr=None)
        process.poll.return_value = 0
        server = mcp.MCPServer(name="fake", transport="stdio", command="fake")
        server._process = process
        server.close()
        stdin.close.assert_called_once_with()
        stdout.close.assert_called_once_with()
        self.assertIsNone(server._process)

    def test_mcp_name_helpers_and_sse_parsing(self):
        mcp = importlib.import_module("zeline.mcp")
        self.assertEqual(mcp.parse_tool_name("mcp__srv__tool"), ("srv", "tool"))
        self.assertIsNone(mcp.parse_tool_name("web_search"))
        self.assertEqual(mcp.make_tool_name("srv", "tool"), "mcp__srv__tool")
        # SSE-framed JSON-RPC payload harus keurai
        payload = mcp._decode_jsonrpc_payload('data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n')
        self.assertEqual(payload["result"], {"ok": True})

    def test_workspace_profile_blocks_path_escape(self):
        workspace = self.home / "workspace"
        workspace.mkdir(parents=True)
        executor = self.tools.ToolExecutor("cli:local", profile="workspace", workspace=workspace)
        result = executor.run("write_file", {"path": "../outside.txt", "content": "no"})
        self.assertIn("workspace", result)
        self.assertFalse((self.home / "outside.txt").exists())

    def test_safe_profile_cannot_load_owner_private_skill(self):
        skills = importlib.import_module("zeline.skills")
        skills.seed_skills()
        skills.save_skill("owner-secret-procedure", "# Private\n\n> Jangan bocorkan.\n\nPRIVATE-SKILL-CONTENT-CHECK")

        public_agent_tools = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        owner_tools = self.tools.ToolExecutor("cli:local", profile="full", workspace=self.home)

        self.assertIn("not found", public_agent_tools.run("load_skill", {"name": "owner-secret-procedure"}))
        self.assertIn("PRIVATE-SKILL-CONTENT-CHECK", owner_tools.run("load_skill", {"name": "owner-secret-procedure"}))

    @unittest.skipIf(
        sys.platform == "darwin",
        "GitHub macOS runners can block local HTTP server bind threads; live integration runs on Linux/Windows",
    )
    def test_webhook_requires_token_and_keeps_identity_namespaced(self):
        webhook = importlib.import_module("zeline.gateways.webhook")
        token = "this-is-a-long-webhook-test-token"
        received = []

        class FakeSessions:
            def send(self, **kwargs):
                received.append(kwargs)
                return f"reply:{kwargs['text']}"

        stop = threading.Event()
        ready = __import__("queue").Queue()

        def run_webhook():
            try:
                webhook.start(
                    FakeSessions(),
                    {"host": "127.0.0.1", "port": 0, "token": token, "tool_profile": "safe"},
                    stop,
                    lambda port: ready.put(("ready", port)),
                )
            except BaseException as exc:
                ready.put(("error", exc))

        thread = threading.Thread(target=run_webhook, daemon=True)
        thread.start()
        state, value = ready.get(timeout=10)
        if state == "error":
            raise value
        port = value
        deadline = time.monotonic() + 5
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
            headers={"Content-Type": "application/json", "X-Zeline-Token": token},
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
        telegram = importlib.import_module("zeline.gateways.telegram")
        self.assertTrue(telegram.validate_config({"token": "x", "tool_profile": "safe", "allowed": []}))
        self.assertEqual(telegram.validate_config({"token": "123:abc", "tool_profile": "safe", "allowed": []}), [])
        self.assertTrue(telegram._allowed(123, []))
        self.assertFalse(telegram._allowed(123, [456]))
        parts = telegram._split_message("a" * 8_010)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 4_000 for part in parts))

    def test_split_message_never_breaks_code_fence(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        # Prose + satu blok kode raksasa (>2 potongan) + prose penutup.
        code_body = "\n".join(f"line_{i} = compute({i})" for i in range(600))
        text = (
            "Berikut kodenya, jalankan di Termux:\n\n"
            f"```python\n{code_body}\n```\n\n"
            "Selesai — simpan lalu jalankan."
        )
        parts = telegram._split_message(text)
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), 4_000)
            # Tiap potongan yang berisi fence harus punya fence pembuka & penutup
            # seimbang (tidak pernah kebuka tanpa ditutup).
            self.assertEqual(part.count("```") % 2, 0, f"fence tidak seimbang di: {part[:60]!r}")
        # Semua baris kode harus tetap ada (tidak ada yang hilang saat dipecah).
        rejoined = "\n".join(parts)
        self.assertIn("line_0 = compute(0)", rejoined)
        self.assertIn("line_599 = compute(599)", rejoined)

    def test_split_message_keeps_short_code_block_intact_as_one_fence(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        text = "Jalankan ini:\n\n```bash\nmkdir -p ~/gamestore\ncd ~/gamestore\n```\n\nlalu buka browser."
        parts = telegram._split_message(text)
        # Muat dalam satu pesan → tidak dipecah sama sekali.
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].count("```"), 2)

    def test_telegram_full_profile_requires_owner_allowlist(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        errors = telegram.validate_config({"token": "123:abc", "tool_profile": "full", "allowed": []})
        self.assertTrue(any("allowlist" in error.lower() for error in errors))
        self.assertEqual(telegram.validate_config({"token": "123:abc", "tool_profile": "full", "allowed": [111222333]}), [])

    def test_telegram_working_status_matches_hermes_style(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        self.assertEqual(telegram._working_status_text(125), "⏳ Working — 2 min 5 s · provider is slow to respond")
        self.assertEqual(telegram._working_status_text(8), "⏳ Working — 8 s")

    def test_api_call_retries_send_on_transient_network_error(self):
        # sendMessage must retry on ConnectionError so a reply isn't lost when
        # Termux's link drops momentarily; it succeeds on a later attempt.
        telegram = importlib.import_module("zeline.gateways.telegram")
        import requests as _rq

        class OKResp:
            ok = True
            def json(self):
                return {"ok": True, "result": {"message_id": 1}}

        calls = {"n": 0}
        def flaky(url, json=None, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise _rq.ConnectionError("boom")
            return OKResp()

        with mock.patch.object(telegram.time, "sleep"), mock.patch.object(telegram.requests, "post", side_effect=flaky):
            out = telegram._api_call("bot-api", "sendMessage", chat_id=1, text="hi")
        self.assertIsNotNone(out)
        self.assertEqual(calls["n"], 3)  # retried until it went through

    def test_api_call_does_not_retry_non_retryable_method(self):
        # answerCallbackQuery is time-sensitive → NOT retried (single attempt).
        telegram = importlib.import_module("zeline.gateways.telegram")
        import requests as _rq
        calls = {"n": 0}
        def always_fail(url, json=None, timeout=None):
            calls["n"] += 1
            raise _rq.ConnectionError("boom")
        with mock.patch.object(telegram.time, "sleep"), mock.patch.object(telegram.requests, "post", side_effect=always_fail):
            out = telegram._api_call("bot-api", "answerCallbackQuery", callback_query_id="x")
        self.assertIsNone(out)
        self.assertEqual(calls["n"], 1)  # no retry

    def test_api_call_parse_error_fallback_unescapes_entities_to_plain_text(self):
        # Saat Telegram menolak HTML (tag pre/code tak seimbang), pesan dikirim
        # ulang sebagai teks polos. Entitas HTML (&gt; &amp; &lt;) HARUS
        # dikembalikan ke bentuk asli (> & <) — user tidak boleh melihat
        # '2&gt;/dev/null' mentah seperti bug di terminal card sebelumnya.
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Resp:
            def __init__(self, ok, payload):
                self.ok = ok
                self._payload = payload
            def json(self):
                return self._payload

        sent_texts = []

        def fake_post(url, json=None, timeout=None):
            sent_texts.append(json.get("text"))
            if json.get("parse_mode") == "HTML":
                return Resp(False, {"ok": False, "description": "Bad Request: can't parse entities: Can't find end tag \"pre\""})
            return Resp(True, {"ok": True, "result": {"message_id": 1}})

        with mock.patch.object(telegram.requests, "post", side_effect=fake_post):
            out = telegram._api_call(
                "bot-api", "sendMessage", chat_id=1,
                text='🖥️ Zeline Terminal\n<pre>which cloudflared 2&gt;/dev/null &amp;&amp; echo done</pre>',
                parse_mode="HTML",
            )
        self.assertIsNotNone(out)
        plain = sent_texts[-1]  # percobaan kedua (teks polos)
        self.assertNotIn("<pre>", plain)      # tag dibuang
        self.assertNotIn("&gt;", plain)        # entitas dikembalikan
        self.assertNotIn("&amp;", plain)
        self.assertIn("2>/dev/null", plain)    # terbaca natural
        self.assertIn("&& echo done", plain)

    def test_skills_block_shortens_long_descriptions(self):
        # The per-turn skills listing must stay compact — long multi-sentence
        # descriptions get trimmed to keep the injected system prompt lean.
        skills = importlib.import_module("zeline.skills")
        long_desc = "First short sentence. " + ("x" * 400)
        self.assertEqual(skills._short_desc(long_desc), "First short sentence")
        self.assertLessEqual(len(skills._short_desc("y" * 400)), 92)

    def test_dispatch_update_routes_text_to_agent(self):
        # _dispatch_update memproses satu update: pesan teks biasa → jalur agent.
        telegram = importlib.import_module("zeline.gateways.telegram")
        started = []

        class Sessions:
            pass

        with mock.patch.object(telegram, "_api_call", return_value={"result": {"message_id": 1}}), \
             mock.patch.object(telegram, "_start_agent_reply", side_effect=lambda *a, **k: started.append(k.get("text"))):
            update = {"update_id": 5, "message": {"chat": {"id": 111222333}, "text": "halo"}}
            telegram._dispatch_update(
                "bot-api", "token", Sessions(), update,
                allowed=[111222333], tool_profile="full", stop_event=threading.Event(),
            )
        self.assertEqual(started, ["halo"])

    def test_dispatch_update_denies_unlisted_chat(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        sent = []

        with mock.patch.object(telegram, "_api_call", side_effect=lambda a, m, **k: sent.append((m, k.get("text"))) or {"result": {"message_id": 1}}), \
             mock.patch.object(telegram, "_start_agent_reply") as start_reply:
            update = {"update_id": 9, "message": {"chat": {"id": 999}, "text": "halo"}}
            telegram._dispatch_update(
                "bot-api", "token", object(), update,
                allowed=[111222333], tool_profile="full", stop_event=threading.Event(),
            )
        start_reply.assert_not_called()
        self.assertTrue(any("not permitted" in str(t) for _m, t in sent))

    def test_telegram_working_heartbeat_keeps_typing_alive(self):
        # Heartbeat harus terus mengirim 'sendChatAction typing' agar indikator
        # 'sedang mengetik' tidak hilang saat model berpikir lama — TAPI tidak
        # membuat bubble progres baru (tidak ada sendMessage) selama menunggu.
        telegram = importlib.import_module("zeline.gateways.telegram")
        done = threading.Event()
        with mock.patch.object(telegram, "_api_call") as api:
            live = telegram._LiveStatus("bot-api", 42, model="m")
            worker = telegram._start_working_heartbeat("bot-api", 42, done, interval=0.01, status=live)
            time.sleep(0.05)
            done.set()
            worker.join(timeout=1)
        methods = [c.args[1] for c in api.call_args_list if len(c.args) > 1]
        self.assertIn("sendChatAction", methods)  # typing terus di-refresh
        self.assertNotIn("sendMessage", methods)  # tapi tidak bikin bubble 'Processing'

    def test_telegram_bubble_appears_only_on_tool_activity(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        sent = []
        with mock.patch.object(telegram, "_api_call", side_effect=lambda a, m, **k: sent.append(m) or {"result": {"message_id": 7}}):
            live = telegram._LiveStatus("bot-api", 42, model="m")
            live.set_waiting()   # menunggu LLM → tidak boleh bikin bubble
            live.tick()          # heartbeat → tidak boleh bikin bubble
            self.assertEqual([m for m in sent if m == "sendMessage"], [])
            live.add("🌐 Searching bitcoin")  # tool jalan → bubble muncul sekarang
            self.assertIn("sendMessage", sent)

    def test_telegram_renders_safe_markdown_as_html(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        source = "## Judul\n**Penting** pakai `zeline doctor`.\n```html\n<div>aman</div>\n```"
        rendered = telegram._markdown_to_telegram_html(source)
        self.assertIn("<b>Judul</b>", rendered)
        self.assertIn("<b>Penting</b>", rendered)
        self.assertIn("<code>zeline doctor</code>", rendered)
        self.assertIn('<pre><code class="language-html">&lt;div&gt;aman&lt;/div&gt;</code></pre>', rendered)
        self.assertNotIn("<div>aman</div>", rendered)

    def test_telegram_converts_markdown_table_to_labeled_list(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        source = (
            "| Tipe | Harga | Sifat |\n"
            "|---|---|---|\n"
            "| Zero 50k | ~$299 | Permanen |\n"
            "| 1-Step 50k | ~$319 | Permanen |"
        )
        rendered = telegram._markdown_to_telegram_html(source)
        # Tidak boleh ada pipe tabel mentah yang bocor ke output.
        self.assertNotIn("|---", rendered)
        self.assertNotIn("| Harga |", rendered)
        # Baris pertama jadi judul tebal, kolom lain jadi 'Header: nilai'.
        self.assertIn("<b>Zero 50k</b>", rendered)
        self.assertIn("Harga: ~$299", rendered)
        self.assertIn("Sifat: Permanen", rendered)
        self.assertIn("<b>1-Step 50k</b>", rendered)

    def test_telegram_normalizes_messy_output(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        messy = "Judul\n\n\n\n* poin satu\n+ poin dua\n•  poin tiga\nprose  dengan   spasi    ganda"
        cleaned = telegram._normalize_markdown(messy)
        # Baris kosong beruntun dirapatkan ke maksimal satu.
        self.assertNotIn("\n\n\n", cleaned)
        # Semua penanda bullet campur diseragamkan ke "- ".
        self.assertIn("- poin satu", cleaned)
        self.assertIn("- poin dua", cleaned)
        self.assertIn("- poin tiga", cleaned)
        # Spasi ganda di prose dirapikan.
        self.assertIn("prose dengan spasi ganda", cleaned)

    def test_telegram_bullets_and_headings_render_cleanly(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        source = "## Ringkasan\n- item satu\n- item dua"
        rendered = telegram._markdown_to_telegram_html(source)
        self.assertIn("<b>Ringkasan</b>", rendered)
        # Bullet "- " menjadi "• " yang rapi di Telegram.
        self.assertIn("• item satu", rendered)
        self.assertIn("• item dua", rendered)

    def test_telegram_normalize_preserves_code_block_content(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        source = "Teks\n```python\ndef f():\n    x  =  1\n    return x\n```"
        cleaned = telegram._normalize_markdown(source)
        # Spasi di dalam blok kode tidak boleh diutak-atik.
        self.assertIn("    x  =  1", cleaned)

    def test_telegram_agent_reply_uses_html_parse_mode(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def send(self, **_kwargs): return "**Berhasil**"

        with mock.patch.object(telegram, "_api_call") as api:
            telegram._send_agent_reply("bot-api", Sessions(), chat_id=1, identity="telegram:1", text="hi", tool_profile="safe")
        self.assertEqual(api.call_args.kwargs["parse_mode"], "HTML")
        self.assertIn("<b>Berhasil</b>", api.call_args.kwargs["text"])

    def test_telegram_streams_model_narration_as_separate_bubbles(self):
        # Kalimat rencana model yang menyertai tool call harus dikirim sebagai
        # bubble chat SEBELUM balasan akhir — inilah yang bikin alur kebaca
        # hidup (bubble penjelasan → tool → bubble berikutnya), bukan diam lalu
        # satu dump panjang. Simulasikan agent yang bernarasi 2x lalu selesai.
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def send(self, **kwargs):
                kwargs["on_narration"]("Gua bikin struktur HTML dulu.")
                kwargs["on_tool"]("write_file", {"path": "index.html"})
                kwargs["on_narration"]("Oke jalan, sekarang gua tambahin CSS.")
                kwargs["on_tool"]("write_file", {"path": "style.css"})
                return "Selesai — web ada di ~/site, jalanin `python -m http.server 8095`."

        sent = []
        with mock.patch.object(
            telegram, "_api_call",
            side_effect=lambda a, m, **k: sent.append((m, k.get("text"))) or {"result": {"message_id": len(sent)}},
        ):
            telegram._send_agent_reply("bot-api", Sessions(), chat_id=1, identity="telegram:1", text="bikin web", tool_profile="full")

        texts = [t for m, t in sent if m == "sendMessage" and t]
        joined = "\n---\n".join(texts)
        # Kedua kalimat narasi harus muncul sebagai pesan tersendiri.
        self.assertTrue(any("struktur HTML" in t for t in texts), joined)
        self.assertTrue(any("tambahin CSS" in t for t in texts), joined)
        # Balasan akhir juga terkirim.
        self.assertTrue(any("Selesai" in t for t in texts), joined)
        # Narasi datang SEBELUM balasan akhir (urutan hidup, bukan dump di akhir).
        idx_narr = next(i for i, t in enumerate(texts) if "struktur HTML" in t)
        idx_final = next(i for i, t in enumerate(texts) if "Selesai" in t)
        self.assertLess(idx_narr, idx_final)

    def test_telegram_live_status_never_blames_model_as_slow(self):
        # User membenci header yang menyalahkan model ("is slow to respond").
        # Tidak ada header sama sekali: tanpa aktivitas tool, _render() kosong.
        telegram = importlib.import_module("zeline.gateways.telegram")
        with mock.patch.object(telegram, "_api_call", return_value={"ok": True, "result": {"message_id": 1}}):
            live = telegram._LiveStatus("bot-api", 1, model="tabi/claude")
            live.set_waiting()
            live.phase_started = live.phase_started - 999  # paksa "sudah lama menunggu"
            rendered = live._render()
        self.assertEqual(rendered, "")
        self.assertNotIn("slow to respond", rendered)
        self.assertNotIn("Processing", rendered)
        self.assertNotIn("tabi/claude", rendered)

    def test_progress_ui_calls_never_block_agent_loop(self):
        # BUG yang diperbaiki: baris progres UI (bubble Processing, edit feed,
        # sendChatAction) dulu dipanggil dgn timeout 65s + retry. Di jaringan
        # Termux yang sering drop, tiap update MENAHAN loop agent → efek
        # 'lambat/cek-cek doang/bubble ilang-ilangan'. Sekarang panggilan UI
        # pakai timeout pendek & attempts=1 (fail-fast, dilewati diam-diam).
        telegram = importlib.import_module("zeline.gateways.telegram")
        seen = []

        def fake_api(_api, method, **kwargs):
            seen.append((method, kwargs.get("timeout"), kwargs.get("attempts")))
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 999}}
            return {"ok": True}

        with mock.patch.object(telegram, "_api_call", side_effect=fake_api):
            live = telegram._LiveStatus("bot-api", 1, model="m")
            live.add("🌐 Searching bitcoin")   # bikin + edit bubble progres
            live.set_waiting()
            live.finalize()

        # Semua panggilan progres UI harus fail-fast: timeout pendek, 1 attempt.
        progress = [(m, t, a) for m, t, a in seen if m in {"sendMessage", "editMessageText", "deleteMessage", "sendChatAction"}]
        self.assertTrue(progress)
        for method, timeout, attempts in progress:
            self.assertEqual(timeout, telegram._PROGRESS_TIMEOUT, method)
            self.assertEqual(attempts, telegram._PROGRESS_ATTEMPTS, method)

    def test_final_reply_sent_as_new_bubble_not_edited_live(self):
        # Streaming/live-edit dimatikan: jawaban akhir dikirim SEKALI sebagai
        # pesan baru yang utuh (sendMessage), bukan di-edit berulang. Tidak ada
        # editMessageText untuk balasan.
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def send(self, **_kwargs):
                return "Ini jawaban **final**."

        sent = []
        with mock.patch.object(
            telegram, "_api_call",
            side_effect=lambda a, m, **k: sent.append((m, k.get("text"))) or {"result": {"message_id": 55}},
        ):
            telegram._send_agent_reply("bot-api", Sessions(), chat_id=1, identity="telegram:1", text="tanya", tool_profile="safe")

        # Jawaban akhir dikirim sebagai sendMessage (bubble baru), bukan edit.
        final_sends = [t for m, t in sent if m == "sendMessage" and t and "<b>final</b>" in t]
        self.assertEqual(len(final_sends), 1)
        edits = [t for m, t in sent if m == "editMessageText" and t and "<b>final</b>" in t]
        self.assertEqual(edits, [])

    def test_no_streaming_reply_symbol_remains(self):
        # _StreamingReply sudah dihapus total (live-edit dimatikan) — pastikan
        # simbolnya tidak lagi ada di modul gateway.
        telegram = importlib.import_module("zeline.gateways.telegram")
        self.assertFalse(hasattr(telegram, "_StreamingReply"))

    def test_whatsapp_adapts_common_markdown(self):
        whatsapp = importlib.import_module("zeline.gateways.whatsapp")
        rendered = whatsapp._markdown_to_whatsapp("## Judul\n**Penting** dan `zeline doctor`\n```bash\nzeline status\n```")
        self.assertIn("*Judul*", rendered)
        self.assertIn("*Penting*", rendered)
        self.assertIn("`zeline doctor`", rendered)
        self.assertIn("```bash", rendered)

    def test_telegram_registers_hermes_style_command_picker(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        commands = telegram._telegram_commands()
        self.assertEqual([item["command"] for item in commands], ["start", "model", "status", "repository", "savetask", "updatetask", "completedtask", "deletetask", "stop", "new", "steer", "promoteskill"])
        self.assertEqual(commands[0]["description"], "Start Zeline")
        self.assertIn("active turn", commands[8]["description"].lower())

    def test_telegram_status_reports_hermes_style_runtime_and_coding_tools(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def status(self, identity):
                self.identity = identity
                return {
                    "session_id": "zel-abc123",
                    "title": "Bangun aplikasi",
                    "created": "2026-08-09 10:00:00",
                    "last_activity": "2026-08-09 10:05:00",
                    "model": "deepseek-v4-flash",
                    "context": "12 messages",
                    "agent_running": True,
                }

        sessions = Sessions()
        with mock.patch.object(telegram, "_provider_label", return_value="provider.test"), mock.patch.object(telegram, "_api_call") as api:
            handled = telegram._handle_command_update(
                "bot-api", "/status", sessions, "telegram:42", 42,
                stop_event=threading.Event(), tool_profile="full",
            )
        self.assertTrue(handled)
        text = api.call_args.kwargs["text"]
        self.assertEqual(sessions.identity, "telegram:42")
        self.assertEqual(api.call_args.kwargs["parse_mode"], "HTML")
        self.assertEqual(text, (
            "╭─ <b>Zeline Gateway Status</b>\n"
            "├ Session ID : <code>zel-abc123</code>\n"
            "├ Provider : <code>provider.test</code>\n"
            "├ Model : <code>deepseek-v4-flash</code>\n"
            "├ Title : Bangun aplikasi\n"
            "├ Context : 12 messages\n"
            "├ Agent Running : Yes\n"
            "├ Platform : Telegram\n"
            "╰───────────────"
        ))

    def test_telegram_repository_sends_canonical_markdown_as_document_only(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        repository = self.home / "repository.md"
        repository.parent.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(telegram, "REPOSITORY_FILE", repository), mock.patch.object(telegram, "_send_document", return_value=True) as send, mock.patch.object(telegram, "_api_call") as api:
            handled = telegram._handle_command_update("bot-api", "/repository", object(), "telegram:42", 42, stop_event=threading.Event(), tool_profile="full")
        self.assertTrue(handled)
        send.assert_called_once_with("bot-api", 42, repository)
        api.assert_not_called()
        self.assertEqual(repository.name, "repository.md")
        self.assertEqual(repository.read_text(encoding="utf-8"), "## Repository Archive\n\n| # | Repository | Link |\n|---|------------|------|\n")

    def test_telegram_savetask_uses_discussed_title_and_project_link(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        repository = self.home / "repository.md"
        class Sessions:
            def task_snapshot(self, identity):
                self.identity = identity
                return {"title": "Bangun dashboard keuangan mobile yang sangat panjang", "messages": ["Deploy di https://finance.example.app sekarang"]}
        sessions = Sessions()
        with mock.patch.object(telegram, "REPOSITORY_FILE", repository), mock.patch.object(telegram, "_api_call") as api:
            handled = telegram._handle_command_update("bot-api", "/savetask", sessions, "telegram:42", 42, stop_event=threading.Event(), tool_profile="full", message_id=99)
        self.assertTrue(handled)
        self.assertEqual(sessions.identity, "telegram:42")
        saved = repository.read_text(encoding="utf-8")
        self.assertIn("| 🟡 | Bangun Dashboard Keuangan Mobile | [finance.example.app](https://finance.example.app) |", saved)
        self.assertEqual(api.call_args.kwargs["text"], "Task saved to repository.md.")

    def test_telegram_savetask_falls_back_to_save_message_deep_link(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        repository = self.home / "repository.md"
        class Sessions:
            def task_snapshot(self, _identity): return {"title": "Perbaiki autentikasi bot", "messages": ["tolong perbaiki login"]}
        with mock.patch.object(telegram, "REPOSITORY_FILE", repository), mock.patch.object(telegram, "_api_call"):
            telegram._handle_command_update("bot-api", "/savetask", Sessions(), "telegram:111222333", 111222333, stop_event=threading.Event(), tool_profile="full", message_id=123)
        self.assertIn("[Telegram message](tg://openmessage?user_id=111222333&message_id=123)", repository.read_text(encoding="utf-8"))

    def test_repository_crud_is_linked_and_matches_name_or_link(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        repository = self.home / "repository.md"
        first = {"title": "Bangun dashboard keuangan", "messages": ["https://old.example.app"]}
        updated = {"title": "Dashboard finance premium", "messages": ["https://new.example.app"]}
        with mock.patch.object(telegram, "REPOSITORY_FILE", repository):
            self.assertEqual(telegram._save_task(first, 42, 10), "saved")
            self.assertEqual(telegram._save_task(first, 42, 10), "duplicate")
            self.assertEqual(telegram._update_task("old.example.app", updated, 42, 11), "updated")
            self.assertIn("Dashboard Finance Premium", repository.read_text(encoding="utf-8"))
            self.assertNotIn("old.example.app", repository.read_text(encoding="utf-8"))
            self.assertEqual(telegram._delete_task("dashboard finance",), "deleted")
            self.assertEqual(repository.read_text(encoding="utf-8"), telegram.REPOSITORY_HEADER)

    def test_completed_task_changes_only_status_to_green(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        repository = self.home / "repository.md"
        with mock.patch.object(telegram, "REPOSITORY_FILE", repository):
            telegram._save_task({"title": "Project alpha", "messages": ["https://alpha.example.app"]}, 42, 1)
            self.assertEqual(telegram._complete_task("alpha.example.app"), "completed")
            saved = repository.read_text(encoding="utf-8")
            self.assertIn("| 🟢 | Project Alpha |", saved)
            self.assertNotIn("| 🟡 | Project Alpha |", saved)
            self.assertEqual(telegram._complete_task("project alpha"), "already_completed")

    def test_telegram_completedtask_requires_and_matches_name_or_link(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        repository = self.home / "repository.md"
        with mock.patch.object(telegram, "REPOSITORY_FILE", repository):
            telegram._save_task({"title": "Project alpha", "messages": ["https://alpha.example.app"]}, 42, 1)
            with mock.patch.object(telegram, "_api_call") as api:
                telegram._handle_command_update("bot-api", "/completedtask", object(), "telegram:42", 42, stop_event=threading.Event(), tool_profile="full")
            self.assertEqual(api.call_args.kwargs["text"], "Usage: /completedtask <project or link>")
            with mock.patch.object(telegram, "_api_call") as api:
                telegram._handle_command_update("bot-api", "/completedtask alpha.example.app", object(), "telegram:42", 42, stop_event=threading.Event(), tool_profile="full")
            self.assertEqual(api.call_args.kwargs["text"], "Task marked as finished in repository.md.")
            self.assertIn("| 🟢 | Project Alpha |", repository.read_text(encoding="utf-8"))

    def test_telegram_update_and_delete_require_project_name_or_link(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        class Sessions:
            def task_snapshot(self, _identity): return {"title": "Project baru", "messages": ["https://new.example.app"]}
        for command, usage in (("/updatetask", "Usage: /updatetask <project or link>"), ("/deletetask", "Usage: /deletetask <project or link>")):
            with mock.patch.object(telegram, "_api_call") as api:
                telegram._handle_command_update("bot-api", command, Sessions(), "telegram:42", 42, stop_event=threading.Event(), tool_profile="full", message_id=50)
            self.assertEqual(api.call_args.kwargs["text"], usage)

    def test_telegram_update_and_delete_find_by_name_or_link(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        repository = self.home / "repository.md"
        class Sessions:
            def task_snapshot(self, _identity): return {"title": "Project versi dua", "messages": ["https://new.example.app"]}
        with mock.patch.object(telegram, "REPOSITORY_FILE", repository):
            telegram._save_task({"title": "Project alpha", "messages": ["https://old.example.app"]}, 42, 1)
            with mock.patch.object(telegram, "_api_call") as api:
                telegram._handle_command_update("bot-api", "/updatetask old.example.app", Sessions(), "telegram:42", 42, stop_event=threading.Event(), tool_profile="full", message_id=2)
            self.assertEqual(api.call_args.kwargs["text"], "Task updated in repository.md.")
            with mock.patch.object(telegram, "_api_call") as api:
                telegram._handle_command_update("bot-api", "/deletetask project versi", Sessions(), "telegram:42", 42, stop_event=threading.Event(), tool_profile="full", message_id=3)
            self.assertEqual(api.call_args.kwargs["text"], "Task deleted from repository.md.")
            self.assertEqual(repository.read_text(encoding="utf-8"), telegram.REPOSITORY_HEADER)

    def test_session_task_snapshot_returns_title_and_recent_conversation(self):
        sessions_module = importlib.import_module("zeline.sessions")
        store = sessions_module.SessionStore()
        session = store.get_or_create("telegram:42", "safe")
        session.title = "Bangun mini app"
        session.agent.messages.extend([
            {"role": "user", "content": "buat mini app"},
            {"role": "assistant", "content": "siap"},
        ])
        self.assertEqual(store.task_snapshot("telegram:42"), {"title": "Bangun mini app", "messages": ["buat mini app", "siap"]})
        self.assertIsNone(store.task_snapshot("telegram:404"))

    def test_full_profile_exposes_coding_toolchain(self):
        tools = importlib.import_module("zeline.tools")
        executor = tools.ToolExecutor("telegram:owner", profile="full", workspace=str(self.home))
        names = [schema["function"]["name"] for schema in executor.schemas]
        for expected in ("read_file", "write_file", "edit_file", "patch_file", "search_files", "update_task", "run_shell"):
            self.assertIn(expected, names)

    def test_tool_enablement_is_snapshotted_for_one_executor_session(self):
        """Config changes affect new sessions, not schemas mid-model-turn."""
        tools = importlib.import_module("zeline.tools")
        executor = tools.ToolExecutor("telegram:stable", profile="full", workspace=str(self.home))
        self.assertIn("run_shell", {item["function"]["name"] for item in executor.schemas})
        with mock.patch.object(tools.config, "DISABLED_TOOLS", frozenset({"run_shell"})):
            still_same = {item["function"]["name"] for item in executor.schemas}
            fresh = tools.ToolExecutor("telegram:fresh", profile="full", workspace=str(self.home))
            fresh_names = {item["function"]["name"] for item in fresh.schemas}
        self.assertIn("run_shell", still_same)
        self.assertNotIn("run_shell", fresh_names)

    def test_gateway_runtime_rejects_elevated_profile_without_exact_owner_policy(self):
        gateways = importlib.import_module("zeline.gateways")
        base = {
            "enabled": True,
            "token": "123456789:valid-looking-test-token",
            "allowed": ["111222333"],
            "tool_profile": "full",
        }
        errors = gateways.validate_gateway("telegram", dict(base))
        self.assertTrue(any("owner_identity" in item for item in errors))
        configured = dict(base, owner_identity="111222333", remote_code_execution_ack=True)
        self.assertEqual(gateways.validate_gateway("telegram", configured), [])
        wildcard = dict(configured, tool_profile="workspace", allowed=["*"])
        self.assertTrue(any("exact owner allowlist" in item for item in gateways.validate_gateway("telegram", wildcard)))

    def test_webhook_runtime_never_accepts_elevated_profile(self):
        gateways = importlib.import_module("zeline.gateways")
        cfg = {
            "enabled": True,
            "token": "long-enough-test-token",
            "host": "127.0.0.1",
            "port": 8765,
            "tool_profile": "full",
            "remote_code_execution_ack": True,
        }
        errors = gateways.validate_gateway("webhook", cfg)
        self.assertTrue(any("safe" in item and "webhook" in item.lower() for item in errors))

    def test_safe_profile_exposes_deep_research_tool(self):
        tools = importlib.import_module("zeline.tools")
        executor = tools.ToolExecutor("telegram:user", profile="safe", workspace=str(self.home))
        names = [schema["function"]["name"] for schema in executor.schemas]
        # Riset multi-sumber tersedia untuk semua profile, termasuk gateway publik.
        self.assertIn("deep_research", names)
        self.assertIn("web_search", names)
        self.assertIn("web_fetch", names)

    def test_deep_research_synthesizes_multiple_sources(self):
        tools = importlib.import_module("zeline.tools")
        with mock.patch.object(tools, "_search_result_urls", return_value=["https://a.example", "https://b.example"]), \
             mock.patch.object(tools, "_web_fetch", side_effect=["Isi artikel A yang panjang.", "Isi artikel B yang panjang."]):
            out = tools._deep_research("topik riset")
        self.assertIn("https://a.example", out)
        self.assertIn("https://b.example", out)
        self.assertIn("Isi artikel A", out)
        self.assertIn("sintesis", out.lower())

    def test_deep_research_falls_back_to_web_search_when_no_urls(self):
        tools = importlib.import_module("zeline.tools")
        with mock.patch.object(tools, "_search_result_urls", return_value=[]), \
             mock.patch.object(tools, "_web_search", return_value="hasil pencarian ringkas") as ws:
            out = tools._deep_research("topik")
        ws.assert_called_once()
        self.assertEqual(out, "hasil pencarian ringkas")

    def test_full_profile_patch_and_task_tools_execute_real_actions(self):
        tools = importlib.import_module("zeline.tools")
        executor = tools.ToolExecutor("telegram:owner", profile="full", workspace=str(self.home))
        self.assertIn("OK", executor.run("write_file", {"path": "app.py", "content": "name = 'old'\n"}))
        patched = executor.run("patch_file", {"path": "app.py", "old_text": "'old'", "new_text": "'new'"})
        self.assertIn("patched", patched)
        self.assertEqual((self.home / "app.py").read_text(encoding="utf-8"), "name = 'new'\n")
        task = executor.run("update_task", {"task": "Run tests", "status": "in_progress"})
        self.assertIn("Run tests", task)
        self.assertIn("in_progress", task)

    def test_full_profile_execute_code_and_update_skill_execute_real_actions(self):
        tools = importlib.import_module("zeline.tools")
        executor = tools.ToolExecutor("telegram:owner", profile="full", workspace=str(self.home))
        code = executor.run("execute_code", {"code": "print(6 * 7)"})
        self.assertIn("exit=0", code)
        self.assertIn("42", code)
        saved = executor.run("save_skill", {"name": "demo-skill", "content": "# Demo\n\nold step\n"})
        self.assertIn("saved", saved)
        updated = executor.run("update_skill", {"name": "demo-skill", "old_text": "old step", "new_text": "new step"})
        self.assertIn("Patched SKILL.md", updated)
        self.assertIn("new step", executor.run("load_skill", {"name": "demo-skill"}))

    def test_safe_progress_line_balances_truncated_html_tags(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        # Long shell command inside <pre> that gets cut mid-tag must be re-balanced
        # so Telegram doesn't reject the edit with "Can't find end tag pre".
        raw = "🖥️ Zeline Terminal\n<pre>" + ("echo hello && " * 40) + "</pre>"
        out = telegram._safe_progress_line(raw, limit=80)
        self.assertLessEqual(out.count("<pre>"), out.count("</pre>"))
        self.assertTrue(out.count("<pre>") == out.count("</pre>"))
        self.assertNotIn("\n", out)
        # Short line with balanced tags passes through untouched (minus newline).
        ok = telegram._safe_progress_line("📖 Reading <code>a.py</code> L1-100")
        self.assertEqual(ok, "📖 Reading <code>a.py</code> L1-100")

    def test_telegram_tool_progress_uses_hermes_style_labels_and_argument_preview(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        self.assertEqual(telegram._tool_progress_text("load_skill", {"name": "test-driven-development"}), "📚 Reading skill test-driven-development")
        shell = telegram._tool_progress_text("run_shell", {"command": "python -m unittest tests.test_agent"})
        self.assertEqual(shell, "<pre>python -m unittest tests.test_agent</pre>")
        self.assertTrue(shell.endswith("</pre>"))
        self.assertNotIn("Zeline Terminal", shell)
        self.assertNotIn("📺", shell)
        # read_file dgn offset/limit → tampilkan rentang baris; basename saja (bukan path lokal).
        self.assertEqual(telegram._tool_progress_text("read_file", {"path": "zeline/agent.py", "offset": 1, "limit": 300}), "📖 Reading <code>agent.py</code> L1-300")
        # read_file tanpa offset/limit → tanpa rentang baris.
        self.assertEqual(telegram._tool_progress_text("read_file", {"path": "/data/data/com.termux/files/home/hotel-dashboard.html"}), "📖 Reading <code>hotel-dashboard.html</code>")
        self.assertEqual(telegram._tool_progress_text("write_file", {"path": "app.py"}), "📝 Writing <code>app.py</code>")
        self.assertEqual(telegram._tool_progress_text("edit_file", {"path": "app.py"}), "🎬 Editing <code>app.py</code>")
        self.assertEqual(telegram._tool_progress_text("patch_file", {"path": "app.py"}), "🎬 Editing <code>app.py</code>")
        self.assertEqual(telegram._tool_progress_text("search_files", {"query": "name"}), "🔎 Searching files name")
        self.assertEqual(telegram._tool_progress_text("add_memory", {"fact": "x"}), "🧠 Memory save")
        self.assertEqual(telegram._tool_progress_text("system_env", {}), "🧰 System_env")
        task = telegram._tool_progress_text("update_task", {"task": "Run tests", "status": "in_progress"})
        self.assertEqual(task, "📋 Updating tasks\n<code>in_progress</code> · Run tests")

    def test_telegram_terminal_progress_has_no_title_or_emoji(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        # Semua perintah shell (pencarian maupun coding) tampil sebagai blok
        # <pre> polos — TANPA judul 'Zeline Terminal' & TANPA emoji (dihapus
        # atas permintaan user).
        search = telegram._tool_progress_text("run_shell", {"command": "python -searching ftmo -v"})
        self.assertEqual(search, "<pre>python -searching ftmo -v</pre>")
        self.assertNotIn("Zeline Terminal", search)
        self.assertNotIn("📺", search)
        coding = telegram._tool_progress_text("run_shell", {"command": "pytest -q"})
        self.assertEqual(coding, "<pre>pytest -q</pre>")
        self.assertNotIn("Zeline Terminal", coding)
        self.assertNotIn("📺", coding)

    def test_telegram_web_progress_hides_raw_links(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        # web_fetch tidak menampilkan URL mentah (dan tidak jadi baris feed).
        fetched = telegram._tool_progress_text("web_fetch", {"url": "https://ftmo.com/en/"})
        self.assertNotIn("ftmo.com", fetched)
        self.assertEqual(fetched, "")
        # web_search hanya penanda ringkas: subjek (kata pertama) + '…', bukan kueri panjang.
        search = telegram._tool_progress_text("web_search", {"query": "FundedNext prop trading firm evaluation challenge"})
        self.assertEqual(search, "🌐 Searching FundedNext…")
        self.assertTrue(search.endswith("…"))
        # research menampilkan kueri lengkap (detail riset ada di sini).
        research = telegram._tool_progress_text("deep_research", {"query": "FundedNext prop firm review rules payout"})
        self.assertTrue(research.startswith("🪩 Researching FundedNext prop firm review"))

    def test_telegram_finalize_line_converts_searching_to_reading(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        # Saat selesai, web Searching & Researching (yang di-collapse) jadi satu
        # penanda '📖 Reading data/other'; baris file dibiarkan apa adanya.
        self.assertEqual(telegram._finalize_line("🌐 Searching FundedNext…"), "📖 Reading data/other")
        self.assertEqual(telegram._finalize_line("🪩 Researching FundedNext prop firm review"), "📖 Reading data/other")
        self.assertEqual(telegram._finalize_line("📖 Reading <code>agent.py</code> L1-27"), "📖 Reading <code>agent.py</code> L1-27")

    def test_telegram_live_status_collapses_only_search_research(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        def fake_api(_api, method, **kwargs):
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 999}}
            return {"ok": True}

        with mock.patch.object(telegram, "_api_call", side_effect=fake_api):
            live = telegram._LiveStatus("bot-api", 1)
            live.add("🌐 Searching FTMO 2025")
            live.add("🌐 Searching FTMO OANDA")   # kategori sama → collapse
            live.add("🌐 Searching FTMO rules")   # tetap satu baris search
            live.add("🪩 Researching FTMO")
        # Search/research tetap di-collapse (repetitif): 1 search + 1 research.
        searches = [l for l in live.lines if l.startswith("🌐")]
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0], "🌐 Searching FTMO rules")  # baris terbaru
        self.assertEqual(len([l for l in live.lines if l.startswith("🪩")]), 1)

    def test_telegram_live_status_does_not_collapse_coding_actions(self):
        # Aksi coding (baca/tulis/edit file, shell) TIDAK di-collapse — tiap
        # langkah harus kelihatan sendiri, bukan diringkas jadi satu baris.
        telegram = importlib.import_module("zeline.gateways.telegram")

        def fake_api(_api, method, **kwargs):
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 999}}
            return {"ok": True}

        with mock.patch.object(telegram, "_api_call", side_effect=fake_api):
            live = telegram._LiveStatus("bot-api", 1)
            live.add("📖 Reading <code>a.py</code> L1-100")
            live.add("📖 Reading <code>b.py</code> L1-100")
            live.add("📝 Writing <code>c.py</code>")
            live.add("📺 Zeline Terminal\n<pre>ls</pre>")
        # Semua 4 aksi coding tampil terpisah (tidak digabung).
        self.assertEqual(len(live.lines), 4)

    def test_telegram_live_status_orders_search_research_first(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        def fake_api(_api, method, **kwargs):
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 999}}
            return {"ok": True}

        with mock.patch.object(telegram, "_api_call", side_effect=fake_api):
            live = telegram._LiveStatus("bot-api", 1)
            live.add("🪩 Researching FundingPips rules pricing")
            live.add("🌐 Searching FundingPips")
            live.add("📖 Reading <code>notes.md</code> L1-50")
            rendered = live._render()
        lines = [l for l in rendered.split("\n") if l.strip() and not l.startswith("<pre>")]
        # Search & research ditata dulu; aksi lain menyusul kronologis.
        self.assertEqual(lines[0], "🌐 Searching FundingPips")
        self.assertEqual(lines[1], "🪩 Researching FundingPips rules pricing")

    def test_telegram_turn_triggers_background_reflection(self):
        # Setelah turn sukses, gateway harus memicu sessions.reflect(identity)
        # di background — inilah yang bikin Zeline "sering self-improvement".
        telegram = importlib.import_module("zeline.gateways.telegram")
        called = {"reflect": None}

        class Sessions:
            def send(self, **_kwargs):
                return "beres"
            def reflect(self, identity, *a, **k):
                called["reflect"] = identity
                return None  # tidak ada skill baru → tidak kirim pesan

        with mock.patch.object(telegram, "_api_call", return_value={"result": {"message_id": 5}}):
            telegram._send_agent_reply("bot-api", Sessions(), chat_id=42, identity="telegram:42", text="hi", tool_profile="full")
            # beri thread background kesempatan jalan
            time.sleep(0.15)
        self.assertEqual(called["reflect"], "telegram:42")

    def test_telegram_progress_supports_code_skill_and_self_improvement(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        self.assertEqual(telegram._tool_progress_text("execute_code", {"code": "from pathlib import Path\nprint(Path.home())"}), "🐍 Running code from <code>from pathlib import Path</code>...")
        self.assertEqual(telegram._tool_progress_text("update_skill", {"name": "zeline-development"}), "📝 Updating skill <code>zeline-development</code>")
        # save_skill juga punya progress + hasil self-improvement.
        self.assertEqual(telegram._tool_progress_text("save_skill", {"name": "riset-prop-firm"}), "💡 Saving skill <code>riset-prop-firm</code>")
        result = telegram._tool_result_text("update_skill", {"name": "zeline-development"}, "Patched SKILL.md in skill 'zeline-development' (1 replacement).")
        self.assertEqual(result, "📒 Improvement: Patched SKILL.md in skill 'zeline-development' (1 replacement).")
        saved = telegram._tool_result_text("save_skill", {"name": "riset-prop-firm"}, "OK, private skill 'riset-prop-firm' saved.")
        self.assertEqual(saved, "📒 Improvement: OK, private skill 'riset-prop-firm' saved.")

    def test_telegram_live_status_no_header_when_only_waiting(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        def fake_api(_api, method, **kwargs):
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 999}}
            return {"ok": True}

        # Tanpa aktivitas tool (cuma menunggu) → tidak ada header/teks apa pun.
        with mock.patch.object(telegram, "_api_call", side_effect=fake_api):
            live = telegram._LiveStatus("bot-api", 1, model="tabi/claude")
            live.set_waiting()
            self.assertEqual(live._render(), "")

    def test_telegram_live_status_feed_only_no_header(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        def fake_api(_api, method, **kwargs):
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 999}}
            return {"ok": True}

        with mock.patch.object(telegram, "_api_call", side_effect=fake_api):
            live = telegram._LiveStatus("bot-api", 1, model="tabi/claude")
            live.add("🌐 Searching FTMO")
            rendered = live._render()
        # Feed aktivitas apa adanya — TANPA header 'Processing'/'Working'/dll.
        self.assertEqual(rendered, "🌐 Searching FTMO")
        self.assertNotIn("Processing", rendered)
        self.assertNotIn("Working", rendered)
        self.assertNotIn("Menunggu", rendered)

    def test_telegram_collapses_tool_activity_into_single_live_message(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def send(self, **kwargs):
                kwargs["on_tool"]("web_search", {"query": "FTMO rules"})
                kwargs["on_tool"]("web_fetch", {"url": "https://ftmo.com"})
                kwargs["on_tool"]("web_fetch", {"url": "https://investopedia.com"})
                return "hasil riset"

        # sendMessage mengembalikan message_id agar update berikutnya memakai editMessageText.
        def fake_api(_api, method, **kwargs):
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 999}}
            return {"ok": True}

        with mock.patch.object(telegram, "_api_call", side_effect=fake_api) as api:
            telegram._send_agent_reply("bot-api", Sessions(), chat_id=1, identity="telegram:1", text="riset ftmo", tool_profile="safe")

        methods = [c.args[1] for c in api.call_args_list if len(c.args) > 1]
        # Hanya satu bubble live dibuat (sendMessage pertama), sisanya editMessageText.
        live_sends = [
            c for c in api.call_args_list
            if len(c.args) > 1 and c.args[1] == "sendMessage" and "hasil riset" not in str(c.kwargs.get("text", ""))
        ]
        self.assertEqual(len(live_sends), 1)
        self.assertIn("editMessageText", methods)  # update via edit
        # Bubble progres DIKUNCI sebagai catatan alur (feed final apa adanya,
        # TANPA header '✅ Successful' yang sudah dihapus), bukan dihapus.
        self.assertNotIn("deleteMessage", methods)
        self.assertNotIn("✅ Successful", str(api.call_args_list))
        # Feed final = baris aktivitas yang sudah di-finalize (Searching→Reading).
        finalized = [
            c for c in api.call_args_list
            if len(c.args) > 1 and c.args[1] == "editMessageText" and "Reading data/other" in str(c.kwargs.get("text", ""))
        ]
        self.assertTrue(finalized)
        # Jawaban final terkirim sebagai pesan terpisah.
        finals = [
            c for c in api.call_args_list
            if len(c.args) > 1 and c.args[1] == "sendMessage" and "hasil riset" in str(c.kwargs.get("text", ""))
        ]
        self.assertEqual(len(finals), 1)

    def test_telegram_direct_answer_removes_empty_progress_bubble(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def send(self, **kwargs):
                return "jawaban langsung"  # tanpa tool sama sekali

        def fake_api(_api, method, **kwargs):
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 999}}
            return {"ok": True}

        with mock.patch.object(telegram, "_api_call", side_effect=fake_api) as api:
            telegram._send_agent_reply("bot-api", Sessions(), chat_id=1, identity="telegram:1", text="hai", tool_profile="safe")

        methods = [c.args[1] for c in api.call_args_list if len(c.args) > 1]
        # Tanpa aktivitas tool, tidak ada bubble "✅ Selesai" kosong; kalaupun
        # sempat terbuat, dihapus. Jawaban final tetap terkirim.
        finals = [
            c for c in api.call_args_list
            if len(c.args) > 1 and c.args[1] == "sendMessage" and "jawaban langsung" in str(c.kwargs.get("text", ""))
        ]
        self.assertEqual(len(finals), 1)
        self.assertNotIn("✅ Successful", str(api.call_args_list))

    def test_telegram_model_picker_marks_current_model_and_uses_short_callbacks(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        models = ["vendor/model-a", "vendor/model-b"]
        text, markup = telegram._model_picker_payload(models, "vendor/model-b")
        buttons = [button for row in markup["inline_keyboard"] for button in row]
        self.assertIn("Current: vendor/model-b", text)
        self.assertEqual([button["callback_data"] for button in buttons[:2]], ["model:0", "model:1"])
        self.assertEqual(buttons[1]["text"], "✓ model-b")
        self.assertLessEqual(max(len(button["callback_data"]) for button in buttons), 64)

    def test_telegram_model_picker_shows_full_id_when_tail_collides(self):
        # Router IDs sharing the same final segment (e.g. Gr/claude-opus-4-8 and
        # tabi/claude-opus-4-8) must show the FULL id so the buttons aren't
        # two identical "claude-opus-4-8" rows with no provider prefix.
        telegram = importlib.import_module("zeline.gateways.telegram")
        models = ["Gr/claude-opus-4-8", "tabi/claude-opus-4-8", "th-orchestra"]
        text, markup = telegram._model_picker_payload(models, "tabi/claude-opus-4-8")
        buttons = [button for row in markup["inline_keyboard"] for button in row]
        labels = [b["text"] for b in buttons if b["callback_data"].startswith("model:") and b["callback_data"] != "model:cancel"]
        # Colliding ones keep their route prefix; current one is check-marked.
        self.assertIn("Gr/claude-opus-4-8", labels)
        self.assertIn("✓ tabi/claude-opus-4-8", labels)
        # Non-colliding id still shows the short tail.
        self.assertIn("th-orchestra", labels)
        # Callbacks stay index-based and within Telegram's 64-byte limit.
        self.assertLessEqual(max(len(b["callback_data"]) for b in buttons), 64)

    def test_discover_provider_models_uses_cache_to_avoid_repeat_calls(self):
        # Picker taps provider then model → without cache that's 2 network calls.
        # Second call within TTL must hit the cache (no second HTTP request).
        telegram = importlib.import_module("zeline.gateways.telegram")
        telegram._MODELS_CACHE.clear()
        provider = {"base_url": "https://prov.example/v1", "api_key": "k", "model": "m"}

        class FakeResp:
            ok = True
            def json(self):
                return {"data": [{"id": "a"}, {"id": "b"}]}

        with mock.patch.object(telegram.requests, "get", return_value=FakeResp()) as get:
            first = telegram._discover_provider_models(provider)
            second = telegram._discover_provider_models(provider)
        self.assertEqual(first, ["a", "b"])
        self.assertEqual(second, ["a", "b"])
        # Only ONE HTTP call despite two lookups → cache works.
        self.assertEqual(get.call_count, 1)
        telegram._MODELS_CACHE.clear()

    def test_telegram_model_root_picker_lists_named_providers_first(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        providers = [
            {"slug": "token-harbor", "name": "Token Harbor", "model": "model-a"},
            {"slug": "nvidia", "name": "NVIDIA NIM", "model": "model-b"},
        ]
        text, markup = telegram._provider_picker_payload(providers, "token-harbor")
        buttons = [button for row in markup["inline_keyboard"] for button in row]
        self.assertIn("Select a provider", text)
        self.assertEqual(buttons[0], {"text": "✓ Token Harbor", "callback_data": "provider:0"})
        self.assertEqual(buttons[1], {"text": "NVIDIA NIM", "callback_data": "provider:1"})

    def test_telegram_provider_callback_opens_models_with_back_button(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        providers = [{"slug": "token-harbor", "name": "Token Harbor", "model": "model-a", "base_url": "https://api.example/v1", "api_key": "key"}]
        callback = {"id": "cb-1", "data": "provider:0", "message": {"message_id": 9, "chat": {"id": 42}}}
        with mock.patch.object(telegram, "_configured_providers", return_value=providers), mock.patch.object(telegram, "_discover_provider_models", return_value=["model-a", "model-b"]), mock.patch.object(telegram, "_api_call") as api:
            telegram._handle_callback("bot-api", callback, object())
        edit = api.call_args_list[-1]
        self.assertEqual(edit.args[1], "editMessageText")
        self.assertIn("Provider: Token Harbor", edit.kwargs["text"])
        buttons = [button for row in edit.kwargs["reply_markup"]["inline_keyboard"] for button in row]
        self.assertIn({"text": "« Back", "callback_data": "provider:back"}, buttons)

    def test_telegram_model_command_without_argument_opens_inline_picker(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        with mock.patch.object(telegram, "_discover_models", return_value=["model-a", "model-b"]), mock.patch.object(telegram, "_api_call") as api:
            handled = telegram._handle_command_update(
                "bot-api", "/model", object(), "telegram:42", 42,
                stop_event=threading.Event(), tool_profile="safe",
            )
        self.assertTrue(handled)
        self.assertEqual(api.call_args.args[1], "sendMessage")
        self.assertIn("inline_keyboard", api.call_args.kwargs["reply_markup"])

    def test_telegram_model_callback_switches_model_and_edits_picker(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        cfg = self.config.config_copy()
        cfg["provider"]["api_key"] = "test-key"
        self.config.save_config(cfg)

        class Sessions:
            def __init__(self): self.reset_id = None; self.switched_id = None
            def reset(self, identity): self.reset_id = identity; return True
            def switch_provider(self, identity): self.switched_id = identity

        sessions = Sessions()
        callback = {"id": "cb-1", "data": "model:1", "message": {"message_id": 9, "chat": {"id": 42}}}
        with mock.patch.object(telegram, "_discover_models", return_value=["model-a", "model-b"]), mock.patch.object(telegram, "_fetch_model_capabilities", return_value={}), mock.patch.object(telegram, "_api_call") as api:
            telegram._handle_callback("bot-api", callback, sessions)
        self.assertEqual(self.config.config_copy()["provider"]["model"], "model-b")
        # Ganti model harus MEMPERTAHANKAN konteks (switch_provider), bukan reset.
        self.assertEqual(sessions.switched_id, "telegram:42")
        self.assertIsNone(sessions.reset_id)
        confirm = api.call_args.kwargs["text"]
        self.assertIn("context preserved", confirm)
        self.assertNotIn("Konteks", confirm)
        self.assertNotIn("New session started", confirm)
        methods = [call.args[1] for call in api.call_args_list]
        self.assertEqual(methods, ["answerCallbackQuery", "editMessageText"])

    def test_telegram_stop_cancels_active_turn_without_stopping_gateway(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def status(self, _identity): return {"title": "Bangun aplikasi", "agent_running": True}
            def stop(self, identity): self.stopped = identity; return True

        sessions = Sessions()
        gateway_stop = threading.Event()
        with mock.patch.object(telegram, "_api_call") as api:
            handled = telegram._handle_command_update(
                "bot-api", "/stop", sessions, "telegram:42", 42,
                stop_event=gateway_stop, tool_profile="safe",
            )
        self.assertTrue(handled)
        self.assertEqual(sessions.stopped, "telegram:42")
        self.assertFalse(gateway_stop.is_set())
        self.assertEqual(api.call_args.kwargs["text"], "❄️ Bangun aplikasi")

    def test_telegram_stop_when_idle_uses_exact_message(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        class Sessions:
            def status(self, _identity): return {"title": "New Session", "agent_running": False}
            def stop(self, _identity): return False
        with mock.patch.object(telegram, "_api_call") as api:
            telegram._handle_command_update("bot-api", "/stop", Sessions(), "telegram:42", 42, stop_event=threading.Event(), tool_profile="safe")
        self.assertEqual(api.call_args.kwargs["text"], "No active task to stop.")

    def test_telegram_new_stops_old_turn_and_resets_session(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def stop(self, identity): self.stopped = identity; return True
            def reset(self, identity): self.reset_id = identity; return True

        sessions = Sessions()
        with mock.patch.object(telegram, "_api_call") as api:
            handled = telegram._handle_command_update(
                "bot-api", "/new", sessions, "telegram:42", 42,
                stop_event=threading.Event(), tool_profile="safe",
            )
        self.assertTrue(handled)
        self.assertEqual(sessions.stopped, "telegram:42")
        self.assertEqual(sessions.reset_id, "telegram:42")
        text = api.call_args.kwargs["text"]
        self.assertIn("🌟 Session reset! Starting fresh.", text)
        self.assertIn("✦ Model :", text)
        self.assertIn("✦ Provider :", text)
        self.assertIn("✦ Context : 0 tokens", text)
        self.assertIn("✦ Endpoint :", text)
        self.assertIn("✦ Tip :", text)

    def test_telegram_steer_targets_active_turn_or_runs_normally_when_idle(self):
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def __init__(self, accepted): self.accepted = accepted
            def steer(self, identity, text): self.steer_args = (identity, text); return self.accepted

        active = Sessions(True)
        with mock.patch.object(telegram, "_api_call") as api, mock.patch.object(telegram, "_start_agent_reply") as start_reply:
            handled = telegram._handle_command_update(
                "bot-api", "/steer fokus ke bug", active, "telegram:42", 42,
                stop_event=threading.Event(), tool_profile="safe",
            )
        self.assertTrue(handled)
        self.assertEqual(active.steer_args, ("telegram:42", "fokus ke bug"))
        self.assertIn("Steer queued", api.call_args.kwargs["text"])
        start_reply.assert_not_called()

        idle = Sessions(False)
        with mock.patch.object(telegram, "_api_call"), mock.patch.object(telegram, "_start_agent_reply") as start_reply:
            telegram._handle_command_update(
                "bot-api", "/steer fokus ke bug", idle, "telegram:42", 42,
                stop_event=threading.Event(), tool_profile="safe",
            )
        start_reply.assert_called_once_with(
            "bot-api", idle, chat_id=42, identity="telegram:42",
            text="fokus ke bug", tool_profile="safe",
        )

    def test_telegram_model_command_persists_model_and_keeps_context(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        cfg = self.config.config_copy()
        cfg["provider"]["api_key"] = "test-key"
        self.config.save_config(cfg)

        class Sessions:
            def __init__(self): self.reset_id = None; self.switched_id = None
            def reset(self, identity): self.reset_id = identity; return True
            def switch_provider(self, identity): self.switched_id = identity

        sessions = Sessions()
        reply = telegram._handle_command("/model vendor/new-model", sessions, "telegram:42", stop_event=threading.Event())
        self.assertIn("vendor/new-model", reply)
        # Konteks HARUS dijaga saat ganti model (switch_provider), bukan reset.
        self.assertEqual(sessions.switched_id, "telegram:42")
        self.assertIsNone(sessions.reset_id)
        self.assertNotIn("reset", reply.lower())
        self.assertEqual(self.config.config_copy()["provider"]["model"], "vendor/new-model")

    def test_telegram_accepts_zip_larger_than_legacy_256_kb_limit(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        payload = b"z" * (729 * 1024)
        document = {"file_name": "SUPERAGENT-V7-FORSALE-FINAL.zip", "file_size": len(payload), "file_id": "zip-file"}
        response = mock.Mock(ok=True, content=payload)
        with mock.patch.object(telegram, "_api_call", return_value={"result": {"file_path": "documents/zip-file"}}), \
             mock.patch.object(telegram.requests, "get", return_value=response):
            content, error = telegram._download_document("https://api.telegram.org/bottoken", "token", document)
        self.assertIsNone(error)
        self.assertEqual(content, payload)

    def test_telegram_extracts_zip_with_more_than_legacy_64_text_files(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        import io, zipfile
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for index in range(65):
                archive.writestr(f"skills/skill-{index}.md", f"skill {index}")
        text, error = telegram._extract_document_text("skills.zip", data.getvalue(), "application/zip")
        self.assertIsNone(error)
        self.assertIn("skill 64", text)

    def test_telegram_truncates_extracted_zip_text_to_message_limit(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        import io, zipfile
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            archive.writestr("skills/large.md", "x" * 20_000)
        text, error = telegram._extract_document_text("skills.zip", data.getvalue(), "application/zip")
        self.assertIsNone(error)
        prompt = telegram._build_document_prompt("skills.zip", text)
        self.assertLessEqual(len(prompt), 16_000)

    def test_telegram_extracts_text_and_safe_zip_entries(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        import io, zipfile
        text, error = telegram._extract_document_text("notes.md", b"# Hello\nZeline", "text/markdown")
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
        telegram = importlib.import_module("zeline.gateways.telegram")
        text, error = telegram._extract_document_text("image.png", b"not-a-real-image", "image/png")
        self.assertIsNone(error)
        self.assertIn("image", text.lower())
        self.assertIn("vision-capable", text.lower())

    def test_whatsapp_adapter_declares_validation_and_safe_defaults(self):
        whatsapp = importlib.import_module("zeline.gateways.whatsapp")
        self.assertEqual(whatsapp.validate_config({"enabled": True, "allowed": [], "tool_profile": "safe"}), [])
        self.assertTrue(whatsapp.validate_config({"enabled": True, "allowed": "bad", "tool_profile": "safe"}))
        self.assertTrue(whatsapp.validate_config({"enabled": True, "allowed": [], "tool_profile": "safe", "callback_port": "bad"}))
        bridge = whatsapp.render_bridge("bridge-test-token")
        self.assertIn("fromMe", bridge)
        self.assertIn("x-zeline-bridge", bridge.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
