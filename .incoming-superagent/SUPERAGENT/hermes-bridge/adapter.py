#!/usr/bin/env python3
"""
hermes-bridge/adapter.py — SUPERAGENT V7 ↔ Hermes Runtime Bridge
Maps SUPERAGENT tools (sk*) to Hermes function calls.
Enables Hermes-native execution of SUPERAGENT crypto ops.

Usage:
  python3 adapter.py swap ETH USDC 100 --from-chain ethereum --to-chain base
  python3 adapter.py bridge USDC 500 --from base --to ethereum
  python3 adapter.py mint-nft --contract 0x... --quantity 3
  python3 adapter.py deploy-token --name "MyToken" --symbol "MTK"
  python3 adapter.py verify-integrity
  python3 adapter.py status

Architecture:
  SUPERAGENT skills (sk10/sk13/H1-H10) → Hermes dispatch → hermes/scripts/*.py
  This adapter provides a unified CLI that routes to the right script.

v2.0 — Adds HermesNativeBridge (direct Python import, no subprocess) +
       HermesMCPTool dataclass + OpenAI function-calling schema generator.
       HermesBridgeLegacy (subprocess) kept as fallback.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ─── Hermes paths ───
HERMES_ROOT = Path(os.environ.get("HERMES_ROOT", Path.home() / ".hermes"))
HERMES_SCRIPTS = HERMES_ROOT / "skills" / "hermes" / "scripts"
SUPERAGENT_ROOT = Path(__file__).parent.parent  # openclaw/ root

# ─── Tool mapping: SUPERAGENT → Hermes script ───
TOOL_MAP = {
    "swap": {
        "script": "swap_engine.py",
        "description": "Execute token swap via 1inch aggregator",
        "required_env": ["RPC_URL", "PRIVATE_KEY"],
        "skill": "H1",
    },
    "bridge": {
        "script": "bridge_engine.py",
        "description": "Cross-chain bridge via LI.FI",
        "required_env": ["RPC_URL", "PRIVATE_KEY"],
        "skill": "H2",
    },
    "deploy-token": {
        "script": "deploy_engine.py",
        "description": "Compile and deploy smart contract",
        "required_env": ["RPC_URL", "PRIVATE_KEY", "ETHERSCAN_API_KEY"],
        "skill": "H10",
    },
    "mint-nft": {
        "script": "nft_engine.py",
        "description": "Mint NFT from any marketplace",
        "required_env": ["RPC_URL", "PRIVATE_KEY"],
        "skill": "H6",
    },
    "airdrop-farm": {
        "script": "airdrop_runner.py",
        "description": "Multi-wallet airdrop farming",
        "required_env": ["RPC_URL", "PRIVATE_KEY"],
        "skill": "sk31",
    },
    "contract-read": {
        "script": "contract_reader.py",
        "description": "Read contract state (no gas)",
        "required_env": ["RPC_URL"],
        "skill": "H9",
    },
    "contract-write": {
        "script": "contract_writer.py",
        "description": "Execute contract write (gated via governor)",
        "required_env": ["RPC_URL", "PRIVATE_KEY"],
        "skill": "H9",
    },
    "governor": {
        "script": "governor.py",
        "description": "Spend governor — check caps and limits",
        "required_env": [],
        "skill": "sk11",
    },
    "verify-integrity": {
        "script": None,  # Special: runs skill_integrity.py
        "description": "Verify SKILLS.lock integrity",
        "required_env": [],
        "skill": "sk0",
    },
}


# ══════════════════════════════════════════════════════════════════════
# Dataclasses
# ══════════════════════════════════════════════════════════════════════

@dataclass
class BridgeResult:
    """Result from Hermes bridge operation."""
    success: bool
    tool: str
    action: str
    tx_hash: Optional[str] = None
    output: str = ""
    error: str = ""
    gas_estimate: Optional[float] = None
    chain: str = ""

    def to_dict(self) -> dict:
        """Serialize to dict for JSON output."""
        return {
            "success": self.success,
            "tool": self.tool,
            "action": self.action,
            "tx_hash": self.tx_hash,
            "output": self.output,
            "error": self.error,
            "gas_estimate": self.gas_estimate,
            "chain": self.chain,
        }


@dataclass
class HermesMCPTool:
    """MCP-compatible tool definition for Hermes runtime.

    Represents a single tool with OpenAI function-calling compatible schema.
    """
    name: str
    description: str
    parameters: dict  # JSON Schema for parameters
    skill: str = ""
    required_env: list[str] = field(default_factory=list)

    def to_openai_function(self) -> dict:
        """Return OpenAI function-calling compatible JSON schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def execute(self, bridge: "HermesNativeBridge", arguments: dict) -> BridgeResult:
        """Execute this tool via the provided bridge instance.

        Args:
            bridge: HermesNativeBridge instance
            arguments: Tool arguments (key-value dict)

        Returns:
            BridgeResult with execution outcome
        """
        return bridge.call_tool(self.name, arguments)


# ══════════════════════════════════════════════════════════════════════
# HermesBridgeLegacy — subprocess-based (renamed from HermesBridge)
# ══════════════════════════════════════════════════════════════════════

