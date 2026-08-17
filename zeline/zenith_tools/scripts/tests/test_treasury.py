#!/usr/bin/env python3
"""
tests/test_treasury.py — Unit tests for Treasury class (V7)
Tests: Wallet/Transaction dataclasses, add_wallet, log_tx, total_balance,
pnl_report (daily/weekly/monthly), revenue_streams_report, auto_revenue_check.
"""

import sys
import os
import json
import unittest
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Ensure the tools module is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from tools.treasury import Treasury, Wallet, Transaction


class TestWalletDataclass(unittest.TestCase):
    """Test Wallet dataclass instantiation and defaults."""

    def test_create_wallet_defaults(self):
        w = Wallet(
            name="Main Hot",
            address="0xAbCdEf1234567890AbCdEf1234567890AbCdEf1234",
            tier="hot",
            chain="ethereum",
        )
        self.assertEqual(w.name, "Main Hot")
        self.assertEqual(w.tier, "hot")
        self.assertEqual(w.chain, "ethereum")
        self.assertEqual(w.balance_usd, 0.0)
        self.assertEqual(w.last_updated, "")

    def test_create_wallet_with_balance(self):
        w = Wallet(
            name="Cold Storage",
            address="0x1111222233334444555566667777888899990000",
            tier="cold",
            chain="ethereum",
            balance_usd=50000.0,
            last_updated="2026-07-01T12:00:00Z",
        )
        self.assertEqual(w.balance_usd, 50000.0)
        self.assertEqual(w.last_updated, "2026-07-01T12:00:00Z")

    def test_wallet_tiers(self):
        """All three wallet tiers should work."""
        tiers = ["hot", "warm", "cold"]
        wallets = []
        for tier in tiers:
            w = Wallet(name=f"{tier}-wallet", address=f"0x{tier}",
                       tier=tier, chain="ethereum", balance_usd=1000.0)
            wallets.append(w)
        self.assertEqual(len(wallets), 3)
        for w, expected in zip(wallets, tiers):
            self.assertEqual(w.tier, expected)


class TestTransactionDataclass(unittest.TestCase):
    """Test Transaction dataclass instantiation and defaults."""

    def test_create_transaction_required(self):
        tx = Transaction(
            id="tx-001",
            timestamp="2026-07-01T10:00:00Z",
            type="revenue",
            amount_usd=150.0,
            asset="ETH",
            chain="ethereum",
            category="mev",
        )
        self.assertEqual(tx.id, "tx-001")
        self.assertEqual(tx.type, "revenue")
        self.assertEqual(tx.amount_usd, 150.0)
        self.assertEqual(tx.asset, "ETH")
        self.assertEqual(tx.category, "mev")
        self.assertEqual(tx.project, "")
        self.assertEqual(tx.tx_hash, "")
        self.assertEqual(tx.notes, "")

    def test_create_transaction_full(self):
        tx = Transaction(
            id="tx-002",
            timestamp="2026-07-02T14:30:00Z",
            type="cost",
            amount_usd=25.0,
            asset="ETH",
            chain="ethereum",
            category="gas",
            project="bridge-arb",
            tx_hash="0xdeadbeef",
            notes="Bridge gas cost",
        )
        self.assertEqual(tx.project, "bridge-arb")
        self.assertEqual(tx.tx_hash, "0xdeadbeef")
        self.assertEqual(tx.notes, "Bridge gas cost")

    def test_transaction_types(self):
        """Revenue, cost, and transfer types all work."""
        types = ["revenue", "cost", "transfer"]
        for i, t_type in enumerate(types):
            tx = Transaction(
                id=f"tx-{i}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                type=t_type,
                amount_usd=100.0,
                asset="USDC",
                chain="ethereum",
                category="other",
            )
            self.assertEqual(tx.type, t_type)


