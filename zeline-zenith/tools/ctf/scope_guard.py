#!/usr/bin/env python3
"""
scope_guard.py — Authorization scope enforcement for the CTF agent.

The whitehat line: the agent only ever touches targets that are explicitly on
the authorized allowlist for THIS competition. Anything else is refused, hard.

Usage:
    from scope_guard import assert_in_scope, load_scope
    assert_in_scope("https://chal.ctf.example/01")   # raises ScopeError if not

Allowlist is read from scope.json in the workdir (or path passed in). Format:

    {
      "competition": "Example CTF 2026",
      "allow_hosts": ["chal.ctf.example", "*.ctf.example"],
      "allow_cidrs": ["10.13.37.0/24"],
      "deny_hosts": ["admin.ctf.example"]
    }

Wildcards are matched on the host label (left-most). deny_hosts always wins.
"""
from __future__ import annotations

import fnmatch
import ipaddress
import json
import socket
import sys
from urllib.parse import urlparse


class ScopeError(Exception):
    """Raised when a target is outside the authorized scope."""


def load_scope(path: str = "scope.json") -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _host_from_target(target: str) -> str:
    if "://" in target:
        return urlparse(target).hostname or ""
    # bare host or host:port
    return target.split("/")[0].split(":")[0]


def _resolve_ips(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
        return sorted({i[4][0] for i in infos})
    except socket.gaierror:
        return []


def is_in_scope(target: str, scope: dict) -> tuple[bool, str]:
    host = _host_from_target(target)
    if not host:
        return False, "could not parse host from target"

    # deny wins
    for pat in scope.get("deny_hosts", []):
        if fnmatch.fnmatch(host, pat):
            return False, f"host matches deny rule '{pat}'"

    # explicit host allow
    for pat in scope.get("allow_hosts", []):
        if fnmatch.fnmatch(host, pat):
            return True, f"host matches allow rule '{pat}'"

    # CIDR allow (resolve host to IPs)
    cidrs = scope.get("allow_cidrs", [])
    if cidrs:
        # If host is already an IP literal, use it directly (no DNS needed).
        if host.replace(".", "").isdigit() or ":" in host:
            ips = [host]
        else:
            ips = _resolve_ips(host)
        for ip in ips:
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                continue
            for cidr in cidrs:
                if addr in ipaddress.ip_network(cidr, strict=False):
                    return True, f"{ip} in allowed CIDR '{cidr}'"

    return False, "target not on allowlist"


def assert_in_scope(target: str, scope_path: str = "scope.json") -> None:
    scope = load_scope(scope_path)
    ok, reason = is_in_scope(target, scope)
    if not ok:
        raise ScopeError(
            f"OUT OF SCOPE: {target} ({reason}). "
            f"Refusing. Competition='{scope.get('competition', '?')}'."
        )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: scope_guard.py <target> [scope.json]")
        sys.exit(2)
    tgt = sys.argv[1]
    sp = sys.argv[2] if len(sys.argv) > 2 else "scope.json"
    try:
        assert_in_scope(tgt, sp)
        print(f"[+] IN SCOPE: {tgt}")
    except (ScopeError, FileNotFoundError) as exc:
        print(f"[-] {exc}")
        sys.exit(1)