class HermesBridgeLegacy:
    """Adapter between SUPERAGENT V7 and Hermes runtime (subprocess CLI)."""

    def __init__(self, hermes_root: Optional[Path] = None):
        self.hermes_root = hermes_root or HERMES_ROOT
        self.scripts_dir = self.hermes_root / "skills" / "hermes" / "scripts"

    def check_env(self, tool: str) -> dict:
        """Check required environment variables for a tool."""
        info = TOOL_MAP.get(tool, {})
        required = info.get("required_env", [])
        missing = [v for v in required if not os.environ.get(v)]
        return {
            "tool": tool,
            "ready": len(missing) == 0,
            "missing_env": missing,
            "available_env": [v for v in required if os.environ.get(v)],
        }

    def check_all_env(self) -> dict:
        """Check environment for all tools."""
        results = {}
        for tool in TOOL_MAP:
            results[tool] = self.check_env(tool)
        return results

    def execute(self, tool: str, args: dict) -> BridgeResult:
        """Execute a tool through Hermes bridge (subprocess)."""
        info = TOOL_MAP.get(tool)
        if not info:
            return BridgeResult(success=False, tool=tool, action="unknown",
                               error=f"Unknown tool: {tool}. Available: {list(TOOL_MAP.keys())}")

        # Check env
        env_check = self.check_env(tool)
        if not env_check["ready"]:
            return BridgeResult(
                success=False, tool=tool, action="env_check",
                error=f"Missing env vars: {env_check['missing_env']}. "
                      f"Set in .env or export before running."
            )

        # Special case: integrity check
        if tool == "verify-integrity":
            return self._verify_integrity()

        # Execute script
        script_path = self.scripts_dir / info["script"]
        if not script_path.exists():
            return BridgeResult(
                success=False, tool=tool, action="script_not_found",
                error=f"Script not found: {script_path}. "
                      f"Make sure Hermes is installed at {self.hermes_root}"
            )

        # Build command
        cmd = [sys.executable, str(script_path)]
        for k, v in args.items():
            cmd.extend([f"--{k.replace('_', '-')}", str(v)])

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                             cwd=str(self.scripts_dir),
                             env={**os.environ, "PYTHONPATH": str(self.scripts_dir.parent)})

            # Try to extract tx hash from output
            tx_hash = None
            output = r.stdout[-2000:] if r.stdout else ""

            for line in output.split("\n"):
                if "tx_hash" in line.lower() or "0x" in line[:10]:
                    match = re.search(r'0x[a-fA-F0-9]{64}', line)
                    if match:
                        tx_hash = match.group(0)

            return BridgeResult(
                success=r.returncode == 0,
                tool=tool,
                action=info["description"],
                tx_hash=tx_hash,
                output=output,
                error=r.stderr[-500:] if r.stderr else "",
                chain=args.get("chain", args.get("from_chain", "")),
            )
        except subprocess.TimeoutExpired:
            return BridgeResult(success=False, tool=tool, action="timeout",
                               error="Operation timed out after 120s")
        except Exception as e:
            return BridgeResult(success=False, tool=tool, action="error",
                               error=str(e))

    def _verify_integrity(self) -> BridgeResult:
        """Run SKILLS.lock integrity check (subprocess)."""
        integrity_script = SUPERAGENT_ROOT / "tools" / "skill_integrity.py"
        if not integrity_script.exists():
            return BridgeResult(
                success=False, tool="verify-integrity",
                error=f"skill_integrity.py not found at {integrity_script}"
            )
        try:
            r = subprocess.run(
                [sys.executable, str(integrity_script), "verify"],
                capture_output=True, text=True, timeout=30,
                cwd=str(SUPERAGENT_ROOT),
            )
            return BridgeResult(
                success=r.returncode == 0,
                tool="verify-integrity",
                action="SKILLS.lock integrity verification",
                output=r.stdout[-1000:] if r.stdout else "",
                error=r.stderr[-500:] if r.stderr else "",
            )
        except Exception as e:
            return BridgeResult(success=False, tool="verify-integrity", error=str(e))

    def status(self) -> dict:
        """Get bridge status — what's available, what's configured."""
        env_status = self.check_all_env()
        available = [t for t, s in env_status.items() if s["ready"]]
        unavailable = [t for t, s in env_status.items() if not s["ready"]]

        script_status = {}
        for tool, info in TOOL_MAP.items():
            if tool == "verify-integrity":
                script_status[tool] = True
            elif info.get("script"):
                script_status[tool] = (self.scripts_dir / info["script"]).exists()

        return {
            "bridge_version": "1.0.0",
            "hermes_root": str(self.hermes_root),
            "scripts_dir": str(self.scripts_dir),
            "tools_available": len(available),
            "tools_total": len(TOOL_MAP),
            "ready_tools": available,
            "unconfigured_tools": unavailable,
            "scripts_present": script_status,
            "superagent_root": str(SUPERAGENT_ROOT),
        }


# ══════════════════════════════════════════════════════════════════════
# HermesNativeBridge — Direct Python import, no subprocess
# ══════════════════════════════════════════════════════════════════════

# ─── Parameter schemas for each tool (OpenAI function-calling format) ───

