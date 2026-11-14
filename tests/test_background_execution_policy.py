"""Offline prompt contracts; imports run with disposable HOME/state and no secrets."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BackgroundExecutionPolicyTests(unittest.TestCase):
    def test_execution_guidance_matches_available_background_controls(self):
        script = r'''
import socket

def blocked(*args, **kwargs):
    raise AssertionError("network is forbidden in policy tests")

socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
from zeline import config, tools

defs = {tool.name: tool for tool in tools.TOOL_DEFS}
# Verify the actual public schema, not an imagined process API.
assert set(defs["process_control"].parameters["properties"]["action"]["enum"]) == {"list", "poll", "log", "kill"}
assert "notify_on_complete" not in defs["run_shell"].parameters["properties"]
assert "action must be one of list, poll, log, kill" in tools._process_control("wait")

for name, guidance in (
    ("system prompt", config.SYSTEM_PROMPT_TEMPLATE),
    ("run_shell", defs["run_shell"].description),
    ("process_control", defs["process_control"].description),
):
    text = guidance.lower()
    assert "repeated shell sleep + log checks" in text, name + ": missing anti-loop guidance"
    assert "bounded polling" in text, name + ": missing bounded fallback"
    assert "no completion notifications or blocking wait" in text, name + ": invents background support"
assert "completion notifications or a real process wait when available" in config.SYSTEM_PROMPT_TEMPLATE
assert "legitimate sleep inside scripts is allowed" in config.SYSTEM_PROMPT_TEMPLATE.lower()
assert "timeout" in defs["run_shell"].description
print("execution guidance and real background-control schema verified")
'''
        with tempfile.TemporaryDirectory(prefix="zeline-policy-") as temporary:
            home = Path(temporary)
            env = {
                key: os.environ[key]
                for key in ("PATH", "LD_LIBRARY_PATH", "LD_PRELOAD", "TMPDIR", "PREFIX", "SYSTEMROOT")
                if key in os.environ
            }
            env.update(
                HOME=str(home), ZELINE_HOME=str(home / "state"),
                USERPROFILE=str(home),
                PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1",
                PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
            )
            result = subprocess.run(
                [sys.executable, "-B", "-c", script], cwd=temporary,
                env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
