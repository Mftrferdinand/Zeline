"""Offline regressions for the skills bundled in PR #263.

Uses only stdlib unittest because the release CI installs runtime dependencies,
not pytest or the optional Telegram client package.
"""
import asyncio
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import hmac
import importlib.util
import json
from pathlib import Path
import re
import runpy
import shutil
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "zeline" / "skills"


class _FakeFilter:
    def __and__(self, other):
        return self

    def __invert__(self):
        return self


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@contextmanager
def database(path):
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def _telegram_stubs() -> dict[str, ModuleType]:
    telegram = ModuleType("telegram")
    telegram.Update = object
    telegram.InlineKeyboardButton = lambda *args, **kwargs: (args, kwargs)
    telegram.InlineKeyboardMarkup = lambda rows: rows
    ext = ModuleType("telegram.ext")
    for name in ("Application", "CommandHandler", "CallbackQueryHandler", "MessageHandler"):
        setattr(ext, name, type(name, (), {}))
    context_types = type("ContextTypes", (), {"DEFAULT_TYPE": object})
    ext.ContextTypes = context_types
    ext.filters = SimpleNamespace(TEXT=_FakeFilter(), COMMAND=_FakeFilter())
    return {"telegram": telegram, "telegram.ext": ext}


class NewSkillTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(lambda: Path(self.temp.name).exists() and self.temp.cleanup())
        self.root = Path(self.temp.name)
        templates = SKILLS / "telegram-commerce-bot" / "templates"
        shutil.copy(templates / "tripay-gateway.py", self.root / "tripay_gateway.py")
        self.path_patch = patch.object(sys, "path", [str(self.root), *sys.path])
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.modules_patch = patch.dict(sys.modules, _telegram_stubs())
        self.modules_patch.start()
        self.addCleanup(self.modules_patch.stop)
        sys.modules.pop("tripay_gateway", None)
        self.shop = load(templates / "bot-template.py", "shop_template_audit")
        self.shop.DB_PATH = str(self.root / "nested" / "shop.db")
        self.shop.init_db()
        with database(self.shop.DB_PATH) as db:
            db.execute("INSERT INTO orders (user_id,product,price,tripay_ref,payment_amount) "
                       "VALUES (42,'Product',100,'REF',100)")
            db.execute("INSERT INTO stock (product,credential) VALUES ('Product','secret')")

    def test_callback_authenticates_exact_raw_body(self):
        gateway = load(SKILLS / "telegram-commerce-bot/templates/tripay-gateway.py", "tripay_signature_audit")
        raw = b'{ "status": "PAID", "reference": "REF" }'
        signature = hmac.new(b"key", raw, hashlib.sha256).hexdigest()
        self.assertTrue(gateway.TripayPayment.verify_callback(raw, signature, "key"))
        self.assertFalse(gateway.TripayPayment.verify_callback(raw + b" ", signature, "key"))
        self.assertFalse(gateway.TripayPayment.verify_callback({}, signature, "key"))

    def test_qris_timeout_is_reported_without_retry(self):
        gateway = load(SKILLS / "telegram-commerce-bot/templates/tripay-gateway.py", "tripay_timeout_audit")
        calls = []
        def post(url, **kwargs):
            calls.append(kwargs)
            self.assertEqual(kwargs["timeout"], 30)
            raise gateway.requests.Timeout("offline")
        with patch.object(gateway.requests, "post", post):
            result = gateway.TripayPayment("key", "private", "merchant").create_qris(100, order_id="ORDER")
        self.assertFalse(result["success"])
        self.assertEqual(len(calls), 1)

    def test_delivery_requires_paid_order(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        self.assertFalse(asyncio.run(self.shop.deliver_order(1, bot)))
        bot.send_message.assert_not_called()

    def test_failed_delivery_reserves_same_stock_for_retry(self):
        with database(self.shop.DB_PATH) as db:
            db.execute("UPDATE orders SET status='paid'")
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("offline")))
        self.assertFalse(asyncio.run(self.shop.deliver_order(1, bot)))
        with database(self.shop.DB_PATH) as db:
            self.assertEqual(db.execute("SELECT status FROM orders").fetchone()[0], "delivery_failed")
            self.assertEqual(db.execute("SELECT status FROM stock").fetchone()[0], "reserved")
        bot.send_message = AsyncMock()
        self.assertTrue(asyncio.run(self.shop.deliver_order(1, bot)))
        self.assertTrue(asyncio.run(self.shop.deliver_order(1, bot)))
        bot.send_message.assert_awaited_once()

    def test_foreign_buyer_cannot_query_order(self):
        query = SimpleNamespace(data="check_1", from_user=SimpleNamespace(id=99),
                                answer=AsyncMock(), edit_message_text=AsyncMock())
        def forbidden(*args):
            raise AssertionError("payment queried for another buyer")
        self.shop.tripay.check_payment = forbidden
        asyncio.run(self.shop.button_handler(SimpleNamespace(callback_query=query), SimpleNamespace(bot=None)))

    def test_wrong_amount_never_delivers(self):
        query = SimpleNamespace(data="check_1", from_user=SimpleNamespace(id=42),
                                answer=AsyncMock(), edit_message_text=AsyncMock())
        self.shop.tripay.check_payment = lambda ref: {"success": True, "status": "PAID", "reference": ref, "amount": 1}
        bot = SimpleNamespace(send_message=AsyncMock())
        asyncio.run(self.shop.button_handler(SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot)))
        bot.send_message.assert_not_called()
        with database(self.shop.DB_PATH) as db:
            self.assertEqual(db.execute("SELECT status FROM orders").fetchone()[0], "pending")

    def test_matching_payment_delivers_to_buyer(self):
        query = SimpleNamespace(data="check_1", from_user=SimpleNamespace(id=42),
                                answer=AsyncMock(), edit_message_text=AsyncMock())
        self.shop.tripay.check_payment = lambda ref: {
            "success": True, "status": "PAID", "reference": ref, "amount": 100,
        }
        bot = SimpleNamespace(send_message=AsyncMock())
        asyncio.run(self.shop.button_handler(SimpleNamespace(callback_query=query), SimpleNamespace(bot=bot)))
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.call_args.args[0], 42)
        with database(self.shop.DB_PATH) as db:
            self.assertEqual(db.execute("SELECT status FROM orders").fetchone()[0], "done")

    def test_payment_detail_preserves_reference_and_bounds_request(self):
        gateway = load(SKILLS / "telegram-commerce-bot/templates/tripay-gateway.py", "tripay_detail_audit")
        def get(url, **kwargs):
            self.assertEqual(kwargs["timeout"], 30)
            self.assertEqual(kwargs["params"], {"reference": "REF&other=x"})
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
                "success": True,
                "data": {"reference": "REF&other=x", "status": "PAID", "amount": 100},
            })
        with patch.object(gateway.requests, "get", get):
            result = gateway.TripayPayment("key", "private", "merchant").check_payment("REF&other=x")
        self.assertEqual(result["reference"], "REF&other=x")

    def test_create_accepts_checkout_url_without_pay_url(self):
        gateway = load(SKILLS / "telegram-commerce-bot/templates/tripay-gateway.py", "tripay_create_audit")
        payload = {
            "reference": "REF", "merchant_ref": "ORDER", "qr_url": "https://example.invalid/qr",
            "qr_string": "QR", "checkout_url": "https://example.invalid/pay",
            "amount": 105, "status": "UNPAID",
        }
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"success": True, "data": payload},
        )
        with patch.object(gateway.requests, "post", return_value=response):
            result = gateway.TripayPayment("key", "private", "merchant").create_qris(100)
        self.assertEqual(result["pay_url"], payload["checkout_url"])

    def test_checkout_stores_gateway_total_including_fees(self):
        product = next(iter(self.shop.PRODUCTS))
        price = self.shop.PRODUCTS[product]["price"]
        self.shop.tripay.create_qris = lambda **kwargs: {
            "success": True, "reference": "NEW", "amount": price + 500,
            "qr_url": "https://example.invalid/qr",
        }
        query = SimpleNamespace(
            data="buy_" + product,
            from_user=SimpleNamespace(id=42, username="buyer"),
            message=SimpleNamespace(chat_id=42),
            answer=AsyncMock(), edit_message_text=AsyncMock(),
        )
        asyncio.run(self.shop.button_handler(
            SimpleNamespace(callback_query=query),
            SimpleNamespace(bot=SimpleNamespace(send_photo=AsyncMock())),
        ))
        with database(self.shop.DB_PATH) as db:
            amount = db.execute(
                "SELECT payment_amount FROM orders WHERE tripay_ref='NEW'"
            ).fetchone()[0]
        self.assertEqual(amount, price + 500)

    def test_init_db_creates_parent(self):
        path = self.root / "newdir" / "db.sqlite"
        self.shop.DB_PATH = str(path)
        self.shop.init_db()
        self.assertTrue(path.exists())

    def test_documentation_pipeline_does_not_pass_scripts_as_arguments(self):
        for path in (SKILLS / "documentation-site").rglob("*.md"):
            self.assertNotIn("python3 section_*.py", path.read_text(encoding="utf-8"), str(path))

    def test_delivery_concurrent_calls_send_once(self):
        with database(self.shop.DB_PATH) as db:
            db.execute("UPDATE orders SET status='paid'")
        async def scenario():
            started, release = asyncio.Event(), asyncio.Event()
            async def send(*args, **kwargs):
                started.set()
                await release.wait()
            bot = SimpleNamespace(send_message=AsyncMock(side_effect=send))
            first = asyncio.create_task(self.shop.deliver_order(1, bot))
            await started.wait()
            self.assertFalse(await self.shop.deliver_order(1, bot))
            release.set()
            self.assertTrue(await first)
            bot.send_message.assert_awaited_once()
        asyncio.run(scenario())

    def test_airdrop_accepts_timezone_aware_api_dates(self):
        data = [{
            "date": datetime.now(timezone.utc).isoformat(),
            "title": {"rendered": "Test"},
            "link": "https://example.invalid/",
        }]
        response = SimpleNamespace(read=lambda: json.dumps(data).encode())
        output = io.StringIO()
        with patch.object(sys, "argv", ["fetch-latest-airdrops.py", "7"]), \
                patch.object(urllib.request, "urlopen", return_value=response), \
                redirect_stdout(output):
            runpy.run_path(str(SKILLS / "riset-airdrop/scripts/fetch-latest-airdrops.py"))
        self.assertIn("**Test**", output.getvalue())

    def test_rebrand_example_is_plain_python(self):
        root = self.root / "rebrand"
        root.mkdir()
        text = (SKILLS / "fork-and-rebrand-webapp/SKILL.md").read_text()
        section = text.split("## Step 3", 1)[1].split("## Step 4", 1)[0]
        code = re.search(r"```python\n(.*?)```", section, re.S).group(1)
        code = code.replace('Path.home() / "NEWNAME"', repr(str(root)))
        target = root / "file with spaces.jsx"
        target.write_text("OldBrand oldbrand")
        exec(compile(code, "<skill-example>", "exec"), {})
        self.assertEqual(target.read_text(), "NewBrand newbrand")


if __name__ == "__main__":
    unittest.main()