TOOL_PARAMETER_SCHEMAS = {
    "swap": {
        "type": "object",
        "properties": {
            "token_in": {
                "type": "string",
                "description": "Input token symbol or contract address (e.g. ETH, USDC, 0x...)"
            },
            "token_out": {
                "type": "string",
                "description": "Output token symbol or contract address (e.g. USDC, ETH)"
            },
            "amount": {
                "type": "number",
                "description": "Amount of input token to swap (in human-readable units, e.g. 100.0)"
            },
            "chain": {
                "type": "string",
                "description": "Source chain: ethereum, base, arbitrum, optimism, polygon, bsc"
            },
            "slippage": {
                "type": "number",
                "description": "Slippage tolerance in percent (default 0.5)"
            },
        },
        "required": ["token_in", "token_out", "amount", "chain"],
    },
    "bridge": {
        "type": "object",
        "properties": {
            "token": {
                "type": "string",
                "description": "Token symbol or contract address to bridge"
            },
            "amount": {
                "type": "number",
                "description": "Amount of token to bridge (human-readable units)"
            },
            "from_chain": {
                "type": "string",
                "description": "Source chain: ethereum, base, arbitrum, optimism, polygon"
            },
            "to_chain": {
                "type": "string",
                "description": "Destination chain: ethereum, base, arbitrum, optimism, polygon"
            },
            "to_address": {
                "type": "string",
                "description": "(Optional) Destination wallet address. Defaults to sender address."
            },
        },
        "required": ["token", "amount", "from_chain", "to_chain"],
    },
    "deploy-token": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Token name (e.g. MyToken)"
            },
            "symbol": {
                "type": "string",
                "description": "Token symbol (e.g. MTK)"
            },
            "chain": {
                "type": "string",
                "description": "Chain to deploy on: ethereum, base, arbitrum, optimism, polygon"
            },
            "supply": {
                "type": "number",
                "description": "Initial supply (in human-readable units)"
            },
            "project_dir": {
                "type": "string",
                "description": "Path to Foundry project directory containing the contract"
            },
        },
        "required": ["name", "symbol", "chain"],
    },
    "mint-nft": {
        "type": "object",
        "properties": {
            "contract": {
                "type": "string",
                "description": "NFT contract address"
            },
            "quantity": {
                "type": "integer",
                "description": "Number of NFTs to mint"
            },
            "chain": {
                "type": "string",
                "description": "Chain: ethereum, base, arbitrum, optimism, polygon"
            },
            "token_id": {
                "type": "string",
                "description": "(Optional) Specific token ID to buy"
            },
        },
        "required": ["contract", "quantity", "chain"],
    },
    "airdrop-farm": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Task name to execute (e.g. swap, bridge, stake)"
            },
            "wallet_label": {
                "type": "string",
                "description": "Wallet label or 'all' for all wallets"
            },
        },
        "required": ["task"],
    },
    "contract-read": {
        "type": "object",
        "properties": {
            "contract": {
                "type": "string",
                "description": "Contract address to read from"
            },
            "function": {
                "type": "string",
                "description": "Function name to call (e.g. balanceOf, totalSupply)"
            },
            "args": {
                "type": "array",
                "description": "Function arguments as array",
                "items": {"type": "string"},
            },
            "chain": {
                "type": "string",
                "description": "Chain: ethereum, base, arbitrum, optimism, polygon, bsc"
            },
        },
        "required": ["contract", "function", "chain"],
    },
    "contract-write": {
        "type": "object",
        "properties": {
            "contract": {
                "type": "string",
                "description": "Contract address to write to"
            },
            "function": {
                "type": "string",
                "description": "Function name to call"
            },
            "args": {
                "type": "array",
                "description": "Function arguments as array",
                "items": {"type": "string"},
            },
            "chain": {
                "type": "string",
                "description": "Chain: ethereum, base, arbitrum, optimism, polygon, bsc"
            },
            "value": {
                "type": "number",
                "description": "ETH value to send with the transaction"
            },
        },
        "required": ["contract", "function", "chain"],
    },
    "governor": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Governor action: status, limits, history, reset_killswitch",
                "enum": ["status", "limits", "history", "reset_killswitch"],
            },
        },
        "required": ["action"],
    },
    "verify-integrity": {
        "type": "object",
        "properties": {
            "strict": {
                "type": "boolean",
                "description": "Exit with non-zero on any finding (default: true)"
            },
        },
        "required": [],
    },
}


