"""Tests for the Bitly connector (personal access token). All HTTP is mocked."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import bitly as bitly_mod
from zeline.connectors.bitly import BitlyConnector

TOKEN = "bitly_fake_access_token_123"
GROUP_GUID = "Bj1abcdefgH"
LINKS = [
    {"link": "https://bit.ly/aaa", "long_url": "https://example.com/first"},
    {"link": "https://bit.ly/bbb", "long_url": "https://example.com/second"},
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

    store.save("bitly", {"access_token": TOKEN, "default_group_guid": GROUP_GUID})


def _request_side_effect(mapping):
    def _side_effect(method, url, *args, **kwargs):
        key = (method, url)
        if key in mapping:
            payload, status = mapping[key]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected {method} {url}")

    return _side_effect


class BitlyConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-bitly-test-"))
        _patch_store(self, self.tmp)
        self.conn = BitlyConnector()

    def test_connect_success_saves_token_and_group_guid(self):
        from zeline.connectors import store

        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"login": "tester", "default_group_guid": GROUP_GUID}),
        ) as get:
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Bitly.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api-ssl.bitly.com/v4/user")
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(
            store.load("bitly"),
            {"access_token": TOKEN, "default_group_guid": GROUP_GUID},
        )

    def test_connect_token_kwarg_alias(self):
        with mock.patch(
            "requests.get",
            return_value=FakeResponse({"default_group_guid": GROUP_GUID}),
        ):
            result = self.conn.connect(token=TOKEN)
        self.assertEqual(result, "Connected to Bitly.")

    def test_connect_missing_token_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("")
        self.assertTrue(result.startswith("ERROR: no access token"))
        self.assertIsNone(store.load("bitly"))
        get.assert_not_called()

    def test_connect_401_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)):
            result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR: Bitly rejected the access token"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("bitly"))

    def test_connect_403_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=403)):
            result = self.conn.connect("bogus-token")
        self.assertTrue(result.startswith("ERROR: Bitly rejected the access token"))
        self.assertIsNone(store.load("bitly"))

    def test_connect_500_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=500)):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR: could not reach Bitly"))
        self.assertIsNone(store.load("bitly"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(TOKEN)
        self.assertTrue(result.startswith("ERROR: could not reach Bitly"))
        self.assertIsNone(store.load("bitly"))

    def test_connect_non_json_200_still_connects(self):
        from zeline.connectors import store

        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.get", return_value=resp):
            result = self.conn.connect(TOKEN)
        self.assertEqual(result, "Connected to Bitly.")
        self.assertEqual(
            store.load("bitly"),
            {"access_token": TOKEN, "default_group_guid": None},
        )

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(
            self.conn.status(),
            {"connected": True, "detail": "personal access token"},
        )

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_status_without_secret(self):
        _seed_connected()
        status = self.conn.status()
        self.assertNotIn(TOKEN, str(status))
        self.assertNotIn(GROUP_GUID, str(status))

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Bitly disconnected.")
        self.assertEqual(self.conn.disconnect(), "Bitly was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "bitly")
        self.assertEqual(self.conn.name, "Bitly")
        self.assertEqual(self.conn.description, "Shorten URLs and list Bitly links.")
        self.assertEqual(self.conn.auth_kind, "pat")


class BitlyOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-bitly-test-"))
        _patch_store(self, self.tmp)
        self.conn = BitlyConnector()
        _seed_connected()

    def test_shorten(self):
        mapping = {
            ("POST", "https://api-ssl.bitly.com/v4/shorten"): (
                {"link": "https://bit.ly/xyz", "long_url": "https://example.com/x"},
                201,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.shorten("https://example.com/x")
        self.assertEqual(result, "https://bit.ly/xyz")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://api-ssl.bitly.com/v4/shorten")
        self.assertEqual(kwargs["json"], {"long_url": "https://example.com/x"})
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["timeout"], 30)

    def test_shorten_422_raises(self):
        mapping = {
            ("POST", "https://api-ssl.bitly.com/v4/shorten"): ({"message": "invalid"}, 422),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.shorten("https://example.com/x")
        self.assertEqual(str(ctx.exception), "ERROR: Bitly API 422 on /shorten.")

    def test_shorten_missing_url_raises(self):
        with mock.patch("requests.request") as req:
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.shorten("")
        self.assertTrue(str(ctx.exception).startswith("ERROR: no URL"))
        req.assert_not_called()

    def test_shorten_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.shorten("https://example.com/x")
        self.assertIn("ERROR: Bitly API request failed", str(ctx.exception))

    def test_shorten_no_link_in_response_raises(self):
        mapping = {
            ("POST", "https://api-ssl.bitly.com/v4/shorten"): ({"id": "bit.ly/xyz"}, 201),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.shorten("https://example.com/x")
        self.assertIn("no shortened link", str(ctx.exception))

    def test_list_links(self):
        mapping = {
            ("GET", f"https://api-ssl.bitly.com/v4/groups/{GROUP_GUID}/bitlinks"): (
                {"links": list(LINKS)},
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_links(limit=2)
        self.assertEqual(
            result,
            "https://bit.ly/aaa → https://example.com/first\n"
            "https://bit.ly/bbb → https://example.com/second",
        )
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(
            args[1], f"https://api-ssl.bitly.com/v4/groups/{GROUP_GUID}/bitlinks"
        )
        self.assertEqual(kwargs["params"], {"size": 2})
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_links_limit_clamped(self):
        mapping = {
            ("GET", f"https://api-ssl.bitly.com/v4/groups/{GROUP_GUID}/bitlinks"): (
                {
                    "links": [
                        {"link": f"https://bit.ly/l{i}", "long_url": f"https://example.com/{i}"}
                        for i in range(100)
                    ]
                },
                200,
            ),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            result = self.conn.list_links(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(req.call_args.kwargs["params"], {"size": 100})

    def test_list_links_min_limit(self):
        mapping = {
            ("GET", f"https://api-ssl.bitly.com/v4/groups/{GROUP_GUID}/bitlinks"): ({"links": []}, 200),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)) as req:
            self.assertEqual(self.conn.list_links(limit=0), "No Bitly links found.")
        self.assertEqual(req.call_args.kwargs["params"], {"size": 1})

    def test_list_links_403_raises(self):
        mapping = {
            ("GET", f"https://api-ssl.bitly.com/v4/groups/{GROUP_GUID}/bitlinks"): ({}, 403),
        }
        with mock.patch("requests.request", side_effect=_request_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_links()
        self.assertIn("ERROR: Bitly API 403", str(ctx.exception))

    def test_list_links_network_error(self):
        import requests

        with mock.patch("requests.request", side_effect=requests.ConnectionError("down")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_links()
        self.assertIn("ERROR: Bitly API request failed", str(ctx.exception))

    def test_list_links_without_group_guid_raises(self):
        from zeline.connectors import store

        store.save("bitly", {"access_token": TOKEN, "default_group_guid": None})
        with mock.patch("requests.request") as req:
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_links()
        self.assertIn("default group", str(ctx.exception))
        req.assert_not_called()

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("bitly")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_links()
        self.assertIn("zeline connect bitly", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.shorten("https://example.com/x")

    def test_secret_not_in_error_or_status(self):
        self.assertNotIn(TOKEN, str(self.conn.status()))
        with mock.patch("requests.request", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_links()
        self.assertNotIn(TOKEN, str(ctx.exception))


class BitlyRegistryTests(unittest.TestCase):
    def test_bitly_registered(self):
        from zeline.connectors import get

        conn = get("bitly")
        self.assertIsInstance(conn, BitlyConnector)

    def test_module_import_does_not_leak(self):
        self.assertEqual(bitly_mod.BitlyConnector.id, "bitly")


if __name__ == "__main__":
    unittest.main()