class TestTreasuryAddWallet(unittest.TestCase):
    """Test Treasury add_wallet and wallet management."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.treasury = Treasury(data_dir=Path(self.tmpdir))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_add_single_wallet(self):
        w = Wallet(name="Hot 1", address="0xAAA", tier="hot",
                   chain="ethereum", balance_usd=5000.0)
        self.treasury.add_wallet(w)
        self.assertIn("0xAAA", self.treasury.wallets)
        self.assertEqual(self.treasury.wallets["0xAAA"].balance_usd, 5000.0)

    def test_add_multiple_wallets(self):
        for i in range(5):
            w = Wallet(name=f"Wallet-{i}", address=f"0xW{i:04d}",
                       tier="hot", chain="base", balance_usd=100.0 * i)
            self.treasury.add_wallet(w)
        self.assertEqual(len(self.treasury.wallets), 5)

    def test_add_wallet_persists(self):
        w = Wallet(name="Persist", address="0xPER",
                   tier="cold", chain="ethereum", balance_usd=99999.0)
        self.treasury.add_wallet(w)

        # Reload from disk
        t2 = Treasury(data_dir=Path(self.tmpdir))
        self.assertIn("0xPER", t2.wallets)
        self.assertEqual(t2.wallets["0xPER"].balance_usd, 99999.0)


class TestTreasuryLogTx(unittest.TestCase):
    """Test Treasury log_tx and transaction persistence."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.treasury = Treasury(data_dir=Path(self.tmpdir))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_log_single_tx(self):
        tx = Transaction(
            id="tx-a", timestamp="2026-07-01T10:00:00Z",
            type="revenue", amount_usd=200.0,
            asset="ETH", chain="ethereum", category="mev",
        )
        self.treasury.log_tx(tx)
        self.assertEqual(len(self.treasury.transactions), 1)

    def test_log_multiple_txs(self):
        for i in range(10):
            tx = Transaction(
                id=f"tx-{i}",
                timestamp=f"2026-07-{i+1:02d}T10:00:00Z",
                type="revenue" if i % 2 == 0 else "cost",
                amount_usd=float(i * 10),
                asset="USDC", chain="arbitrum",
                category="yield" if i % 2 == 0 else "gas",
            )
            self.treasury.log_tx(tx)
        self.assertEqual(len(self.treasury.transactions), 10)

    def test_log_tx_persists(self):
        tx = Transaction(
            id="tx-persist", timestamp="2026-07-05T12:00:00Z",
            type="revenue", amount_usd=500.0,
            asset="SOL", chain="solana", category="airdrop",
        )
        self.treasury.log_tx(tx)

        t2 = Treasury(data_dir=Path(self.tmpdir))
        self.assertEqual(len(t2.transactions), 1)
        self.assertEqual(t2.transactions[0].amount_usd, 500.0)


