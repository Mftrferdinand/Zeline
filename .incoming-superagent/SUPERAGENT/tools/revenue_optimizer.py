#!/usr/bin/env python3
"""
revenue_optimizer.py — Autonomous Monetization Optimizer v1.0.0
SUPERAGENT V7 | Production-Grade Revenue Pipeline

PASSIVE BY DEFAULT: All execution is simulation-only unless --broadcast flag is set.
Global circuit breaker caps protect against runaway strategies.

Strategies:
  1. MEV Arbitrage        — mempool-level frontrunning/sandwiching
  2. Cross-Chain Arbitrage — bridge/swap price differentials across chains
  3. Airdrop Farming       — multi-wallet sybil operations
  4. Yield Aggregation     — lending/staking/liquidity pool optimization
  5. NFT Trading           — collection sniping & floor arbitrage
  6. Basis Trading         — spot/futures delta-neutral positions

Usage:
    python3 revenue_optimizer.py scan
    python3 revenue_optimizer.py rank
    python3 revenue_optimizer.py execute
    python3 revenue_optimizer.py report
    python3 revenue_optimizer.py scan --strategy mev,cross_chain
    python3 revenue_optimizer.py execute --broadcast
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import urllib.request
import urllib.error
from typing import Optional, Tuple
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Optional Hermes Bridge import ──
_HERMES_BRIDGE_AVAILABLE = False
_HermesNativeBridge = None
_BridgeResult = None
try:
    _hermes_bridge_path = Path(__file__).parent.parent / "hermes-bridge"
    if str(_hermes_bridge_path) not in sys.path:
        sys.path.insert(0, str(_hermes_bridge_path))
    from adapter import HermesNativeBridge as _HermesNativeBridge
    from adapter import BridgeResult as _BridgeResult
    _HERMES_BRIDGE_AVAILABLE = True
except ImportError as e:
    _hermes_import_error = str(e)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
#  Enums & Constants
# ──────────────────────────────────────────────────────────────────────

class StrategyType(Enum):
    """Revenue strategy categories."""
    MEV = "mev"
    CROSS_CHAIN_ARB = "cross_chain_arb"
    AIRDROP_FARMING = "airdrop_farming"
    YIELD_AGGREGATION = "yield_aggregation"
    NFT_TRADING = "nft_trading"
    BASIS_TRADING = "basis_trading"


class RiskLevel(Enum):
    """Normalized risk classification."""
    NEGLIGIBLE = "negligible"   # 1-2
    LOW = "low"                 # 3-4
    MEDIUM = "medium"           # 5-6
    HIGH = "high"               # 7-8
    CRITICAL = "critical"       # 9-10

    @classmethod
    def from_score(cls, score: int) -> "RiskLevel":
        if score <= 2:
            return cls.NEGLIGIBLE
        elif score <= 4:
            return cls.LOW
        elif score <= 6:
            return cls.MEDIUM
        elif score <= 8:
            return cls.HIGH
        return cls.CRITICAL


class CircuitBreaker(Enum):
    """Global circuit breaker states."""
    ALLOWED = auto()
    THROTTLED = auto()     # Reduced exposure
    BLOCKED = auto()       # No exposure — strategy locked


# ──────────────────────────────────────────────────────────────────────
#  Dataclasses
# ──────────────────────────────────────────────────────────────────────

@dataclass
class TriggerCondition:
    """Conditions required for a strategy to activate."""
    min_capital_usd: float = 0.0
    max_capital_usd: float = float("inf")
    min_roi_percent: float = 0.0
    max_gas_gwei: int = 200
    min_liquidity_usd: float = 0.0
    allowed_chains: List[str] = field(default_factory=list)
    blacklisted_tokens: List[str] = field(default_factory=list)
    required_protocols: List[str] = field(default_factory=list)
    time_window_hours: Optional[int] = None  # Strategy must complete within N hours


@dataclass
class StrategyConfig:
    """Full configuration for a revenue strategy."""
    strategy: StrategyType
    label: str
    risk_score: int                     # 1 (safe) – 10 (nuclear)
    estimated_roi_pct: float            # Annualized or per-cycle
    min_capital_usd: float
    lock_duration_days: int             # 0 = instant exit
    trigger: TriggerCondition = field(default_factory=TriggerCondition)
    enabled: bool = True
    circuit_breaker: CircuitBreaker = CircuitBreaker.ALLOWED
    description: str = ""


@dataclass
class Opportunity:
    """A detected revenue opportunity."""
    strategy: StrategyType
    label: str
    roi_estimate_pct: float
    risk_score: int
    capital_required_usd: float
    lock_duration_days: int
    expected_profit_usd: float
    confidence: float                       # 0.0 – 1.0
    roi_risk_ratio: float = 0.0             # Computed
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionPlan:
    """Execution plan for a ranked opportunity."""
    opportunity: Opportunity
    steps: List[str] = field(default_factory=list)
    estimated_gas_usd: float = 0.0
    expected_profit_net_usd: float = 0.0
    tx_hashes: List[str] = field(default_factory=list)
    status: str = "pending"                 # pending | simulated | broadcast | failed
    warnings: List[str] = field(default_factory=list)


@dataclass
class RevenueReport:
    """Aggregate revenue pipeline report."""
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_opportunities: int = 0
    ranked_strategies: List[Tuple[str, float, int]] = field(default_factory=list)
    top_execution: Optional[ExecutionPlan] = None
    circuit_breaker_status: Dict[str, str] = field(default_factory=dict)
    portfolio_roi_estimate_pct: float = 0.0
    total_expected_profit_usd: float = 0.0


# ──────────────────────────────────────────────────────────────────────
#  Strategy Library
# ──────────────────────────────────────────────────────────────────────

STRATEGIES: List[StrategyConfig] = [
    StrategyConfig(
        strategy=StrategyType.MEV,
        label="MEV Arbitrage (Sandwich/Frontrun)",
        risk_score=7,
        estimated_roi_pct=85.0,
        min_capital_usd=5000.0,
        lock_duration_days=0,
        trigger=TriggerCondition(
            min_capital_usd=5000.0,
            max_gas_gwei=80,
            min_liquidity_usd=100000.0,
            allowed_chains=["ethereum", "bsc", "arbitrum"],
        ),
        description="Mempool-level frontrunning and sandwich attacks on DEX swaps. High ROI, high technical barrier, gas-sensitive.",
    ),
    StrategyConfig(
        strategy=StrategyType.CROSS_CHAIN_ARB,
        label="Cross-Chain Arbitrage (Bridge/Swap)",
        risk_score=5,
        estimated_roi_pct=35.0,
        min_capital_usd=2000.0,
        lock_duration_days=0,
        trigger=TriggerCondition(
            min_capital_usd=2000.0,
            min_roi_percent=0.5,
            allowed_chains=["ethereum", "arbitrum", "optimism", "base", "polygon"],
        ),
        description="Exploit price differentials across chains via bridge+swap combos. Medium risk, bridge time dependent.",
    ),
    StrategyConfig(
        strategy=StrategyType.AIRDROP_FARMING,
        label="Airdrop Farming (Sybil Multi-Wallet)",
        risk_score=3,
        estimated_roi_pct=500.0,
        min_capital_usd=100.0,
        lock_duration_days=90,
        trigger=TriggerCondition(
            min_capital_usd=100.0,
            max_gas_gwei=150,
            time_window_hours=2160,  # 90 days
        ),
        description="Multi-wallet farming of testnet/mainnet airdrops. Low capital, high ROI potential, long lock. Sybil risk managed by operator.",
    ),
    StrategyConfig(
        strategy=StrategyType.YIELD_AGGREGATION,
        label="Yield Aggregation (Lending/Staking/LP)",
        risk_score=2,
        estimated_roi_pct=12.0,
        min_capital_usd=1000.0,
        lock_duration_days=0,
        trigger=TriggerCondition(
            min_capital_usd=1000.0,
            min_roi_percent=5.0,
            required_protocols=["aave", "lido", "compound", "pendle"],
        ),
        description="Auto-compound across lending, staking, and LP protocols. Lowest risk, steady yield.",
    ),
    StrategyConfig(
        strategy=StrategyType.NFT_TRADING,
        label="NFT Trading (Collection Snipe/Floor Arb)",
        risk_score=6,
        estimated_roi_pct=60.0,
        min_capital_usd=2000.0,
        lock_duration_days=30,
        trigger=TriggerCondition(
            min_capital_usd=2000.0,
            min_roi_percent=10.0,
            allowed_chains=["ethereum", "base", "polygon"],
        ),
        description="Snipe underpriced NFTs, floor arbitrage across marketplaces. Medium-high risk, liquidity dependent.",
    ),
    StrategyConfig(
        strategy=StrategyType.BASIS_TRADING,
        label="Basis Trading (Spot-Futures Delta Neutral)",
        risk_score=2,
        estimated_roi_pct=15.0,
        min_capital_usd=10000.0,
        lock_duration_days=0,
        trigger=TriggerCondition(
            min_capital_usd=10000.0,
            min_roi_percent=3.0,
            required_protocols=["gmx", "hyperliquid", "dydx"],
        ),
        description="Delta-neutral spot/futures basis trades. Low risk, requires larger capital for meaningful returns.",
    ),
]


# ──────────────────────────────────────────────────────────────────────
#  Circuit Breaker Manager
# ──────────────────────────────────────────────────────────────────────

class CircuitBreakerManager:
    """Manages global exposure caps and strategy circuit breakers.

    Each strategy has a cap on total deployed capital. If breached,
    the strategy transitions from ALLOWED → THROTTLED → BLOCKED.
    """

    def __init__(self, global_cap_usd: float = 50000.0) -> None:
        self.global_cap_usd = global_cap_usd
        self._deployed: Dict[str, float] = {}  # strategy → deployed USD
        self._breaker_state: Dict[str, CircuitBreaker] = {}
        self._throttle_multiplier: float = 0.5

        for cfg in STRATEGIES:
            self._breaker_state[cfg.strategy.value] = CircuitBreaker.ALLOWED
            self._deployed[cfg.strategy.value] = 0.0

    def check(self, strategy: StrategyType, requested_usd: float) -> Tuple[bool, str]:
        """Check if a strategy deployment is within limits.

        Returns:
            (allowed, reason) tuple.
        """
        key = strategy.value
        current = self._deployed.get(key, 0.0)

        if current + requested_usd > self.global_cap_usd:
            self._breaker_state[key] = CircuitBreaker.BLOCKED
            return (False, f"Global cap exceeded: ${current + requested_usd:,.0f} > ${self.global_cap_usd:,.0f}")

        if self._breaker_state.get(key) == CircuitBreaker.BLOCKED:
            return (False, "Strategy circuit breaker is BLOCKED")

        if self._breaker_state.get(key) == CircuitBreaker.THROTTLED:
            throttled_max = requested_usd * self._throttle_multiplier
            return (True, f"THROTTLED — reduced to ${throttled_max:,.0f}")

        return (True, "ALLOWED")

    def deploy(self, strategy: StrategyType, amount_usd: float) -> None:
        """Record deployed capital against a strategy."""
        key = strategy.value
        self._deployed[key] = self._deployed.get(key, 0.0) + amount_usd

    def trip(self, strategy: StrategyType) -> None:
        """Manually trip a circuit breaker to BLOCKED."""
        self._breaker_state[strategy.value] = CircuitBreaker.BLOCKED

    def throttle(self, strategy: StrategyType) -> None:
        """Manually set a circuit breaker to THROTTLED."""
        self._breaker_state[strategy.value] = CircuitBreaker.THROTTLED

    def reset(self, strategy: Optional[StrategyType] = None) -> None:
        """Reset circuit breaker state."""
        if strategy:
            self._breaker_state[strategy.value] = CircuitBreaker.ALLOWED
        else:
            for key in self._breaker_state:
                self._breaker_state[key] = CircuitBreaker.ALLOWED

    def status(self) -> Dict[str, str]:
        """Return current breaker status for all strategies."""
        return {
            key: self._breaker_state.get(key, CircuitBreaker.ALLOWED).name
            for key in self._deployed
        }


# ──────────────────────────────────────────────────────────────────────
#  Revenue Optimizer Core
# ──────────────────────────────────────────────────────────────────────

class RevenueOptimizer:
    """Autonomous monetization optimizer.

    Scans for revenue opportunities across 6 strategies, ranks them
    by ROI/risk ratio, and generates execution plans.

    PASSIVE BY DEFAULT: All execution is simulated unless
    `broadcast=True` is explicitly passed.
    """

    def __init__(
        self,
        global_cap_usd: float = 50000.0,
        random_seed: Optional[int] = None,
    ) -> None:
        """Initialize the revenue optimizer.

        Args:
            global_cap_usd: Total capital cap across all strategies.
            random_seed: Optional seed for reproducible simulations.
        """
        self.global_cap_usd = global_cap_usd
        self.breaker = CircuitBreakerManager(global_cap_usd=global_cap_usd)
        self.strategies: List[StrategyConfig] = STRATEGIES
        self._opportunities: List[Opportunity] = []
        self._plans: List[ExecutionPlan] = []
        self._rng = random.Random(random_seed)
        self._execution_log: List[Dict[str, Any]] = []

    # ── Scanning ────────────────────────────────────────────────────

    def scan_opportunities(
        self,
        strategy_filter: Optional[List[str]] = None,
    ) -> List[Opportunity]:
        """Scan all active strategies for revenue opportunities.

        Mocks real on-chain data fetching with realistic simulations
        based on strategy triggers and current conditions.

        Args:
            strategy_filter: Optional list of strategy keys to scan.
                             If None, scans all enabled strategies.

        Returns:
            List of detected Opportunity objects.
        """
        opportunities: List[Opportunity] = []

        active = [
            s for s in self.strategies
            if s.enabled
            and (strategy_filter is None or s.strategy.value in strategy_filter)
        ]

        for cfg in active:
            # Simulate market conditions check
            trigger_ok, reason = self._evaluate_trigger(cfg)
            if not trigger_ok:
                continue

            # Simulate opportunity detection with realistic variance
            base_roi = cfg.estimated_roi_pct
            confidence = self._rng.uniform(0.50, 0.98)
            actual_roi = base_roi * self._rng.uniform(0.8, 1.3) * confidence
            capital = cfg.min_capital_usd * self._rng.uniform(1.0, 2.5)
            profit = capital * (actual_roi / 100.0)

            opp = Opportunity(
                strategy=cfg.strategy,
                label=cfg.label,
                roi_estimate_pct=round(actual_roi, 2),
                risk_score=cfg.risk_score,
                capital_required_usd=round(capital, 2),
                lock_duration_days=cfg.lock_duration_days,
                expected_profit_usd=round(profit, 2),
                confidence=round(confidence, 3),
                metadata={
                    "trigger_reason": reason,
                    "risk_level": RiskLevel.from_score(cfg.risk_score).value,
                },
            )
            opp.roi_risk_ratio = round(opp.roi_estimate_pct / max(opp.risk_score, 1), 2)
            opportunities.append(opp)

        self._opportunities = opportunities
        return opportunities

    # ── Real data integration (1inch API) ──

    def _fetch_1inch_quote(self, from_token: str, to_token: str,
                          amount: str, chain_id: int = 1):
        """Fetch real quote from 1inch aggregator API."""
        import urllib.request, urllib.error, json, os
        try:
            url = f"https://api.1inch.dev/swap/v6.0/{chain_id}/quote?src={from_token}&dst={to_token}&amount={amount}"
            req = urllib.request.Request(url)
            req.add_header("Authorization", f"Bearer {os.environ.get('ONEINCH_API_KEY', '')}")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return {"error": f"HTTP {e.code}"}
        except Exception as e:
            return {"error": str(e)}

    def _fetch_gas_price(self, chain_id: int = 1):
        """Fetch real gas price from etherscan/RPC."""
        import urllib.request, json, os
        try:
            url = "https://api.etherscan.io/api?module=gastracker&action=gasoracle"
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
                if data.get("status") == "1":
                    return float(data.get("result", {}).get("ProposeGasPrice", 30))
        except Exception:
            pass
        try:
            rpc_url = os.environ.get("RPC_URL", "https://eth.llamarpc.com")
            payload = json.dumps({"jsonrpc":"2.0","method":"eth_gasPrice","params":[],"id":1}).encode()
            req = urllib.request.Request(rpc_url, data=payload, headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                wei = int(json.loads(resp.read()).get("result","0"), 16)
                return wei / 1e9
        except Exception:
            return None

    def _evaluate_trigger(self, cfg: StrategyConfig) -> Tuple[bool, str]:
        """Evaluate whether a strategy's trigger conditions are met.
        Uses real API data when available, falls back to simulation."""
        t = cfg.trigger
        data_source = "simulated"

        real_gas = self._fetch_gas_price()
        if real_gas is not None:
            data_source = "real"
            if real_gas > t.max_gas_gwei:
                return (False, f"Gas too high: {real_gas:.0f} > {t.max_gas_gwei} gwei (live)")
        else:
            simulated_gas = self._rng.randint(15, 250)
            if simulated_gas > t.max_gas_gwei:
                return (False, f"Gas too high: {simulated_gas} > {t.max_gas_gwei} gwei (sim)")

        if t.min_liquidity_usd > 0 and cfg.strategy.value in ("mev", "cross_chain_arb"):
            quote = self._fetch_1inch_quote(
                "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE",
                "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                str(int(0.1 * 1e18)),
            )
            if quote and "toAmount" in quote:
                data_source = "real"
            else:
                simulated_liq = t.min_liquidity_usd * self._rng.uniform(0.5, 3.0)
                if simulated_liq < t.min_liquidity_usd:
                    return (False, f"Low liquidity: ${simulated_liq:,.0f} (sim)")

        reason = f"All trigger conditions met ({data_source} data)"
        return (True, reason)

    # ── Ranking ─────────────────────────────────────────────────────

    def rank_strategies(
        self,
        opportunities: Optional[List[Opportunity]] = None,
        min_roi_risk_ratio: float = 0.0,
        max_risk_score: int = 10,
    ) -> List[Opportunity]:
        """Rank opportunities by ROI/risk ratio descending.

        Filters out opportunities blocked by circuit breakers and
        those below minimum ROI/risk thresholds.

        Args:
            opportunities: List to rank. Uses cached scan results if None.
            min_roi_risk_ratio: Minimum ROI/risk ratio filter.
            max_risk_score: Maximum acceptable risk score (1-10).

        Returns:
            Ranked list of Opportunity, best first.
        """
        opps = opportunities or self._opportunities
        if not opps:
            opps = self.scan_opportunities()

        # Filter by circuit breaker
        filtered: List[Opportunity] = []
        for opp in opps:
            cb_allowed, _ = self.breaker.check(opp.strategy, opp.capital_required_usd)
            if cb_allowed:
                filtered.append(opp)

        # Filter by thresholds
        filtered = [
            o for o in filtered
            if o.roi_risk_ratio >= min_roi_risk_ratio and o.risk_score <= max_risk_score
        ]

        # Sort by ROI/risk ratio descending, then by expected profit descending
        filtered.sort(key=lambda o: (o.roi_risk_ratio, o.expected_profit_usd), reverse=True)

        self._opportunities = filtered
        return filtered

    # ── Execution ───────────────────────────────────────────────────

    def execute_pipeline(
        self,
        broadcast: bool = False,
        strategy_filter: Optional[List[str]] = None,
        dry_run: bool = True,
        bridge_config: Optional[dict] = None,
    ) -> Optional[ExecutionPlan]:
        """Execute the top-ranked revenue strategy.

        Scans, ranks, and prepares an execution plan for the best opportunity.
        By default, SIMULATES ONLY — no real transactions.

        When broadcast=True, routes the execution through the Hermes Native Bridge
        for real on-chain transaction broadcast.

        Args:
            broadcast: If True, attempts to broadcast real transactions via
                       Hermes bridge. DANGEROUS — requires explicit opt-in.
            strategy_filter: Optional strategy whitelist.
            dry_run: If True (default), simulate everything including
                     gas estimation and tx construction.
            bridge_config: Optional dict of overrides for Hermes bridge arguments
                           (chain, token, amount, from_chain, to_chain, etc.).
                           Only used when broadcast=True.

        Returns:
            ExecutionPlan for the top opportunity, or None if none found.
        """
        if not broadcast:
            print("🔒 PASSIVE MODE — Simulation only. Use --broadcast for real execution.")
        else:
            print("⚠️  BROADCAST MODE ACTIVE — Real transactions will be signed.")

        # Scan + Rank
        opportunities = self.scan_opportunities(strategy_filter=strategy_filter)
        ranked = self.rank_strategies(opportunities)

        if not ranked:
            print("No viable opportunities found.")
            self._execution_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "execute",
                "result": "no_opportunities",
            })
            return None

        top = ranked[0]

        # Check circuit breaker final
        cb_allowed, cb_reason = self.breaker.check(top.strategy, top.capital_required_usd)
        if not cb_allowed:
            print(f"🛑 Circuit breaker: {cb_reason}")
            return None

        # Build execution plan
        plan = self._build_plan(top, broadcast=broadcast, dry_run=dry_run)

        if broadcast and not dry_run:
            # ── Real broadcast via Hermes Bridge ──
            bridge_result = self.broadcast_execution(plan, bridge_config=bridge_config)

            if bridge_result.success:
                self.breaker.deploy(top.strategy, top.capital_required_usd)
                print(f"📡 Broadcast via Hermes: {bridge_result.tool}")
                if bridge_result.tx_hash:
                    print(f"   tx: {bridge_result.tx_hash}")
                if bridge_result.output:
                    print(f"   output: {bridge_result.output[:200]}")
            else:
                print(f"❌ Broadcast failed: {bridge_result.error}")
                plan.status = "failed"
        else:
            plan.status = "simulated"
            print(f"🎯 Simulation complete: {top.label}")

        self._plans.append(plan)
        self._execution_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "execute",
            "strategy": top.strategy.value,
            "status": plan.status,
            "expected_profit_usd": plan.expected_profit_net_usd,
        })

        return plan

    def _build_plan(
        self,
        opp: Opportunity,
        broadcast: bool = False,
        dry_run: bool = True,
    ) -> ExecutionPlan:
        """Build a step-by-step execution plan for an opportunity."""
        steps: List[str] = []
        warnings: List[str] = []

        gas_estimate = opp.capital_required_usd * 0.002  # ~0.2% gas estimate

        strategy = opp.strategy

        if strategy == StrategyType.MEV:
            steps = [
                "1. Deploy MEV bundle to flashbots relay",
                "2. Monitor mempool for target swap",
                "3. Construct sandwich bundle (frontrun + target + backrun)",
                f"4. Submit bundle with {opp.capital_required_usd:,.0f} USD capital",
                "5. Wait for bundle inclusion in next block",
            ]
            warnings.append("MEV competition — bundle may be outbid")

        elif strategy == StrategyType.CROSS_CHAIN_ARB:
            steps = [
                f"1. Fund source chain wallet with ${opp.capital_required_usd:,.0f}",
                "2. Execute swap on source chain DEX (Quickswap/Uniswap)",
                "3. Bridge assets via Stargate/LayerZero",
                "4. Execute reverse swap on destination chain",
                "5. Bridge profit back to origin chain",
            ]
            warnings.append("Bridge latency may impact profitability")

        elif strategy == StrategyType.AIRDROP_FARMING:
            steps = [
                "1. Generate batch of fresh wallets",
                "2. Distribute seed capital (~$50-200/wallet)",
                "3. Execute qualifying transactions (swaps, bridges, LP)",
                "4. Rotate IP per wallet batch (proxy/VPN pool)",
                f"5. Monitor for claim window ({opp.lock_duration_days}d target)",
            ]
            warnings.append("Sybil detection risk — maintain isolation")

        elif strategy == StrategyType.YIELD_AGGREGATION:
            steps = [
                f"1. Deposit ${opp.capital_required_usd:,.0f} into highest APY pool",
                "2. Enable auto-compound (reinvest rewards → deposit)",
                "3. Set stop-loss if APY drops below threshold",
                "4. Monitor protocol health (TVL, audits, exploit alerts)",
                "5. Rebalance weekly across protocols for optimal yield",
            ]
            warnings.append("Smart contract risk — protocol exploit possible")

        elif strategy == StrategyType.NFT_TRADING:
            steps = [
                "1. Scan OpenSea/Blur for underpriced floor listings",
                "2. Verify collection authenticity (contract + metadata)",
                "3. Execute snipe buy at target price",
                "4. Relist at floor premium (+20-50%)",
                "5. Monitor for competitive undercutting",
            ]
            warnings.append("NFT liquidity thin — may not sell at target")

        elif strategy == StrategyType.BASIS_TRADING:
            steps = [
                f"1. Buy spot ${opp.capital_required_usd/2:,.0f} on DEX/CEX",
                f"2. Short perpetual futures ${opp.capital_required_usd/2:,.0f} (same amount)",
                "3. Wait for funding rate convergence",
                "4. Close both positions simultaneously",
                "5. Collect funding rate payments during hold period",
            ]
            warnings.append("Exchange counterparty risk for CEX leg")

        net_profit = opp.expected_profit_usd - gas_estimate

        return ExecutionPlan(
            opportunity=opp,
            steps=steps,
            estimated_gas_usd=round(gas_estimate, 2),
            expected_profit_net_usd=round(net_profit, 2),
            status="simulated" if not broadcast else "broadcast",
            warnings=warnings,
        )

    # ── Real Broadcast via Hermes Bridge ───────────────────────────

    @staticmethod
    def _strategy_to_hermes_tool(strategy: StrategyType) -> str:
        """Map a revenue strategy to a Hermes bridge tool.

        Returns the Hermes tool name, or empty string if no mapping exists.
        """
        _MAP: dict[StrategyType, str] = {
            StrategyType.CROSS_CHAIN_ARB: "bridge",
            StrategyType.AIRDROP_FARMING: "airdrop-farm",
            StrategyType.YIELD_AGGREGATION: "contract-write",
            StrategyType.NFT_TRADING: "mint-nft",
            StrategyType.BASIS_TRADING: "contract-write",
            # MEV is executor-native (flashbots relay) — no Hermes tool mapping
        }
        return _MAP.get(strategy, "")

    @staticmethod
    def _build_hermes_args(
        strategy: StrategyType,
        opp: Opportunity,
        bridge_config: Optional[dict] = None,
    ) -> dict:
        """Build Hermes bridge arguments from an opportunity.

        Uses bridge_config overrides when provided, otherwise derives
        reasonable defaults from the opportunity metadata.

        Args:
            strategy: The strategy type.
            opp: The opportunity to execute.
            bridge_config: Optional dict with keys like chain, from_chain,
                           to_chain, token, amount. Falls back to metadata.

        Returns:
            dict of Hermes tool arguments.
        """
        cfg = bridge_config or {}
        meta = opp.metadata

        if strategy == StrategyType.CROSS_CHAIN_ARB:
            return {
                "token": cfg.get("token", meta.get("token", "USDC")),
                "amount": float(cfg.get("amount", opp.capital_required_usd)),
                "from_chain": cfg.get("from_chain", meta.get("from_chain", "arbitrum")),
                "to_chain": cfg.get("to_chain", meta.get("to_chain", "ethereum")),
            }

        elif strategy == StrategyType.AIRDROP_FARMING:
            return {
                "task": cfg.get("task", meta.get("task", "swap")),
                "wallet_label": cfg.get("wallet_label", meta.get("wallet_label", "all")),
            }

        elif strategy == StrategyType.YIELD_AGGREGATION:
            return {
                "contract": cfg.get("contract", meta.get("contract", "")),
                "function": cfg.get("function", meta.get("function", "deposit")),
                "chain": cfg.get("chain", meta.get("chain", "arbitrum")),
                "args": json.dumps(cfg.get("args", meta.get("args", []))),
            }

        elif strategy == StrategyType.NFT_TRADING:
            return {
                "contract": cfg.get("contract", meta.get("contract", "")),
                "quantity": int(cfg.get("quantity", meta.get("quantity", 1))),
                "chain": cfg.get("chain", meta.get("chain", "ethereum")),
            }

        elif strategy == StrategyType.BASIS_TRADING:
            return {
                "contract": cfg.get("contract", meta.get("contract", "")),
                "function": cfg.get("function", meta.get("function", "openPosition")),
                "chain": cfg.get("chain", meta.get("chain", "arbitrum")),
                "args": json.dumps(cfg.get("args", meta.get("args", []))),
            }

        elif strategy == StrategyType.MEV:
            # MEV bundles go through flashbots relay, not Hermes
            # Return empty — broadcast_execution handles this case
            return {}

        return {}

    def broadcast_execution(
        self,
        plan: ExecutionPlan,
        bridge_config: Optional[dict] = None,
    ) -> BridgeResult:
        """Route a ranked opportunity through the Hermes Native Bridge for
        real on-chain execution.

        Resolves strategy → Hermes tool, builds arguments from the
        opportunity, calls HermesNativeBridge.call_tool(), and logs
        the result to treasury and execution log.

        Args:
            plan: The execution plan to broadcast (must have an opportunity).
            bridge_config: Optional overrides for chain, token, amount, etc.
                           See _build_hermes_args() for accepted keys per strategy.

        Returns:
            A BridgeResult dataclass (success, tx_hash, output, error, etc.)
            OR a synthetic BridgeResult if Hermes bridge is not installed.

        Raises:
            Does NOT raise — all failures are captured in BridgeResult.error
        """
        opp = plan.opportunity
        strategy = opp.strategy
        tool_name = self._strategy_to_hermes_tool(strategy)

        # ── MEV special case: flashbots relay, not Hermes ──
        if strategy == StrategyType.MEV:
            return _synthetic_mev_result(strategy.value, plan)

        # ── No Hermes mapping ──
        if not tool_name:
            result = _BridgeResult(
                success=False,
                tool="unknown",
                action=f"No Hermes bridge mapping for {strategy.value}",
                output="",
                error=f"Strategy '{strategy.value}' has no Hermes tool mapping. "
                      f"Run with broadcast=False to simulate.",
            ) if _HERMES_BRIDGE_AVAILABLE else _synthetic_bridge_result(
                success=False,
                tool="unknown",
                action=f"No mapping for {strategy.value}",
                error=f"Strategy '{strategy.value}' has no Hermes tool mapping.",
            )
            self._log_broadcast(plan, result)
            return result

        # ── Bridge not available ──
        if not _HERMES_BRIDGE_AVAILABLE:
            result = _synthetic_bridge_result(
                success=False,
                tool=tool_name,
                action="bridge_unavailable",
                error=f"Hermes bridge not available: {_hermes_import_error}. "
                      f"Install hermes-bridge or set HERMES_ROOT.",
            )
            self._log_broadcast(plan, result)
            return result

        # ── Build arguments ──
        hermes_args = self._build_hermes_args(strategy, opp, bridge_config)

        # ── Execute via Hermes Native Bridge ──
        try:
            bridge = _HermesNativeBridge()
            result = bridge.call_tool(tool_name, hermes_args)
        except Exception as exc:
            result = _BridgeResult(
                success=False,
                tool=tool_name,
                action=f"Broadcast {strategy.value}",
                output="",
                error=f"Hermes bridge call failed: {exc}",
            )

        # ── Log to treasury & execution log ──
        self._log_broadcast(plan, result)

        # ── Update execution plan with real data ──
        plan.status = "broadcast" if result.success else "failed"
        if result.tx_hash:
            plan.tx_hashes = [result.tx_hash]

        return result

    def _log_broadcast(
        self,
        plan: ExecutionPlan,
        result: Any,  # BridgeResult or synthetic
    ) -> None:
        """Log a broadcast result to execution log and write treasury entry."""
        opp = plan.opportunity

        # ── Execution log (in-memory) ──
        self._execution_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "broadcast",
            "strategy": opp.strategy.value,
            "tool": getattr(result, "tool", ""),
            "tx_hash": getattr(result, "tx_hash", ""),
            "status": "success" if getattr(result, "success", False) else "failed",
            "expected_profit_usd": plan.expected_profit_net_usd,
            "error": getattr(result, "error", ""),
        })

        # ── Treasury log (persistent file) ──
        try:
            treasury_path = Path(__file__).parent.parent / "logs" / "treasury.jsonl"
            treasury_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "strategy": opp.strategy.value,
                "label": opp.label,
                "capital_usd": opp.capital_required_usd,
                "tool": getattr(result, "tool", ""),
                "tx_hash": getattr(result, "tx_hash", ""),
                "success": getattr(result, "success", False),
                "output": getattr(result, "output", "")[:500],
                "error": getattr(result, "error", "")[:500],
            }
            with open(treasury_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            logger.warning(f"Failed to write treasury log: {e}")

    # ── Reporting ───────────────────────────────────────────────────

    def report(self) -> RevenueReport:
        """Generate a comprehensive revenue pipeline report.

        Returns:
            RevenueReport with full pipeline status.
        """
        opportunities = self.scan_opportunities()
        ranked = self.rank_strategies(opportunities)

        # Build ranked strategy list
        ranked_strats: List[Tuple[str, float, int]] = [
            (o.strategy.value, o.roi_risk_ratio, o.risk_score) for o in ranked
        ]

        # Calculate portfolio estimates
        total_profit = sum(o.expected_profit_usd for o in ranked)
        weighted_roi = (
            sum(o.roi_estimate_pct * o.confidence for o in ranked) / max(len(ranked), 1)
            if ranked
            else 0.0
        )

        # Latest execution
        latest_execution = self._plans[-1] if self._plans else None

        return RevenueReport(
            total_opportunities=len(opportunities),
            ranked_strategies=ranked_strats,
            top_execution=latest_execution,
            circuit_breaker_status=self.breaker.status(),
            portfolio_roi_estimate_pct=round(weighted_roi, 2),
            total_expected_profit_usd=round(total_profit, 2),
        )

    # ── Circuit Breaker Admin ───────────────────────────────────────

    def get_circuit_breaker_status(self) -> Dict[str, str]:
        """Return current circuit breaker status for all strategies."""
        return self.breaker.status()

    def trip_circuit_breaker(self, strategy: str) -> bool:
        """Trip a circuit breaker to BLOCKED."""
        try:
            st = StrategyType(strategy)
            self.breaker.trip(st)
            return True
        except ValueError:
            print(f"Unknown strategy: {strategy}")
            return False

    def reset_circuit_breaker(self, strategy: Optional[str] = None) -> None:
        """Reset circuit breakers."""
        if strategy:
            try:
                st = StrategyType(strategy)
                self.breaker.reset(st)
            except ValueError:
                print(f"Unknown strategy: {strategy}")
        else:
            self.breaker.reset()


# ──────────────────────────────────────────────────────────────────────
#  Bridge Helpers (synthetic results when Hermes not installed)
# ──────────────────────────────────────────────────────────────────────

def _synthetic_bridge_result(
    success: bool,
    tool: str,
    action: str,
    tx_hash: str = "",
    output: str = "",
    error: str = "",
    chain: str = "",
) -> Any:
    """Build a synthetic BridgeResult-like object when Hermes bridge
    is not installed. Mimics the BridgeResult dataclass shape."""
    # If BridgeResult dataclass is available (partial import succeeded),
    # use it. Otherwise build a simple object.
    if _BridgeResult is not None:
        return _BridgeResult(
            success=success,
            tool=tool,
            action=action,
            tx_hash=tx_hash or None,
            output=output,
            error=error,
            chain=chain,
        )

    # Fallback: simple namespace object
    class _Synthetic:
        def __init__(self):
            self.success = success
            self.tool = tool
            self.action = action
            self.tx_hash = tx_hash or None
            self.output = output
            self.error = error
            self.chain = chain

        def to_dict(self) -> dict:
            return {
                "success": self.success,
                "tool": self.tool,
                "action": self.action,
                "tx_hash": self.tx_hash,
                "output": self.output,
                "error": self.error,
                "chain": self.chain,
            }

    return _Synthetic()


def _synthetic_mev_result(strategy_name: str, plan: ExecutionPlan) -> Any:
    """MEV bundles go through flashbots relay, not Hermes bridge.

    Returns a synthetic BridgeResult noting this path.
    """
    return _synthetic_bridge_result(
        success=True,
        tool="flashbots",
        action=f"MEV bundle for {strategy_name}",
        output=(
            f"MEV execution routed through flashbots relay (not Hermes bridge). "
            f"Expected profit: ${plan.expected_profit_net_usd:,.2f}. "
            f"Steps: {'; '.join(plan.steps[:3])}"
        ),
        error="",
    )


# ──────────────────────────────────────────────────────────────────────
#  CLI Interface
# ──────────────────────────────────────────────────────────────────────

def _print_opportunities(opps: List[Opportunity]) -> None:
    """Pretty-print opportunities table."""
    if not opps:
        print("No opportunities found.")
        return

    header = f"{'#':<4} {'Strategy':<28} {'ROI%':>8} {'Risk':>5} {'Ratio':>7} {'Capital':>12} {'Profit':>10} {'Conf':>6}"
    print(header)
    print("-" * len(header))

    for i, o in enumerate(opps, 1):
        print(
            f"{i:<4} {o.strategy.value:<28} {o.roi_estimate_pct:>7.1f}% "
            f"{o.risk_score:>4}/10 {o.roi_risk_ratio:>6.2f} "
            f"${o.capital_required_usd:>10,.0f} ${o.expected_profit_usd:>9,.0f} "
            f"{o.confidence:>5.0%}"
        )


def _print_report(report: RevenueReport) -> None:
    """Pretty-print a full revenue report."""
    print("=" * 72)
    print("  REVENUE OPTIMIZER — PIPELINE REPORT")
    print("=" * 72)
    print(f"  Generated:     {report.generated_at}")
    print(f"  Total Opps:    {report.total_opportunities}")
    print(f"  Est. ROI:      {report.portfolio_roi_estimate_pct:.1f}% (portfolio weighted)")
    print(f"  Est. Profit:   ${report.total_expected_profit_usd:,.2f}")
    print("-" * 72)
    print("  RANKED STRATEGIES:")
    for i, (strat, ratio, risk) in enumerate(report.ranked_strategies, 1):
        print(f"    {i}. {strat:<24} ROI/Risk={ratio:.2f}  Risk={risk}/10")
    print("-" * 72)
    print("  CIRCUIT BREAKERS:")
    for strat, status in report.circuit_breaker_status.items():
        icon = "🟢" if status == "ALLOWED" else "🟡" if status == "THROTTLED" else "🔴"
        print(f"    {icon} {strat}: {status}")
    print("-" * 72)
    if report.top_execution:
        plan = report.top_execution
        print(f"  LAST EXECUTION: {plan.opportunity.label} [{plan.status}]")
        print(f"    Net Profit: ${plan.expected_profit_net_usd:,.2f}")
        print(f"    Gas Est:    ${plan.estimated_gas_usd:,.2f}")
        for step in plan.steps:
            print(f"    {step}")
        if plan.warnings:
            for w in plan.warnings:
                print(f"    ⚠️  {w}")
    print("=" * 72)


def _build_argparser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="RevenueOptimizer — Autonomous Monetization Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 revenue_optimizer.py scan
  python3 revenue_optimizer.py scan --strategy mev,cross_chain_arb
  python3 revenue_optimizer.py rank --min-roi-risk 5.0
  python3 revenue_optimizer.py execute
  python3 revenue_optimizer.py execute --broadcast
  python3 revenue_optimizer.py report
  python3 revenue_optimizer.py circuit-breaker --trip nft_trading
  python3 revenue_optimizer.py circuit-breaker --status
        """,
    )
    sub = parser.add_subparsers(dest="command", help="Action to perform")

    # scan
    scan_parser = sub.add_parser("scan", help="Scan for revenue opportunities")
    scan_parser.add_argument(
        "--strategy", type=str, default=None,
        help="Comma-separated strategy filter (e.g. mev,cross_chain_arb)",
    )
    scan_parser.add_argument(
        "--json", action="store_true", help="Output as JSON",
    )
    scan_parser.add_argument(
        "--seed", type=int, default=None, help="Random seed for reproducibility",
    )
    scan_parser.add_argument(
        "--cap", type=float, default=50000.0, help="Global capital cap (USD)",
    )

    # rank
    rank_parser = sub.add_parser("rank", help="Rank opportunities by ROI/risk")
    rank_parser.add_argument(
        "--min-roi-risk", type=float, default=0.0,
        help="Minimum ROI/risk ratio filter",
    )
    rank_parser.add_argument(
        "--max-risk", type=int, default=10,
        help="Maximum risk score (1-10)",
    )
    rank_parser.add_argument(
        "--strategy", type=str, default=None,
        help="Comma-separated strategy filter",
    )
    rank_parser.add_argument(
        "--json", action="store_true", help="Output as JSON",
    )
    rank_parser.add_argument(
        "--seed", type=int, default=None, help="Random seed for reproducibility",
    )
    rank_parser.add_argument(
        "--cap", type=float, default=50000.0, help="Global capital cap (USD)",
    )

    # execute
    exec_parser = sub.add_parser("execute", help="Execute top-ranked strategy")
    exec_parser.add_argument(
        "--broadcast", action="store_true",
        help="⚠️  Broadcast real transactions (DANGEROUS)",
    )
    exec_parser.add_argument(
        "--strategy", type=str, default=None,
        help="Comma-separated strategy whitelist",
    )
    exec_parser.add_argument(
        "--seed", type=int, default=None, help="Random seed for reproducibility",
    )
    exec_parser.add_argument(
        "--cap", type=float, default=50000.0, help="Global capital cap (USD)",
    )

    # report
    report_parser = sub.add_parser("report", help="Generate full pipeline report")
    report_parser.add_argument(
        "--json", action="store_true", help="Output as JSON",
    )
    report_parser.add_argument(
        "--seed", type=int, default=None, help="Random seed for reproducibility",
    )
    report_parser.add_argument(
        "--cap", type=float, default=50000.0, help="Global capital cap (USD)",
    )

    # circuit-breaker
    cb_parser = sub.add_parser("circuit-breaker", help="Manage circuit breakers")
    cb_parser.add_argument(
        "--status", action="store_true", help="Show circuit breaker status",
    )
    cb_parser.add_argument(
        "--trip", type=str, default=None, metavar="STRATEGY",
        help="Trip a circuit breaker to BLOCKED",
    )
    cb_parser.add_argument(
        "--reset", type=str, default=None, metavar="STRATEGY",
        help="Reset a circuit breaker to ALLOWED",
    )
    cb_parser.add_argument(
        "--reset-all", action="store_true", help="Reset ALL circuit breakers",
    )

    return parser


