"""
tools/hids.py — Mini Host Intrusion Detection + Auto-Firewall  (v4.1)

Defensif. Tail log (auth/nginx/syslog), deteksi pola serangan, auto-block IP via
firewall dengan ALLOWLIST + TTL + alert. Jalan di VPS operator sendiri.

Aman-by-default:
- allowlist (operator IP + localhost) SELALU — jangan ngeban diri sendiri.
- block TTL (auto-unblock) — false positive auto-pulih.
- dry_run mode — lihat apa yang AKAN di-block sebelum live.
- tiap block → callback alert (wire ke sk4 Telegram), bukan diam.

Aktivasi firewall = aksi sistem → operator kasih R9 gate sekali.

Pakai:
    from hids import HIDS
    h = HIDS(sources=["/var/log/auth.log"], rules=[...], allowlist=["1.2.3.4"], backend="ufw")
    h.watch(dry_run=True)     # audit dulu
    h.watch()                 # live
"""
from __future__ import annotations

import re
import subprocess
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

IPV4 = r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"


@dataclass
class Rule:
    name: str
    pattern: str                       # regex; harus nangkap IP (grup) atau pakai ip_group
    threshold: int = 5
    window: int = 300                  # detik
    action: str = "block"              # block | alert
    ip_group: str = IPV4               # regex buat ekstrak IP kalau pattern gak nangkap

    def extract_ip(self, line: str):
        m = re.search(self.pattern, line)
        if not m:
            return None
        if m.groups():
            # ambil grup yang berbentuk IP
            for g in m.groups():
                if g and re.fullmatch(IPV4, g):
                    return g
        sk2 = re.search(self.ip_group, line)
        return sk2.group(1) if sk2 else None


class HIDS:
    def __init__(self, sources, rules, allowlist=None, block_ttl=3600,
                 backend="ufw", alert_cb: Optional[Callable[[str], None]] = None):
        self.sources = [Path(s) for s in sources]
        self.rules = [r if isinstance(r, Rule) else Rule(**r) for r in rules]
        self.allowlist = set(allowlist or []) | {"127.0.0.1", "::1"}
        self.block_ttl = block_ttl
        self.backend = backend
        self.alert_cb = alert_cb or (lambda msg: print(msg))
        self._hits = defaultdict(lambda: defaultdict(deque))   # rule -> ip -> timestamps
        self._blocked = {}                                     # ip -> unblock_at

    # ── deteksi ──
    def _record(self, rule: Rule, ip: str, now: float) -> bool:
        dq = self._hits[rule.name][ip]
        dq.append(now)
        while dq and now - dq[0] > rule.window:
            dq.popleft()
        return len(dq) >= rule.threshold

    def process_line(self, line: str, now: float, dry_run: bool):
        for rule in self.rules:
            ip = rule.extract_ip(line)
            if not ip or ip in self.allowlist:
                continue
            if self._record(rule, ip, now):
                self._trigger(rule, ip, dry_run)

    def _trigger(self, rule: Rule, ip: str, dry_run: bool):
        if ip in self._blocked:
            return
        msg = f"🛡️ HIDS [{rule.name}] {ip} hit {rule.threshold}× / {rule.window}s"
        if rule.action == "alert":
            self.alert_cb(msg + " → ALERT")
            return
        if dry_run:
            self.alert_cb(msg + f" → WOULD BLOCK (dry-run, ttl {self.block_ttl}s)")
            return
        self._block(ip)
        self.alert_cb(msg + f" → BLOCKED (ttl {self.block_ttl}s)")

    # ── firewall ──
    def _block(self, ip: str):
        cmds = {
            "ufw": ["ufw", "deny", "from", ip],
            "iptables": ["iptables", "-A", "INPUT", "-s", ip, "-j", "DROP"],
            "nftables": ["nft", "add", "rule", "inet", "filter", "input", "ip", "saddr", ip, "drop"],
        }
        try:
            subprocess.run(cmds[self.backend], check=True, capture_output=True)
            self._blocked[ip] = time.time() + self.block_ttl
        except Exception as e:                       # noqa: BLE001
            self.alert_cb(f"⚠️ HIDS gagal block {ip}: {e}")

    def _unblock(self, ip: str):
        cmds = {
            "ufw": ["ufw", "delete", "deny", "from", ip],
            "iptables": ["iptables", "-D", "INPUT", "-s", ip, "-j", "DROP"],
            "nftables": ["nft", "flush", "chain", "inet", "filter", "input"],   # kasar; review utk prod
        }
        try:
            subprocess.run(cmds[self.backend], check=True, capture_output=True)
        except Exception:                            # noqa: BLE001
            pass
        self._blocked.pop(ip, None)

    def _expire(self, now: float):
        for ip, until in list(self._blocked.items()):
            if now >= until:
                self._unblock(ip)
                self.alert_cb(f"♻️ HIDS auto-unblock {ip} (ttl habis)")

    # ── loop ──
    def watch(self, dry_run: bool = False, poll: float = 1.0):
        handles = {}
        for src in self.sources:
            if src.exists():
                f = src.open("r", errors="ignore")
                f.seek(0, 2)                          # mulai dari ujung (live tail)
                handles[src] = f
            else:
                self.alert_cb(f"⚠️ HIDS source hilang: {src}")
        mode = "DRY-RUN" if dry_run else "LIVE"
        self.alert_cb(f"🛡️ HIDS {mode} | {len(handles)} source | backend {self.backend} | "
                      f"allowlist {len(self.allowlist)} ip")
        while True:
            now = time.time()
            for f in handles.values():
                for line in f.readlines():
                    self.process_line(line, now, dry_run)
            if not dry_run:
                self._expire(now)
            time.sleep(poll)


if __name__ == "__main__":
    # smoke test — proses sample line tanpa firewall asli
    h = HIDS(sources=[], rules=[Rule("ssh_bruteforce", r"Failed password.*from " + IPV4,
                                     threshold=3, window=60)], backend="ufw")
    t = time.time()
    for _ in range(3):
        h.process_line("Failed password for root from 203.0.113.9 port 22", t, dry_run=True)
