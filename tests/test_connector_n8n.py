"""Tests for the n8n connector (API key + base URL). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import n8n as n8n_mod
from zeline.connectors.n8n import N8nConnector

BASE_URL = "https://n8n.example.com"
TOKEN = "n8n_fake_api_key_123"
WORKFLOWS = [
    {"id": "wf1", "name": "Daily Report", "active": True},
    {"id": "wf2", "name": "Sync Contacts", "active": False},
]


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


def _patch_store(testcase, tmp: Path):
    """Redirect the connector credential store into a temp dir."""
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save("n8n", {"api_key": TOKEN, "base_url": BASE_URL})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class N8nConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-n8n-test-"))
        _patch_store(self, self.tmp)
        self.conn = N8nConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get", return_value=FakeResponse(list(WORKFLOWS))
        ) as get:
            result = self.conn.connect(TOKEN, base_url=BASE_URL)
        self.assertEqual(result, f"Connected to n8n at {BASE_URL} (2 workflows).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE_URL}/api/v1/workflows")
        self.assertEqual(kwargs["headers"]["X-N8N-API-KEY"], TOKEN)
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("n8n"), {"api_key": TOKEN, "base_url": BASE_URL})

    def test_connect_token_kwarg_alias(self):
        with mock.patch("requests.get", return_value=FakeResponse([])):
            result = self.conn.connect(token=TOKEN, base_url=BASE_URL)
        self.assertEqual(result, f"Connected to n8n at {BASE_URL} (0 workflows).")

    def test_connect_base_url_trailing_slash_stripped(self):
        with mock.patch(
            "requests.get", return_value=FakeResponse({"data": WORKFLOWS})
        ) as get:
            result = self.conn.connect(TOKEN, base_url=f"{BASE_URL}//")
        self.assertTrue(result.startswith("Connected to n8n at"))
        self.assertEqual(get.call_args[0][0], f"{BASE_URL}/api/v1/workflows")

    def test_connect_missing_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("", base_url=BASE_URL)
        self.assertTrue(result.startswith("ERROR: no API key"))
        self.assertIsNone(store.load("n8n"))
        get.assert_not_called()

    def test_connect_missing_base_url_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR: no base URL"))
        self.assertIsNone(store.load("n8n"))
        get.assert_not_called()

    def test_connect_401_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("bogus-token", base_url=BASE_URL)
        self.assertTrue(result.startswith("ERROR: n8n rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("n8n"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN, base_url=BASE_URL)
        self.assertTrue(result.startswith(f"ERROR: could not reach {BASE_URL}"))
        self.assertIsNone(store.load("n8n"))

    def test_connect_non_json_200_still_connects(self):
        from zeline.connectors import store

        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(TOKEN, base_url=BASE_URL)
        self.assertEqual(result, f"Connected to n8n at {BASE_URL} (0 workflows).")
        self.assertEqual(store.load("n8n"), {"api_key": TOKEN, "base_url": BASE_URL})

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(
            self.conn.status(),
            {"connected": True, "detail": f"linked to {BASE_URL}"},
        )

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "n8n disconnected.")
        self.assertEqual(self.conn.disconnect(), "n8n was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "n8n")
        self.assertEqual(self.conn.name, "n8n")
        self.assertEqual(self.conn.description, "List and trigger n8n workflows via API.")
        self.assertEqual(self.conn.auth_kind, "pat")


class N8nOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-n8n-test-"))
        _patch_store(self, self.tmp)
        self.conn = N8nConnector()
        _seed_connected()

    def test_list_workflows(self):
        mapping = {
            ("GET", f"{BASE_URL}/api/v1/workflows"): (list(WORKFLOWS), 200),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_workflows(limit=2)
        self.assertEqual(result, "wf1: Daily Report (active)\nwf2: Sync Contacts (inactive)")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], f"{BASE_URL}/api/v1/workflows")
        self.assertEqual(kwargs["headers"]["X-N8N-API-KEY"], TOKEN)
        self.assertEqual(kwargs["params"], {"limit": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_workflows_dict_envelope(self):
        mapping = {
            ("GET", f"{BASE_URL}/api/v1/workflows"): ({"data": WORKFLOWS[:1]}, 200),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            result = self.conn.list_workflows()
        self.assertEqual(result, "wf1: Daily Report (active)")

    def test_list_workflows_limit_clamped(self):
        mapping = {
            ("GET", f"{BASE_URL}/api/v1/workflows"): (
                [{"id": f"wf{i}", "name": f"Flow {i}", "active": True} for i in range(100)],
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_workflows(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"limit": 100})

    def test_list_workflows_min_limit(self):
        mapping = {("GET", f"{BASE_URL}/api/v1/workflows"): ([], 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.list_workflows(limit=0)
        self.assertEqual(req.call_args.kwargs["params"], {"limit": 1})

    def test_list_workflows_empty(self):
        mapping = {("GET", f"{BASE_URL}/api/v1/workflows"): ([], 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            self.assertEqual(self.conn.list_workflows(), "No workflows found.")

    def test_list_workflows_500_raises(self):
        mapping = {("GET", f"{BASE_URL}/api/v1/workflows"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_workflows()
        self.assertEqual(str(ctx.exception), "ERROR: n8n API 500 on /api/v1/workflows.")

    def test_list_workflows_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_workflows()
        self.assertIn("ERROR: n8n API request failed", str(ctx.exception))

    def test_list_workflows_unreadable_body(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.request", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_workflows()
        self.assertIn("unreadable response", str(ctx.exception))

    def test_get_workflow(self):
        payload = {"id": "wf1", "name": "Daily Report", "active": True, "nodes": [{}, {}]}
        mapping = {("GET", f"{BASE_URL}/api/v1/workflows/wf1"): (payload, 200)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.get_workflow("wf1")
        self.assertEqual(result, "wf1: Daily Report (active, 2 nodes)")
        self.assertEqual(req.call_args[0][1], f"{BASE_URL}/api/v1/workflows/wf1")

    def test_get_workflow_404_raises(self):
        mapping = {("GET", f"{BASE_URL}/api/v1/workflows/nope"): ({}, 404)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.get_workflow("nope")
        self.assertIn("ERROR: n8n API 404", str(ctx.exception))

    def test_execute_workflow(self):
        mapping = {
            ("POST", f"{BASE_URL}/api/v1/workflows/wf1/execute"): (
                {"id": "exec-42", "status": "running"},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.execute_workflow("wf1")
        self.assertEqual(result, "Execution exec-42 started for workflow wf1.")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], f"{BASE_URL}/api/v1/workflows/wf1/execute")
        self.assertEqual(kwargs["json"], {})
        self.assertEqual(kwargs["headers"]["X-N8N-API-KEY"], TOKEN)

    def test_execute_workflow_with_data(self):
        mapping = {
            ("POST", f"{BASE_URL}/api/v1/workflows/wf2/execute"): ({"id": "exec-7"}, 200),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.conn.execute_workflow("wf2", {"city": "Jakarta"})
        self.assertEqual(req.call_args.kwargs["json"], {"city": "Jakarta"})

    def test_execute_workflow_500_raises(self):
        mapping = {("POST", f"{BASE_URL}/api/v1/workflows/wf1/execute"): ({}, 500)}
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.execute_workflow("wf1")
        self.assertEqual(str(ctx.exception), "ERROR: n8n API 500 on /api/v1/workflows/wf1/execute.")

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("n8n")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_workflows()
        self.assertIn("zeline connect n8n", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.get_workflow("wf1")
        with self.assertRaises(RuntimeError):
            self.conn.execute_workflow("wf1")

    def test_secret_not_in_error_or_status(self):
        self.assertNotIn(TOKEN, str(self.conn.status()))
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_workflows()
        self.assertNotIn(TOKEN, str(ctx.exception))


class N8nRegistryTests(unittest.TestCase):
    def test_n8n_registered(self):
        from zeline.connectors import get

        conn = get("n8n")
        self.assertIsInstance(conn, N8nConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(n8n_mod.N8nConnector.id, "n8n")


if __name__ == "__main__":
    unittest.main()
