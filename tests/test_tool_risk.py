"""Risk classification per tool + automatic approval enforcement.

Every native tool carries one of the five risk classes (Read/Write/Network/
Install/Destructive). Install/Destructive calls — and Write calls that escape
the session workspace — must trigger operator approval through the ``ask_user``
picker instead of running on the model's authority alone. The legacy
``SAFE_PROFILES`` keep gating *visibility* exactly as before.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

#: The full audited classification matrix: tool name -> expected risk class.
#: This table is the contract; changing a tool's class means updating it here
#: deliberately, not by accident.
EXPECTED_RISKS = {
    "send_file": "read",
    "git": "write",
    "schedule_task": "install",
    "runtime_info": "read",
    "add_memory": "write",
    "remove_memory": "write",
    "restore_memory": "write",
    "list_memory": "read",
    "consolidate_memory": "write",
    "goal_add": "write",
    "goal_update": "write",
    "goal_list": "read",
    "goal_get": "read",
    "sync_memory": "write",
    "load_skill": "read",
    "web_search": "read",
    "web_fetch": "read",
    "network_route": "install",
    "deep_research": "read",
    "analyze_media": "read",
    "generate_image": "write",
    "generate_video": "write",
    "edit_image": "write",
    "edit_video": "write",
    "text_to_speech": "write",
    "qr_code": "write",
    "transcribe_audio": "read",
    "pdf_tool": "write",
    "http_request": "destructive",
    "browser": "destructive",
    "code_intel": "read",
    "system_env": "read",
    "read_file": "read",
    "write_file": "write",
    "edit_file": "write",
    "patch_file": "write",
    "search_files": "read",
    "download_file": "write",
    "undo_file": "write",
    "update_task": "write",
    "manage_skill": "install",
    "resolve_lesson": "write",
    "execute_code": "destructive",
    "run_shell": "destructive",
    "process_control": "destructive",
    "delegate_task": "write",
    # Install-class: a spawned worker acts unattended after the turn, so the
    # spawn always asks and the declared worker grants are shown for approval.
    "spawn_worker": "install",
    "worker_status": "read",
    "worker_result": "read",
    "recall_history": "read",
    "ask_user": "read",
    "github_repos": "read",
    "github_issues": "read",
    "github_create_issue": "network",
    "github_issue_comment": "network",
    "github_prs": "read",
    "gmail_search": "read",
    "gmail_read": "read",
    "gmail_send": "network",
    "google_calendar": "read",
    "sheets_read": "read",
    "drive_list": "read",
    "whatsapp_send": "network",
    "whatsapp_template": "network",
    # Self-improving skills: content fixes only as approved proposals;
    # review apply and rollback both INSTALL (they mutate the agent's own
    # skill set / rewrite skill content).
    "propose_skill_fix": "write",
    "apply_skill_proposal": "install",
    "review_skills": "read",
    "apply_skill_review": "install",
    "rollback_skill_change": "install",
    # Newer agent tools (added after 60254cc): episodic memory, GEPA
    # self-improvement, learned skills, FTS5 session search, skill hub,
    # live worker steering, user model. Risk classes verified against
    # TOOL_DEFS in zeline/tools.py 2026-10-08.
    "episode_add": "write",
    "episode_list": "read",
    "gepa_drafts": "read",
    "gepa_learn": "read",
    "improve_skill": "write",
    "learn_skill": "write",
    "list_learned_skills": "read",
    "search_sessions": "read",
    "skill_install": "write",
    "voice_speak": "write",
    "voice_transcribe": "read",
    "clawhub_install": "write",
    "clawhub_search": "read",
    "workflow_execute": "install",
    "workflow_pause": "write",
    "workflow_resume": "write",
    "workflow_status": "read",
    "email_send": "network",
    "peer_send": "write",
    "skill_pack": "read",
    "steer_worker": "write",
    "user_model_get": "read",
    "user_model_set": "write",

    # Connector tools (waves 1-6): read-only ops = read (auto-allow),
    # API mutations = network (always approval). Verified 2026-10-08.
    "activecampaign_create_contact": "network",
    "activecampaign_list_contacts": "read",
    "airtable_create_record": "network",
    "airtable_list_records": "read",
    "algolia_list_indexes": "read",
    "algolia_search_index": "read",
    "apollo_enrich_person": "read",
    "apollo_people_search": "read",
    "asana_create_task": "network",
    "asana_list_tasks": "read",
    "beehiiv_list_posts": "read",
    "betterstack_list_monitors": "read",
    "bitbucket_list_prs": "read",
    "bitbucket_list_repos": "read",
    "bitly_list_links": "read",
    "bitly_shorten": "network",
    "bluesky_post": "network",
    "bluesky_read_timeline": "read",
    "box_get_file_info": "read",
    "box_list_files": "read",
    "buffer_create_post": "network",
    "buffer_list_profiles": "read",
    "bunnycdn_list_pull_zones": "read",
    "bunnycdn_list_storage_zones": "read",
    "calendly_list_events": "read",
    "chargebee_list_customers": "read",
    "chargebee_list_subscriptions": "read",
    "clickup_create_task": "network",
    "clickup_list_tasks": "read",
    "close_create_lead": "network",
    "close_list_leads": "read",
    "cloudflare_list_dns_records": "read",
    "cloudflare_list_zones": "read",
    "cloudinary_list_resources": "read",
    "cloudinary_resource_info": "read",
    "coinbase_list_accounts": "read",
    "coinbase_spot_price": "read",
    "confluence_get_page": "read",
    "confluence_search_pages": "read",
    "convertkit_list_subscribers": "read",
    "crates_io_crate_info": "read",
    "crates_io_search_crates": "read",
    "cronitor_list_monitors": "read",
    "datadog_list_monitors": "read",
    "devto_create_article": "network",
    "devto_list_articles": "read",
    "digitalocean_list_droplets": "read",
    "discord_list_channels": "read",
    "discord_send_message": "network",
    "dropbox_get_metadata": "read",
    "dropbox_list_files": "read",
    "fathom_list_sites": "read",
    "flyio_list_apps": "read",
    "freshdesk_create_ticket": "network",
    "freshdesk_list_tickets": "read",
    "ghost_create_post": "network",
    "ghost_list_posts": "read",
    "gitbook_list_content": "read",
    "gitbook_list_spaces": "read",
    "gitlab_list_issues": "read",
    "gitlab_list_mrs": "read",
    "gitlab_list_projects": "read",
    "hackernews_get_item": "read",
    "hackernews_top_stories": "read",
    "healthchecks_list_checks": "read",
    "height_list_tasks": "read",
    "heroku_list_apps": "read",
    "hetzner_list_servers": "read",
    "hubspot_create_contact": "network",
    "hubspot_list_contacts": "read",
    "hunter_domain_search": "read",
    "hunter_verify_email": "read",
    "intercom_list_conversations": "read",
    "jenkins_job_status": "read",
    "jenkins_list_jobs": "read",
    "jira_create_issue": "network",
    "jira_search": "read",
    "jotform_get_submissions": "read",
    "jotform_list_forms": "read",
    "lemlist_campaign_stats": "read",
    "lemlist_list_campaigns": "read",
    "lemon_squeezy_list_customers": "read",
    "lemon_squeezy_list_orders": "read",
    "linear_create_issue": "network",
    "linear_list_issues": "read",
    "linkedin_get_profile": "read",
    "linkedin_share_post": "network",
    "mailchimp_list_audiences": "read",
    "mailchimp_list_campaigns": "read",
    "mailgun_list_messages": "read",
    "mailgun_send_email": "network",
    "mastodon_post_toot": "network",
    "mastodon_read_timeline": "read",
    "meilisearch_list_indexes": "read",
    "meilisearch_search_index": "read",
    "monday_list_boards": "read",
    "monday_list_items": "read",
    "n8n_execute_workflow": "network",
    "n8n_get_workflow": "read",
    "n8n_list_workflows": "read",
    "notion_create_page": "network",
    "notion_query_database": "read",
    "notion_search": "read",
    "npm_registry_package_info": "read",
    "npm_registry_search": "read",
    "onesignal_send_push": "network",
    "openweathermap_current_weather": "read",
    "openweathermap_forecast": "read",
    "opsgenie_list_alerts": "read",
    "packagist_package_info": "read",
    "packagist_search_packages": "read",
    "paddle_list_customers": "read",
    "paddle_list_transactions": "read",
    "pagerduty_list_incidents": "read",
    "paypal_get_order": "read",
    "paypal_list_invoices": "read",
    "pipedrive_create_deal": "network",
    "pipedrive_list_deals": "read",
    "plausible_list_sites": "read",
    "plausible_site_stats": "read",
    "polar_list_orders": "read",
    "polar_list_products": "read",
    "producthunt_search_posts": "read",
    "producthunt_todays_hunts": "read",
    "pushover_send_notification": "network",
    "pypi_registry_package_info": "read",
    "railway_list_projects": "read",
    "reddit_list_posts": "read",
    "reddit_search": "read",
    "render_list_deploys": "read",
    "render_list_services": "read",
    "resend_send_email": "network",
    "rubygems_package_info": "read",
    "rubygems_search": "read",
    "sendgrid_send_email": "network",
    "sentry_list_issues": "read",
    "shortcut_create_story": "network",
    "shortcut_list_stories": "read",
    "slack_list_channels": "read",
    "slack_read_history": "read",
    "slack_send_message": "network",
    "stripe_list_charges": "read",
    "stripe_list_customers": "read",
    "surveymonkey_list_surveys": "read",
    "tally_list_forms": "read",
    "teams_send_message": "network",
    "teamwork_list_projects": "read",
    "teamwork_list_tasks": "read",
    "telegram_bot_get_me": "read",
    "telegram_bot_send_message": "network",
    "todoist_add_task": "network",
    "todoist_list_tasks": "read",
    "trello_create_card": "network",
    "trello_list_boards": "read",
    "trello_list_cards": "read",
    "twilio_list_messages": "read",
    "twilio_send_sms": "network",
    "typeform_get_responses": "read",
    "typeform_list_forms": "read",
    "typesense_list_collections": "read",
    "typesense_search_collection": "read",
    "vercel_list_deployments": "read",
    "vonage_send_sms": "network",
    "vultr_list_instances": "read",
    "webflow_list_collections": "read",
    "webflow_list_sites": "read",
    "wise_get_rate": "read",
    "wise_list_profiles": "read",
    "wrike_create_task": "network",
    "wrike_list_tasks": "read",
    "x_api_post_tweet": "network",
    "x_api_read_timeline": "read",
    "zendesk_create_ticket": "network",
    "zendesk_list_tickets": "read",
    "zoho_crm_create_contact": "network",
    "zoho_crm_list_contacts": "read",
}

DANGEROUS = {"install", "destructive", "network"}


def fresh_modules():
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    tools = importlib.import_module("zeline.tools")
    interaction = importlib.import_module("zeline.interaction")
    config = importlib.import_module("zeline.config")
    agent_module = importlib.import_module("zeline.agent")
    return tools, interaction, config, agent_module


class _AllowAllPolicy:
    """Test-only: mensimulasikan policy yang dipasang send() di produksi.

    Helper executor() di bawah dipakai untuk menguji perilaku tool/profile,
    bukan gate-nya — jadi gate dilewati dengan allow-all. Tanpa policy,
    fallback fail-closed (verdict owner) akan me-deny tool mutasi.
    """

    on_tool = None

    def decide(self, executor, name, args):
        return "allow"


class RiskTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._saved_env = dict(os.environ)
        os.environ["ZELINE_HOME"] = str(self.home / "state")
        os.environ["ZELINE_API_KEY"] = "test-key"
        os.environ["ZELINE_BASE_URL"] = "http://provider.test/v1"
        os.environ["ZELINE_MODEL"] = "test-model"
        self.tools, self.interaction, self.config, self.agent_module = fresh_modules()
        self.config.STREAM_RESPONSES = False
        self.workspace = self.home / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved_env)
        # Never leak a pending question or channel into the next test.
        try:
            self.interaction._PENDING.clear()
            self.interaction._CHANNELS.clear()
        except Exception:
            pass
        self._tmp.cleanup()

    def executor(self, profile="full", identity="test:risk"):
        ex = self.tools.ToolExecutor(identity, profile=profile, workspace=str(self.workspace))
        # Uji perilaku tool/profile, bukan gate: lewati gate seperti di produksi.
        ex.approval_policy = _AllowAllPolicy()
        return ex


class ClassificationTests(RiskTestBase):
    def test_every_native_tool_has_a_valid_risk_class(self):
        tools = self.tools
        # Count is tied to the audited matrix: adding a tool without updating
        # EXPECTED_RISKS fails here by design (no silent drift like the old 69).
        self.assertEqual(len(tools.TOOL_DEFS), len(EXPECTED_RISKS))
        names = [d.name for d in tools.TOOL_DEFS]
        self.assertEqual(len(names), len(set(names)), "duplicate tool names")
        for definition in tools.TOOL_DEFS:
            with self.subTest(tool=definition.name):
                self.assertIn(definition.risk, tools.TOOL_RISKS)

    def test_classification_matrix_matches_audit(self):
        actual = {d.name: d.risk for d in self.tools.TOOL_DEFS}
        self.assertEqual(actual, EXPECTED_RISKS)

    def test_unknown_risk_class_is_rejected_at_definition_time(self):
        with self.assertRaises(ValueError):
            self.tools.ToolDef(
                "bogus", "x", {}, frozenset({"full"}), risk="nonsense",
            )


class ApprovalDecisionTests(RiskTestBase):
    def test_install_destructive_and_network_tools_require_approval(self):
        executor = self.executor()
        for definition in self.tools.TOOL_DEFS:
            if definition.risk in DANGEROUS:
                with self.subTest(tool=definition.name):
                    question = executor.approval_question(definition.name, {})
                    self.assertIsNotNone(question)
                    self.assertIn(definition.name, question)
                    self.assertIn(definition.risk, question)

    def test_read_tools_never_require_approval(self):
        executor = self.executor()
        for definition in self.tools.TOOL_DEFS:
            if definition.risk == "read":
                with self.subTest(tool=definition.name):
                    self.assertIsNone(executor.approval_question(definition.name, {}))

    def test_write_inside_workspace_does_not_require_approval(self):
        executor = self.executor()
        self.assertIsNone(
            executor.approval_question("write_file", {"path": "notes/todo.txt", "content": "x"})
        )
        self.assertIsNone(executor.approval_question("git", {"action": "status", "path": "."}))

    def test_write_outside_workspace_requires_approval(self):
        executor = self.executor()
        for path in ("/etc/passwd", "../escape.txt", str(self.home / "outside.txt")):
            with self.subTest(path=path):
                question = executor.approval_question(
                    "write_file", {"path": path, "content": "x"}
                )
                self.assertIsNotNone(question)
                self.assertIn("workspace", question)

    def test_write_tool_without_path_args_does_not_require_approval(self):
        executor = self.executor()
        self.assertIsNone(executor.approval_question("add_memory", {"fact": "x"}))

    def test_unknown_tool_returns_no_question(self):
        self.assertIsNone(self.executor().approval_question("no_such_tool", {}))

    def test_tool_invisible_in_profile_returns_no_question(self):
        # run_shell is not exposed on "safe": _dispatch reports the profile
        # error, the approval gate stays out of the way.
        executor = self.executor(profile="safe")
        self.assertIsNone(executor.approval_question("run_shell", {"command": "ls"}))

    def test_ask_user_itself_is_never_gated(self):
        # Gating the approval mechanism would recurse.
        executor = self.executor()
        self.assertIsNone(
            executor.approval_question("ask_user", {"question": "x?", "options": ["Allow", "Deny"]})
        )

    def test_parallel_safe_tools_never_need_approval(self):
        # The agent runs these concurrently; a blocking approval there would
        # wedge the pool. Keep the invariant: parallel-safe ⟹ read-only.
        executor = self.executor()
        for name in self.agent_module._PARALLEL_SAFE_TOOLS:
            with self.subTest(tool=name):
                definition = next(d for d in self.tools.TOOL_DEFS if d.name == name)
                self.assertEqual(definition.risk, "read")
                self.assertIsNone(executor.approval_question(name, {}))


class NetworkMutationTests(RiskTestBase):
    """Verdict 1: NETWORK means *mutating* network use, and it always asks.

    Pure read-only fetch (search, page fetch, inference against the already-
    trusted provider) is READ — risk is the effect, not the channel.
    """

    READ_ONLY_NETWORK = (
        "web_search",
        "web_fetch",
        "deep_research",
        "analyze_media",
        "transcribe_audio",
    )

    def test_read_only_network_tools_do_not_ask(self):
        executor = self.executor()
        for name in self.READ_ONLY_NETWORK:
            with self.subTest(tool=name):
                self.assertIsNone(executor.approval_question(name, {}))

    def test_voice_loop_never_asks(self):
        # transcribe_audio backs every voice message; an approval picker
        # there would make voice chat unusable.
        executor = self.executor()
        self.assertIsNone(
            executor.approval_question("transcribe_audio", {"audio": "voice.ogg"})
        )
        self.assertIsNone(
            executor.approval_question("analyze_media", {"path_or_url": "voice.ogg"})
        )

    def test_mutating_network_tools_ask(self):
        executor = self.executor()
        for name in (
            "gmail_send",
            "whatsapp_send",
            "whatsapp_template",
            "github_create_issue",
            "github_issue_comment",
        ):
            with self.subTest(tool=name):
                question = executor.approval_question(name, {})
                self.assertIsNotNone(question)
                self.assertIn("network", question)
                self.assertIn("mutating", question)

    def test_download_is_a_workspace_write_not_a_network_mutation(self):
        executor = self.executor()
        self.assertIsNone(
            executor.approval_question(
                "download_file",
                {"url": "https://example.com/a.zip", "path": "dl/a.zip"},
            )
        )

    def test_download_escaping_the_workspace_asks(self):
        # A read-only tool with a sneaky side effect: the destination is
        # caged at runtime, but the approval gate sees the raw args first.
        executor = self.executor()
        question = executor.approval_question(
            "download_file",
            {"url": "https://example.com/a.zip", "path": "/etc/cron.d/evil"},
        )
        self.assertIsNotNone(question)
        self.assertIn("workspace", question)

    def test_raw_capability_tools_stay_destructive(self):
        # Unscoped network power (any method, full browser) is still
        # Destructive: worst capability wins.
        executor = self.executor()
        for name in ("http_request", "browser", "run_shell", "execute_code"):
            with self.subTest(tool=name):
                question = executor.approval_question(name, {"command": "x"})
                self.assertIsNotNone(question)
                self.assertIn("destructive", question)


class NonNativeDefaultDenyTests(RiskTestBase):
    """Verdict 5: unclassified MCP/custom/OpenAPI tools default to Destructive.

    Zeline cannot audit what an external tool really does, so anything not
    explicitly trusted in the config file fails closed. Trust comes only
    from a per-server ``trust.risk_cap`` in the config file — never from chat.
    """

    def setUp(self):
        super().setUp()
        self.mcp_mod = importlib.import_module("zeline.mcp")

    def _executor_with_mcp(self, servers):
        executor = self.executor()
        executor.mcp = self.mcp_mod.MCPRegistry(servers=servers)
        return executor

    def _server(self, name, risk_cap=None):
        return self.mcp_mod.MCPServer(
            name=name, transport="stdio", command="true", risk_cap=risk_cap
        )

    def test_unclassified_mcp_tool_asks_as_destructive(self):
        executor = self._executor_with_mcp([self._server("docs")])
        question = executor.approval_question("mcp__docs__search", {"q": "x"})
        self.assertIsNotNone(question)
        self.assertIn("destructive", question)
        self.assertIn("default-deny", question)

    def test_mcp_tool_on_unknown_server_is_not_gated(self):
        # Nothing registered under that server: the dispatcher reports the
        # error, approval has nothing to decide.
        executor = self._executor_with_mcp([self._server("docs")])
        self.assertIsNone(executor.approval_question("mcp__ghost__search", {}))

    def test_malformed_mcp_name_is_not_gated(self):
        executor = self._executor_with_mcp([self._server("docs")])
        self.assertIsNone(executor.approval_question("mcp__", {}))
        self.assertIsNone(executor.approval_question("mcp__docs", {}))

    def test_trusted_cap_lowers_the_class(self):
        executor = self._executor_with_mcp([self._server("docs", risk_cap="read")])
        self.assertIsNone(executor.approval_question("mcp__docs__search", {}))

    def test_trusted_cap_network_still_asks(self):
        executor = self._executor_with_mcp([self._server("hooks", risk_cap="network")])
        question = executor.approval_question("mcp__hooks__notify", {})
        self.assertIsNotNone(question)
        self.assertIn("network", question)
        self.assertIn("mutating", question)

    def test_invalid_cap_fails_closed(self):
        # A typo in the config must never silently widen permissions.
        executor = self._executor_with_mcp([self._server("docs", risk_cap="banana")])
        question = executor.approval_question("mcp__docs__search", {})
        self.assertIsNotNone(question)
        self.assertIn("destructive", question)

    def test_cap_is_per_server_not_per_tool(self):
        # Trust is a statement about the server. A server update that adds
        # (or changes) a tool does not escape the cap — and a cap of "read"
        # covers tools the operator never audited individually. This is the
        # documented sharp edge of the mechanism.
        executor = self._executor_with_mcp([self._server("docs", risk_cap="read")])
        self.assertIsNone(executor.approval_question("mcp__docs__brand_new_tool", {}))

    def test_cap_does_not_leak_to_other_servers(self):
        executor = self._executor_with_mcp(
            [self._server("docs", risk_cap="read"), self._server("other")]
        )
        self.assertIsNone(executor.approval_question("mcp__docs__search", {}))
        question = executor.approval_question("mcp__other__search", {})
        self.assertIsNotNone(question)
        self.assertIn("destructive", question)

    def test_config_file_parsing_accepts_trust_section(self):
        registry = self.mcp_mod.MCPRegistry.from_config(
            {"mcp": {"servers": {"docs": {
                "transport": "stdio",
                "command": "true",
                "trust": {"risk_cap": "Read"},
            }}}}
        )
        self.assertEqual(registry.risk_cap_for("docs"), "read")
        self.assertIsNone(registry.risk_cap_for("nope"))

    def test_config_without_trust_section_stays_destructive(self):
        registry = self.mcp_mod.MCPRegistry.from_config(
            {"mcp": {"servers": {"docs": {"transport": "stdio", "command": "true"}}}}
        )
        self.assertIsNone(registry.risk_cap_for("docs"))
        executor = self.executor()
        executor.mcp = registry
        question = executor.approval_question("mcp__docs__search", {})
        self.assertIsNotNone(question)
        self.assertIn("destructive", question)

    def test_custom_tool_defaults_to_destructive(self):
        executor = self.executor()
        executor.custom = mock.Mock()
        executor.custom.has_tool.side_effect = lambda n: n == "custom_foo"
        question = executor.approval_question("custom_foo", {})
        self.assertIsNotNone(question)
        self.assertIn("destructive", question)
        self.assertIsNone(executor.approval_question("custom_bar", {}))

    def test_openapi_tool_defaults_to_destructive(self):
        executor = self.executor()
        executor.openapi = mock.Mock()
        executor.openapi.has_tool.side_effect = lambda n: n == "api_foo"
        question = executor.approval_question("api_foo", {})
        self.assertIsNotNone(question)
        self.assertIn("destructive", question)
        self.assertIsNone(executor.approval_question("api_bar", {}))


class FullDetailTests(RiskTestBase):
    """Verdict 6: the picker may summarize, but the full text stays accessible.

    A truncated approval is a blind approval. ``interaction.ask`` keeps the
    untruncated text on the entry; renderers deliver it as a code block BEFORE
    the picker so the operator inspects the real command before deciding.
    """

    def _captured_entry(self, question, options=("Allow", "Deny")):
        captured = {}

        def renderer(entry):
            captured["entry"] = entry
            return "Deny"

        self.interaction.register_channel("test:detail", renderer)
        try:
            self.interaction.ask("test:detail", question, options)
        finally:
            self.interaction.unregister_channel("test:detail")
        return captured["entry"]

    def test_short_question_has_no_full_text(self):
        entry = self._captured_entry("Run this?")
        self.assertEqual(entry.question, "Run this?")
        self.assertEqual(entry.full_text, "")

    def test_boundary_exactly_500_chars_is_not_truncated(self):
        entry = self._captured_entry("x" * 500)
        self.assertEqual(len(entry.question), 500)
        self.assertEqual(entry.full_text, "")

    def test_501_chars_truncates_with_ellipsis_and_keeps_full_text(self):
        full = "y" * 501
        entry = self._captured_entry(full)
        self.assertEqual(len(entry.question), 500)
        self.assertTrue(entry.question.endswith("…"))
        self.assertEqual(entry.full_text, full)

    def test_detail_chunks_preserve_content(self):
        text = "z" * (self.interaction.MAX_DETAIL_CHARS + 1)
        chunks = self.interaction.detail_chunks(text)
        self.assertEqual(len(chunks), 2)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(c) <= self.interaction.MAX_DETAIL_CHARS for c in chunks))

    def test_approval_question_carries_the_full_command(self):
        # The whole point: a 900-char command must reach the approver whole,
        # not cut at the old 300-char summary budget.
        executor = self.executor()
        command = "deploy --target prod --payload " + "A" * 900
        question = executor.approval_question("run_shell", {"command": command})
        self.assertIsNotNone(question)
        self.assertIn(command, question)

    def test_long_approval_delivers_full_command_before_picker(self):
        executor = self.executor()
        command = "echo " + "B" * 900
        question = executor.approval_question("run_shell", {"command": command})
        entry = self._captured_entry(question, ("Allow", "Deny"))
        # Picker stays short...
        self.assertLessEqual(len(entry.question), 500)
        self.assertTrue(entry.question.endswith("…"))
        # ...but the full command survived intact for the code block.
        self.assertIn(command, entry.full_text)

    def test_telegram_renderer_sends_code_block_first(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        entry = self.interaction.PendingQuestion(
            identity="test:tg",
            question="Allow tool 'run_shell'?…",
            options=("Allow", "Deny"),
            full_text="echo " + "C" * 900,
        )
        calls = []
        with mock.patch.object(telegram, "_api_call", side_effect=lambda *a, **k: calls.append((a, k))):
            telegram._render_ask_question("api", 123, entry)
        self.assertEqual(len(calls), 2)
        detail_text = calls[0][1]["text"]
        self.assertIn("<pre>", detail_text)
        self.assertIn("C" * 900, detail_text)
        self.assertEqual(calls[0][1].get("parse_mode"), "HTML")
        picker_text = calls[1][1]["text"]
        self.assertTrue(picker_text.startswith("❓"))
        self.assertIn("Allow", str(calls[1][1].get("reply_markup")))

    def test_telegram_renderer_skips_detail_when_nothing_truncated(self):
        telegram = importlib.import_module("zeline.gateways.telegram")
        entry = self.interaction.PendingQuestion(
            identity="test:tg", question="Run this?", options=("Allow", "Deny")
        )
        calls = []
        with mock.patch.object(telegram, "_api_call", side_effect=lambda *a, **k: calls.append((a, k))):
            telegram._render_ask_question("api", 123, entry)
        self.assertEqual(len(calls), 1)

    def test_html_in_command_cannot_break_out_of_code_block(self):
        # The full command is operator-supplied-model-controlled text; it must
        # not be able to inject markup into the Telegram message.
        telegram = importlib.import_module("zeline.gateways.telegram")
        entry = self.interaction.PendingQuestion(
            identity="test:tg",
            question="q…",
            options=("Allow", "Deny"),
            full_text="echo </pre><b>pwn</b>",
        )
        calls = []
        with mock.patch.object(telegram, "_api_call", side_effect=lambda *a, **k: calls.append((a, k))):
            telegram._render_ask_question("api", 123, entry)
        detail_text = calls[0][1]["text"]
        self.assertNotIn("</pre><b>", detail_text)
        self.assertIn("&lt;/pre&gt;", detail_text)


class LegacyProfileTests(RiskTestBase):
    def test_safe_profile_still_blocks_shell_like_before(self):
        executor = self.executor(profile="safe")
        result = executor.run("run_shell", {"command": "echo hi"})
        self.assertTrue(result.startswith("ERROR"))
        self.assertIn("not allowed for profile 'safe'", result)

    def test_safe_profile_still_blocks_write_tools_like_before(self):
        executor = self.executor(profile="safe")
        result = executor.run("write_file", {"path": "x.txt", "content": "x"})
        self.assertIn("not allowed for profile 'safe'", result)

    def test_workspace_profile_still_allows_workspace_writes(self):
        executor = self.executor(profile="workspace")
        result = executor.run("write_file", {"path": "note.txt", "content": "hello"})
        self.assertNotIn("ERROR", result)
        self.assertEqual((self.workspace / "note.txt").read_text(), "hello")

    def test_unknown_profile_still_rejected(self):
        with self.assertRaises(ValueError):
            self.executor(profile="nonsense")


class FakeProviderResponse:
    def __init__(self, payload, status_code=200):
        import json

        self.text = json.dumps(payload)
        self.status_code = status_code
        self.ok = status_code < 400
        self.encoding = "utf-8"


class ApprovalEnforcementTests(RiskTestBase):
    """End-to-end through the real agent loop with a mocked provider.

    A Destructive tool call must surface an approval question via the ask_user
    seam, hold execution until the operator answers, run on Allow, and stay
    blocked on Deny.
    """

    def _agent_with_shell_call(self, identity):
        agent = self.agent_module.Zeline(
            identity=identity, tool_profile="full", workspace=str(self.workspace)
        )
        first = {
            "choices": [{"message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-sh-1",
                    "type": "function",
                    "function": {
                        "name": "run_shell",
                        "arguments": '{"command": "touch APPROVAL_MARKER_XYZ"}',
                    },
                }],
            }}],
        }
        final = {"choices": [{"message": {"role": "assistant", "content": "done."}}]}
        post = mock.patch.object(
            self.agent_module.requests,
            "post",
            side_effect=[
                FakeProviderResponse(first),
                FakeProviderResponse(final),
                FakeProviderResponse(final),
            ],
        )
        return agent, post

    def _register_answerer(self, identity, answer, record):
        asked = threading.Event()

        def renderer(entry):
            record["question"] = entry.question
            record["options"] = tuple(entry.options)
            asked.set()
            return answer

        self.interaction.register_channel(identity, renderer)
        return asked

    def test_deny_blocks_execution_and_reports_it(self):
        identity = "test:risk-deny"
        agent, post = self._agent_with_shell_call(identity)
        record = {}
        self._register_answerer(identity, "Deny", record)
        marker = self.workspace / "APPROVAL_MARKER_XYZ"
        with post:
            reply = agent.send("run the shell command")
        self.assertEqual(reply, "done.")
        # The approval question went through the ask_user seam...
        self.assertIn("run_shell", record["question"])
        self.assertIn("destructive", record["question"])
        self.assertEqual(record["options"], ("Allow once", "Allow sesi ini", "Deny"))
        # ...and the tool never executed.
        self.assertFalse(marker.exists(), "denied tool must not execute")
        tool_messages = [m for m in agent.messages if m.get("role") == "tool"]
        self.assertTrue(tool_messages)
        self.assertIn("not approved", tool_messages[-1]["content"])

    def test_allow_runs_the_tool(self):
        identity = "test:risk-allow"
        agent, post = self._agent_with_shell_call(identity)
        record = {}
        self._register_answerer(identity, "Allow once", record)
        marker = self.workspace / "APPROVAL_MARKER_XYZ"
        with post:
            agent.send("run the shell command")
        self.assertTrue(marker.exists(), "allowed tool must execute")

    def test_execution_is_held_until_the_operator_answers(self):
        identity = "test:risk-held"
        agent, post = self._agent_with_shell_call(identity)
        marker = self.workspace / "APPROVAL_MARKER_XYZ"
        asked = threading.Event()
        release = threading.Event()
        record = {}

        def renderer(entry):
            record["question"] = entry.question
            asked.set()
            self.assertTrue(release.wait(timeout=30), "test did not release the answer")
            return "Deny"

        self.interaction.register_channel(identity, renderer)
        result = {}
        worker = threading.Thread(
            target=lambda: result.update(reply=agent.send("run the shell command")),
            daemon=True,
        )
        with post:
            worker.start()
            self.assertTrue(asked.wait(timeout=30), "approval question was never asked")
            # The question is asked and the tool has NOT run: execution held.
            self.assertFalse(marker.exists())
            self.assertIn("run_shell", record["question"])
            release.set()
            worker.join(timeout=30)
        self.assertFalse(worker.is_alive(), "agent turn did not finish after the answer")
        self.assertFalse(marker.exists(), "tool ran before/without approval")


if __name__ == "__main__":
    unittest.main()
