"""Run the real chat REPL against a stubbed turn (no network, no model)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zeline import cli  # noqa: E402


def fake_send(self, identity, text, tool_profile, on_tool=None, **_kwargs):
    if on_tool:
        on_tool("run_shell", {"command": "export TOKEN=sup3rsecret"})
    q = cli.interaction.ask("cli:local", "Pilih mode deploy?", ["Staging", "Production"])
    return f"Dipilih: {q}\n\n```bash\nzeline doctor\n```"


with mock.patch.object(cli.config, "GATEWAY_SETUP_COMPLETE", True), \
     mock.patch.object(cli.config, "SETUP_COMPLETE", True), \
     mock.patch.object(cli.config, "PROVIDER", {"model_verified": True}), \
     mock.patch.object(cli.config, "API_KEY", "test-only"), \
     mock.patch.object(cli.SessionStore, "send", fake_send), \
     mock.patch.object(cli, "_run_reflection", lambda *_a, **_k: None):
    raise SystemExit(cli.cmd_chat())
