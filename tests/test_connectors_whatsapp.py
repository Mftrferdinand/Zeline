"""Tests for the WhatsApp connector (Business Cloud API). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import whatsapp as whatsapp_mod
from zeline.connectors.whatsapp import WhatsAppConnector, _normalize_number


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


def _patch_store(testcase, tmp: Path):
    """Redirect the connector credential store into a temp dir.

    Patches pathlib.Path.home (like test_connectors.fresh_home) instead of
    the store module, so the redirect also applies to *freshly re-imported*
    copies of zeline.connectors.store — test_cli/test_agent evict every
    zeline.* entry from sys.modules in setUp, and string-based re-imports
    would otherwise escape a module-attribute patch.
    """
    patcher = mock.patch("pathlib.Path.home", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected(tmp: Path):
    from zeline.connectors import store

    store.save(
        "whatsapp",
        {
            "access_token": "AT",
            "phone_number_id": "12345",
            "business_account_id": "",
            "display_phone_number": "+1 555-0100",
            "verified_name": "Acme",
        },
    )


class WhatsAppConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-wa-test-"))
        _patch_store(self, self.tmp)
        self.conn = WhatsAppConnector()

    def test_connect_success_saves_credential(self):
        from zeline.connectors import store

        fake = FakeResponse({"display_phone_number": "+1 555-0100", "verified_name": "Acme"})
        with mock.patch("requests.get", return_value=fake) as get:
            result = self.conn.connect(access_token="AT", phone_number_id="12345")
        self.assertIn("Connected to WhatsApp number +1 555-0100 (Acme).", result)
        args, kwargs = get.call_args
        self.assertIn("12345", args[0])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer AT")
        saved = store.load("whatsapp")
        self.assertEqual(saved["access_token"], "AT")
        self.assertEqual(saved["phone_number_id"], "12345")

    def test_connect_rejected_stores_nothing(self):
        from zeline.connectors import store

        fake = FakeResponse({"error": {"message": "bad token"}}, status=401)
        with mock.patch("requests.get", return_value=fake):
            result = self.conn.connect(access_token="BAD", phone_number_id="12345")
        self.assertTrue(result.startswith("ERROR:"))
        self.assertIsNone(store.load("whatsapp"))

    def test_connect_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(access_token="AT", phone_number_id="12345")
        self.assertTrue(result.startswith("ERROR: could not reach"))

    def test_connect_missing_args(self):
        self.assertTrue(self.conn.connect().startswith("ERROR:"))
        self.assertTrue(self.conn.connect(access_token="AT").startswith("ERROR:"))

    def test_status_masked(self):
        _seed_connected(self.tmp)
        status = self.conn.status()
        self.assertTrue(status["connected"])
        self.assertNotIn("555-0100", status["detail"])
        self.assertIn("0100", status["detail"])  # last 4 digits only

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected(self.tmp)
        self.assertEqual(self.conn.disconnect(), "WhatsApp disconnected.")
        self.assertEqual(self.conn.disconnect(), "WhatsApp was not connected.")


class WhatsAppSendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-wa-test-"))
        _patch_store(self, self.tmp)
        self.conn = WhatsAppConnector()
        _seed_connected(self.tmp)

    def test_send_text(self):
        fake = FakeResponse({"messages": [{"id": "wamid.abc"}]})
        with mock.patch("requests.post", return_value=fake) as post:
            result = self.conn.send_text("0812-3456-789", "hello")
        self.assertEqual(result, "Message sent (id wamid.abc).")
        args, kwargs = post.call_args
        self.assertTrue(args[0].endswith("/12345/messages"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer AT")
        payload = kwargs["json"]
        self.assertEqual(payload["messaging_product"], "whatsapp")
        self.assertEqual(payload["to"], "08123456789")
        self.assertEqual(payload["type"], "text")
        self.assertEqual(payload["text"]["body"], "hello")
        self.assertFalse(payload["text"]["preview_url"])

    def test_send_text_normalizes_number(self):
        fake = FakeResponse({"messages": [{"id": "wamid.x"}]})
        with mock.patch("requests.post", return_value=fake) as post:
            self.conn.send_text("0812 3456 789", "hi")
        self.assertEqual(post.call_args[1]["json"]["to"], "08123456789")

    def test_send_text_api_error(self):
        fake = FakeResponse({"error": {"message": "nope"}}, status=400)
        with mock.patch("requests.post", return_value=fake):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.send_text("0812", "hi")
        self.assertIn("ERROR: WhatsApp API 400", str(ctx.exception))

    def test_send_text_empty_args(self):
        self.assertTrue(self.conn.send_text("", "hi").startswith("ERROR:"))
        self.assertTrue(self.conn.send_text("0812", "  ").startswith("ERROR:"))

    def test_send_template_with_parameters(self):
        fake = FakeResponse({"messages": [{"id": "wamid.t"}]})
        with mock.patch("requests.post", return_value=fake) as post:
            result = self.conn.send_template(
                "+62812", "hello_world", language="id", parameters=["Budi"]
            )
        self.assertEqual(result, "Message sent (id wamid.t).")
        payload = post.call_args[1]["json"]
        self.assertEqual(payload["type"], "template")
        self.assertEqual(payload["template"]["name"], "hello_world")
        self.assertEqual(payload["template"]["language"], {"code": "id"})
        comps = payload["template"]["components"]
        self.assertEqual(comps[0]["parameters"], [{"type": "text", "text": "Budi"}])

    def test_send_template_no_parameters(self):
        fake = FakeResponse({"messages": [{"id": "wamid.t2"}]})
        with mock.patch("requests.post", return_value=fake) as post:
            self.conn.send_template("0812", "ping")
        payload = post.call_args[1]["json"]
        self.assertEqual(payload["template"]["language"], {"code": "en_US"})
        self.assertNotIn("components", payload["template"])

    def test_send_template_empty_name(self):
        self.assertTrue(self.conn.send_template("0812", " ").startswith("ERROR:"))

    def test_send_when_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("whatsapp")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.send_text("0812", "hi")
        self.assertIn("zeline connect whatsapp", str(ctx.exception))


class NormalizeNumberTests(unittest.TestCase):
    def test_strips_spaces_dashes(self):
        self.assertEqual(_normalize_number("0812-3456-789"), "08123456789")
        self.assertEqual(_normalize_number("0812 3456 789"), "08123456789")

    def test_keeps_leading_plus(self):
        self.assertEqual(_normalize_number("+62 812-3456-789"), "+628123456789")


class RegistryTests(unittest.TestCase):
    def test_whatsapp_registered(self):
        from zeline.connectors import get
        from zeline.connectors import whatsapp as fresh_mod

        conn = get("whatsapp")
        # Compare by class name, not isinstance: test_cli/test_agent evict
        # zeline.* from sys.modules, so the registry may hold a connector
        # class from a freshly re-imported module instance.
        self.assertEqual(type(conn).__name__, "WhatsAppConnector")
        self.assertEqual(type(conn).__module__, fresh_mod.__name__)
        self.assertEqual(conn.auth_kind, "token")


class ToolHandlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-wa-test-"))
        _patch_store(self, self.tmp)

    def test_tooldefs_registered(self):
        import zeline.tools

        self.assertIn("whatsapp_send", [d.name for d in zeline.tools.TOOL_DEFS])
        self.assertIn("whatsapp_template", [d.name for d in zeline.tools.TOOL_DEFS])

    def test_connector_tool_disconnected(self):
        from zeline.tools import _connector_tool

        result = _connector_tool("whatsapp", "send_text", to="0812", text="hi")
        self.assertIn("zeline connect whatsapp", result)


if __name__ == "__main__":
    unittest.main()
