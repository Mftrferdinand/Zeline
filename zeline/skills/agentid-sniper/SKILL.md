---
name: agentid-sniper
description: Compatibility alias for the canonical Zeline AgentID Watcher bundled skill.
license: MIT
---

# Zeline AgentID Watcher compatibility alias

Use `load_skill` with name `zeline-agentid-watcher` for the maintained entrypoint, references and complete licensed source bundle. This name is retained only for compatibility with older tasks and script paths. New tasks should use the canonical Zeline name.

The legacy script `scripts/agentid_sniper.py` routes offline inspection/check/preparation to the canonical helper. It does not start a scanner, wallet transaction, Telegram poller, updater or background service. No permissions are granted by loading this alias.