class TestTreasuryBalance(unittest.TestCase):
    """Test total_balance and total_value_by_tier."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.treasury = Treasury(data_dir=Path(self.tmpdir))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_total_balance_empty(self):
        self.assertEqual(self.treasury.total_balance(), 0.0)

    def test_total_balance_multiple(self):
        wallets = [
            Wallet("W1", "0xA", "hot", "eth", 1000.0),
            Wallet("W2", "0xB", "warm", "base", 2000.0),
            Wallet("W3", "0xC", "cold", "eth", 5000.0),
        ]
        for w in wallets:
            self.treasury.add_wallet(w)
        self.assertEqual(self.treasury.total_balance(), 8000.0)

    def test_total_value_by_tier(self):
        wallets = [
            Wallet("H1", "0xH1", "hot", "eth", 100.0),
            Wallet("H2", "0xH2", "hot", "base", 200.0),
            Wallet("W1", "0xW1", "warm", "arb", 500.0),
            Wallet("C1", "0xC1", "cold", "eth", 10000.0),
        ]
        for w in wallets:
            self.treasury.add_wallet(w)
        tiers = self.treasury.total_value_by_tier()
        self.assertEqual(tiers["hot"], 300.0)
        self.assertEqual(tiers["warm"], 500.0)
        self.assertEqual(tiers["cold"], 10000.0)


class TestPnlReport(unittest.TestCase):
    """Test pnl_report for daily, weekly, monthly periods."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.treasury = Treasury(data_dir=Path(self.tmpdir))
        now = datetime.now(timezone.utc)

        # Add some wallets
        w = Wallet("Main", "0xMAIN", "hot", "eth", 5000.0)
        self.treasury.add_wallet(w)

        # Add transactions at various ages
        for i in range(3):
            # Recent transactions (within 1 day)
            tx = Transaction(
                id=f"today-{i}",
                timestamp=(now - timedelta(hours=i * 3)).isoformat(),
                type="revenue",
                amount_usd=100.0 + i * 50,
                asset="ETH", chain="ethereum", category="mev",
            )
            self.treasury.log_tx(tx)

        for i in range(2):
            # 3 days ago (in weekly but not daily)
            tx = Transaction(
                id=f"3d-{i}",
                timestamp=(now - timedelta(days=3, hours=i)).isoformat(),
                type="cost",
                amount_usd=25.0,
                asset="ETH", chain="ethereum", category="gas",
            )
            self.treasury.log_tx(tx)

        for i in range(2):
            # 6 days ago (in weekly + monthly but not daily)
            tx = Transaction(
                id=f"6d-{i}",
                timestamp=(now - timedelta(days=6, hours=i)).isoformat(),
                type="revenue",
                amount_usd=80.0,
                asset="USDC", chain="base", category="airdrop",
            )
            self.treasury.log_tx(tx)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_pnl_daily(self):
        report = self.treasury.pnl_report("daily")
        self.assertEqual(report["period"], "daily")
        # 3 revenue txs from today: 100 + 150 + 200 = 450
        self.assertEqual(report["revenue"], 450.0)
        self.assertEqual(report["costs"], 0.0)  # cost txs are 3d old
        self.assertEqual(report["net_profit"], 450.0)
        self.assertEqual(report["tx_count"], 3)

    def test_pnl_weekly(self):
        report = self.treasury.pnl_report("weekly")
        self.assertEqual(report["period"], "weekly")
        # Revenue: 450 (today) + 160 (6d-old) = 610
        # Costs: 50 (3d-old)
        self.assertAlmostEqual(report["revenue"], 610.0)
        self.assertAlmostEqual(report["costs"], 50.0)
        self.assertAlmostEqual(report["net_profit"], 560.0)
        self.assertEqual(report["tx_count"], 7)

    def test_pnl_monthly(self):
        report = self.treasury.pnl_report("monthly")
        self.assertEqual(report["period"], "monthly")
        # All 7 txs are within 30 days
        self.assertAlmostEqual(report["revenue"], 610.0)
        self.assertAlmostEqual(report["costs"], 50.0)
        self.assertAlmostEqual(report["net_profit"], 560.0)

    def test_pnl_includes_total_balance(self):
        report = self.treasury.pnl_report("daily")
        self.assertEqual(report["total_balance"], 5000.0)

    def test_pnl_includes_by_category(self):
        report = self.treasury.pnl_report("weekly")
        self.assertIn("mev", report["by_category"])
        self.assertIn("gas", report["by_category"])
        self.assertIn("airdrop", report["by_category"])

    def test_pnl_includes_tiers(self):
        report = self.treasury.pnl_report("daily")
        self.assertEqual(report["tiers"]["hot"], 5000.0)

    def test_pnl_roi_calculation(self):
        report = self.treasury.pnl_report("weekly")
        if report["costs"] > 0:
            expected_roi = round((report["net_profit"] / report["costs"]) * 100, 2)
            self.assertEqual(report["roi_pct"], expected_roi)

    def test_pnl_defaults_to_daily(self):
        report = self.treasury.pnl_report()
        self.assertEqual(report["period"], "daily")

    def test_pnl_unknown_period_defaults_to_daily(self):
        # Unknown period: the code falls through to the else clause
        # which also sets since = now - timedelta(days=1), same as daily
        report = self.treasury.pnl_report("quarterly")
        # The period field retains the string passed in, but the data is
        # identical to daily range
        self.assertEqual(report["revenue"], 450.0)
        self.assertEqual(report["costs"], 0.0)
        self.assertEqual(report["tx_count"], 3)