class HermesNativeBridge:
    """Native Python bridge — imports Hermes scripts directly, no subprocess.

    Advantages over HermesBridgeLegacy:
    - Zero subprocess overhead (no process spawn, no stdout parsing)
    - Structured return types (dataclass, not string)
    - Direct error propagation (real tracebacks)
    - MCP-compatible function schema generation
    - Async-native for swap/bridge/NFT operations
    """

    def __init__(self, hermes_root: Optional[Path] = None):
        self.hermes_root = hermes_root or HERMES_ROOT
        self.scripts_dir = self.hermes_root / "skills" / "hermes" / "scripts"
        self._modules: dict[str, Any] = {}
        self._fallback_legacy: Optional[HermesBridgeLegacy] = None

    @property
    def legacy(self) -> HermesBridgeLegacy:
        """Lazy fallback to subprocess-based legacy bridge."""
        if self._fallback_legacy is None:
            self._fallback_legacy = HermesBridgeLegacy(self.hermes_root)
        return self._fallback_legacy

    def _resolve_scripts_dir(self) -> Optional[Path]:
        """Find hermes scripts directory from multiple possible locations.

        Precedence:
        1. Configured self.scripts_dir
        2. Path relative to THIS file (hermes-bridge/../skills/hermes/scripts/)
        3. SUPERAGENT_ROOT/skills/hermes/scripts/
        """
        if self.scripts_dir.exists():
            return self.scripts_dir

        # Auto-detect relative to this adapter
        bridge_dir = Path(__file__).parent  # hermes-bridge/
        adjacent = bridge_dir.parent / "skills" / "hermes" / "scripts"
        if adjacent.exists():
            return adjacent

        # SUPERAGENT_ROOT fallback
        sa_root = Path(__file__).parent.parent
        sa_scripts = sa_root / "skills" / "hermes" / "scripts"
        if sa_scripts.exists():
            return sa_scripts

        return None

    def _ensure_path(self) -> Path:
        """Ensure hermes scripts are on sys.path for direct imports."""
        scripts_dir = self._resolve_scripts_dir()
        if scripts_dir is None:
            raise RuntimeError(
                f"Hermes scripts not found. Tried: {self.scripts_dir}, "
                f"{Path(__file__).parent.parent / 'skills' / 'hermes' / 'scripts'}"
            )

        scripts_str = str(scripts_dir)
        if scripts_str not in sys.path:
            sys.path.insert(0, scripts_str)

        # Also add parent for cross-script imports (e.g., swap_engine imported by bridge_engine)
        parent_str = str(scripts_dir.parent)
        if parent_str not in sys.path:
            sys.path.insert(0, parent_str)

        return scripts_dir

    def _import_module(self, script_name: str):
        """Import a hermes script module directly (cached)."""
        if script_name in self._modules:
            return self._modules[script_name]

        self._ensure_path()
        module_name = script_name.replace(".py", "")

        try:
            mod = __import__(module_name)
            self._modules[script_name] = mod
            return mod
        except ImportError as e:
            raise RuntimeError(
                f"Failed to import hermes script '{script_name}': {e}. "
                f"Falling back to HermesBridgeLegacy."
            ) from e

    # ─── Environment checks ───

    def check_env(self, tool: str) -> dict:
        """Check required environment variables for a tool."""
        info = TOOL_MAP.get(tool, {})
        required = info.get("required_env", [])
        missing = [v for v in required if not os.environ.get(v)]
        return {
            "tool": tool,
            "ready": len(missing) == 0,
            "missing_env": missing,
            "available_env": [v for v in required if os.environ.get(v)],
        }

    def check_all_env(self) -> dict:
        """Check environment for all tools."""
        return {tool: self.check_env(tool) for tool in TOOL_MAP}

    # ─── Native operation wrappers ───

    def swap(self, token_in: str, token_out: str, amount: float, chain: str,
             slippage: float = 0.5) -> BridgeResult:
        """Execute a token swap via 1inch aggregator (native import)."""
        try:
            mod = self._import_module("swap_engine.py")

            # Map chain name to chain_id
            chain_id = _chain_name_to_id(chain)

            from web3 import Web3
            from eth_account import Account

            rpc_url = os.environ["RPC_URL"]
            private_key = os.environ["PRIVATE_KEY"]

            w3 = Web3(Web3.HTTPProvider(rpc_url))
            account: Any = Account.from_key(private_key)

            import asyncio
            result = asyncio.run(_do_swap(mod, w3, account, token_in, token_out,
                                          amount, chain_id, slippage))

            return BridgeResult(
                success=result.status == "sent",
                tool="swap",
                action=f"Swap {amount} {token_in} → {token_out} on {chain}",
                tx_hash=result.tx_hash,
                output=f"Status: {result.status}",
                error=result.error or "",
                chain=chain,
            )
        except Exception as e:
            # Fallback to legacy subprocess on import failure
            return self.legacy.execute("swap", {
                "token_in": token_in, "token_out": token_out,
                "amount": amount, "chain": chain, "slippage": slippage,
            })

    def bridge(self, token: str, amount: float, from_chain: str,
               to_chain: str, to_address: Optional[str] = None) -> BridgeResult:
        """Execute cross-chain bridge via LI.FI (native import)."""
        try:
            mod = self._import_module("bridge_engine.py")

            from web3 import Web3
            from eth_account import Account

            rpc_url = os.environ["RPC_URL"]
            private_key = os.environ["PRIVATE_KEY"]

            from_chain_id = _chain_name_to_id(from_chain)
            to_chain_id = _chain_name_to_id(to_chain)

            w3 = Web3(Web3.HTTPProvider(rpc_url))
            account: Any = Account.from_key(private_key)

            # Get token addresses — use sentinel for native
            from_token_addr = _resolve_token_address(token, from_chain_id)
            to_token_addr = _resolve_token_address(token, to_chain_id)

            import asyncio
            bridge_fn = getattr(mod, "lifi_bridge", None)
            if bridge_fn is None:
                raise RuntimeError("lifi_bridge function not found in bridge_engine.py")

            amount_wei = w3.to_wei(amount, "ether") if token.upper() in _NATIVE_TOKENS else int(amount * 10**18)

            result = asyncio.run(bridge_fn(
                w3, account, to_chain_id,
                from_token_addr, to_token_addr,
                amount_wei, to_address,
            ))

            return BridgeResult(
                success=result.status == "sent",
                tool="bridge",
                action=f"Bridge {amount} {token} {from_chain} → {to_chain}",
                tx_hash=result.src_tx_hash,
                output=f"Status: {result.status}, dst_tx: {result.dst_tx_hash}",
                error=result.error or "",
                chain=f"{from_chain}→{to_chain}",
            )
        except Exception as e:
            return self.legacy.execute("bridge", {
                "token": token, "amount": amount,
                "from_chain": from_chain, "to_chain": to_chain,
                "to_address": to_address or "",
            })

    def mint_nft(self, contract: str, quantity: int, chain: str,
                 token_id: Optional[str] = None) -> BridgeResult:
        """Mint/buy NFT from marketplace (native import)."""
        try:
            mod = self._import_module("nft_engine.py")

            from web3 import Web3
            from eth_account import Account

            rpc_url = os.environ["RPC_URL"]
            private_key = os.environ["PRIVATE_KEY"]
            chain_id = _chain_name_to_id(chain)

            w3 = Web3(Web3.HTTPProvider(rpc_url))
            account: Any = Account.from_key(private_key)

            buy_nft_fn = getattr(mod, "buy_nft_evm", None)
            if buy_nft_fn is None:
                raise RuntimeError("buy_nft_evm not found in nft_engine.py")

            import asyncio
            result = asyncio.run(buy_nft_fn(
                w3, account, contract,
                token_id or "0",  # token_id defaults
                os.environ.get("RESERVOIR_API_KEY"),
            ))

            return BridgeResult(
                success=result.status == "sent",
                tool="mint-nft",
                action=f"Mint {quantity} NFT from {contract} on {chain}",
                tx_hash=result.tx_hashes[0] if result.tx_hashes else None,
                output=f"Status: {result.status}, txs: {result.tx_hashes}",
                error=result.error or "",
                chain=chain,
            )
        except Exception as e:
            return self.legacy.execute("mint-nft", {
                "contract": contract, "quantity": quantity,
                "chain": chain, "token_id": token_id or "",
            })

    def deploy_token(self, name: str, symbol: str, chain: str,
                     supply: Optional[float] = None,
                     project_dir: Optional[str] = None) -> BridgeResult:
        """Compile and deploy a smart contract (native import)."""
        try:
            mod = self._import_module("deploy_engine.py")

            from web3 import Web3
            from eth_account import Account

            rpc_url = os.environ["RPC_URL"]
            private_key = os.environ["PRIVATE_KEY"]
            chain_id = _chain_name_to_id(chain)

            w3 = Web3(Web3.HTTPProvider(rpc_url))
            account: Any = Account.from_key(private_key)

            deploy_fn = getattr(mod, "deploy_contract", None)
            if deploy_fn is None:
                raise RuntimeError("deploy_contract not found in deploy_engine.py")

            # Prepare deployment args via deploy engine
            bytecode = _build_erc20_bytecode(name, symbol, supply)
            import asyncio

            async def _deploy():
                return await deploy_fn(w3, account, bytecode, chain_id)

            result = asyncio.run(_deploy())

            return BridgeResult(
                success=result.status == "deployed",
                tool="deploy-token",
                action=f"Deploy {name} ({symbol}) on {chain}",
                tx_hash=getattr(result, "tx_hash", None),
                output=f"Status: {result.status}, address: {result.address}",
                error=getattr(result, "detail", "") if result.status != "deployed" else "",
                chain=chain,
            )
        except Exception as e:
            return self.legacy.execute("deploy-token", {
                "name": name, "symbol": symbol, "chain": chain,
                "supply": supply or "", "project_dir": project_dir or "",
            })

    def contract_read(self, contract: str, function: str, chain: str,
                      args: Optional[list] = None) -> BridgeResult:
        """Read contract state (no gas, native import)."""
        try:
            mod = self._import_module("contract_reader.py")

            chain_id = _chain_name_to_id(chain)
            fetch_abi = getattr(mod, "fetch_abi", None)
            get_w3 = getattr(mod, "get_w3", None)

            if fetch_abi is None or get_w3 is None:
                raise RuntimeError("Required functions not found in contract_reader.py")

            w3 = get_w3(chain_id, os.environ.get("RPC_URL"))
            abi = fetch_abi(contract, chain_id)

            contract_obj = w3.eth.contract(address=w3.to_checksum_address(contract), abi=abi)
            fn = getattr(contract_obj.functions, function)
            result = fn(*args).call() if args else fn().call()

            return BridgeResult(
                success=True,
                tool="contract-read",
                action=f"Read {function}() from {contract} on {chain}",
                output=str(result),
                chain=chain,
            )
        except Exception as e:
            return self.legacy.execute("contract-read", {
                "contract": contract, "function": function,
                "chain": chain, "args": json.dumps(args or []),
            })

    def contract_write(self, contract: str, function: str, chain: str,
                       args: Optional[list] = None,
                       value: float = 0.0) -> BridgeResult:
        """Execute contract write (gated via governor, native import)."""
        try:
            mod = self._import_module("contract_writer.py")

            from eth_account import Account

            chain_id = _chain_name_to_id(chain)
            private_key = os.environ["PRIVATE_KEY"]
            account: Any = Account.from_key(private_key)

            get_w3 = getattr(mod, "get_w3", None)
            fetch_abi = getattr(mod, "fetch_abi", None)

            if get_w3 is None or fetch_abi is None:
                # Try importing from contract_reader as fallback
                cr = self._import_module("contract_reader.py")
                get_w3 = getattr(cr, "get_w3")
                fetch_abi = getattr(cr, "fetch_abi")

            w3 = get_w3(chain_id, os.environ.get("RPC_URL"))
            abi = fetch_abi(contract, chain_id)

            ContractWriter = getattr(mod, "ContractWriter", None)
            if ContractWriter is None:
                raise RuntimeError("ContractWriter not found in contract_writer.py")

            writer = ContractWriter(contract, chain_id, account, abi)
            fn = getattr(writer.contract.functions, function)
            tx_fn = fn(*args) if args else fn()
            tx_hash = writer.send_transaction(tx_fn, value=w3.to_wei(value, "ether") if value else 0)

            return BridgeResult(
                success=True,
                tool="contract-write",
                action=f"Write {function}() to {contract} on {chain}",
                tx_hash=tx_hash,
                chain=chain,
            )
        except Exception as e:
            return self.legacy.execute("contract-write", {
                "contract": contract, "function": function,
                "chain": chain, "args": json.dumps(args or []),
                "value": str(value),
            })

    def governor(self, action: str = "status") -> BridgeResult:
        """Query governor state (native import)."""
        try:
            mod = self._import_module("governor.py")

            if action == "status":
                # Query governor state
                governor_class = getattr(mod, "Governor", None) or getattr(mod, "SpendGovernor", None)
                if governor_class:
                    gov = governor_class()
                    summary = gov.summary() if hasattr(gov, "summary") else str(gov)
                else:
                    summary = "Governor module loaded but no class found."

            elif action == "limits":
                GovernorLimits = getattr(mod, "GovernorLimits", None)
                if GovernorLimits:
                    summary = str(GovernorLimits.from_env() if hasattr(GovernorLimits, "from_env") else GovernorLimits())
                else:
                    summary = "GovernorLimits dataclass not found."

            elif action == "history":
                governor_class = getattr(mod, "Governor", None) or getattr(mod, "SpendGovernor", None)
                if governor_class and hasattr(governor_class, "history"):
                    gov = governor_class()
                    summary = str(gov.history())
                else:
                    summary = "History not available."

            elif action == "reset_killswitch":
                reset_fn = getattr(mod, "reset_killswitch", None)
                if reset_fn:
                    reset_fn()
                    summary = "Killswitch reset."
                else:
                    summary = "reset_killswitch() not found in governor module."

            else:
                summary = f"Unknown governor action: {action}"

            return BridgeResult(
                success=True,
                tool="governor",
                action=f"Governor {action}",
                output=summary,
            )
        except Exception as e:
            return self.legacy.execute("governor", {"action": action})

    def verify_integrity(self, strict: bool = True) -> BridgeResult:
        """Verify SKILLS.lock integrity.

        Delegates to subprocess because skill_integrity.py is a CLI tool
        with argparse at module level. Uses the legacy bridge's impl.
        """
        return self.legacy._verify_integrity()

    # ─── call_tool — unified dispatch (MCP-compatible) ───

    def call_tool(self, tool_name: str, arguments: dict) -> BridgeResult:
        """Native function call dispatch — no subprocess.

        Maps tool_name to the corresponding method and executes.
        Falls back to HermesBridgeLegacy if native execution fails.

        Args:
            tool_name: Tool to execute (swap, bridge, mint-nft, etc.)
            arguments: Key-value arguments for the tool

        Returns:
            BridgeResult with execution outcome
        """
        if tool_name not in TOOL_MAP:
            return BridgeResult(
                success=False, tool=tool_name, action="unknown",
                error=f"Unknown tool: {tool_name}. Available: {list(TOOL_MAP.keys())}"
            )

        # Check env before attempting native execution
        env_check = self.check_env(tool_name)
        if not env_check["ready"]:
            return BridgeResult(
                success=False, tool=tool_name, action="env_check",
                error=f"Missing env vars: {env_check['missing_env']}. "
                      f"Set in .env or export before running."
            )

        # Dispatch to native methods
        dispatch = {
            "swap": self._native_swap_dispatch,
            "bridge": self._native_bridge_dispatch,
            "mint-nft": self._native_mint_nft_dispatch,
            "deploy-token": self._native_deploy_token_dispatch,
            "airdrop-farm": self._native_airdrop_dispatch,
            "contract-read": self._native_contract_read_dispatch,
            "contract-write": self._native_contract_write_dispatch,
            "governor": self._native_governor_dispatch,
            "verify-integrity": self._native_verify_dispatch,
        }

        handler = dispatch.get(tool_name)
        if handler is None:
            return self.legacy.execute(tool_name, arguments)

        try:
            return handler(arguments)
        except Exception as e:
            # Fallback to legacy subprocess on any native failure
            return self.legacy.execute(tool_name, arguments)

    # ─── Native dispatch helpers ───

    def _native_swap_dispatch(self, args: dict) -> BridgeResult:
        return self.swap(
            token_in=str(args.get("token_in", "")),
            token_out=str(args.get("token_out", "")),
            amount=float(args.get("amount", 0)),
            chain=str(args.get("chain", "ethereum")),
            slippage=float(args.get("slippage", 0.5)),
        )

    def _native_bridge_dispatch(self, args: dict) -> BridgeResult:
        return self.bridge(
            token=str(args.get("token", "")),
            amount=float(args.get("amount", 0)),
            from_chain=str(args.get("from_chain", "")),
            to_chain=str(args.get("to_chain", "")),
            to_address=args.get("to_address"),
        )

    def _native_mint_nft_dispatch(self, args: dict) -> BridgeResult:
        return self.mint_nft(
            contract=str(args.get("contract", "")),
            quantity=int(args.get("quantity", 1)),
            chain=str(args.get("chain", "ethereum")),
            token_id=args.get("token_id"),
        )

    def _native_deploy_token_dispatch(self, args: dict) -> BridgeResult:
        return self.deploy_token(
            name=str(args.get("name", "")),
            symbol=str(args.get("symbol", "")),
            chain=str(args.get("chain", "ethereum")),
            supply=float(args["supply"]) if args.get("supply") else None,
            project_dir=args.get("project_dir"),
        )

    def _native_airdrop_dispatch(self, args: dict) -> BridgeResult:
        # Airdrop runner is complex scheduler — always fall back to legacy
        return self.legacy.execute("airdrop-farm", args)

    def _native_contract_read_dispatch(self, args: dict) -> BridgeResult:
        parsed_args = json.loads(args.get("args", "[]")) if isinstance(args.get("args"), str) else (args.get("args") or [])
        return self.contract_read(
            contract=str(args.get("contract", "")),
            function=str(args.get("function", "")),
            chain=str(args.get("chain", "ethereum")),
            args=parsed_args,
        )

    def _native_contract_write_dispatch(self, args: dict) -> BridgeResult:
        parsed_args = json.loads(args.get("args", "[]")) if isinstance(args.get("args"), str) else (args.get("args") or [])
        return self.contract_write(
            contract=str(args.get("contract", "")),
            function=str(args.get("function", "")),
            chain=str(args.get("chain", "ethereum")),
            args=parsed_args,
            value=float(args.get("value", 0)),
        )

    def _native_governor_dispatch(self, args: dict) -> BridgeResult:
        return self.governor(action=str(args.get("action", "status")))

    def _native_verify_dispatch(self, args: dict) -> BridgeResult:
        return self.verify_integrity(strict=bool(args.get("strict", True)))

    # ─── MCP tool definitions ───

    def get_tool_schemas(self) -> list[dict]:
        """Return OpenAI function-calling compatible JSON schemas for all tools."""
        schemas = []
        for tool_name, info in TOOL_MAP.items():
            params = TOOL_PARAMETER_SCHEMAS.get(tool_name, {
                "type": "object",
                "properties": {},
                "required": [],
            })
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": info["description"],
                    "parameters": params,
                },
            })
        return schemas

    def get_tools(self) -> list[HermesMCPTool]:
        """Return MCP-compatible tool definitions for Hermes runtime."""
        tools = []
        for tool_name, info in TOOL_MAP.items():
            params = TOOL_PARAMETER_SCHEMAS.get(tool_name, {
                "type": "object",
                "properties": {},
                "required": [],
            })
            tools.append(HermesMCPTool(
                name=tool_name,
                description=info["description"],
                parameters=params,
                skill=info.get("skill", ""),
                required_env=info.get("required_env", []),
            ))
        return tools

    def get_function_schemas(self) -> list[dict]:
        """Alias for get_tool_schemas() — returns function-calling compatible JSON schemas."""
        return self.get_tool_schemas()

    # ─── Status ───

    def execute(self, tool: str, args: dict) -> BridgeResult:
        """Unified execute — tries native first, falls back to legacy."""
        return self.call_tool(tool, args)

    def status(self) -> dict:
        """Get bridge status — what's available, what's configured."""
        env_status = self.check_all_env()
        available = [t for t, s in env_status.items() if s["ready"]]
        unavailable = [t for t, s in env_status.items() if not s["ready"]]

        scripts_dir = self._resolve_scripts_dir()
        script_status = {}
        for tool, info in TOOL_MAP.items():
            if tool == "verify-integrity":
                script_status[tool] = True
            elif info.get("script") and scripts_dir:
                script_status[tool] = (scripts_dir / info["script"]).exists()
            else:
                script_status[tool] = False

        return {
            "bridge_version": "2.0.0",
            "bridge_mode": "native",
            "hermes_root": str(self.hermes_root),
            "scripts_dir": str(scripts_dir) if scripts_dir else "not found",
            "tools_available": len(available),
            "tools_total": len(TOOL_MAP),
            "ready_tools": available,
            "unconfigured_tools": unavailable,
            "scripts_present": script_status,
            "superagent_root": str(SUPERAGENT_ROOT),
            "mcp_tools_count": len(self.get_tools()),
        }


