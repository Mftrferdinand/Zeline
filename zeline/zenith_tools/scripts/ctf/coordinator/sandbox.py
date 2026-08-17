"""sandbox.py — per-challenge isolated Docker container; exec shell commands."""
from __future__ import annotations

import shlex
import subprocess
import uuid


class Sandbox:
    """
    Wraps a long-lived Docker container for one challenge. The agent issues
    shell commands that run inside it. The challenge workdir is mounted at /work.

    Network policy:
      - network='none'  -> offline (rev/crypto/forensics/local-pwn). Default.
      - network='bridge'-> only when a remote target is in play; pair with the
        orchestrator scope guard so the agent can't reach off-scope hosts.
    """

    def __init__(self, image: str, host_workdir: str, network: str = "none"):
        self.image = image
        self.host_workdir = host_workdir
        self.network = network
        self.name = f"ctf-{uuid.uuid4().hex[:10]}"
        self._started = False

    def start(self):
        cmd = [
            "docker", "run", "-d", "--rm",
            "--name", self.name,
            "--network", self.network,
            "--memory", "4g", "--cpus", "2",
            "--pids-limit", "512",
            "-v", f"{self.host_workdir}:/work",
            "-w", "/work",
            self.image, "sleep", "infinity",
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        self._started = True

    def run(self, command: str, timeout: int = 120) -> dict:
        """Execute a shell command inside the container. Returns stdout/stderr/rc."""
        if not self._started:
            self.start()
        try:
            p = subprocess.run(
                ["docker", "exec", self.name, "bash", "-lc", command],
                capture_output=True, text=True, timeout=timeout,
            )
            out, err, rc = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired:
            out, err, rc = "", f"[timeout after {timeout}s]", 124
        # truncate to keep model context sane
        return {
            "stdout": out[-12000:],
            "stderr": err[-4000:],
            "returncode": rc,
        }

    def stop(self):
        if self._started:
            subprocess.run(["docker", "rm", "-f", self.name],
                           capture_output=True, text=True)
            self._started = False

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