class TestRevenueStreamsReport(unittest.TestCase):
    """Test revenue_streams_report."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.treasury = Treasury(data_dir=Path(self.tmpdir))
        now = datetime.now(timezone.utc)

        # Revenue streams
        revenue_data = [
            ("mev", 500.0),
            ("mev", 300.0),
            ("airdrop", 1000.0),
            ("airdrop", 200.0),
            ("airdrop", 150.0),
            ("yield", 50.0),
            ("nft", 800.0),
        ]
        for i, (cat, amt) in enumerate(revenue_data):
            tx = Transaction(
                id=f"rev-{i}",
                timestamp=(now - timedelta(days=i)).isoformat(),
                type="revenue",
                amount_usd=amt,
                asset="ETH", chain="ethereum", category=cat,
            )
            self.treasury.log_tx(tx)

        # Also add some cost transactions (should not appear in revenue report)
        for i in range(3):
            tx = Transaction(
                id=f"cost-{i}",
                timestamp=now.isoformat(),
                type="cost",
                amount_usd=10.0,
                asset="ETH", chain="ethereum", category="gas",
            )
            self.treasury.log_tx(tx)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_revenue_streams_top_stream(self):
        report = self.treasury.revenue_streams_report()
        self.assertEqual(report["period"], "30d")
        # airdrop total: 1000 + 200 + 150 = 1350 (should be top)
        self.assertEqual(report["top_stream"], "airdrop")

    def test_revenue_streams_stream_stats(self):
        report = self.treasury.revenue_streams_report()
        streams = report["streams"]
        self.assertIn("airdrop", streams)
        self.assertIn("mev", streams)
        self.assertIn("yield", streams)
        self.assertIn("nft", streams)

        airdrop = streams["airdrop"]
        self.assertEqual(airdrop["count"], 3)
        self.assertAlmostEqual(airdrop["total"], 1350.0)
        self.assertAlmostEqual(airdrop["avg"], 450.0)

    def test_revenue_streams_total(self):
        report = self.treasury.revenue_streams_report()
        expected = 500 + 300 + 1000 + 200 + 150 + 50 + 800  # 3000
        self.assertAlmostEqual(report["total_revenue"], expected)

    def test_revenue_streams_empty(self):
        t = Treasury(data_dir=Path(tempfile.mkdtemp()))
        try:
            report = t.revenue_streams_report()
            self.assertEqual(report["top_stream"], "none")
            self.assertEqual(report["total_revenue"], 0.0)
        finally:
            shutil.rmtree(str(t.data_dir))


class TestAutoRevenueCheck(unittest.TestCase):
    """Test auto_revenue_check for finding idle wallets."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.treasury = Treasury(data_dir=Path(self.tmpdir))
        now = datetime.now(timezone.utc)

        # Add wallets
        self.treasury.add_wallet(Wallet(
            "ActiveWallet", "0xACTIVE", "hot", "ethereum", balance_usd=500.0,
        ))
        self.treasury.add_wallet(Wallet(
            "IdleWallet", "0xIDLE", "warm", "base", balance_usd=2000.0,
        ))
        self.treasury.add_wallet(Wallet(
            "SmallWallet", "0xSMALL", "hot", "arbitrum", balance_usd=50.0,
        ))

        # Log recent tx for active wallet
        tx = Transaction(
            id="active-tx", timestamp=now.isoformat(),
            type="transfer", amount_usd=100.0,
            asset="ETH", chain="ethereum", category="other",
            tx_hash="0xACTIVE_TX_HASH",
        )
        self.treasury.log_tx(tx)

        # Log old tx for idle wallet (>7 days ago)
        old_tx = Transaction(
            id="idle-old-tx",
            timestamp=(now - timedelta(days=14)).isoformat(),
            type="revenue", amount_usd=2000.0,
            asset="ETH", chain="base", category="airdrop",
            tx_hash="0xIDLE_OLD_TX_0xidle",
        )
        self.treasury.log_tx(old_tx)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_auto_revenue_returns_list(self):
        result = self.treasury.auto_revenue_check()
        self.assertIsInstance(result, list)

    def test_auto_revenue_idle_wallet_detected(self):
        result = self.treasury.auto_revenue_check()
        # IdleWallet has $2000 and last tx >7d ago
        idle_wallets = [r for r in result if r["type"] == "idle_wallet"]
        self.assertGreaterEqual(len(idle_wallets), 1)
        idle = idle_wallets[0]
        self.assertIn("suggestion", idle)

    def test_auto_revenue_ignores_small_wallets(self):
        result = self.treasury.auto_revenue_check()
        for r in result:
            # SmallWallet has $50 (<$100 threshold)
            if r.get("wallet") == "SmallWallet":
                self.fail("Small wallet under $100 should not appear")

    def test_auto_revenue_empty_wallets(self):
        t = Treasury(data_dir=Path(tempfile.mkdtemp()))
        try:
            result = t.auto_revenue_check()
            self.assertEqual(result, [])
        finally:
            shutil.rmtree(str(t.data_dir))


class TestTreasuryPersistence(unittest.TestCase):
    """Test that data persists and reloads correctly across Treasury instances."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_full_roundtrip(self):
        t1 = Treasury(data_dir=Path(self.tmpdir))

        w = Wallet("Full", "0xFULL", "cold", "ethereum", balance_usd=42000.0)
        t1.add_wallet(w)

        tx = Transaction(
            id="roundtrip", timestamp="2026-07-01T00:00:00Z",
            type="revenue", amount_usd=999.0,
            asset="ETH", chain="ethereum", category="mev",
        )
        t1.log_tx(tx)

        t2 = Treasury(data_dir=Path(self.tmpdir))
        self.assertEqual(len(t2.wallets), 1)
        self.assertEqual(t2.wallets["0xFULL"].balance_usd, 42000.0)
        self.assertEqual(len(t2.transactions), 1)
        self.assertEqual(t2.transactions[0].amount_usd, 999.0)


if __name__ == "__main__":
    unittest.main()
