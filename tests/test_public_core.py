"""Regression tests untuk fondasi Zeline publik.

Jalankan tanpa provider/API key sungguhan:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import hashlib
import html
import http.client
import importlib
import json
import re
import os
import stat
import socket
import subprocess
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
        soul_path = SOURCE_ROOT / "zeline" / "SOUL.md"
        self.assertTrue(soul_path.is_file())
        soul = soul_path.read_text(encoding="utf-8").strip()
        self.assertTrue(soul.startswith("# SOUL.md — Zeline"))
        self.assertEqual(self.config.SOUL, soul)
        self.assertIn("<zeline_soul>", self.config.SYSTEM_PROMPT)
        self.assertIn(soul, self.config.SYSTEM_PROMPT)
        self.assertIn("</zeline_soul>", self.config.SYSTEM_PROMPT)
        self.assertIn("Zeline", self.config.SYSTEM_PROMPT)
        self.assertIn("Zerolinear", self.config.SYSTEM_PROMPT)
        self.assertEqual(self.config.NAME, "Zeline")
        self.assertIn("execute", self.config.SYSTEM_PROMPT.lower())

    def test_runtime_soul_requires_authorization_and_never_promises_bypasses(self):
        soul = " ".join(self.config.SOUL.casefold().split())
        self.assertIn("authorized scope", soul)
        self.assertIn("never expose credentials", soul)
        self.assertIn("never promise a safeguard bypass", soul)
        self.assertIn("continue with every safe and useful part", soul)

    def test_runtime_soul_is_high_agency_execution_first_without_theatrics(self):
        """Public Zeline adopts high agency, not a private brand or persona."""
        soul = " ".join(self.config.SOUL.casefold().split())
        for required in (
            "execution-first",
            "persist through recoverable failures",
            "keep the operator visibly informed",
            "stop immediately",
            "do not resume cancelled work",
            "recover, adapt, and continue",
        ):
            with self.subTest(required=required):
                self.assertIn(required, soul)
        self.assertIn("not blind obedience", soul)
        self.assertIn("not theatrical", soul)

    def test_runtime_fails_clearly_when_packaged_soul_is_missing_or_empty(self):
        soul_path = SOURCE_ROOT / "zeline" / "SOUL.md"
        real_read_text = Path.read_text

        for replacement, message in (
            (FileNotFoundError(soul_path), "missing its canonical SOUL.md"),
            ("  \n", "SOUL.md is empty"),
        ):
            with self.subTest(message=message):
                def read_text(path, *args, **kwargs):
                    if Path(path) == soul_path:
                        if isinstance(replacement, Exception):
                            raise replacement
                        return replacement
                    return real_read_text(path, *args, **kwargs)

                with (
                    mock.patch.object(Path, "read_text", read_text),
                    self.assertRaisesRegex(RuntimeError, message),
                ):
                    self.config._load_soul()

    def test_system_prompt_avoids_blanket_refusals(self):
        prompt = " ".join(self.config.SYSTEM_PROMPT.casefold().split())
        self.assertIn(
            "evaluate the requested action, not just the topic, project label, technology, "
            "industry, or the presence of a dual-use component",
            prompt,
        )
        self.assertIn(
            "routine reading, debugging, maintenance, explanation, formatting, and other benign "
            "work should not be rejected merely because a nearby use could be risky",
            prompt,
        )
        self.assertIn(
            "do not refuse the entire request when only one specific step is unsafe, unauthorized, "
            "or impossible",
            prompt,
        )
        self.assertIn(
            "refuse only that specific part, explain the boundary briefly, and continue with the "
            "safe and useful parts immediately",
            prompt,
        )
        self.assertIn(
            "do not invent legal or policy claims, assume wrongdoing without evidence, or replace "
            "the user's requested product with a different one by default",
            prompt,
        )
        self.assertIn(
            "when ownership, authorization, consent, or intended scope genuinely changes what can "
            "be done and cannot be determined from context, ask one concise clarifying question",
            prompt,
        )

    def test_system_prompt_retains_action_safeguards(self):
        prompt = " ".join(self.config.SYSTEM_PROMPT.casefold().split())
        self.assertIn(
            "never promise to bypass safeguards or conceal wrongdoing",
            prompt,
        )
        self.assertIn(
            "offer the nearest lawful, authorized, technically honest path while preserving the "
            "user's goal",
            prompt,
        )
        self.assertIn(
            "only modify/manage the operator's own assets/accounts or explicitly authorized "
            "ones",
            prompt,
        )
        self.assertIn(
            "this ownership restriction does not prohibit reading public web pages",
            prompt,
        )
        self.assertIn(
            "do not invent a tos/legal/security refusal merely because a public website is "
            "third-party or uses cloudflare",
            prompt,
        )
        self.assertIn(
            "confirm with the operator before actions that move funds or are irreversible",
            prompt,
        )
        self.assertIn(
            "never log, print raw, or send secrets (private key, seed, api key) to outsiders",
            prompt,
        )

    def test_existing_zeline_config_keeps_agent_name_and_model(self):
        saved = self.config.config_copy()
        saved["name"] = "Lucian"
        saved["provider"]["model"] = "keep-this-model"
        self.config.save_config(saved)

        normalized = self.config.stored_config_copy()

        self.assertEqual(normalized["name"], "Lucian")
        self.assertEqual(normalized["provider"]["model"], "keep-this-model")

    def test_seeded_zenith_skill_corpus_is_available_to_public_gateway(self):
        skill_system = importlib.import_module("zeline.skills")
        skill_system.seed_skills()
        content = skill_system.load_skill("zeline-zenith-z0")
        self.assertIn("Skill Registry", content)

    def test_zenith_short_aliases_resolve_without_prefix_collisions(self):
        """The registry's skN shortcuts must select exactly their intended skill."""
        skill_system = importlib.import_module("zeline.skills")
        skill_system.seed_skills()
        # z0–z59 (from sk rebrand) + z61–z95 (from m/x rebrand). z60 is a gap.
        for index in list(range(60)) + list(range(61, 96)):
            with self.subTest(index=index):
                content = skill_system.load_skill(f"z{index}")
                self.assertNotIn("ERROR", content)
                self.assertIn(f"zeline-zenith-z{index}", content)

    def test_bundled_skill_cross_references_use_canonical_zenith_paths(self):
        skill_root = Path(__file__).resolve().parents[1] / "zeline" / "skills"
        stale_paths = []
        for path in sorted(skill_root.rglob("*.md")):
            matches = re.findall(r"skills/sk[0-9]+\.md", path.read_text(encoding="utf-8"))
            stale_paths.extend(f"{path.relative_to(skill_root)}: {match}" for match in matches)
        self.assertEqual(stale_paths, [])

    def test_seed_skills_refreshes_known_unmodified_bundled_revision(self):
        skills = importlib.import_module("zeline.skills")
        source_root = self.home / "bundled-revision-source"
        source_root.mkdir(parents=True)
        replacement = source_root / "zeline-zenith-z0.md"
        replacement.write_text("# Fixed registry\n\n> replacement\n", encoding="utf-8")
        target = skills.PUBLIC_SKILLS_DIR / replacement.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# Original registry\n\n> old\n", encoding="utf-8")
        old_digest = hashlib.sha256(target.read_bytes()).hexdigest()

        with mock.patch.dict(
            skills.BUNDLED_SKILL_UPDATE_DIGESTS,
            {replacement.name: (old_digest,)},
            clear=True,
        ):
            skills.seed_skills(source=source_root)

        self.assertEqual(target.read_bytes(), replacement.read_bytes())

    def test_seed_skills_preserves_customized_bundled_skill_during_refresh(self):
        skills = importlib.import_module("zeline.skills")
        source_root = self.home / "custom-bundled-revision-source"
        source_root.mkdir(parents=True)
        replacement = source_root / "zeline-zenith-z0.md"
        replacement.write_text("# Fixed registry\n\n> replacement\n", encoding="utf-8")
        target = skills.PUBLIC_SKILLS_DIR / replacement.name
        target.parent.mkdir(parents=True, exist_ok=True)
        custom = b"# My custom registry\n\n> keep this\n"
        target.write_bytes(custom)
        known_stock_digest = hashlib.sha256(b"# Original registry\n\n> old\n").hexdigest()

        with mock.patch.dict(
            skills.BUNDLED_SKILL_UPDATE_DIGESTS,
            {replacement.name: (known_stock_digest,)},
            clear=True,
        ):
            skills.seed_skills(source=source_root)

        self.assertEqual(target.read_bytes(), custom)

    def test_seed_skills_refresh_does_not_follow_symlink(self):
        skills = importlib.import_module("zeline.skills")
        skills._ensure_dirs()
        source_root = self.home / "symlink-bundled-revision-source"
        source_root.mkdir(parents=True)
        replacement = source_root / "zeline-zenith-z0.md"
        replacement.write_text("# Fixed registry\n\n> replacement\n", encoding="utf-8")
        victim = skills.SKILLS_ROOT / "outside-refresh-target.md"
        original = b"# Outside target\n"
        victim.write_bytes(original)
        target = skills.PUBLIC_SKILLS_DIR / replacement.name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.symlink_to(victim)
        except (OSError, NotImplementedError):
            self.skipTest("symlink is not supported on this platform")
        digest = hashlib.sha256(victim.read_bytes()).hexdigest()

        with mock.patch.dict(
            skills.BUNDLED_SKILL_UPDATE_DIGESTS,
            {replacement.name: (digest,)},
            clear=True,
        ):
            skills.seed_skills(source=source_root)

        self.assertTrue(target.is_symlink())
        self.assertEqual(victim.read_bytes(), original)

    def test_zenith_cross_reference_update_map_covers_pre_fix_revisions(self):
        skills = importlib.import_module("zeline.skills")
        expected = {
            "zeline-zenith-z0.md": {
                "577d36a35e97b4c461e769c723dc4a6187e99dd4646c5584f59e7d759be67a09",
                "629a599da79b90c6016d739ba19fe70afb8d7d79d56649507ef36d2767c7ba9a",
            },
            "zeline-zenith-z52.md": {
                "9afdaf5bf7613db366046418fb07cc90952ece1734c88d2aa4e839fefa39f0e6",
                "3bc375a999d48666cf809245298864710879ba4a6a799a617595ddec06114b78",
            },
        }
        for name, digests in expected.items():
            with self.subTest(name=name):
                self.assertTrue(digests.issubset(skills.BUNDLED_SKILL_UPDATE_DIGESTS[name]))

    def test_bundled_skills_do_not_expose_upstream_branding(self):
        skill_root = Path(__file__).resolve().parents[1] / "zeline" / "skills"
        # Every text type a skill may ship must be listed here, because the
        # branding scan below only reads these suffixes — an unlisted type would be
        # a hole in the scan. The `unknown` assertion enforces that: a new file kind
        # fails this test until it is added to the scanned set.
        source_suffixes = {
            ".md", ".txt", ".py", ".sh", ".ts", ".js", ".json", ".yml", ".yaml", ".html", ".css",
        }
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
        upstream_terms = (
            "her" + "mes", "open" + "claw", "claw" + "hub", "aes" + "ora",
            "super" + "agent", "iron" + "claw",
        )
        leaked = []
        for path in sources:
            folded = path.read_text(encoding="utf-8").casefold()
            hits = sorted(term for term in upstream_terms if term in folded)
            if hits:
                leaked.append(f"{path.relative_to(skill_root)}: {','.join(hits)}")
        self.assertEqual(leaked, [])

    def test_renamed_zenith_corpus_is_bundled_under_new_name_only(self):
        skill_root = Path(__file__).resolve().parents[1] / "zeline" / "skills"
        zenith = sorted(path.name for path in skill_root.glob("zeline-zenith-z*.md"))
        self.assertGreaterEqual(len(zenith), 109)

    def test_skills_module_does_not_expose_retired_pack_branding(self):
        source = (SOURCE_ROOT / "zeline" / "skills.py").read_text(encoding="utf-8").casefold()
        retired_terms = ("super" + "agent", "iron" + "claw")
        for term in retired_terms:
            self.assertNotIn(term, source)

    def test_retired_bundled_digest_map_is_complete_and_well_formed(self):
        skills = importlib.import_module("zeline.skills")
        retired = skills.RETIRED_BUNDLED_SKILL_DIGESTS
        self.assertGreaterEqual(len(retired), 60)
        self.assertEqual(len(set(retired)), len(retired))
        for filename_digest, content_digests in retired.items():
            with self.subTest(filename_digest=filename_digest):
                self.assertRegex(filename_digest, r"^[0-9a-f]{64}$")
                self.assertIsInstance(content_digests, tuple)
                self.assertTrue(content_digests)
                for digest in content_digests:
                    self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_seed_skills_removes_content_addressed_retired_copy(self):
        skills = importlib.import_module("zeline.skills")
        stale = skills.PUBLIC_SKILLS_DIR / "retired-seeded-copy.md"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("untouched seeded content\n", encoding="utf-8")
        filename_digest = hashlib.sha256(stale.name.encode("utf-8")).hexdigest()
        content_digest = hashlib.sha256(stale.read_bytes()).hexdigest()
        with mock.patch.dict(
            skills.RETIRED_BUNDLED_SKILL_DIGESTS,
            {filename_digest: (content_digest,)},
            clear=True,
        ):
            skills.seed_skills()
        self.assertFalse(stale.exists())

    def test_seed_skills_preserves_modified_content_addressed_copy(self):
        skills = importlib.import_module("zeline.skills")
        custom = skills.PUBLIC_SKILLS_DIR / "retired-customized-copy.md"
        custom.parent.mkdir(parents=True, exist_ok=True)
        custom.write_text("user customization\n", encoding="utf-8")
        filename_digest = hashlib.sha256(custom.name.encode("utf-8")).hexdigest()
        with mock.patch.dict(
            skills.RETIRED_BUNDLED_SKILL_DIGESTS,
            {filename_digest: (hashlib.sha256(b"old seeded content").hexdigest(),)},
            clear=True,
        ):
            skills.seed_skills()
        self.assertEqual(custom.read_text(encoding="utf-8"), "user customization\n")

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
        self.assertIn("openai", result)
        # Infra details must NOT be exposed: neither the secret key nor the
        # provider base URL / host (relay/router identity is not disclosed).
        self.assertNotIn("never-print-this", result)
        self.assertNotIn("provider.example", result)
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

    def test_narrow_tmdb_skill_is_not_bundled_after_global_policy_fix(self):
        skills = importlib.import_module("zeline.skills")
        skills.seed_skills()
        entries = skills.list_skill_entries(include_private=False)
        self.assertNotIn(
            ("public", "tmdb-media-web-maintenance"),
            {(scope, name) for scope, name, _title, _description in entries},
        )

    def test_nba_betting_skill_is_not_bundled_and_has_upgrade_cleanup(self):
        skills = importlib.import_module("zeline.skills")
        name = "nba-betting-analyst.md"
        source = SOURCE_ROOT / "zeline" / "skills" / name
        self.assertFalse(source.exists())
        filename_digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
        expected_revisions = {
            "56eef747ca4dd3335fe414b54a611beb107cf8ac607f320fd18377e0c93d1f40",
            "414d753de16b33eb73d938cbe6b7ecf36420eee635151e388c0dad6a73248bb4",
        }
        self.assertEqual(
            set(skills.RETIRED_BUNDLED_SKILL_DIGESTS[filename_digest]),
            expected_revisions,
        )

    def test_seed_skills_removes_only_known_unmodified_narrow_tmdb_skill(self):
        skills = importlib.import_module("zeline.skills")
        old = skills.PUBLIC_SKILLS_DIR / "tmdb-media-web-maintenance.md"
        old.parent.mkdir(parents=True, exist_ok=True)
        legacy_content = "# Previously bundled narrow TMDB skill\n"
        old.write_text(legacy_content, encoding="utf-8")
        # Hash the persisted bytes: Windows text writes may normalize LF to CRLF.
        digest = hashlib.sha256(old.read_bytes()).hexdigest()

        with mock.patch.dict(
            skills.LEGACY_BUNDLED_SKILL_DIGESTS,
            {"tmdb-media-web-maintenance.md": (digest,)},
            clear=True,
        ):
            skills.seed_skills()

        self.assertFalse(old.exists())

    def test_seed_skills_preserves_customized_narrow_tmdb_skill(self):
        skills = importlib.import_module("zeline.skills")
        old = skills.PUBLIC_SKILLS_DIR / "tmdb-media-web-maintenance.md"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text("# My customized media workflow\n", encoding="utf-8")

        skills.seed_skills()

        self.assertEqual(old.read_text(encoding="utf-8"), "# My customized media workflow\n")

    def test_legacy_media_digest_matches_last_bundled_source(self):
        skills = importlib.import_module("zeline.skills")
        # SHA-256 independently captured from the exact file merged in b4bea85.
        # Keep this literal so a production-map typo fails on shallow CI clones.
        expected = "35f51a79be0c313bec2ec3f014200a00beeee7938173b5a20ccae0e5b62b8a4d"
        self.assertIn(
            expected,
            skills.LEGACY_BUNDLED_SKILL_DIGESTS["tmdb-media-web-maintenance.md"],
        )

    def test_legacy_skill_cleanup_compares_parent_identity_cross_platform(self):
        skills_source = (SOURCE_ROOT / "zeline" / "skills.py").read_text(encoding="utf-8")
        self.assertIn("resolved.parent.samefile(public_root)", skills_source)
        self.assertNotIn("resolved.parent != public_root", skills_source)

    def test_legacy_skill_cleanup_rejects_path_traversal(self):
        skills = importlib.import_module("zeline.skills")
        skills._ensure_dirs()
        victim = skills.SKILLS_ROOT / "outside-public.md"
        victim.write_text("do not delete\n", encoding="utf-8")
        digest = hashlib.sha256(victim.read_bytes()).hexdigest()

        with mock.patch.dict(
            skills.LEGACY_BUNDLED_SKILL_DIGESTS,
            {"../outside-public.md": (digest,)},
            clear=True,
        ):
            skills.seed_skills()

        self.assertTrue(victim.exists())

    def test_legacy_skill_cleanup_preserves_symlink(self):
        skills = importlib.import_module("zeline.skills")
        skills._ensure_dirs()
        target = skills.SKILLS_ROOT / "outside-target.md"
        target.write_text("do not delete\n", encoding="utf-8")
        link = skills.PUBLIC_SKILLS_DIR / "tmdb-media-web-maintenance.md"
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink is not supported on this platform")

        skills.seed_skills()

        self.assertTrue(link.is_symlink())
        self.assertTrue(target.exists())

    def test_system_prompt_contains_default_response_formatting_rules(self):
        self.assertIn("**bold**", self.config.SYSTEM_PROMPT)
        self.assertIn("fenced block", self.config.SYSTEM_PROMPT)
        self.assertIn("terminal", self.config.SYSTEM_PROMPT.lower())
        # Spacing/readability guidance must be present (blank-line paragraph rule).
        self.assertIn("BLANK line", self.config.SYSTEM_PROMPT)

    def test_agent_system_prompt_has_clean_self_identity_and_no_infra_leak(self):
        # Build a full agent prompt and assert it teaches a clean, one-line model
        # answer without leaking the provider base URL / relay host.
        agent_mod = importlib.import_module("zeline.agent")
        cfg = self.config.config_copy()
        cfg["provider"].update({
            "base_url": "http://localhost:20128/v1",
            "api_key": "secret-key",
            "model": "some/model-id",
            "protocol": "openai",
        })
        self.config.save_config(cfg)
        agent = agent_mod.Zeline(identity="telegram:1", tool_profile="safe")
        prompt = agent.messages[0]["content"]
        # Teaches self-identity + one-line answer, forbids infra disclosure.
        self.assertIn("Self-identity", prompt)
        self.assertIn("runtime_info", prompt)
        # The base URL / relay host must NOT be embedded in the system prompt.
        self.assertNotIn("localhost:20128", prompt)
        self.assertNotIn("http://localhost", prompt)
        # The secret key is never in the prompt.
        self.assertNotIn("secret-key", prompt)

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

    def test_network_route_is_owner_only_and_masks_credentials(self):
        tools = importlib.import_module("zeline.tools")
        routes = importlib.import_module("zeline.network_routes")
        route_file = self.home / "network-routes.json"
        with mock.patch.object(routes, "ROUTES_FILE", route_file):
            full = tools.ToolExecutor("telegram:owner", profile="full", workspace=self.home)
            safe = tools.ToolExecutor("telegram:guest", profile="safe", workspace=self.home)
            # all_schemas: profile membership is under test here, and `schemas`
            # may withhold a tool's detail behind the tool_search catalogue.
            self.assertIn("network_route", {item["function"]["name"] for item in full.all_schemas})
            self.assertNotIn("network_route", {item["function"]["name"] for item in safe.all_schemas})
            result = full.run("network_route", {
                "action": "add", "label": "uk", "proxy_url": "socks5h://alice:secret@proxy.test:1080", "country": "GB",
            })
            self.assertIn("OK", result)
            listed = full.run("network_route", {"action": "list"})
            self.assertIn("***@proxy.test:1080", listed)
            self.assertNotIn("alice", listed)
            self.assertNotIn("secret", listed)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(route_file.stat().st_mode), 0o600)

    def test_owner_web_fetch_uses_route_after_geo_block(self):
        tools = importlib.import_module("zeline.tools")
        routes = importlib.import_module("zeline.network_routes")

        class Resp:
            ok = True
            text = "Page not found region country geo"
            url = "https://ftmo.com/block/ID.html"
            def iter_content(self, n):
                yield b"Page not found region country geo"

        with mock.patch.object(tools.requests, "get", return_value=Resp()), \
             mock.patch.object(tools, "_fetch_via_wayback", return_value=None), \
             mock.patch.object(tools, "_fetch_with_network_routes", return_value="[via network route uk] FTMO PRICING") as routed:
            out = tools._web_fetch("https://ftmo.com/en/pricing/", use_private_routes=True)
        routed.assert_called()
        self.assertIn("FTMO PRICING", out)

    def test_routed_fetch_preserves_captcha_escalation_marker(self):
        tools = importlib.import_module("zeline.tools")
        routes = importlib.import_module("zeline.network_routes")
        challenge = '<html><title>Just a moment...</title><script>window._cf_chl_opt={}</script></html>'

        class Resp:
            ok = True
            url = "https://ftmo.com/en/pricing/"
            def iter_content(self, n):
                yield challenge.encode()

        with mock.patch.object(routes, "enabled_routes", return_value=[{"label": "uk", "country": "GB", "proxy_url": "http://proxy.test:8080"}]), \
             mock.patch.object(tools.requests, "get", return_value=Resp()):
            out = tools._fetch_with_network_routes("https://ftmo.com/en/pricing/")
        self.assertIn("CLOUDFLARE_CHALLENGE", out)
        self.assertIn("route=uk", out)

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
        self.assertNotIn("analyze_media", {item["function"]["name"] for item in safe.all_schemas})
        # workspace/full punya tool-nya.
        ws = self.home / "media-ws"
        ws.mkdir(parents=True, exist_ok=True)
        executor = self.tools.ToolExecutor("cli:local", profile="workspace", workspace=ws)
        self.assertIn("analyze_media", {item["function"]["name"] for item in executor.all_schemas})
        # URL internal diblokir.
        self.assertIn("blocked", executor.run("analyze_media", {"path_or_url": "http://169.254.169.254/x.png"}))
        # File audio → ditranskripsi (bukan mengarang isi). Tanpa provider,
        # jalur transkrip tetap kelihatan lewat kata "transcrib".
        audio = ws / "clip.ogg"
        audio.write_bytes(b"fakeaudio")
        self.assertIn("transcrib", executor.run("analyze_media", {"path_or_url": "clip.ogg"}).lower())
        # File video → audio ditranskripsi, gambar diarahkan ke ekstraksi frame.
        vid = ws / "clip.mp4"
        vid.write_bytes(b"fakevideo")
        self.assertIn("frames", executor.run("analyze_media", {"path_or_url": "clip.mp4"}).lower())

    def test_download_file_is_workspace_gated_and_ssrf_protected(self):
        safe = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        self.assertNotIn("download_file", {item["function"]["name"] for item in safe.all_schemas})
        # workspace profile punya, tapi SSRF + path escape diblokir
        workspace = self.home / "dl-ws"
        workspace.mkdir(parents=True)
        executor = self.tools.ToolExecutor("cli:local", profile="workspace", workspace=workspace)
        self.assertIn("blocked", executor.run("download_file", {"url": "http://169.254.169.254/x", "path": "meta.txt"}))
        self.assertIn("workspace", executor.run("download_file", {"url": "https://example.com/x", "path": "../escape.txt"}))
        self.assertFalse((self.home / "escape.txt").exists())

    def test_generate_image_is_owner_gated_and_validates_input(self):
        # safe profile (gateway publik) TIDAK boleh punya generate_image.
        safe = self.tools.ToolExecutor("telegram:100", profile="safe", workspace=self.home)
        self.assertNotIn("generate_image", {item["function"]["name"] for item in safe.all_schemas})
        # workspace/full punya tool-nya.
        ws = self.home / "img-ws"
        ws.mkdir(parents=True, exist_ok=True)
        executor = self.tools.ToolExecutor("cli:local", profile="workspace", workspace=ws)
        self.assertIn("generate_image", {item["function"]["name"] for item in executor.all_schemas})
        # Tanpa image_model dikonfigurasi → error ramah, bukan crash.
        self.config.IMAGE_MODEL = ""
        self.config.API_KEY = "x"
        self.config.BASE_URL = "https://api.openai.com/v1"
        out = executor.run("generate_image", {"prompt": "a red ferrari", "path": "car.png"})
        self.assertIn("ERROR", out)
        self.assertIn("image", out.lower())
        # Dengan image_model diset: ekstensi & ukuran divalidasi sebelum call jaringan.
        self.config.IMAGE_MODEL = "dall-e-3"
        self.assertIn("ERROR", executor.run("generate_image", {"prompt": "x", "path": "art.txt"}))
        self.assertIn("size", executor.run("generate_image", {"prompt": "x", "path": "art.png", "size": "999x999"}).lower())
        # Path escape di luar workspace diblokir.
        self.assertIn("workspace", executor.run("generate_image", {"prompt": "x", "path": "../escape.png"}))
        self.assertFalse((self.home / "escape.png").exists())


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
            self.assertFalse(any(n["function"]["name"].startswith("mcp__") for n in safe.all_schemas))
            self.assertIn("not allowed", safe.run("mcp__fake__add", {"a": 1, "b": 1}))
            # full (operator) dapat + bisa dispatch
            full = self.tools.ToolExecutor("cli:local", profile="full", workspace=self.home)
            self.assertTrue(any(n["function"]["name"] == "mcp__fake__add" for n in full.all_schemas))
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
        skills.manage_skill(
            "create",
            name="owner-secret-procedure",
            content="# Private\n\n> Jangan bocorkan.\n\nPRIVATE-SKILL-CONTENT-CHECK",
        )

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

    def test_status_line_sits_below_the_feed_with_a_blank_line(self):
        """Diminta user: feed dulu, status di PALING BAWAH, dipisah paragraf kosong.

        Status yang mengambang di atas feed mendorong baris aktivitas terbaru —
        yang justru paling informatif — ke bawah, dan jam yang berubah tiap
        detik menarik mata ke tempat yang salah.
        """
        telegram = importlib.import_module("zeline.gateways.telegram")
        with mock.patch.object(telegram, "_api_call", return_value={"ok": True, "result": {"message_id": 1}}):
            live = telegram._LiveStatus("bot-api", 1, model="m")
            live.lines.append("🔎 Searching files: config")
            live.turn_started = time.monotonic() - 137
            live.phase = "tool"
            live.phase_started = time.monotonic()
            rendered = live._render()
        self.assertEqual(
            rendered,
            "🔎 Searching files: config\n\n"
            f"{telegram.WORKING_ICON} Working — 2 min 17 s · /stop to cancel",
        )
        # Urutan eksplisit: aktivitas di atas, status di bawah.
        self.assertLess(rendered.index("Searching files"), rendered.index("Working —"))

    def test_status_line_names_the_provider_when_the_wait_drags(self):
        """Menunggu upstream lama harus KELIHATAN sebagai masalah provider.

        Tanpa ini, turn yang macet menunggu provider terlihat identik dengan turn
        yang sedang sibuk bekerja, dan user tidak bisa membedakan "Zeline lambat"
        dari "provider lambat".
        """
        telegram = importlib.import_module("zeline.gateways.telegram")
        with mock.patch.object(telegram, "_api_call", return_value={"ok": True, "result": {"message_id": 1}}):
            live = telegram._LiveStatus("bot-api", 1, model="m")
            live.lines.append("🔎 Searching files: config")
            live.turn_started = time.monotonic() - 137
            live.phase = "waiting"
            live.phase_started = time.monotonic() - (telegram._PROVIDER_WAIT_NOTE_SECONDS + 5)
            slow = live._render()
            # Menunggu sebentar itu normal → jangan diumumkan.
            live.phase_started = time.monotonic() - 5
            normal = live._render()
            # Tool sedang jalan → lambatnya bukan urusan provider.
            live.phase = "tool"
            live.phase_started = time.monotonic() - 120
            during_tool = live._render()
        self.assertTrue(slow.endswith(telegram.PROVIDER_WAIT_NOTE), slow)
        self.assertIn("waiting for provider response - streaming", slow)
        self.assertNotIn(telegram.PROVIDER_WAIT_NOTE, normal)
        self.assertNotIn(telegram.PROVIDER_WAIT_NOTE, during_tool)

    def test_telegram_working_status_shows_progress_and_stop_hint(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        # Header status harus membuktikan agent MASIH kerja: jam berjalan + /stop.
        line = telegram._working_status_text(125, iteration=4, maximum=20, remaining_seconds=235)
        self.assertIn(f"{telegram.WORKING_ICON} Working — 2 min 5 s", line)
        self.assertEqual(line, f"{telegram.WORKING_ICON} Working — 2 min 5 s · /stop to cancel")
        self.assertNotIn("step", line)
        self.assertNotIn("left", line)
        # Satu baris saja — user membenci status yang turun ke baris baru.
        self.assertNotIn("\n", line)
        # Tidak pernah menyalahkan provider/model.
        self.assertNotIn("slow to respond", line)
        self.assertIn(f"{telegram.WORKING_ICON} Working — 8 s", telegram._working_status_text(8))

    def test_working_icon_is_the_developer_emoji_not_an_hourglass(self):
        """Diminta user: 🧑🏻‍💻, bukan ⏳ (jam pasir = menunggu, bukan mengerjakan)."""
        telegram = importlib.import_module("zeline.gateways.telegram")
        self.assertEqual(telegram.WORKING_ICON, "🧑🏻‍💻")
        line = telegram._working_status_text(45)
        self.assertTrue(line.startswith("🧑🏻‍💻"), line)
        self.assertNotIn("⏳", line)

    def test_a_turn_with_no_tools_says_thinking_not_working(self):
        """Sapaan yang lambat bukan "Working" — nol pekerjaan dikerjakan.

        Keluhan nyata: "kenapa setiap gua chat, mau hay ataupun oy, dia harus
        Working dulu? kan Working khusus kalo gua kasih kerjaan coding".
        """
        telegram = importlib.import_module("zeline.gateways.telegram")
        line = telegram._working_status_text(70, working=False)
        self.assertTrue(line.startswith(telegram.THINKING_ICON), line)
        self.assertIn("Thinking — 1 min 10 s", line)
        self.assertNotIn("Working", line)
        self.assertIn("/stop to cancel", line)

    def test_telegram_status_bubble_appears_when_work_takes_long(self):
        # Keluhan nyata: "punya gituan ga biar gua ga didiemin terus".
        # Turn pendek tetap bersih (tanpa bubble), turn panjang WAJIB memunculkan
        # status berjalan — TAPI hanya sebagai "Working" kalau tool memang jalan.
        telegram = importlib.import_module("zeline.gateways.telegram")
        with mock.patch.object(telegram, "_api_call", return_value={"ok": True, "result": {"message_id": 5}}):
            live = telegram._LiveStatus("bot-api", 1, model="m")
            self.assertEqual(live._render(), "")  # baru mulai → diam itu benar
            live.lines.append("🌐 Searching bitcoin")  # ada pekerjaan nyata
            live.turn_started -= telegram._STATUS_AFTER_SECONDS + 5
            live.set_iteration(3, 20)
            rendered = live._render()
        self.assertIn("Working", rendered)
        self.assertIn(telegram.WORKING_ICON, rendered)
        self.assertNotIn("step", rendered)
        self.assertNotIn("left", rendered)
        self.assertIn("/stop to cancel", rendered)

    def test_a_slow_greeting_never_renders_a_working_header(self):
        """30 detik tanpa tool = tidak ada bubble sama sekali; 60s+ = Thinking.

        Angka nyata di device ini untuk sapaan (5 sampel lewat jalur agent):
        4,8s / 5,4s / 8,4s / 12,8s / 33,3s — median 8,4s. Satu sampel melewati
        30 detik, dan dulu itulah yang memunculkan "Working" untuk kata "p".
        """
        telegram = importlib.import_module("zeline.gateways.telegram")
        with mock.patch.object(telegram, "_api_call", return_value={"ok": True}):
            live = telegram._LiveStatus("bot-api", 1, model="m")
            # 35 detik, nol tool → masih diam. Dulu di sini muncul "⏳ Working".
            live.turn_started = time.monotonic() - 35.0
            self.assertEqual(live._header(), "")
            self.assertEqual(live._render(), "")
            # Lewat ambang thinking → beri kabar, tapi sebut Thinking.
            live.turn_started = time.monotonic() - (telegram._THINKING_AFTER_SECONDS + 1)
            header = live._header()
            self.assertIn("Thinking", header)
            self.assertNotIn("Working", header)

    def test_telegram_working_header_waits_full_30_seconds(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        self.assertEqual(telegram._STATUS_AFTER_SECONDS, 30.0)
        self.assertEqual(telegram._THINKING_AFTER_SECONDS, 60.0)
        with mock.patch.object(telegram, "_api_call", return_value={"ok": True}):
            live = telegram._LiveStatus("bot-api", 1, model="m")
            live.lines.append("📖 Reading file x.py")  # ada pekerjaan nyata
            live.turn_started = time.monotonic() - 29.0
            self.assertEqual(live._header(), "")
            live.turn_started = time.monotonic() - 30.1
            self.assertIn(f"{telegram.WORKING_ICON} Working — 30 s", live._header())

    def test_telegram_long_wait_creates_status_bubble_from_heartbeat(self):
        """Heartbeat pada turn yang BENAR-BENAR kerja tetap memunculkan bubble.

        Turn tanpa tool sengaja dikecualikan (lihat
        ``test_a_slow_greeting_never_renders_a_working_header``): sapaan lambat
        tidak boleh dilabeli "Working".
        """
        telegram = importlib.import_module("zeline.gateways.telegram")
        sent = []

        def fake_api(_api, method, **kwargs):
            sent.append((method, str(kwargs.get("text") or "")))
            return {"ok": True, "result": {"message_id": 9}}

        with mock.patch.object(telegram, "_api_call", side_effect=fake_api):
            live = telegram._LiveStatus("bot-api", 1, model="m")
            live.lines.append("📖 Reading file x.py")  # tool sudah jalan
            live.turn_started -= telegram._STATUS_AFTER_SECONDS + 1
            live.tick()  # heartbeat saja, tanpa tool baru
        created = [text for method, text in sent if method == "sendMessage"]
        self.assertTrue(created)
        self.assertIn(f"{telegram.WORKING_ICON} Working", created[0])

    def test_heartbeat_on_a_toolless_turn_creates_no_bubble_before_the_thinking_mark(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        sent = []

        def fake_api(_api, method, **kwargs):
            sent.append(method)
            return {"ok": True, "result": {"message_id": 9}}

        with mock.patch.object(telegram, "_api_call", side_effect=fake_api):
            live = telegram._LiveStatus("bot-api", 1, model="m")
            live.turn_started -= telegram._STATUS_AFTER_SECONDS + 1
            live.tick()
        self.assertEqual([m for m in sent if m == "sendMessage"], [])

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

        with mock.patch.object(telegram.time, "sleep"), mock.patch.object(telegram._HTTP, "post", side_effect=flaky):
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
        with mock.patch.object(telegram.time, "sleep"), mock.patch.object(telegram._HTTP, "post", side_effect=always_fail):
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

        with mock.patch.object(telegram._HTTP, "post", side_effect=fake_post):
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

    def test_update_trace_summarizes_without_leaking_message_content(self):
        # Log gateway harus cukup untuk diagnosa 'kenapa bot diam' TAPI bukan
        # arsip percakapan: isi pesan tidak boleh masuk log.
        telegram = importlib.import_module("zeline.gateways.telegram")
        secret = "saldo rekening 12345 rahasia"
        trace = telegram._update_trace(
            {"update_id": 1, "message": {"chat": {"id": 77}, "from": {"id": 88}, "text": secret}}
        )
        self.assertIn("chat=77", trace)
        self.assertIn("user=88", trace)
        self.assertIn(f"len={len(secret)}", trace)
        self.assertNotIn("rahasia", trace)
        self.assertNotIn("12345", trace)

    def test_update_trace_labels_commands_media_and_callbacks(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        # Nama command dicatat (berguna: '/model dijawab?'), argumen tidak.
        command = telegram._update_trace(
            {"message": {"chat": {"id": 1}, "from": {"id": 2}, "text": "/model bai/secret-model"}}
        )
        self.assertIn("command=/model", command)
        self.assertNotIn("secret-model", command)
        # Media dikenali per jenis supaya jelas jalur mana yang dipakai. Muatan
        # dibuat non-kosong seperti update Telegram nyata — _dispatch_update juga
        # memakai truthiness yang sama, jadi dict kosong memang bukan media.
        self.assertIn("photo", telegram._update_trace({"message": {"chat": {"id": 1}, "photo": [{"file_id": "p"}]}}))
        self.assertIn("document", telegram._update_trace({"message": {"chat": {"id": 1}, "document": {"file_id": "d"}}}))
        self.assertIn("audio", telegram._update_trace({"message": {"chat": {"id": 1}, "voice": {"file_id": "v"}}}))
        self.assertIn("video", telegram._update_trace({"message": {"chat": {"id": 1}, "video": {"file_id": "m"}}}))
        # callback_data aman: itu identitas tombol buatan kita, bukan teks user.
        callback = telegram._update_trace(
            {"callback_query": {"id": "9", "data": "grp:0:1", "from": {"id": 5}, "message": {"chat": {"id": 6}}}}
        )
        self.assertIn("callback", callback)
        self.assertIn("data=grp:0:1", callback)
        self.assertIn("chat=6", callback)

    def test_dispatch_update_passes_reply_to_message_id(self):
        # Balasan final harus nempel (quote) ke pesan user supaya jelas menjawab
        # pertanyaan yang mana ketika user kirim beberapa bubble terpisah.
        telegram = importlib.import_module("zeline.gateways.telegram")
        captured = {}

        class Sessions:
            pass

        with mock.patch.object(telegram, "_api_call", return_value={"result": {"message_id": 1}}), \
             mock.patch.object(telegram, "_start_agent_reply", side_effect=lambda *a, **k: captured.update(k)):
            update = {"update_id": 6, "message": {"message_id": 4242, "chat": {"id": 111222333}, "text": "pertanyaan kedua"}}
            telegram._dispatch_update(
                "bot-api", "token", Sessions(), update,
                allowed=[111222333], tool_profile="full", stop_event=threading.Event(),
            )
        self.assertEqual(captured.get("reply_to_message_id"), 4242)

    def test_send_agent_reply_quotes_user_message(self):
        # Bubble jawaban pertama dikirim dengan reply_to_message_id; part lanjutan
        # tidak, dan allow_sending_without_reply=True agar aman bila pesan dihapus.
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Sessions:
            def send(self, **_kwargs):
                return "Jawaban singkat."

        sent = []
        with mock.patch.object(
            telegram, "_api_call",
            side_effect=lambda a, m, **k: sent.append((m, k)) or {"result": {"message_id": len(sent)}},
        ):
            telegram._send_agent_reply(
                "bot-api", Sessions(), chat_id=1, identity="telegram:1",
                text="hi", tool_profile="safe", reply_to_message_id=777,
            )
        msgs = [k for m, k in sent if m == "sendMessage" and k.get("text")]
        self.assertTrue(msgs, "expected at least one sendMessage")
        self.assertEqual(msgs[0].get("reply_to_message_id"), 777)
        self.assertTrue(msgs[0].get("allow_sending_without_reply"))

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
        # TUNGGU tick pertama, jangan sleep dengan durasi tetap. Versi lama
        # sleep 0.05s lalu langsung set(done); di runner yang sibuk (macOS CI)
        # thread heartbeat bisa belum terjadwal sama sekali sehingga daftar
        # panggilan kosong dan test gagal tanpa ada bug produk.
        ticked = threading.Event()

        def record(_api, method, **_kwargs):
            if method == "sendChatAction":
                ticked.set()
            return {"result": {"message_id": 7}}

        with mock.patch.object(telegram, "_api_call", side_effect=record) as api:
            live = telegram._LiveStatus("bot-api", 42, model="m")
            worker = telegram._start_working_heartbeat("bot-api", 42, done, interval=0.01, status=live)
            self.assertTrue(ticked.wait(10), "heartbeat tidak pernah me-refresh 'typing'")
            done.set()
            worker.join(timeout=5)
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

    def test_telegram_preserves_paragraph_blank_lines(self):
        # Regresi: pemisah paragraf (\n\n) dari model HARUS bertahan — jangan
        # pernah di-collapse jadi satu newline atau spasi sebelum kirim.
        telegram = importlib.import_module("zeline.gateways.telegram")
        source = "Paragraf pertama yang cukup panjang.\n\nParagraf kedua yang terpisah."
        cleaned = telegram._normalize_markdown(source)
        self.assertIn("\n\n", cleaned)
        self.assertIn("pertama yang cukup panjang.\n\nParagraf kedua", cleaned)

    def test_telegram_inserts_blank_line_between_prose_and_list(self):
        # Kalimat pengantar yang menempel ke list dapat blank line supaya rapi,
        # TAPI antar item list tidak dipisah.
        telegram = importlib.import_module("zeline.gateways.telegram")
        source = "Yang aku butuh:\n- item satu\n- item dua\nLalu lanjut kalimat berikutnya."
        cleaned = telegram._normalize_markdown(source)
        self.assertIn("Yang aku butuh:\n\n- item satu", cleaned)
        # Antar item list tetap rapat (satu newline).
        self.assertIn("- item satu\n- item dua", cleaned)
        # Setelah list berakhir, prose berikutnya dipisah blank line.
        self.assertIn("- item dua\n\nLalu lanjut", cleaned)

    def test_telegram_blank_line_after_heading(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        source = "## Ringkasan\nIsi langsung menempel di bawah heading."
        cleaned = telegram._normalize_markdown(source)
        self.assertIn("## Ringkasan\n\nIsi langsung", cleaned)

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

    def test_telegram_registers_zeline_style_command_picker(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        commands = telegram._telegram_commands()
        self.assertEqual(
            [item["command"] for item in commands],
            ["start", "model", "status", "repository", "deleterepository", "stop", "new", "version", "update"],
        )
        self.assertEqual(commands[0]["description"], "Start Zeline")
        by_name = {item["command"]: item["description"] for item in commands}
        self.assertIn("active turn", by_name["stop"].lower())

    def test_telegram_status_reports_zeline_style_runtime_and_coding_tools(self):
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
            "╭───────────────🚥\n"
            "├ <b>Zeline Gateway Status</b>\n"
            "├ Session ID : <code>zel-abc123</code>\n"
            "├ Provider : <code>provider.test</code>\n"
            "├ Model : <code>deepseek-v4-flash</code>\n"
            "├ Title : Bangun aplikasi\n"
            "├ Context : 12 messages\n"
            "├ Agent Running : Yes\n"
            "╰ Platform : Telegram"
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

    def test_repository_delete_matches_name_or_link(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        repository = self.home / "repository.md"
        with mock.patch.object(telegram, "REPOSITORY_FILE", repository):
            # Seed baris secara manual—_save_task sudah dihapus dari codebase.
            path, rows = telegram._repository_rows()
            rows.append(telegram._task_row({"title": "Project alpha", "messages": ["https://old.example.app"]}, 42, 1))
            telegram._write_repository_rows(path, rows)
            self.assertEqual(telegram._delete_task("dashboard finance"), "not_found")
            self.assertEqual(telegram._delete_task("old.example.app"), "deleted")
            self.assertEqual(repository.read_text(encoding="utf-8"), telegram.REPOSITORY_HEADER)

    def test_telegram_deleterepository_requires_project_name_or_link(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        with mock.patch.object(telegram, "_api_call") as api:
            telegram._handle_command_update("bot-api", "/deleterepository", object(), "telegram:42", 42, stop_event=threading.Event(), tool_profile="full")
        self.assertEqual(api.call_args.kwargs["text"], "Usage: /deleterepository <project or link>")

    def test_telegram_deleterepository_finds_by_name_or_link(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        repository = self.home / "repository.md"
        with mock.patch.object(telegram, "REPOSITORY_FILE", repository):
            path, rows = telegram._repository_rows()
            rows.append(telegram._task_row({"title": "Project alpha", "messages": ["https://old.example.app"]}, 42, 1))
            telegram._write_repository_rows(path, rows)
            with mock.patch.object(telegram, "_api_call") as api:
                telegram._handle_command_update("bot-api", "/deleterepository project alpha", object(), "telegram:42", 42, stop_event=threading.Event(), tool_profile="full", message_id=3)
            self.assertEqual(api.call_args.kwargs["text"], "Repository entry deleted from repository.md.")
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
        names = [schema["function"]["name"] for schema in executor.all_schemas]
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

    def test_gateway_service_ready_marker_covers_an_http_listener(self):
        """Readiness must accept both transports a gateway can announce.

        A poller prints "connected via polling"; an HTTP adapter prints
        "listening http://…". Dropping the HTTP marker makes a gateway that is
        already serving sit out the whole start timeout and get reported as
        failed.
        """
        service = importlib.import_module("zeline.gateway_service")
        self.assertIn("connected via polling", service.READY_MARKERS)
        self.assertIn("listening http://", service.READY_MARKERS)
        # An underscored config key must also match a hyphenated log tag.
        self.assertEqual(service._log_tags("my_gateway"), ("[my-gateway]", "[my_gateway]"))

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
        # Read back rather than assuming the bytes on disk match what was sent:
        # format-on-write may normalize quotes/spacing, so old_text must come
        # from the CURRENT content — exactly what the tool tells the model to do.
        on_disk = (self.home / "app.py").read_text(encoding="utf-8")
        old_literal = "'old'" if "'old'" in on_disk else '"old"'
        new_literal = old_literal.replace("old", "new")
        patched = executor.run("patch_file", {"path": "app.py", "old_text": old_literal, "new_text": new_literal})
        self.assertIn("patched", patched)
        self.assertIn("new", (self.home / "app.py").read_text(encoding="utf-8"))
        self.assertNotIn("old", (self.home / "app.py").read_text(encoding="utf-8"))
        task = executor.run("update_task", {"task": "Run tests", "status": "in_progress"})
        self.assertIn("Run tests", task)
        # The reply is the rendered board, not an echo of the arguments: an echo
        # tells the model nothing it did not already know. `[>]` is in_progress.
        self.assertIn("[>] Run tests", task)
        self.assertIn("(0/1 completed)", task)

    def test_full_profile_execute_code_and_manage_skill_execute_real_actions(self):
        tools = importlib.import_module("zeline.tools")
        skills = importlib.import_module("zeline.skills")
        executor = tools.ToolExecutor("telegram:owner", profile="full", workspace=str(self.home))
        code = executor.run("execute_code", {"code": "print(6 * 7)"})
        self.assertIn("exit=0", code)
        self.assertIn("42", code)

        # create → a folder skill with SKILL.md, not a loose markdown file. The
        # old flat layout is why saved skills had nowhere to put references.
        saved = executor.run("manage_skill", {"action": "create", "name": "demo-skill", "content": "# Demo\n\n> demo skill\n\nold step\n"})
        self.assertIn("demo-skill/SKILL.md", saved)
        skill_dir = skills.PRIVATE_SKILLS_DIR / "demo-skill"
        self.assertTrue((skill_dir / "SKILL.md").is_file())
        self.assertFalse((skills.PRIVATE_SKILLS_DIR / "demo-skill.md").exists())
        # Frontmatter is written so the catalogue keeps a description.
        self.assertIn("name: demo-skill", (skill_dir / "SKILL.md").read_text(encoding="utf-8"))

        # write_file → supporting files, so long detail leaves SKILL.md short.
        wrote = executor.run("manage_skill", {"action": "write_file", "name": "demo-skill", "file_path": "references/api.md", "content": "endpoint detail"})
        self.assertIn("demo-skill/references/api.md", wrote)
        self.assertEqual((skill_dir / "references" / "api.md").read_text(encoding="utf-8"), "endpoint detail")

        # patch → names the artifact it really touched.
        updated = executor.run("manage_skill", {"action": "patch", "name": "demo-skill", "old_text": "old step", "new_text": "new step"})
        self.assertIn("Patched private/demo-skill/SKILL.md (1 replacement)", updated)
        patched_ref = executor.run("manage_skill", {"action": "patch", "name": "demo-skill", "file_path": "references/api.md", "old_text": "endpoint detail", "new_text": "endpoint detail v2"})
        self.assertIn("demo-skill/references/api.md", patched_ref)

        loaded = executor.run("load_skill", {"name": "demo-skill"})
        self.assertIn("new step", loaded)
        self.assertIn("references/api.md", loaded)

        # list → inventory with the shape, so a near-duplicate is visible.
        listed = executor.run("manage_skill", {"action": "list"})
        self.assertIn("demo-skill [private/folder]", listed)

        # delete absorbed_into → merge duplicates; the target must exist first.
        executor.run("manage_skill", {"action": "create", "name": "dupe-skill", "content": "# Dupe\n\n> sama\n\nlangkah\n"})
        missing = executor.run("manage_skill", {"action": "delete", "name": "dupe-skill", "absorbed_into": "nope-skill"})
        self.assertIn("does not exist yet", missing)
        self.assertTrue((skills.PRIVATE_SKILLS_DIR / "dupe-skill").is_dir())
        merged = executor.run("manage_skill", {"action": "delete", "name": "dupe-skill", "absorbed_into": "demo-skill"})
        self.assertIn("absorbed into 'demo-skill'", merged)
        self.assertFalse((skills.PRIVATE_SKILLS_DIR / "dupe-skill").exists())

    def test_manage_skill_repairs_bundled_skills_via_copy_on_write(self):
        """A bundled skill must be fixable, and the fix must survive re-seeding.

        The previous surface only looked in the private scope, so improving a
        shipped skill returned "not found" — Zeline could not repair its own
        skills, only pile new private ones on top. Patching in place would be no
        better: ``seed_skills()`` is deliberately non-overwriting, so the edit
        would be indistinguishable from a stale seeded copy.
        """
        tools = importlib.import_module("zeline.tools")
        skills = importlib.import_module("zeline.skills")
        skills.seed_skills()
        executor = tools.ToolExecutor("cli:local", profile="full", workspace=str(self.home))

        target, entry = "", skills.PUBLIC_SKILLS_DIR / "unset"
        for candidate in sorted(skills.PUBLIC_SKILLS_DIR.iterdir()):
            if (candidate / "SKILL.md").is_file():
                target, entry = candidate.name, candidate / "SKILL.md"
                break
        self.assertTrue(target, "expected at least one bundled folder skill")
        original = entry.read_text(encoding="utf-8")
        # Anchor on a line that occurs exactly once — a bundled SKILL.md opens
        # with `---` frontmatter, so the first line is not unique.
        anchor = next(line for line in original.splitlines() if line.strip() and original.count(line) == 1)

        patched = executor.run("manage_skill", {"action": "patch", "name": target, "old_text": anchor, "new_text": anchor + " (diperbaiki)"})
        self.assertIn("copied from the bundled skill", patched)
        self.assertIn(f"private/{target}/SKILL.md", patched)
        # The shipped copy is untouched; the private override carries the repair.
        self.assertEqual(entry.read_text(encoding="utf-8"), original)
        private_entry = skills.PRIVATE_SKILLS_DIR / target / "SKILL.md"
        self.assertIn("(diperbaiki)", private_entry.read_text(encoding="utf-8"))
        self.assertIn("(diperbaiki)", executor.run("load_skill", {"name": target}))
        # Re-seeding must not resurrect the unpatched text.
        skills.seed_skills()
        self.assertIn("(diperbaiki)", executor.run("load_skill", {"name": target}))
        # A never-adopted public skill cannot be deleted — patch is the only path,
        # so the shipped catalogue can't be silently thinned out.
        untouched = next(
            candidate.name
            for candidate in sorted(skills.PUBLIC_SKILLS_DIR.iterdir())
            if (candidate / "SKILL.md").is_file() and candidate.name != target
        )
        refused = executor.run("manage_skill", {"action": "delete", "name": untouched})
        self.assertIn("patch it instead", refused)
        self.assertTrue((skills.PUBLIC_SKILLS_DIR / untouched / "SKILL.md").is_file())
        # Deleting the private override is allowed and reverts to the bundled copy.
        reverted = executor.run("manage_skill", {"action": "delete", "name": target})
        self.assertIn(f"Deleted private skill '{target}'", reverted)
        self.assertNotIn("(diperbaiki)", executor.run("load_skill", {"name": target}))

    def test_manage_skill_never_writes_outside_the_skill_folder(self):
        """Containment is asserted on the resolved path, not argued from a regex.

        A character allowlist only claims the name "looks reasonable"; the property
        needed is that no file is ever created outside the skill directory.
        """
        tools = importlib.import_module("zeline.tools")
        skills = importlib.import_module("zeline.skills")
        executor = tools.ToolExecutor("cli:local", profile="full", workspace=str(self.home))
        executor.run("manage_skill", {"action": "create", "name": "guard-skill", "content": "# Guard\n\n> guard\n\nlangkah\n"})

        for bad in ("../escaped.md", "references/../../escaped.md", "/etc/passwd", "references/sub/../../../escaped.md", "notes.md", "secrets/key.md"):
            with self.subTest(file_path=bad):
                denied = executor.run("manage_skill", {"action": "write_file", "name": "guard-skill", "file_path": bad, "content": "no"})
                self.assertTrue(denied.startswith("ERROR"), denied)
        self.assertFalse((skills.SKILLS_ROOT / "escaped.md").exists())
        self.assertFalse((skills.PRIVATE_SKILLS_DIR / "escaped.md").exists())
        self.assertEqual(sorted(p.name for p in (skills.PRIVATE_SKILLS_DIR / "guard-skill").iterdir()), ["SKILL.md"])

    def test_manage_skill_promotes_a_legacy_flat_skill_into_a_folder(self):
        """Existing installs hold flat files; a patch must not be a dead end.

        Twenty flat private skills were observed on the operator's device. Without
        promotion, none of them could ever gain a references/ file.
        """
        tools = importlib.import_module("zeline.tools")
        skills = importlib.import_module("zeline.skills")
        skills._ensure_dirs()
        legacy = skills.PRIVATE_SKILLS_DIR / "legacy-flat.md"
        legacy.write_text("# Legacy\n\n> lama\n\nlangkah lama\n", encoding="utf-8")
        executor = tools.ToolExecutor("cli:local", profile="full", workspace=str(self.home))

        patched = executor.run("manage_skill", {"action": "patch", "name": "legacy-flat", "old_text": "langkah lama", "new_text": "langkah baru"})
        self.assertIn("promoted from a flat file", patched)
        self.assertFalse(legacy.exists())
        promoted = skills.PRIVATE_SKILLS_DIR / "legacy-flat" / "SKILL.md"
        self.assertIn("langkah baru", promoted.read_text(encoding="utf-8"))
        # Exactly one catalogue entry — not one flat plus one folder.
        names = [name for _scope, name, _title, _desc in skills.list_skill_entries(include_private=True)]
        self.assertEqual(names.count("legacy-flat"), 1)

    def test_shell_timeout_is_raisable_so_real_installs_do_not_fail_at_60s(self):
        """pip/npm/apt installs routinely exceed 60s; the agent must be able to wait."""
        tools = importlib.import_module("zeline.tools")
        self.assertEqual(self.config.DEFAULT_SHELL_TIMEOUT_SECONDS, 60)
        self.assertGreaterEqual(self.config.SHELL_MAX_TIMEOUT_SECONDS, 600)

        # Default, invalid, and non-positive values fall back to the default.
        for supplied in (None, "", "abc", 0, -5):
            with self.subTest(supplied=supplied):
                self.assertEqual(tools._clamp_timeout(supplied), self.config.DEFAULT_SHELL_TIMEOUT_SECONDS)
        # A larger request is honoured, but never beyond the hard ceiling.
        self.assertEqual(tools._clamp_timeout(600), 600)
        self.assertEqual(tools._clamp_timeout("300"), 300)
        self.assertEqual(tools._clamp_timeout(10_000), self.config.SHELL_MAX_TIMEOUT_SECONDS)

        # The declared schema must expose timeout/background so the model can use them.
        by_name = {d.name: d for d in tools.TOOL_DEFS}
        shell_props = by_name["run_shell"].parameters["properties"]
        self.assertIn("timeout", shell_props)
        self.assertIn("background", shell_props)
        self.assertIn("timeout", by_name["execute_code"].parameters["properties"])
        self.assertEqual(by_name["process_control"].profiles, frozenset({"full"}))

        executor = tools.ToolExecutor("telegram:owner", profile="full", workspace=str(self.home))
        captured: dict[str, object] = {}
        real_popen = tools.subprocess.Popen

        class TrackedPopen(real_popen):  # type: ignore[misc,valid-type]
            def communicate(self, *args, **kwargs):
                # Timeout yang benar-benar diberlakukan pada proses foreground.
                captured.setdefault("timeout", kwargs.get("timeout"))
                return super().communicate(*args, **kwargs)

        with mock.patch.object(tools.subprocess, "Popen", TrackedPopen):
            out = executor.run("run_shell", {"command": "printf ok", "timeout": 600})
        self.assertIn("exit=0", out)
        self.assertIn("ok", out)
        self.assertEqual(captured["timeout"], 600)

        # A real timeout must tell the agent how to retry, not just say it failed.
        timed_out = executor.run("run_shell", {"command": "sleep 5", "timeout": 1})
        self.assertIn("timed out", timed_out)
        self.assertIn("larger timeout", timed_out)
        self.assertIn("background=true", timed_out)

    def test_stop_force_kills_running_foreground_command(self):
        """/stop harus MEMAKSA: proses shell yang jalan ikut dibunuh.

        Keluhan nyata: "kadang susah di suruh stop, harus matiin gateway terus".
        Penyebabnya foreground command dijalankan tanpa handle proses, jadi
        pembatalan baru terasa setelah perintah selesai sendiri.
        """
        tools = importlib.import_module("zeline.tools")
        executor = tools.ToolExecutor("telegram:stopme", profile="full", workspace=str(self.home))
        result: dict[str, str] = {}
        # Perintah sleep panjang yang portabel (cmd.exe tidak punya `sleep`).
        sleeper = f'"{sys.executable}" -c "import time; time.sleep(120)"'

        def worker():
            result["out"] = executor.run("run_shell", {"command": sleeper, "timeout": 300})

        thread = threading.Thread(target=worker, daemon=True)
        started = time.monotonic()
        thread.start()

        # Tunggu sampai prosesnya benar-benar terdaftar, lalu paksa batal.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not tools._FG_PROCS.get("telegram:stopme"):
            time.sleep(0.05)
        self.assertTrue(tools._FG_PROCS.get("telegram:stopme"), "process was never tracked")

        killed = tools.cancel_identity("telegram:stopme")
        self.assertGreaterEqual(killed, 1)

        thread.join(timeout=30)
        self.assertFalse(thread.is_alive(), "run_shell did not return after cancel")
        # Berhenti jauh sebelum sleep 120 selesai = benar-benar dipaksa.
        self.assertLess(time.monotonic() - started, 60)
        self.assertIn("exit=", result.get("out", ""))
        # Registry bersih kembali (tidak ada proses menggantung).
        self.assertFalse(tools._FG_PROCS.get("telegram:stopme"))

    def test_session_stop_cancels_turn_and_kills_child_process(self):
        """SessionStore.stop() menyalakan cancel_event DAN membunuh proses anak."""
        sessions_module = importlib.import_module("zeline.sessions")
        tools = importlib.import_module("zeline.tools")
        store = sessions_module.SessionStore(max_sessions=4, persistence=None)
        session = store.get_or_create("telegram:99", "full", workspace=str(self.home))
        with store._lock:
            session.running = True

        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **tools.DETACH_KWARGS,
        )
        tools._fg_track("telegram:99", process)
        try:
            self.assertTrue(store.stop("telegram:99"))
            self.assertTrue(session.cancel_event.is_set())
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.05)
            self.assertIsNotNone(process.poll(), "child process survived /stop")
        finally:
            if process.poll() is None:
                process.kill()
            tools._fg_untrack("telegram:99", process)

    def test_stop_mid_stream_returns_stopped_and_keeps_session_usable(self):
        """Pembatalan saat token masih mengalir tidak boleh merusak sesi.

        Turn dibatalkan di tengah stream → jawaban "Stopped.", dan ekor
        assistant(tool_calls) tanpa hasil tool dibuang supaya pesan berikutnya
        tidak ditolak provider.
        """
        agent_module = importlib.import_module("zeline.agent")
        agent = agent_module.Zeline(identity="telegram:stream", tool_profile="safe", workspace=str(self.home))
        cancel = threading.Event()

        def fake_call_llm(*_args, **_kwargs):
            # Meniru loop stream: batal di tengah pembacaan chunk.
            for _ in range(1000):
                if agent._cancelled():
                    raise agent_module._TurnCancelled()
            return {"role": "assistant", "content": "never reached"}

        with mock.patch.object(agent, "_call_llm", side_effect=fake_call_llm):
            cancel.set()
            reply = agent.send("kerjakan sesuatu yang panjang", should_stop=cancel.is_set)
        self.assertEqual(reply, "Stopped.")
        # Ekor tak lengkap dibuang: pesan terakhir bukan assistant-with-tool_calls.
        self.assertFalse(agent.messages[-1].get("tool_calls"))
        # Sesi masih bisa dipakai lagi setelah stop.
        self.assertIsNone(agent._should_stop)

    def test_dangling_tool_call_is_repaired_and_completed_work_survives(self):
        agent_module = importlib.import_module("zeline.agent")
        agent = agent_module.Zeline(identity="telegram:tail", tool_profile="safe", workspace=str(self.home))
        agent.messages.append({"role": "user", "content": "hi"})
        agent.messages.append({"role": "assistant", "content": "cek dua hal", "tool_calls": [
            {"id": "1", "function": {"name": "web_search", "arguments": "{}"}},
            {"id": "2", "function": {"name": "read_file", "arguments": "{}"}},
        ]})
        # Call 1 finished before /stop landed; call 2 never produced a result.
        agent.messages.append({"role": "tool", "tool_call_id": "1", "content": "hasil nyata"})
        agent._drop_incomplete_tail()
        # Provider contract: every requested call id now has a tool result.
        answered = {m.get("tool_call_id") for m in agent.messages if m.get("role") == "tool"}
        self.assertEqual(answered, {"1", "2"})
        # The finished call keeps its real output; nothing is amputated.
        self.assertIn("hasil nyata", [m.get("content") for m in agent.messages])
        self.assertTrue(any(m.get("content") == "cek dua hal" for m in agent.messages))
        # The interrupted call is labelled, not fabricated.
        placeholder = next(m for m in agent.messages if m.get("tool_call_id") == "2")
        self.assertIn("did not complete", placeholder["content"])

    def test_background_process_lifecycle_is_tracked_pollable_and_killable(self):
        tools = importlib.import_module("zeline.tools")
        executor = tools.ToolExecutor("telegram:owner", profile="full", workspace=str(self.home))
        tools._BG_JOBS.clear()
        # Portable "print then stay alive": `cmd.exe` has no `;` separator and no
        # `sleep`, so a POSIX one-liner exits instantly there and the job is
        # already finished before the first poll.
        long_runner = (
            f'"{sys.executable}" -c '
            '"import sys,time; sys.stdout.write(chr(102)+chr(105)+chr(114)+chr(115)+chr(116)+chr(10)); '
            'sys.stdout.flush(); time.sleep(30)"'
        )
        try:
            started = executor.run("run_shell", {"command": long_runner, "background": True})
            self.assertIn("started background job=", started)
            job_id = started.split("job=", 1)[1].split()[0]

            listed = executor.run("process_control", {"action": "list"})
            self.assertIn(job_id, listed)
            self.assertIn("running", listed)

            deadline = time.time() + 10
            polled = ""
            while time.time() < deadline:
                polled = executor.run("process_control", {"action": "poll", "job_id": job_id})
                if "first" in polled:
                    break
                time.sleep(0.2)
            self.assertIn("status=running", polled)
            self.assertIn("first", polled)
            # poll is incremental: already-read output is not repeated.
            self.assertNotIn("first", executor.run("process_control", {"action": "poll", "job_id": job_id}))
            # log replays the whole file.
            self.assertIn("first", executor.run("process_control", {"action": "log", "job_id": job_id}))

            killed = executor.run("process_control", {"action": "kill", "job_id": job_id})
            self.assertIn("killed", killed)
            self.assertNotIn(job_id, tools._BG_JOBS)
            self.assertIn("unknown job_id", executor.run("process_control", {"action": "poll", "job_id": job_id}))
            self.assertIn("action must be one of", executor.run("process_control", {"action": "nope", "job_id": job_id}))
        finally:
            for job in list(tools._BG_JOBS.values()):
                try:
                    job.process.kill()
                except Exception:
                    pass
            tools._BG_JOBS.clear()

    def test_background_shell_and_process_control_stay_owner_only(self):
        """A public gateway must never gain a detachable shell."""
        tools = importlib.import_module("zeline.tools")
        for profile in ("safe", "workspace"):
            with self.subTest(profile=profile):
                names = {d.name for d in tools.TOOL_DEFS if profile in d.profiles}
                self.assertNotIn("run_shell", names)
                self.assertNotIn("execute_code", names)
                self.assertNotIn("process_control", names)
                executor = tools.ToolExecutor("telegram:public", profile=profile, workspace=str(self.home))
                denied = executor.run("process_control", {"action": "list"})
                self.assertIn("not allowed for profile", denied.lower())
                shell_denied = executor.run("run_shell", {"command": "id", "background": True})
                self.assertIn("not allowed for profile", shell_denied.lower())

    def test_finished_background_output_survives_until_ttl_and_capacity_is_generous(self):
        """A finished build's output must stay readable, and the cap must not be stingy."""
        tools = importlib.import_module("zeline.tools")
        # Generous enough for real parallel work (64 tracked processes).
        self.assertEqual(self.config.MAX_BACKGROUND_PROCESSES, 64)
        self.assertGreaterEqual(self.config.BACKGROUND_FINISHED_TTL_SECONDS, 600)

        executor = tools.ToolExecutor("telegram:owner", profile="full", workspace=str(self.home))
        tools._BG_JOBS.clear()
        try:
            started = executor.run("run_shell", {"command": "printf 'done\\n'", "background": True})
            job_id = started.split("job=", 1)[1].split()[0]

            deadline = time.time() + 10
            while time.time() < deadline:
                if tools._BG_JOBS[job_id].process.poll() is not None:
                    break
                time.sleep(0.1)
            self.assertIsNotNone(tools._BG_JOBS[job_id].process.poll())

            # First poll records the exit; the job is NOT dropped immediately, so a
            # second look can still retrieve the full log of a finished command.
            first = executor.run("process_control", {"action": "poll", "job_id": job_id})
            self.assertIn("exited (exit=0)", first)
            self.assertIn("done", first)
            self.assertIn(job_id, tools._BG_JOBS)
            self.assertIn("done", executor.run("process_control", {"action": "log", "job_id": job_id}))

            # Past the TTL it is forgotten instead of leaking forever.
            tools._BG_JOBS[job_id].finished_at = time.time() - (self.config.BACKGROUND_FINISHED_TTL_SECONDS + 10)
            executor.run("process_control", {"action": "list"})
            self.assertNotIn(job_id, tools._BG_JOBS)
        finally:
            for job in list(tools._BG_JOBS.values()):
                try:
                    job.process.kill()
                except Exception:
                    pass
                job.close_log()
            tools._BG_JOBS.clear()

    def test_system_prompt_teaches_long_installs_instead_of_timeout_failure(self):
        prompt = " ".join(self.config.SYSTEM_PROMPT.casefold().split())
        self.assertIn("installing things is normal authorized work", prompt)
        self.assertIn("pass a bigger `timeout` (up to 900)", prompt)
        self.assertIn("never report an install as \"failed\" when the message says it timed out", prompt)
        self.assertIn("run_shell with background=true", prompt)
        self.assertIn("process_control (list/poll/log/kill)", prompt)

    def test_system_prompt_teaches_stop_semantics(self):
        """Model harus tahu /stop = pembatalan sengaja, bukan error untuk diulang."""
        prompt = " ".join(self.config.SYSTEM_PROMPT.casefold().split())
        self.assertIn("/stop", prompt)
        self.assertIn("force-kills whatever command", prompt)
        self.assertIn("do not resume the cancelled work", prompt)

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

    def test_telegram_tool_progress_uses_zeline_style_labels_and_argument_preview(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        self.assertEqual(telegram._tool_progress_text("load_skill", {"name": "test-driven-development"}), "📚 Reading skill: test-driven-development")
        shell = telegram._tool_progress_text("run_shell", {"command": "python -m unittest tests.test_agent"})
        self.assertEqual(shell, "<pre>python -m unittest tests.test_agent</pre>")
        self.assertTrue(shell.endswith("</pre>"))
        # `<code class="language-...">` di dalam `<pre>` memicu header bahasa +
        # tombol COPY CODE di Telegram; feed progres harus kartu polos.
        self.assertNotIn("<code", shell)
        self.assertNotIn("class=", shell)
        self.assertNotIn("Zeline Terminal", shell)
        self.assertNotIn("📺", shell)
        # read_file dgn offset/limit → tampilkan rentang baris; basename saja (bukan path lokal).
        self.assertEqual(telegram._tool_progress_text("read_file", {"path": "zeline/agent.py", "offset": 1, "limit": 300}), "📖 Reading file <code>agent.py</code> L1-300")
        # read_file tanpa offset/limit → tanpa rentang baris.
        self.assertEqual(telegram._tool_progress_text("read_file", {"path": "/data/data/com.termux/files/home/hotel-dashboard.html"}), "📖 Reading file <code>hotel-dashboard.html</code>")
        self.assertEqual(telegram._tool_progress_text("write_file", {"path": "app.py"}), "📝 Writing file <code>app.py</code>")
        self.assertEqual(telegram._tool_progress_text("edit_file", {"path": "app.py"}), "🎬 Editing file <code>app.py</code>")
        self.assertEqual(telegram._tool_progress_text("patch_file", {"path": "app.py"}), "🎬 Editing file <code>app.py</code>")
        self.assertEqual(telegram._tool_progress_text("search_files", {"query": "name"}), "🔎 Searching files: name")
        self.assertEqual(telegram._tool_progress_text("add_memory", {"fact": "x"}), "🧠 Saving to memory…")
        # Hapus memory TIDAK boleh dilabeli "Saving": verb-nya harus jujur.
        self.assertEqual(telegram._tool_progress_text("remove_memory", {"substring": "x"}), "🧠 Removing from memory…")
        self.assertEqual(telegram._tool_progress_text("system_env", {}), "🧰 Checking system environment…")
        task = telegram._tool_progress_text("update_task", {"task": "Run tests", "status": "in_progress"})
        # Satu baris, tanpa newline.
        self.assertEqual(task, "📋 Updating tasks: in_progress · Run tests")
        self.assertNotIn("\n", task)

    def test_telegram_terminal_progress_has_no_title_or_emoji(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        # Semua perintah shell (pencarian maupun coding) tampil sebagai kartu
        # kode POLOS setinggi satu baris — tanpa judul 'Zeline Terminal', tanpa
        # emoji, tanpa label bahasa, dan tanpa tombol COPY CODE. Yang memicu
        # varian kartu besar di Telegram ada dua: `<code class="language-...">`
        # di dalam `<pre>`, dan isi yang melebihi satu baris.
        search = telegram._tool_progress_text("run_shell", {"command": "python -searching ftmo -v"})
        self.assertEqual(search, "<pre>python -searching ftmo -v</pre>")
        self.assertNotIn("<code", search)
        self.assertNotIn("class=", search)
        self.assertNotIn("Zeline Terminal", search)
        self.assertNotIn("📺", search)
        coding = telegram._tool_progress_text("run_shell", {"command": "pytest -q"})
        self.assertEqual(coding, "<pre>pytest -q</pre>")
        self.assertNotIn("<code", coding)
        self.assertNotIn("Zeline Terminal", coding)
        self.assertNotIn("📺", coding)
        # Command panjang / multiline diratakan jadi SATU baris pendek yang
        # dipotong di batas kata; panjangnya dibatasi agar kartu tetap setinggi
        # satu baris.
        long_cmd = (
            "python3 -c 'from zeline import config, tools\n"
            "cfg = config.load_config()\n"
            "executor = tools.ToolExecutor(cfg, identity=\"cli\", profile=\"full\")'"
        )
        truncated = telegram._tool_progress_text("run_shell", {"command": long_cmd})
        # Penanda potong: tiga titik ASCII, bukan glyph ellipsis "…" (yang di
        # font monospace Telegram menempel ke karakter terakhir dan terlihat
        # seperti potongan yang dipaksakan).
        self.assertTrue(truncated.endswith(f"{telegram._TRUNCATION_MARK}</pre>"))
        self.assertNotIn("…", truncated)
        self.assertNotIn("\n", truncated)
        self.assertNotIn("<code", truncated)
        # Isi mentah (setelah unescape) tidak melebihi batas preview + penanda,
        # jadi kartu tidak bisa tumbuh melewati satu baris.
        import html as _html
        raw_inner = _html.unescape(truncated[len("<pre>"):-len("</pre>")])
        self.assertLessEqual(
            len(raw_inner),
            telegram._TERMINAL_PREVIEW_LIMIT + 1 + len(telegram._TRUNCATION_MARK),
        )
        # Potongan jatuh di batas kata: karakter sebelum penanda adalah spasi,
        # dan tidak ada token yang terputus di tengah.
        self.assertTrue(raw_inner.endswith(f" {telegram._TRUNCATION_MARK}"))

    def test_terminal_preview_limit_fills_the_card_without_wrapping(self):
        """Batas preview DIUKUR dari lebar render, bukan dipilih dari selera.

        Diukur pada screenshot feed di perangkat operator: pitch monospace kartu
        kode 13,2 px/char, area teks dalam kartu 642 px (48,6 char), dan bubble
        terlebar di chat 885 px — teks mulai membungkus ke baris kedua di sekitar
        59 char. Batas lama 37 (41 char dengan penanda) berhenti di 541 px dan
        menyisakan ~101 px kosong, yang membuat kartu terlihat ramping dan
        dipangkas terlalu dini.

        Dua sisi harus dijaga sekaligus: cukup lebar supaya kartu terisi, dan
        tetap di bawah ambang bungkus supaya kartu tinggal SATU baris.
        """
        telegram = importlib.import_module("zeline.gateways.telegram")
        total = telegram._TERMINAL_PREVIEW_LIMIT + 1 + len(telegram._TRUNCATION_MARK)
        # Lebih lebar dari kartu 37-char yang terlihat terlalu ramping…
        self.assertGreaterEqual(telegram._TERMINAL_PREVIEW_LIMIT, 48)
        # …tapi total (termasuk " ...") tidak boleh mencapai ambang bungkus 58.
        self.assertLess(total, 58)

    def test_telegram_web_progress_hides_raw_links(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        # web_fetch tidak menampilkan URL mentah (dan tidak jadi baris feed).
        fetched = telegram._tool_progress_text("web_fetch", {"url": "https://ftmo.com/en/"})
        self.assertNotIn("ftmo.com", fetched)
        self.assertEqual(fetched, "")
        # web_search hanya penanda ringkas: subjek (kata pertama) + '…', bukan kueri panjang.
        search = telegram._tool_progress_text("web_search", {"query": "FundedNext prop trading firm evaluation challenge"})
        self.assertEqual(search, "🌐 Searching web: FundedNext…")
        self.assertTrue(search.endswith("…"))
        # research menampilkan kueri lengkap (detail riset ada di sini).
        research = telegram._tool_progress_text("deep_research", {"query": "FundedNext prop firm review rules payout"})
        self.assertTrue(research.startswith("🌐 Researching: FundedNext prop firm review"))

    def test_telegram_finalize_line_converts_searching_to_reading(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        # Saat selesai, web Searching & Researching (yang di-collapse) diselesaikan
        # jadi '📖 Read web · <subjek>' — TETAP menyebut subjek, bukan generik
        # 'data/other'. Baris file dibiarkan apa adanya.
        self.assertEqual(telegram._finalize_line("🌐 Searching FundedNext…"), "📖 Read web · FundedNext")
        self.assertEqual(telegram._finalize_line("🌐 Researching FundedNext prop firm review"), "📖 Read web · FundedNext prop firm review")
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
            live.add("🌐 Researching FTMO")
        # Search/research tetap di-collapse (repetitif): 1 search + 1 research.
        searches = [l for l in live.lines if l.startswith("🌐 Searching")]
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0], "🌐 Searching FTMO rules")  # baris terbaru
        self.assertEqual(len([l for l in live.lines if l.startswith("🌐 Researching")]), 1)

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
            live.add("🌐 Researching FundingPips rules pricing")
            live.add("🌐 Searching FundingPips")
            live.add("📖 Reading <code>notes.md</code> L1-50")
            rendered = live._render()
        lines = [l for l in rendered.split("\n") if l.strip() and not l.startswith("<pre>")]
        # Search & research ditata dulu; aksi lain menyusul kronologis.
        self.assertEqual(lines[0], "🌐 Searching FundingPips")
        self.assertEqual(lines[1], "🌐 Researching FundingPips rules pricing")

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
        self.assertEqual(telegram._tool_progress_text("execute_code", {"code": "from pathlib import Path\nprint(Path.home())"}), "🐍 Running code: <code>from pathlib import Path</code>…")
        self.assertEqual(telegram._tool_progress_text("manage_skill", {"action": "patch", "name": "zeline-development"}), "📝 Updating skill: <code>zeline-development</code>")
        self.assertEqual(telegram._tool_progress_text("manage_skill", {"action": "create", "name": "riset-prop-firm"}), "💡 Saving skill: <code>riset-prop-firm</code>")
        self.assertEqual(telegram._tool_progress_text("manage_skill", {"action": "write_file", "name": "riset-prop-firm", "file_path": "references/api.md"}), "📄 Writing <code>references/api.md</code> in skill <code>riset-prop-firm</code>")
        self.assertEqual(telegram._tool_progress_text("manage_skill", {"action": "delete", "name": "dupe", "absorbed_into": "riset-prop-firm"}), "🧹 Merging skill <code>dupe</code> into <code>riset-prop-firm</code>")
        self.assertEqual(telegram._tool_progress_text("manage_skill", {"action": "list"}), "🗂 Reviewing saved skills…")
        result = telegram._tool_result_text("manage_skill", {"action": "patch", "name": "zeline-development"}, "Patched private/zeline-development/SKILL.md (1 replacement).")
        self.assertEqual(result, "📒 Improvement: Patched private/zeline-development/SKILL.md (1 replacement).")
        saved = telegram._tool_result_text("manage_skill", {"action": "create", "name": "riset-prop-firm"}, "OK, skill 'riset-prop-firm' created at private/riset-prop-firm/SKILL.md.")
        self.assertEqual(saved, "📒 Improvement: OK, skill 'riset-prop-firm' created at private/riset-prop-firm/SKILL.md.")
        # An inventory read is orientation, not an improvement: reporting it would
        # push the private skill list into the chat on every reflection.
        self.assertIsNone(telegram._tool_result_text("manage_skill", {"action": "list"}, "3 skills:\n- a [private/folder]: x"))

    def test_every_registered_tool_has_a_designed_progress_label(self):
        """Tidak ada tool yang jatuh ke fallback generik `🔧 <nama tool>`.

        Audit sebelumnya: 8 dari 29 tool tampil sebagai `🔧 recall history: lanjut`
        — nama fungsi mentah, bukan label yang menjelaskan pekerjaannya. Test ini
        memaksa setiap tool baru ikut punya cabang label sendiri sejak awal.
        """
        telegram = importlib.import_module("zeline.gateways.telegram")
        tools = importlib.import_module("zeline.tools")
        samples = {
            "add_memory": {"fact": "x"},
            "remove_memory": {"substring": "x"},
            "load_skill": {"name": "s"},
            "web_search": {"query": "q"},
            "web_fetch": {"url": "https://example.com"},
            "network_route": {"action": "list"},
            "deep_research": {"query": "q"},
            "analyze_media": {"path_or_url": "a.png"},
            "generate_image": {"prompt": "p", "path": "o.png"},
            "http_request": {"method": "GET", "url": "https://example.com/api"},
            "browser": {"action": "open", "url": "https://example.com"},
            "code_intel": {"action": "diagnostics", "path": "a.py"},
            "read_file": {"path": "a.py"},
            "write_file": {"path": "a.py"},
            "edit_file": {"path": "a.py"},
            "patch_file": {"path": "a.py"},
            "search_files": {"query": "q"},
            "download_file": {"url": "https://example.com/a.zip", "path": "a.zip"},
            "update_task": {"task": "t", "status": "pending"},
            "manage_skill": {"action": "list"},
            "execute_code": {"code": "print(1)"},
            "run_shell": {"command": "ls"},
            "process_control": {"action": "list"},
            "delegate_task": {"goal": "g"},
            "recall_history": {"query": "q"},
            "ask_user": {"question": "Lanjut?"},
        }
        unlabelled = [
            definition.name
            for definition in tools.TOOL_DEFS
            if telegram._tool_progress_text(definition.name, samples.get(definition.name, {})).startswith("🔧")
        ]
        self.assertEqual(unlabelled, [])

    def test_telegram_progress_labels_runtime_memory_history_and_question(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        # runtime_info bukan "runtime info": yang dibaca identitas runtime aktif.
        self.assertEqual(telegram._tool_progress_text("runtime_info", {}), "🪪 Checking runtime: model &amp; provider…")
        # Memory satu keluarga ikon 🧠; verb-nya yang membedakan baca/simpan/hapus.
        self.assertEqual(telegram._tool_progress_text("list_memory", {}), "🧠 Reading saved memory…")
        self.assertEqual(
            telegram._tool_progress_text("recall_history", {"query": "invoice tabel"}),
            "🕰 Recalling past chat: invoice tabel",
        )
        self.assertEqual(telegram._tool_progress_text("recall_history", {}), "🕰 Recalling recent conversation…")
        self.assertEqual(
            telegram._tool_progress_text("ask_user", {"question": "Squash atau merge commit?"}),
            "🙋 Asking you: Squash atau merge commit?",
        )
        self.assertEqual(telegram._tool_progress_text("ask_user", {}), "🙋 Asking you a question…")

    def test_telegram_progress_labels_browser_code_intel_route_and_download(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        # browser: aksi jadi verb nyata, dan URL diringkas ke HOST saja (query
        # string bisa membawa token).
        opened = telegram._tool_progress_text("browser", {"action": "open", "url": "https://www.example.com/p?token=SECRET"})
        self.assertEqual(opened, "🌍 Opening page: example.com")
        self.assertNotIn("SECRET", opened)
        self.assertEqual(telegram._tool_progress_text("browser", {"action": "click", "selector": "button.login"}), "🖱 Clicking <code>button.login</code>")
        self.assertEqual(telegram._tool_progress_text("browser", {"action": "type", "selector": "#email"}), "⌨️ Typing into <code>#email</code>")
        self.assertEqual(telegram._tool_progress_text("browser", {"action": "screenshot", "path": "shots/home.png"}), "📸 Capturing screenshot <code>home.png</code>")
        self.assertEqual(telegram._tool_progress_text("browser", {"action": "eval", "script": "document.title"}), "🧪 Running JavaScript on the page…")
        # code_intel: sebut file + baris, bukan cuma nama aksi.
        self.assertEqual(
            telegram._tool_progress_text("code_intel", {"action": "definition", "path": "zeline/agent.py", "line": 820}),
            "🧭 Finding definition <code>agent.py</code> L820",
        )
        self.assertEqual(telegram._tool_progress_text("code_intel", {"action": "servers"}), "🩺 Checking language servers…")
        # network_route: label rute boleh tampil, proxy_url TIDAK (user:pass@host).
        route = telegram._tool_progress_text(
            "network_route", {"action": "add", "label": "sg-1", "proxy_url": "socks5h://user:hunter2@1.2.3.4:1080"}
        )
        self.assertEqual(route, "🛰 Adding network route: <code>sg-1</code>")
        self.assertNotIn("hunter2", route)
        # download_file: nama tujuan + host, bukan URL panjang mentah.
        got = telegram._tool_progress_text(
            "download_file", {"url": "https://github.com/o/r/releases/download/v1/install.sh", "path": "tmp/install.sh"}
        )
        self.assertEqual(got, "📥 Downloading <code>install.sh</code> from github.com")
        self.assertNotIn("releases/download", got)

    def test_telegram_progress_renders_mcp_tools_without_double_spaces(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        # Fallback lama mengubah `mcp__mem0__add_memory` jadi "🔧 mcp  mem0  add memory".
        line = telegram._tool_progress_text("mcp__mem0__add_memory", {"text": "x"})
        self.assertEqual(line, "🧩 add memory via mem0")
        self.assertNotIn("  ", line)
        self.assertNotIn("mcp", line)
        self.assertEqual(telegram._tool_progress_text("mcp__notion__query_database", {}), "🧩 query database via notion")

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
        # Feed final = baris aktivitas yang sudah di-finalize (Searching→Read web).
        finalized = [
            c for c in api.call_args_list
            if len(c.args) > 1 and c.args[1] == "editMessageText" and "📖 Read web" in str(c.kwargs.get("text", ""))
        ]
        self.assertTrue(finalized)
        # Subjek yang dicari tetap ikut di baris final (bukan generik 'data/other').
        self.assertIn("FTMO", str(api.call_args_list))
        self.assertNotIn("data/other", str(api.call_args_list))
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

    def test_telegram_model_picker_splits_pages_per_route(self):
        # A router catalog mixing routes (Gr/, tabi/, cx/) must be paginated one
        # route per page instead of one long scrolling list of full ids.
        telegram = importlib.import_module("zeline.gateways.telegram")
        models = ["Gr/claude-opus-5", "Gr/gpt-5", "tabi/claude-opus-5", "cx/gpt-5-codex"]
        text, markup = telegram._model_picker_payload(models, "tabi/claude-opus-5", 0, "9Router")
        buttons = [button for row in markup["inline_keyboard"] for button in row]
        model_buttons = [b for b in buttons if b["callback_data"].startswith("model:") and b["callback_data"] != "model:cancel"]
        # Page 1 = the first route only, labelled with its friendly name.
        self.assertIn("GoRouter", text)
        self.assertIn("(1/3)", text)
        self.assertEqual([b["callback_data"] for b in model_buttons], ["model:0:0", "model:0:1"])
        self.assertEqual([b["text"] for b in model_buttons], ["claude-opus-5", "gpt-5"])
        # Navigation carries the target page in the callback (no process state).
        self.assertIn("grp:0:1", [b["callback_data"] for b in buttons])
        self.assertLessEqual(max(len(b["callback_data"]) for b in buttons), 64)

    def test_telegram_model_picker_pages_wrap_and_keep_global_indexes(self):
        # Page 2 must keep GLOBAL indexes so the existing model: callback path
        # still resolves, and Next on the last page wraps back to the first.
        telegram = importlib.import_module("zeline.gateways.telegram")
        models = ["Gr/a", "tabi/b", "cx/c"]
        text, markup = telegram._model_picker_payload(models, "Gr/a", 0, "9Router", 2)
        buttons = [button for row in markup["inline_keyboard"] for button in row]
        model_buttons = [b for b in buttons if b["callback_data"].startswith("model:") and b["callback_data"] != "model:cancel"]
        self.assertIn("(3/3)", text)
        self.assertEqual([b["callback_data"] for b in model_buttons], ["model:0:2"])
        self.assertIn("grp:0:0", [b["callback_data"] for b in buttons])  # Next wraps
        self.assertIn("grp:0:1", [b["callback_data"] for b in buttons])  # Prev

    def test_telegram_model_picker_single_route_has_no_pagination(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        models = ["Gr/a", "Gr/b"]
        _, markup = telegram._model_picker_payload(models, "Gr/a", 0, "9Router")
        callbacks = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
        self.assertFalse([c for c in callbacks if c.startswith("grp:")])

    def test_telegram_callback_grp_switches_page_without_changing_model(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        provider = {"slug": "9router", "name": "9Router", "base_url": "https://r.example/v1", "api_key": "k", "model": "Gr/a"}
        with mock.patch.object(telegram, "_api_call") as api, \
             mock.patch.object(telegram, "_configured_providers", return_value=[provider]), \
             mock.patch.object(telegram, "_discover_provider_models", return_value=["Gr/a", "tabi/b"]), \
             mock.patch.object(telegram.config, "save_config") as save:
            sessions = mock.Mock()
            telegram._handle_callback(
                "https://api.example",
                {"id": "1", "data": "grp:0:1", "message": {"chat": {"id": 7}, "message_id": 9}},
                sessions,
            )
        # Paging must only re-render the picker: no config write, no session switch.
        save.assert_not_called()
        sessions.switch_provider.assert_not_called()
        edits = [c for c in api.call_args_list if len(c.args) > 1 and c.args[1] == "editMessageText"]
        self.assertTrue(edits)
        self.assertIn("TabiToken", str(edits[-1].kwargs.get("text", "")))

    def test_discover_provider_models_uses_cache_to_avoid_repeat_calls(self):
        # Picker taps provider then model → without cache that's 2 network calls.
        # Second call within TTL must hit the cache (no second HTTP request).
        telegram = importlib.import_module("zeline.gateways.telegram")
        telegram._MODELS_CACHE.clear()
        telegram._MODEL_META_CACHE.clear()
        provider = {"base_url": "https://prov.example/v1", "api_key": "k", "model": "m"}

        class FakeResp:
            ok = True
            def json(self):
                return {"data": [{"id": "a"}, {"id": "b"}]}

        with mock.patch.object(telegram._HTTP, "get", return_value=FakeResp()) as get:
            first = telegram._discover_provider_models(provider)
            second = telegram._discover_provider_models(provider)
        self.assertEqual(first, ["a", "b"])
        self.assertEqual(second, ["a", "b"])
        # Only ONE HTTP call despite two lookups → cache works.
        self.assertEqual(get.call_count, 1)
        telegram._MODELS_CACHE.clear()
        telegram._MODEL_META_CACHE.clear()

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

    def test_telegram_configured_providers_dedupes_active_label_case_drift(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        saved = {
            "providers": {
                "9router": {
                    "name": "9router",
                    "base_url": "http://localhost:20128/v1",
                    "api_key": "secret",
                    "model": "old-model",
                }
            },
            "provider": {
                "name": "9Router",
                "base_url": "http://localhost:20128/v1/",
                "api_key": "secret",
                "model": "new-model",
            },
        }
        with mock.patch.object(telegram.config, "stored_config_copy", return_value=saved):
            providers = telegram._configured_providers()
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0]["slug"], "9router")

    def test_telegram_provider_callback_opens_models_with_back_button(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        providers = [{"slug": "token-harbor", "name": "Token Harbor", "model": "model-a", "base_url": "https://api.example/v1", "api_key": "key"}]
        callback = {"id": "cb-1", "data": "provider:0", "message": {"message_id": 9, "chat": {"id": 42}}}
        with mock.patch.object(telegram, "_configured_providers", return_value=providers), mock.patch.object(telegram, "_discover_provider_models", return_value=["model-a", "model-b"]), mock.patch.object(telegram, "_api_call") as api:
            telegram._handle_callback("bot-api", callback, object())
        edit = api.call_args_list[-1]
        self.assertEqual(edit.args[1], "editMessageText")
        self.assertIn("Token Harbor", edit.kwargs["text"])
        self.assertIn("• 2 models", edit.kwargs["text"])
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
        self.assertIn("Model Switched", confirm)
        self.assertIn("model-b", confirm)
        self.assertNotIn("Konteks", confirm)
        self.assertNotIn("New session started", confirm)
        methods = [call.args[1] for call in api.call_args_list]
        self.assertEqual(methods, ["answerCallbackQuery", "editMessageText"])

    def test_telegram_interactive_edit_retries_and_falls_back_to_send(self):
        """Tap tombol tidak boleh 'hilang' saat satu edit gagal (bug tap-2x).

        editMessageText dulu attempts=1, jadi satu ConnectionError sesaat bikin
        tap pertama tampak tidak berefek. Sekarang edit interaktif diretry dan,
        kalau tetap gagal, hasilnya dikirim sebagai pesan baru.
        """
        telegram = importlib.import_module("zeline.gateways.telegram")
        with mock.patch.object(telegram, "_api_call", return_value=None) as api:
            telegram._edit_interactive("bot-api", 42, 9, "Select a model")
        methods = [call.args[1] for call in api.call_args_list]
        self.assertEqual(methods, ["editMessageText", "sendMessage"])
        self.assertEqual(api.call_args_list[0].kwargs["attempts"], telegram._INTERACTIVE_ATTEMPTS)
        self.assertGreater(telegram._INTERACTIVE_ATTEMPTS, 1)

    def test_telegram_not_modified_edit_counts_as_success(self):
        """'message is not modified' bukan kegagalan → jangan kirim duplikat."""
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Response:
            ok = False
            status_code = 400
            def json(self): return {"ok": False, "description": "Bad Request: message is not modified"}

        with mock.patch.object(telegram._HTTP, "post", return_value=Response()):
            result = telegram._api_call("bot-api", "editMessageText", chat_id=1, message_id=2, text="same")
        self.assertIsNotNone(result)
        self.assertTrue(result.get("ok"))

    def test_telegram_model_capabilities_reuse_picker_catalog_without_second_call(self):
        """Konfirmasi ganti model harus pakai cache /models, bukan request kedua."""
        telegram = importlib.import_module("zeline.gateways.telegram")
        telegram._MODELS_CACHE.clear()
        telegram._MODEL_META_CACHE.clear()
        provider = {"slug": "p", "name": "P", "base_url": "https://api.example/v1", "api_key": "key", "model": "model-a"}
        payload = {"data": [{"id": "model-a", "capabilities": {"contextWindow": 128000}}, {"id": "model-b"}]}

        class Response:
            ok = True
            def json(self): return payload

        with mock.patch.object(telegram._HTTP, "get", return_value=Response()) as get:
            models = telegram._discover_provider_models(provider)
            caps = telegram._fetch_model_capabilities(provider, "model-a")
        self.assertEqual(models, ["model-a", "model-b"])
        self.assertEqual(caps.get("capabilities", {}).get("contextWindow"), 128000)
        self.assertEqual(get.call_count, 1)
        telegram._MODELS_CACHE.clear()
        telegram._MODEL_META_CACHE.clear()

    def test_telegram_error_badge_shows_only_the_actual_status_code(self):
        """Badge error harus menyebut kode yang benar-benar terjadi, bukan daftar."""
        telegram = importlib.import_module("zeline.gateways.telegram")
        agent = importlib.import_module("zeline.agent")
        self.assertTrue(telegram._format_agent_error(agent.provider_status_message(403)).startswith("🪫 403 —"))
        self.assertTrue(telegram._format_agent_error(agent.provider_status_message(429)).startswith("🪫 429 —"))
        self.assertTrue(telegram._format_agent_error("Provider says out of credits").startswith("🪫 Quota/Auth —"))
        self.assertNotIn("401, 403, 429", telegram._format_agent_error(agent.provider_status_message(403)))

    def test_telegram_status_badges_match_what_each_code_actually_means(self):
        """Ikon + teks per kode diambil dari tabel status router, bukan tebakan.

        403 di router = ``permission_error/insufficient_quota`` (kuota habis,
        ada cooldown), BUKAN kunci invalid — kunci invalid memberi 401. Dulu
        keduanya memakai teks "API key is invalid — update it with zeline
        setup", yang menyuruh user mengganti kredensial yang sehat.
        """
        telegram = importlib.import_module("zeline.gateways.telegram")
        agent = importlib.import_module("zeline.agent")
        expected = {
            401: ("🪫 401 —", "api key is invalid"),
            402: ("🪫 402 —", "payment required"),
            403: ("🪫 403 —", "insufficient provider quota"),
            429: ("🪫 429 —", "rate limited"),
            502: ("⏰ 502 —", "bad gateway"),
            503: ("⏱️ 503 —", "temporarily unavailable"),
            504: ("⏰ 504 —", "timed out behind the gateway"),
        }
        for status, (badge, phrase) in expected.items():
            rendered = telegram._format_agent_error(agent.provider_status_message(status))
            with self.subTest(status=status):
                self.assertTrue(rendered.startswith(badge), rendered)
                self.assertIn(phrase, rendered.lower())
                # Kode hanya boleh muncul sekali: awalan "The provider returned
                # HTTP 403 — " dipotong karena badge sudah menampilkannya.
                self.assertEqual(rendered.count(str(status)), 1, rendered)

    def test_telegram_403_does_not_tell_the_user_to_replace_the_api_key(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        agent = importlib.import_module("zeline.agent")
        rendered = telegram._format_agent_error(agent.provider_status_message(403)).lower()
        self.assertNotIn("api key", rendered)
        self.assertNotIn("zeline setup", rendered)

    def test_telegram_gateway_timeout_is_not_labelled_a_client_read_timeout(self):
        """504 provider ≠ read-timeout klien; penyebab & tindakannya berbeda."""
        telegram = importlib.import_module("zeline.gateways.telegram")
        agent = importlib.import_module("zeline.agent")
        rendered = telegram._format_agent_error(agent.provider_status_message(504))
        self.assertNotIn("Read Timeout", rendered)
        self.assertTrue(rendered.startswith("⏰ 504 —"), rendered)

    def test_telegram_timeout_error_reassures_conversation_intact(self):
        """Timeout harus menenangkan user: riwayat aman + saran /new."""
        telegram = importlib.import_module("zeline.gateways.telegram")
        out = telegram._format_agent_error(
            "The model 'x' did not respond within 180s (request timed out)."
        )
        self.assertTrue(out.startswith("⚠️ Read Timeout —"))
        self.assertIn("No messages were dropped", out)
        self.assertIn("/new", out)

    def test_telegram_startup_survives_transient_getme_failure(self):
        """Satu ReadTimeout saat startup TIDAK boleh mematikan gateway.

        Bug: `getMe` dipanggil attempts=1, jadi satu timeout (biasa di Termux)
        bikin gateway berhenti dengan "token could not be verified" — bot mati
        total dan /model tidak dijawab, padahal tokennya valid.
        """
        telegram = importlib.import_module("zeline.gateways.telegram")
        import requests as _rq

        class OK:
            ok = True
            status_code = 200
            def json(self): return {"ok": True, "result": {"username": "zerolinearbot"}}

        calls = {"n": 0}
        def flaky(url, json=None, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise _rq.ReadTimeout("boom")
            return OK()

        with mock.patch.object(telegram.time, "sleep"), mock.patch.object(telegram._HTTP, "post", side_effect=flaky):
            username, failure = telegram._verify_token("bot-api")
        self.assertEqual(username, "zerolinearbot")
        self.assertEqual(failure, "")
        self.assertEqual(calls["n"], 3)

    def test_telegram_startup_stops_immediately_on_real_token_rejection(self):
        """401/404 = token benar-benar salah → berhenti, jangan retry sia-sia."""
        telegram = importlib.import_module("zeline.gateways.telegram")

        class Unauthorized:
            ok = False
            status_code = 401
            def json(self): return {"ok": False, "description": "Unauthorized"}

        calls = {"n": 0}
        def rejected(url, json=None, timeout=None):
            calls["n"] += 1
            return Unauthorized()

        with mock.patch.object(telegram.time, "sleep"), mock.patch.object(telegram._HTTP, "post", side_effect=rejected):
            username, failure = telegram._verify_token("bot-api")
        self.assertIsNone(username)
        self.assertIn("rejected by Telegram", failure)
        self.assertEqual(calls["n"], 1)  # no pointless retry

    def test_telegram_polling_logs_outage_summary_and_recovery(self):
        """Log harus menjelaskan 'kenapa bot diam', bukan 233 baris retry.

        Sebelumnya tiap kegagalan getUpdates dicetak satu baris ("retry #233"),
        menenggelamkan baris penting dan tidak pernah mencatat kapan polling
        PULIH — jadi 'Zeline tadi diam' cuma bisa ditebak dari offset.
        """
        telegram = importlib.import_module("zeline.gateways.telegram")
        _rq = importlib.import_module("requests")

        class OkResponse:
            ok = True
            status_code = 200
            def json(self): return {"ok": True, "result": []}

        stop_event = threading.Event()
        calls = {"n": 0}

        def flaky(url, params=None, timeout=None):
            calls["n"] += 1
            # 25 kegagalan beruntun, lalu pulih, lalu hentikan loop.
            if calls["n"] <= 25:
                raise _rq.ConnectionError("network down")
            stop_event.set()
            return OkResponse()

        printed: list[str] = []
        with mock.patch.object(telegram, "_verify_token", return_value=("zerolinearbot", "")), \
             mock.patch.object(telegram, "_api_call"), \
             mock.patch.object(telegram, "_load_offset", return_value=0), \
             mock.patch.object(telegram, "_save_offset"), \
             mock.patch.object(telegram.requests, "get", side_effect=flaky), \
             mock.patch.object(stop_event, "wait", return_value=False), \
             mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(str(a[0]) if a else "")):
            telegram.start(object(), {"token": "123:abc", "allowed": [1], "tool_profile": "safe"}, stop_event)

        errors = [line for line in printed if "polling error" in line]
        recovered = [line for line in printed if "polling recovered" in line]
        # Diringkas: error pertama + tiap 20 percobaan, BUKAN 25 baris.
        self.assertEqual(len(errors), 2, msg=f"expected throttled error lines, got {errors}")
        self.assertIn("retry #1", errors[0])
        self.assertIn("cannot receive messages", errors[0])
        self.assertIn("retry #20", errors[1])
        # Baris PULIH ada dan menyebut jumlah percobaan gagal.
        self.assertEqual(len(recovered), 1, msg=f"expected one recovery line, got {printed[-5:]}")
        self.assertIn("25 failed attempts", recovered[0])

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
        reply = api.call_args.kwargs["text"]
        self.assertIn("❄️ Stopped — Bangun aplikasi", reply)
        self.assertIn("force-killed", reply)
        self.assertIn("history are intact", reply)

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
        self.assertIn("╭───────────────────🌟", text)
        self.assertIn("Session reset! Starting fresh", text)
        self.assertIn("├ Model :", text)
        self.assertIn("├ Provider :", text)
        self.assertIn("Context : 0 tokens", text)
        self.assertIn("├ Endpoint :", text)
        self.assertIn("╰ Tip :", text)

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
        document = {"file_name": "ZELINE-ZENITH-FORSALE-FINAL.zip", "file_size": len(payload), "file_id": "zip-file"}
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
