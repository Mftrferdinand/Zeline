"""Execution backends: local, docker, ssh, sandbox.

Abstracts where shell commands run: locally, in Docker, via SSH, or in a sandbox.
Configure via ``execution.backend`` in config:
- "local" (default): subprocess on this machine
- "docker": run in a Docker container (image configurable)
- "ssh": run on a remote host via SSH

Backends: local, docker, ssh, sandbox.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ExecResult:
    exit_code: int
    output: str
    timed_out: bool = False


class Backend(ABC):
    @abstractmethod
    def run(self, command: str | list[str], cwd: str | None,
            timeout: int, shell: bool = True) -> ExecResult:
        ...


class LocalBackend(Backend):
    """Direct subprocess execution (current Zeline behavior)."""

    def run(self, command, cwd, timeout, shell=True) -> ExecResult:
        try:
            proc = subprocess.Popen(
                command, shell=shell, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env={**os.environ},
                start_new_session=True,
            )
            try:
                out, _ = proc.communicate(timeout=timeout)
                return ExecResult(int(proc.returncode or 0), out or "")
            except subprocess.TimeoutExpired:
                # S8 fix: kill entire process group (was leaving orphan grandchildren)
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                try:
                    out, _ = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    out = ""
                return ExecResult(-1, out or "", timed_out=True)
        except Exception as exc:
            return ExecResult(-1, f"ERROR: {exc}")


class DockerBackend(Backend):
    """Run commands inside a Docker container."""

    def __init__(self, image: str = "python:3.12-slim",
                 workdir: str = "/work"):
        self.image = image
        self.workdir = workdir

    def run(self, command, cwd, timeout, shell=True) -> ExecResult:
        if isinstance(command, list):
            command = " ".join(shlex.quote(c) for c in command)
        docker_cmd = [
            "docker", "run", "--rm",
            "-w", self.workdir,
        ]
        # Mount workspace for file access
        # S7 fix: validate cwd (absolute, exists, no colon)
        if cwd:
            cwd = os.path.abspath(cwd)
            if ":" in cwd:
                return ExecResult(-1, "ERROR: cwd contains ':' (breaks docker -v)")
            if not os.path.isdir(cwd):
                return ExecResult(-1, f"ERROR: cwd does not exist: {cwd}")
            docker_cmd += ["-v", f"{cwd}:{self.workdir}:rw"]
        docker_cmd += [
            self.image,
            "sh", "-c", command,
        ]
        try:
            proc = subprocess.run(
                docker_cmd, capture_output=True, text=True, timeout=timeout,
            )
            return ExecResult(proc.returncode, proc.stdout + proc.stderr)
        except subprocess.TimeoutExpired:
            return ExecResult(-1, "timed out", timed_out=True)
        except FileNotFoundError:
            return ExecResult(-1, "ERROR: docker not found")
        except Exception as exc:
            return ExecResult(-1, f"ERROR: {exc}")


class SSHBackend(Backend):
    """Run commands on a remote host via SSH."""

    def __init__(self, host: str, user: str | None = None,
                 key_path: str | None = None, port: int = 22,
                 remote_workdir: str | None = None):
        self.host = host
        self.user = user
        self.key_path = key_path
        self.port = port
        # S4 fix: expand ~ in Python (shlex.quote prevents shell expansion)
        raw = remote_workdir or "~/zeline-work"
        self.remote_workdir = os.path.expanduser(raw)

    def _target(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    def run(self, command, cwd, timeout, shell=True) -> ExecResult:
        if isinstance(command, list):
            command = " ".join(shlex.quote(c) for c in command)
        # Use remote_workdir, not local cwd (fix: local path doesn't exist remotely)
        workdir = self.remote_workdir
        if workdir:
            command = f"mkdir -p {shlex.quote(workdir)} && cd {shlex.quote(workdir)} && {command}"
        ssh_cmd = ["ssh", "-p", str(self.port), "-o", "BatchMode=yes"]
        if self.key_path:
            ssh_cmd += ["-i", self.key_path]
        ssh_cmd += [self._target(), command]
        try:
            proc = subprocess.run(
                ssh_cmd, capture_output=True, text=True, timeout=timeout,
            )
            return ExecResult(proc.returncode, proc.stdout + proc.stderr)
        except subprocess.TimeoutExpired:
            return ExecResult(-1, "timed out", timed_out=True)
        except Exception as exc:
            return ExecResult(-1, f"ERROR: {exc}")


def get_backend() -> Backend:
    """Return the configured backend (defaults to local)."""
    try:
        from zeline import config
        backend_name = str(
            getattr(config, "EXECUTION_BACKEND", "local")).lower()
        # S3 fix: validate against BACKENDS, raise on unknown (was silent fail-open)
        if backend_name not in BACKENDS:
            raise ValueError(
                f"Unknown execution backend: {backend_name!r}. "
                f"Valid: {sorted(BACKENDS)}"
            )
        if backend_name == "docker":
            image = str(getattr(config, "DOCKER_IMAGE", "python:3.12-slim"))
            return DockerBackend(image=image)
        if backend_name == "ssh":
            return SSHBackend(
                host=str(getattr(config, "SSH_HOST", "")),
                user=getattr(config, "SSH_USER", None) or None,
                key_path=getattr(config, "SSH_KEY", None) or None,
                port=int(getattr(config, "SSH_PORT", 22)),
                remote_workdir=str(getattr(config, "SSH_WORKDIR", "")) or None,
            )
        if backend_name == "sandbox":
            return SandboxBackend()
    except (ImportError, AttributeError) as exc:
        # Config import failed - log loudly, don't silent fallback
        import sys
        print(f"WARNING: backend config failed ({exc}), using local",
              file=sys.stderr)
    return LocalBackend()


class SandboxBackend(Backend):
    """Lightweight sandbox using bubblewrap (bwrap).

    Restricts filesystem access: system dirs read-only, only workspace writable.
    No network access (by design - documented).

    Requires bwrap installed. Raises RuntimeError at construction if missing.
    """

    def __init__(self):
        import shutil
        if not shutil.which("bwrap"):
            raise ValueError(
                "bwrap not installed. Install bubblewrap to use the sandbox backend."
            )

    def run(self, command, cwd, timeout, shell=True) -> ExecResult:
        if isinstance(command, list):
            command = " ".join(shlex.quote(c) for c in command)
        # S7 fix: validate cwd
        if cwd:
            cwd = os.path.abspath(cwd)
            # BUG 1 fix: reject / (would bind entire host FS rw)
            if cwd == "/":
                return ExecResult(-1, "ERROR: cwd=/ not allowed in sandbox")
            if not os.path.isdir(cwd):
                return ExecResult(-1, f"ERROR: cwd does not exist: {cwd}")
        bwrap_cmd = [
            "bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            # S1 fix: read-only binds for system dirs (was rw - sandbox escape!)
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            # S6: minimal /etc for basic functionality (passwd, DNS, SSL)
            # BUG 2 fix: create /etc dir before file binds (bwrap needs dest to exist)
            "--dir", "/etc",
            "--ro-bind", "/etc/passwd", "/etc/passwd",
            "--ro-bind", "/etc/group", "/etc/group",
            "--ro-bind", "/etc/nsswitch.conf", "/etc/nsswitch.conf",
            "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
            "--ro-bind", "/etc/ssl", "/etc/ssl",
            "--tmpfs", "/tmp",
            "--proc", "/proc",
            "--dev", "/dev",
        ]
        if cwd:
            # S2 fix: create parent dirs in namespace before bind
            parent = os.path.dirname(cwd)
            # Build --dir chain for each missing parent level
            parts = []
            p = cwd
            while p and p != "/":
                parts.append(p)
                p = os.path.dirname(p)
            for d in reversed(parts):
                # Only create dirs that don't exist as system paths
                if not d.startswith(("/usr", "/bin", "/lib", "/etc", "/proc", "/dev", "/tmp")):
                    bwrap_cmd += ["--dir", d]
            bwrap_cmd += ["--bind", cwd, cwd, "--chdir", cwd]
        bwrap_cmd += ["sh", "-c", command]
        try:
            # S5 fix: use process group for proper cleanup
            proc = subprocess.Popen(
                bwrap_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, start_new_session=True,
            )
            try:
                out, _ = proc.communicate(timeout=timeout)
                return ExecResult(proc.returncode or 0, out or "")
            except subprocess.TimeoutExpired:
                # S5/S8 fix: kill entire process group
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                proc.wait()
                return ExecResult(-1, "timed out", timed_out=True)
        except Exception as exc:
            return ExecResult(-1, f"ERROR: {exc}")


# Valid backend names
BACKENDS = frozenset({"local", "docker", "ssh", "sandbox"})
