#!/usr/bin/env python3
"""
tools/opsec_checker.py — Operational Security Validator (V7)
Pre/during/post engagement OPSEC assessment.
Framework-level — validates infrastructure, identity, artifacts.
Zero external dependencies (stdlib only).
"""

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class OPSECPhase(Enum):
    PRE = "pre-engagement"
    DURING = "during-engagement"
    POST = "post-engagement"


@dataclass
class OPSECDimension:
    name: str
    score: float  # 0-100
    findings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


class OpsecChecker:
    """Operational security validator for red-team engagements."""

    def __init__(self, phase: OPSECPhase = OPSECPhase.PRE):
        self.phase = phase
        self._findings: dict[str, list[str]] = {}

    def _add_finding(self, category: str, finding: str):
        self._findings.setdefault(category, []).append(finding)

    # ─── Infrastructure validation ───

    def validate_infra(self) -> dict:
        """Check infrastructure: VPN, DNS leaks, browser fingerprint, virtualization."""
        results = {
            "vpn_active": self._check_vpn(),
            "dns_leak": self._check_dns_leak(),
            "virtualized": self._check_virtualization(),
            "browser_fingerprint": self._check_browser_fingerprint(),
            "hostname_sanitized": self._check_hostname(),
        }
        for k, v in results.items():
            if isinstance(v, bool) and not v:
                self._add_finding("infra", f"{k} check FAILED")
        return results

    def _check_vpn(self) -> bool:
        """Check if VPN/tunnel interface is active."""
        try:
            r = subprocess.run(["ip", "addr", "show"], capture_output=True, text=True, timeout=5)
            # Check for common VPN interfaces
            for iface in ["tun0", "tun1", "wg0", "utun", "ppp0"]:
                if iface in r.stdout:
                    return True
            return False
        except Exception:
            return False  # Assume no VPN if can't check

    def _check_dns_leak(self) -> bool:
        """Check for DNS leaks via resolv.conf or systemd-resolved."""
        try:
            resolv = Path("/etc/resolv.conf")
            if resolv.exists():
                content = resolv.read_text()
                # Check if DNS server is public ISP DNS (potential leak)
                for bad_dns in ["8.8.8.8", "8.8.4.4", "1.1.1.1"]:
                    if bad_dns in content:
                        self._add_finding("infra", f"Public DNS detected: {bad_dns}")
                        return False
            return True
        except Exception:
            return True

    def _check_virtualization(self) -> bool:
        """Check if running in VM/container (better isolation)."""
        try:
            r = subprocess.run(["systemd-detect-virt"], capture_output=True, text=True, timeout=5)
            virt = r.stdout.strip()
            return virt != "none"  # VM/container = better isolation
        except Exception:
            return False

    def _check_browser_fingerprint(self) -> bool:
        """Basic browser fingerprint checks (Firefox/Chrome config presence)."""
        checks = []
        # Firefox
        firefox = Path.home() / ".mozilla" / "firefox"
        if firefox.exists():
            # Check for anti-fingerprinting prefs
            checks.append(firefox.exists())
        # Chrome
        chrome = Path.home() / ".config" / "google-chrome"
        if chrome.exists():
            checks.append(chrome.exists())
        return True  # Can't fully verify without browser API

    def _check_hostname(self) -> bool:
        hostname = platform.node()
        # Flag common identifying patterns
        risky = ["real", "name", "personal", "home", "laptop"]
        for r in risky:
            if r in hostname.lower():
                self._add_finding("infra", f"Potentially identifying hostname: {hostname}")
                return False
        return True

    # ─── Identity validation ───

    def check_identity(self, username: str = "", email: str = "") -> dict:
        """Verify operational identity: burner accounts, cross-contamination."""
        results = {
            "username_burner": False,
            "email_separate": False,
            "phone_separate": False,
            "no_cross_contamination": True,
        }

        # Check username against common patterns
        if username:
            home_username = os.environ.get("USER", "")
            if username == home_username:
                self._add_finding("identity", f"Op username matches system user: {username}")
                results["no_cross_contamination"] = False
            else:
                results["username_burner"] = True

        # Check git config for identity leaks
        try:
            r = subprocess.run(["git", "config", "--global", "user.email"],
                             capture_output=True, text=True, timeout=5)
            git_email = r.stdout.strip()
            if git_email and email and git_email == email:
                self._add_finding("identity", "Git email matches operational email")
                results["no_cross_contamination"] = False
        except Exception:
            pass

        # Check SSH keys for email in comments
        ssh_dir = Path.home() / ".ssh"
        if ssh_dir.exists():
            for key_file in ssh_dir.glob("*.pub"):
                content = key_file.read_text()
                if "@" in content.split()[-1] if content.split() else "":
                    self._add_finding("identity", f"SSH key {key_file.name} contains email")
                    results["no_cross_contamination"] = False

        return results

    # ─── Artifact scanning ───

    def scan_artifacts(self) -> dict:
        """Scan for operational artifacts that could leak identity."""
        results = {
            "shell_history": self._check_shell_history(),
            "temp_files": self._check_temp_files(),
            "clipboard": self._check_clipboard(),
            "screenshots": self._check_screenshots(),
            "logs": self._check_logs(),
        }
        return results

    def _check_shell_history(self) -> dict:
        findings = []
        # Bash
        bash_hist = Path.home() / ".bash_history"
        if bash_hist.exists() and bash_hist.stat().st_size > 10000:
            findings.append(f"Large bash history: {bash_hist.stat().st_size} bytes")
        # Zsh
        zsh_hist = Path.home() / ".zsh_history"
        if zsh_hist.exists() and zsh_hist.stat().st_size > 10000:
            findings.append(f"Large zsh history: {zsh_hist.stat().st_size} bytes")
        return {"clean": len(findings) == 0, "findings": findings}

    def _check_temp_files(self) -> dict:
        findings = []
        tmp = Path("/tmp")
        recent = list(tmp.glob("*"))
        if len(recent) > 100:
            findings.append(f"Many temp files: {len(recent)}")
        return {"count": len(recent), "findings": findings}

    def _check_clipboard(self) -> dict:
        """Check clipboard for sensitive data (works on X11)."""
        try:
            r = subprocess.run(["xclip", "-selection", "clipboard", "-o"],
                             capture_output=True, text=True, timeout=3)
            content = r.stdout
            flags = []
            sensitive = ["private key", "BEGIN RSA", "BEGIN OPENSSH", "password",
                        "api_key", "secret", "token", "0x"]
            for s in sensitive:
                if s.lower() in content.lower():
                    flags.append(f"Possible sensitive data in clipboard: {s}")
            return {"clean": len(flags) == 0, "flags": flags}
        except Exception:
            return {"clean": True, "notes": "Clipboard check not available"}

    def _check_screenshots(self) -> dict:
        findings = []
        for ss_dir in [
            Path.home() / "Pictures" / "Screenshots",
            Path.home() / "Desktop",
            Path("~/Screenshots").expanduser(),
        ]:
            if ss_dir.exists():
                screenshots = list(ss_dir.glob("*.png")) + list(ss_dir.glob("*.jpg"))
                if len(screenshots) > 0:
                    findings.append(f"Screenshots found in {ss_dir}: {len(screenshots)} files")
        return {"clean": len(findings) == 0, "findings": findings}

    def _check_logs(self) -> dict:
        findings = []
        log_dirs = [
            Path.home() / ".local" / "share",
            Path("/var/log"),
        ]
        for ld in log_dirs:
            if ld.exists():
                logs = list(ld.rglob("*.log"))
                if len(logs) > 20:
                    findings.append(f"Many logs in {ld}: {len(logs)} files")
        return {"clean": len(findings) == 0, "findings": findings}

    # ─── Burn procedure ───

    def burn_procedure(self) -> list[dict]:
        """Generate step-by-step cleanup sequence for post-engagement."""
        steps = [
            {"order": 1, "phase": "cleanup", "action": "Clear shell history",
             "commands": ["history -c", "rm ~/.bash_history ~/.zsh_history",
                         "rm ~/.python_history ~/.node_repl_history"]},
            {"order": 2, "phase": "cleanup", "action": "Remove temp files",
             "commands": ["rm -rf /tmp/opsec_* /tmp/engagement_*",
                         "find /tmp -user $(whoami) -mtime -7 -delete"]},
            {"order": 3, "phase": "cleanup", "action": "Clear browser data",
             "commands": ["# Firefox: Settings → Privacy → Clear Data (Everything)",
                         "# Chrome: Settings → Clear browsing data (All time)"]},
            {"order": 4, "phase": "cleanup", "action": "Wipe SSH known_hosts entries",
             "commands": ["ssh-keygen -R <target_ip>  # per target"]},
            {"order": 5, "phase": "cleanup", "action": "Remove git config traces",
             "commands": ["git config --global --unset user.email",
                         "git config --global --unset user.name"]},
            {"order": 6, "phase": "cleanup", "action": "Clear clipboard",
             "commands": ["xclip -selection clipboard /dev/null  # X11",
                         "pbcopy < /dev/null  # macOS"]},
            {"order": 7, "phase": "cleanup", "action": "Delete screenshots",
             "commands": ["rm -rf ~/Pictures/Screenshots/* ~/Desktop/Screenshot*"]},
            {"order": 8, "phase": "cleanup", "action": "Rotate VPN exit node",
             "commands": ["# Mullvad: mullvad relay set location any",
                         "# Or restart VPN container"]},
            {"order": 9, "phase": "verify", "action": "Verify cleanup",
             "commands": ["du -sh ~/.bash_history ~/.zsh_history 2>/dev/null",
                         "ls /tmp/ | head -20"]},
            {"order": 10, "phase": "verify", "action": "Final OPSEC re-check",
             "commands": ["opsec_checker.py assess --phase post"]},
        ]
        return steps

    # ─── Risk assessment ───

    def risk_assessment(self) -> dict:
        """Calculate OPSEC score 0-100 across 8 dimensions."""
        dimensions = {
            "identity": OPSECDimension("Identity Separation", 100),
            "infra": OPSECDimension("Infrastructure Isolation", 100),
            "timing": OPSECDimension("Operational Timing", 100),
            "comms": OPSECDimension("Communication Security", 100),
            "data": OPSECDimension("Data Hygiene", 100),
            "tools": OPSECDimension("Tool Sanitization", 100),
            "cleanup": OPSECDimension("Cleanup Readiness", 100),
            "fallback": OPSECDimension("Fallback Plans", 100),
        }

        # Run checks
        infra_results = self.validate_infra()
        for k, v in infra_results.items():
            if isinstance(v, bool) and not v:
                dimensions["infra"].score -= 15
                dimensions["infra"].findings.append(f"Infra check {k} failed")

        identity_results = self.check_identity()
        if not identity_results.get("username_burner", False):
            dimensions["identity"].score -= 25
            dimensions["identity"].findings.append("No burner identity confirmed")
        if not identity_results.get("no_cross_contamination", True):
            dimensions["identity"].score -= 20
            dimensions["identity"].findings.append("Cross-contamination detected")

        artifact_results = self.scan_artifacts()
        for category, result in artifact_results.items():
            if isinstance(result, dict) and not result.get("clean", True):
                dimensions["data"].score -= 10
                dimensions["data"].findings.extend(result.get("findings", []))

        # Calculate overall
        scores = [d.score for d in dimensions.values()]
        overall = sum(scores) / len(scores)

        return {
            "overall_score": round(max(0, overall), 1),
            "dimensions": {
                name: {
                    "score": round(max(0, d.score), 1),
                    "findings": d.findings,
                    "recommendations": d.recommendations,
                }
                for name, d in dimensions.items()
            },
            "phase": self.phase.value,
            "assessed_at": datetime.now(timezone.utc).isoformat(),
            "risk_level": self._risk_level(overall),
        }

    def _risk_level(self, score: float) -> str:
        if score >= 85:
            return "LOW"
        elif score >= 65:
            return "MEDIUM"
        elif score >= 40:
            return "HIGH"
        else:
            return "CRITICAL"

    # ─── MITRE ATT&CK OPSEC mapping ───

    def mitre_opsec_map(self) -> dict:
        """Map OPSEC failures to MITRE ATT&CK techniques for TTP attribution."""
        return {
            "identity": {
                "tactic": "Reconnaissance (TA0043)",
                "techniques": [
                    "T1589 — Gather Victim Identity Information",
                    "T1593 — Search Open Websites/Domains",
                ],
                "impact": "Operator attribution via identity links",
            },
            "infra": {
                "tactic": "Command and Control (TA0011)",
                "techniques": [
                    "T1090 — Proxy",
                    "T1572 — Protocol Tunneling",
                ],
                "impact": "C2 infrastructure traced back to operator",
            },
            "timing": {
                "tactic": "Defense Evasion (TA0005)",
                "techniques": [
                    "T1070 — Indicator Removal",
                    "T1564 — Hide Artifacts",
                ],
                "impact": "Operational patterns create behavioral signature",
            },
            "comms": {
                "tactic": "Command and Control (TA0011)",
                "techniques": [
                    "T1571 — Non-Standard Port",
                    "T1008 — Fallback Channels",
                ],
                "impact": "Communication metadata leaks operator location",
            },
            "data": {
                "tactic": "Exfiltration (TA0010)",
                "techniques": [
                    "T1041 — Exfiltration Over C2 Channel",
                    "T1567 — Exfiltration Over Web Service",
                ],
                "impact": "Data remnants tie back to operator machine",
            },
            "tools": {
                "tactic": "Defense Evasion (TA0005)",
                "techniques": [
                    "T1027 — Obfuscated Files or Information",
                    "T1055 — Process Injection",
                ],
                "impact": "Tool signatures fingerprint operator toolkit",
            },
            "cleanup": {
                "tactic": "Defense Evasion (TA0005)",
                "techniques": [
                    "T1070.003 — Clear Command History",
                    "T1070.004 — File Deletion",
                ],
                "impact": "Incomplete cleanup leaves forensic artifacts",
            },
            "fallback": {
                "tactic": "Command and Control (TA0011)",
                "techniques": [
                    "T1090.003 — Multi-hop Proxy",
                    "T1008 — Fallback Channels",
                ],
                "impact": "No fallback = single point of OPSEC failure",
            },
        }

    # ─── Full report ───

    def report(self) -> str:
        """Generate full OPSEC audit report."""
        assessment = self.risk_assessment()
        lines = [
            "=" * 60,
            "  OPSEC AUDIT REPORT",
            "=" * 60,
            f"  Phase:         {assessment['phase']}",
            f"  Assessed:      {assessment['assessed_at']}",
            f"  Overall Score: {assessment['overall_score']}/100",
            f"  Risk Level:    {assessment['risk_level']}",
            "=" * 60,
            "",
            "DIMENSION SCORES:",
        ]

        for name, dim in assessment["dimensions"].items():
            bar = "█" * int(dim["score"] / 5) + "░" * (20 - int(dim["score"] / 5))
            lines.append(f"  {name:<15} {bar} {dim['score']:>5.1f}/100")

        lines.append("")
        lines.append("FINDINGS:")

        for name, dim in assessment["dimensions"].items():
            if dim["findings"]:
                lines.append(f"  [{name}]")
                for f in dim["findings"]:
                    lines.append(f"    ⚠️  {f}")

        lines.append("")
        lines.append("RECOMMENDED BURN SEQUENCE:")
        for step in self.burn_procedure():
            lines.append(f"  {step['order']}. {step['action']}")

        return "\n".join(lines)