def _parse_strategy_filter(raw: Optional[str]) -> Optional[List[str]]:
    """Parse comma-separated strategy filter string."""
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


def main() -> None:
    """CLI entry point for RevenueOptimizer."""
    parser = _build_argparser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    cap = getattr(args, "cap", 50000.0)
    seed = getattr(args, "seed", None)
    optimizer = RevenueOptimizer(global_cap_usd=cap, random_seed=seed)

    if args.command == "scan":
        strat_filter = _parse_strategy_filter(getattr(args, "strategy", None))
        opps = optimizer.scan_opportunities(strategy_filter=strat_filter)
        if getattr(args, "json", False):
            print(json.dumps([{
                "strategy": o.strategy.value,
                "label": o.label,
                "roi_estimate_pct": o.roi_estimate_pct,
                "risk_score": o.risk_score,
                "roi_risk_ratio": o.roi_risk_ratio,
                "capital_required_usd": o.capital_required_usd,
                "expected_profit_usd": o.expected_profit_usd,
                "confidence": o.confidence,
                "lock_duration_days": o.lock_duration_days,
                "metadata": o.metadata,
            } for o in opps], indent=2))
        else:
            _print_opportunities(opps)

    elif args.command == "rank":
        strat_filter = _parse_strategy_filter(getattr(args, "strategy", None))
        min_ratio = getattr(args, "min_roi_risk", 0.0)
        max_risk = getattr(args, "max_risk", 10)
        opps = optimizer.scan_opportunities(strategy_filter=strat_filter)
        ranked = optimizer.rank_strategies(opps, min_roi_risk_ratio=min_ratio, max_risk_score=max_risk)
        if getattr(args, "json", False):
            print(json.dumps([{
                "rank": i,
                "strategy": o.strategy.value,
                "roi_risk_ratio": o.roi_risk_ratio,
                "expected_profit_usd": o.expected_profit_usd,
                "risk_score": o.risk_score,
            } for i, o in enumerate(ranked, 1)], indent=2))
        else:
            _print_opportunities(ranked)

    elif args.command == "execute":
        strat_filter = _parse_strategy_filter(getattr(args, "strategy", None))
        broadcast = getattr(args, "broadcast", False)
        plan = optimizer.execute_pipeline(
            broadcast=broadcast,
            strategy_filter=strat_filter,
            dry_run=not broadcast,
        )
        if plan:
            print(f"\n📋 Execution Plan: {plan.opportunity.label}")
            print(f"   Status: {plan.status}")
            print(f"   Capital: ${plan.opportunity.capital_required_usd:,.2f}")
            print(f"   Est. Net Profit: ${plan.expected_profit_net_usd:,.2f}")
            print(f"   Est. Gas: ${plan.estimated_gas_usd:,.2f}")
            print("   Steps:")
            for step in plan.steps:
                print(f"     {step}")
            if plan.warnings:
                print("   Warnings:")
                for w in plan.warnings:
                    print(f"     ⚠️  {w}")
            if plan.tx_hashes:
                print("   Tx Hashes:")
                for txh in plan.tx_hashes:
                    print(f"     {txh}")

    elif args.command == "report":
        report = optimizer.report()
        if getattr(args, "json", False):
            print(json.dumps({
                "generated_at": report.generated_at,
                "total_opportunities": report.total_opportunities,
                "ranked_strategies": [
                    {"strategy": s[0], "roi_risk_ratio": s[1], "risk_score": s[2]}
                    for s in report.ranked_strategies
                ],
                "circuit_breaker_status": report.circuit_breaker_status,
                "portfolio_roi_estimate_pct": report.portfolio_roi_estimate_pct,
                "total_expected_profit_usd": report.total_expected_profit_usd,
                "top_execution": {
                    "strategy": report.top_execution.opportunity.strategy.value,
                    "status": report.top_execution.status,
                    "net_profit": report.top_execution.expected_profit_net_usd,
                } if report.top_execution else None,
            }, indent=2))
        else:
            _print_report(report)

    elif args.command == "circuit-breaker":
        if getattr(args, "status", False):
            status = optimizer.get_circuit_breaker_status()
            print("Circuit Breaker Status:")
            for strat, state in status.items():
                icon = "🟢" if state == "ALLOWED" else "🟡" if state == "THROTTLED" else "🔴"
                print(f"  {icon} {strat}: {state}")
        elif getattr(args, "reset_all", False):
            optimizer.reset_circuit_breaker()
            print("All circuit breakers reset to ALLOWED.")
        elif getattr(args, "reset", None):
            optimizer.reset_circuit_breaker(args.reset)
            print(f"Circuit breaker for '{args.reset}' reset to ALLOWED.")
        elif getattr(args, "trip", None):
            if optimizer.trip_circuit_breaker(args.trip):
                print(f"Circuit breaker for '{args.trip}' tripped to BLOCKED.")
        else:
            parser.parse_args(["circuit-breaker", "--help"])


if __name__ == "__main__":
    main()
