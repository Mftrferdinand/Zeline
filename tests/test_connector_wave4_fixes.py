"""Tests for Wave 4 verifier findings fixes (B-1, M-2, m-3, m-4, m-5). All HTTP mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _patch_store(testcase):
    tmp = Path(tempfile.mkdtemp(prefix="zeline-wave4fix-"))
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


class TokenAliasTests(unittest.TestCase):
    """B-1: CLI generic path calls connect(token=...); connectors must accept it."""

    def setUp(self):
        _patch_store(self)

    def _check(self, module_name, class_name, ok_payload):
        mod = __import__(f"zeline.connectors.{module_name}", fromlist=["x"])
        cls = getattr(mod, class_name)
        conn = cls()
        with mock.patch("requests.get", return_value=FakeResponse(ok_payload)):
            with mock.patch("requests.post", return_value=FakeResponse(ok_payload)):
                result = conn.connect(token="SECRET")
        self.assertFalse(result.startswith("ERROR:"), f"{module_name}: {result}")
        from zeline.connectors import store
        self.assertTrue(store.load(module_name))

    def test_openweathermap_token_alias(self):
        self._check("openweathermap", "OpenWeatherMapConnector", {"cod": 200})

    def test_coinbase_token_alias(self):
        self._check("coinbase", "CoinbaseConnector", {"data": {}})

    def test_wise_token_alias(self):
        self._check("wise", "WiseConnector", [{"id": 1}])

    def test_linkedin_token_alias(self):
        self._check("linkedin", "LinkedInConnector", {"sub": "x"})

    def test_pipedrive_token_alias(self):
        self._check("pipedrive", "PipedriveConnector", {"success": True})

    def test_close_token_alias(self):
        self._check("close", "CloseConnector", {"id": "1"})

    def test_paddle_token_alias(self):
        self._check("paddle", "PaddleConnector", {"data": []})

    def test_box_token_alias(self):
        self._check("box", "BoxConnector", {"login": "a@b.c"})

    def test_webflow_token_alias(self):
        self._check("webflow", "WebflowConnector", {"id": "1"})

    def test_zoho_crm_token_alias(self):
        self._check("zoho_crm", "ZohoCrmConnector", {"users": []})


class SubdomainNormalizationTests(unittest.TestCase):
    """M-2: full URLs must not produce doubled domains."""

    def test_freshdesk_strips_suffix(self):
        from zeline.connectors.freshdesk import _normalize_subdomain
        self.assertEqual(_normalize_subdomain("mycompany"), "mycompany")
        self.assertEqual(
            _normalize_subdomain("https://mycompany.freshdesk.com"),
            "mycompany",
        )
        self.assertEqual(
            _normalize_subdomain("https://mycompany.freshdesk.com/"),
            "mycompany",
        )


class GhostJwtTests(unittest.TestCase):
    """m-3: empty secret must be rejected, not signed with an empty key."""

    def test_empty_secret_rejected(self):
        from zeline.connectors.ghost import _make_jwt
        with self.assertRaises(RuntimeError):
            _make_jwt("kid123", "")


class FreshdeskValidationTests(unittest.TestCase):
    """m-4: priority/status ranges validated before the API call."""

    def setUp(self):
        _patch_store(self)
        from zeline.connectors.freshdesk import FreshdeskConnector
        from zeline.connectors import store
        store.save("freshdesk", {"api_key": "k", "subdomain": "acme"})
        self.conn = FreshdeskConnector()

    def test_bad_priority_rejected(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.create_ticket("s", "d", priority=9)
        self.assertIn("priority", str(ctx.exception))

    def test_bad_status_rejected(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.create_ticket("s", "d", status=9)
        self.assertIn("status", str(ctx.exception))


class ReconnectHintTests(unittest.TestCase):
    """m-5: 401 on short-lived tokens tells the operator how to reconnect."""

    def setUp(self):
        _patch_store(self)

    def _check_401(self, module_name, class_name, op, cid):
        mod = __import__(f"zeline.connectors.{module_name}", fromlist=["x"])
        conn = getattr(mod, class_name)()
        from zeline.connectors import store
        store.save(cid, {"x": "y"})
        with mock.patch("requests.get", return_value=FakeResponse({}, 401)):
            with self.assertRaises(RuntimeError) as ctx:
                op(conn)
        self.assertIn(f"zeline connect {cid}", str(ctx.exception))

    def test_box_401_hint(self):
        self._check_401("box", "BoxConnector", lambda c: c.list_files(), "box")

    def test_surveymonkey_401_hint(self):
        self._check_401("surveymonkey", "SurveyMonkeyConnector", lambda c: c.list_surveys(), "surveymonkey")

    def test_linkedin_401_hint(self):
        self._check_401("linkedin", "LinkedInConnector", lambda c: c.get_profile(), "linkedin")


if __name__ == "__main__":
    unittest.main()