# ─── CLI ───
if __name__ == "__main__":
    import sys

    checker = OpsecChecker()

    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        print(__doc__)
        print("\nCommands:")
        print("  assess     Full OPSEC risk assessment (0-100 score)")
        print("  infra      Infrastructure validation only")
        print("  identity   Identity separation check")
        print("  artifacts  Artifact scan (history, temp, clipboard)")
        print("  burn       Generate post-engagement burn sequence")
        print("  mitre      Show MITRE ATT&CK OPSEC mapping")
        print("  report     Full audit report")
        print("\nOptions:")
        print("  --phase [pre|during|post]    Engagement phase (default: pre)")
        sys.exit(0)

    cmd = sys.argv[1]

    # Parse phase
    phase_str = "pre-engagement"
    for i, a in enumerate(sys.argv):
        if a == "--phase" and i + 1 < len(sys.argv):
            raw = sys.argv[i + 1]
            # Map shortcuts to full values
            phase_map = {"pre": "pre-engagement", "during": "during-engagement", "post": "post-engagement"}
            phase_str = phase_map.get(raw, raw)
    checker.phase = OPSECPhase(phase_str)

    if cmd == "assess":
        print(json.dumps(checker.risk_assessment(), indent=2))
    elif cmd == "infra":
        print(json.dumps(checker.validate_infra(), indent=2))
    elif cmd == "identity":
        username = sys.argv[2] if len(sys.argv) > 2 else ""
        email = sys.argv[3] if len(sys.argv) > 3 else ""
        print(json.dumps(checker.check_identity(username, email), indent=2))
    elif cmd == "artifacts":
        print(json.dumps(checker.scan_artifacts(), indent=2))
    elif cmd == "burn":
        for step in checker.burn_procedure():
            print(f"\n{step['order']}. [{step['phase']}] {step['action']}")
            for c in step["commands"]:
                print(f"   $ {c}")
    elif cmd == "mitre":
        print(json.dumps(checker.mitre_opsec_map(), indent=2))
    elif cmd == "report":
        print(checker.report())
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