# ══════════════════════════════════════════════════════════════════════
# Backward compatibility alias
# ══════════════════════════════════════════════════════════════════════

# Existing code using `HermesBridge` gets the new native bridge.
# Use `HermesBridgeLegacy` explicitly to force subprocess mode.
HermesBridge = HermesNativeBridge


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

# Chain name → chain ID mapping (used by native bridge for resolving)
_CHAIN_NAME_TO_ID: dict[str, int] = {
    "ethereum": 1, "eth": 1,
    "base": 8453,
    "arbitrum": 42161, "arb": 42161,
    "optimism": 10, "op": 10,
    "polygon": 137, "matic": 137,
    "bsc": 56, "bnb": 56,
    "avalanche": 43114, "avax": 43114,
    "linea": 59144,
    "scroll": 534352,
    "zksync": 324, "zksync era": 324,
    "zora": 7777777,
}

# Native tokens per chain (sentinel address used by aggregators)
_NATIVE_TOKENS = {"ETH", "BNB", "MATIC", "AVAX", "FTM"}
_NATIVE_SENTINEL = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"


def _chain_name_to_id(name: str) -> int:
    """Convert chain name (ethereum, base, etc.) to chain ID."""
    name_lower = name.lower().strip()
    if name_lower in _CHAIN_NAME_TO_ID:
        return _CHAIN_NAME_TO_ID[name_lower]
    # Try parsing as integer
    try:
        return int(name)
    except ValueError:
        raise ValueError(f"Unknown chain: {name}. Known: {list(_CHAIN_NAME_TO_ID.keys())}")


def _resolve_token_address(symbol_or_address: str, chain_id: int) -> str:
    """Resolve token symbol to address, or return address as-is.

    Returns NATIVE_SENTINEL for native gas tokens.
    """
    if symbol_or_address.startswith("0x"):
        return symbol_or_address
    if symbol_or_address.upper() in _NATIVE_TOKENS:
        return _NATIVE_SENTINEL

    # Common stablecoin/token addresses (Ethereum mainnet → other chains could differ)
    _TOKEN_ADDRESSES: dict[str, str] = {
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        "WBTC": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
        "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    }
    return _TOKEN_ADDRESSES.get(symbol_or_address.upper(), symbol_or_address)


def _build_erc20_bytecode(name: str, symbol: str, supply: Optional[float] = None) -> str:
    """Build minimal ERC-20 bytecode for deployment.

    This is a simplified path — production use should go through
    deploy_engine.py's Foundry compilation for proper contracts.
    """
    # Placeholder: in production, this is handled by deploy_engine.py
    # via Foundry compilation. Return empty to trigger the deploy_engine path.
    if supply is None:
        supply = 1_000_000
    # Basic ERC-20 constructor args: name, symbol, decimals, totalSupply
    # Actual bytecode would come from Foundry compilation —
    # deploy_token() already routes through deploy_engine for this.
    return ""


async def _do_swap(mod, w3, account, token_in: str, token_out: str,
                   amount: float, chain_id: int, slippage: float):
    """Execute swap via 1inch aggregator using the native swap engine."""
    # Try to use the swap engine's own functions
    swap_fn = getattr(mod, "swap", None) or getattr(mod, "execute_swap", None)
    if swap_fn:
        # Call the engine's main swap function
        result = await swap_fn(
            w3=w3,
            account=account,
            token_in=token_in,
            token_out=token_out,
            amount=amount,
            chain_id=chain_id,
            slippage=slippage,
        )
        return result

    # Fallback: use 1inch API directly via the module's patterns
    from dataclasses import dataclass as _dc

    @_dc
    class _FallbackResult:
        status: str = "error"
        tx_hash: str = None
        error: str = None

    try:
        import httpx
        chain_name = {v: k for k, v in _CHAIN_NAME_TO_ID.items()}.get(chain_id, "ethereum")
        async with httpx.AsyncClient(timeout=30) as client:
            # 1inch swap quote + tx
            amount_wei = w3.to_wei(amount, "ether")
            quote_url = f"https://api.1inch.dev/swap/v6.0/{chain_id}/swap"

            token_in_addr = _resolve_token_address(token_in, chain_id)
            token_out_addr = _resolve_token_address(token_out, chain_id)

            headers = {"Authorization": f"Bearer {os.environ.get('ONEINCH_API_KEY', '')}"}
            params = {
                "src": token_in_addr,
                "dst": token_out_addr,
                "amount": str(amount_wei),
                "from": account.address,
                "slippage": str(slippage),
                "disableEstimate": "true",
            }
            r = await client.get(quote_url, params=params, headers=headers)
            r.raise_for_status()
            tx_data = r.json()["tx"]

            # Send transaction
            tx = {
                "from": account.address,
                "to": tx_data["to"],
                "data": tx_data["data"],
                "value": int(tx_data.get("value", "0x0"), 16),
                "gas": int(tx_data.get("gas", "0x4C4B40"), 16),
                "gasPrice": w3.eth.gas_price,
                "nonce": w3.eth.get_transaction_count(account.address),
                "chainId": chain_id,
            }
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

            return _FallbackResult(status="sent", tx_hash=tx_hash.hex())
    except Exception as e:
        return _FallbackResult(status="error", error=str(e))


# ══════════════════════════════════════════════════════════════════════
# CLI — supports both native and legacy modes
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Determine bridge mode: --native (default) or --legacy
    use_legacy = "--legacy" in sys.argv
    if use_legacy:
        sys.argv.remove("--legacy")
        bridge = HermesBridgeLegacy()
    else:
        bridge = HermesNativeBridge()

    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        print(__doc__)
        print("\nUsage:")
        print("  python3 adapter.py [--legacy] <tool> [args...]")
        print("\nFlags:")
        print("  --legacy      Use subprocess-based HermesBridgeLegacy (v1 fallback)")
        print("  --native      Native Python bridge (default)")
        print("\nAvailable tools:")
        for name, info in TOOL_MAP.items():
            scripts_note = "✓" if info.get("script") else "built-in"
            print(f"  {name:<18} {info['description']:<45} [{info['skill']}] {scripts_note}")
        print("\nExamples:")
        print("  python3 adapter.py swap --token-in ETH --token-out USDC --amount 100 --chain ethereum")
        print("  python3 adapter.py bridge --token USDC --amount 500 --from-chain base --to-chain ethereum")
        print("  python3 adapter.py mint-nft --contract 0x... --quantity 3 --chain ethereum")
        print("  python3 adapter.py deploy-token --name 'MyToken' --symbol 'MTK' --chain base")
        print("  python3 adapter.py contract-read --contract 0x... --function balanceOf --chain ethereum")
        print("  python3 adapter.py verify-integrity")
        print("  python3 adapter.py status")
        print("  python3 adapter.py mcp-schemas  # Export OpenAI function-calling schemas")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "status":
        print(json.dumps(bridge.status(), indent=2))

    elif cmd == "env":
        print(json.dumps(bridge.check_all_env(), indent=2))

    elif cmd == "mcp-schemas" or cmd == "tool-schemas":
        # Export MCP/OpenAI function-calling schemas
        if isinstance(bridge, HermesNativeBridge):
            schemas = bridge.get_function_schemas()
        else:
            # Legacy bridge doesn't have MCP schemas — build from TOOL_MAP
            schemas = []
            for tool_name, info in TOOL_MAP.items():
                params = TOOL_PARAMETER_SCHEMAS.get(tool_name, {
                    "type": "object", "properties": {}, "required": []
                })
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": info["description"],
                        "parameters": params,
                    },
                })
        print(json.dumps(schemas, indent=2))

    elif cmd == "mcp-tools":
        # Export full HermesMCPTool definitions
        if isinstance(bridge, HermesNativeBridge):
            tools = bridge.get_tools()
            output = []
            for t in tools:
                output.append({
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "skill": t.skill,
                    "required_env": t.required_env,
                    "openai_function": t.to_openai_function(),
                })
        else:
            # Build synthetic MCP tools from TOOL_MAP for legacy bridge
            output = []
            for tool_name, info in TOOL_MAP.items():
                params = TOOL_PARAMETER_SCHEMAS.get(tool_name, {
                    "type": "object", "properties": {}, "required": []
                })
                t = HermesMCPTool(
                    name=tool_name,
                    description=info["description"],
                    parameters=params,
                    skill=info.get("skill", ""),
                    required_env=info.get("required_env", []),
                )
                output.append({
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "skill": t.skill,
                    "required_env": t.required_env,
                    "openai_function": t.to_openai_function(),
                })
        print(json.dumps(output, indent=2))

    elif cmd in TOOL_MAP:
        # Parse remaining args as key=value pairs or --key value
        args = {}
        i = 2
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg.startswith("--"):
                key = arg[2:].replace("-", "_")
                if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
                    args[key] = sys.argv[i + 1]
                    i += 2
                else:
                    args[key] = "true"
                    i += 1
            elif "=" in arg:
                k, v = arg.split("=", 1)
                args[k] = v
                i += 1
            else:
                i += 1

        result = bridge.execute(cmd, args)
        print(json.dumps(result.to_dict(), indent=2, default=str))
        sys.exit(0 if result.success else 1)

    else:
        print(f"Unknown command: {cmd}")
        print(f"Available tools: {list(TOOL_MAP.keys())}")
        print(f"Available commands: status, env, mcp-schemas, mcp-tools")
        sys.exit(1)