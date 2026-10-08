"""Tests for the Cloudinary connector (cloud name + API key + secret). All HTTP is mocked."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors.cloudinary import CloudinaryConnector

BASE = "https://api.cloudinary.com/v1_1/demo-cloud"
CLOUD = "demo-cloud"
KEY = "cloudinary_fake_key_123"
SECRET = "cloudinary_fake_secret_456"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


def _patch_store(testcase, tmp: Path):
    patcher = mock.patch("zeline.connectors.store._dir", return_value=tmp)
    patcher.start()
    testcase.addCleanup(patcher.stop)


def _seed_connected():
    from zeline.connectors import store

    store.save(
        "cloudinary",
        {"cloud_name": CLOUD, "api_key": KEY, "api_secret": SECRET},
    )


def _get_side_effect(mapping):
    def _side_effect(url, *args, **kwargs):
        if url in mapping:
            payload, status = mapping[url]
            return FakeResponse(payload, status)
        raise AssertionError(f"unexpected GET {url}")

    return _side_effect


class CloudinaryConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-cloudinary-test-"))
        _patch_store(self, self.tmp)
        self.conn = CloudinaryConnector()

    def test_connect_success_saves_credentials(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({"resources": []})) as get:
            result = self.conn.connect(CLOUD, api_key=KEY, api_secret=SECRET)
        self.assertEqual(result, f"Connected to Cloudinary (cloud name: {CLOUD}).")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE}/resources/image")
        self.assertEqual(kwargs["auth"], (KEY, SECRET))
        self.assertEqual(kwargs["params"], {"max_results": 1})
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(
            store.load("cloudinary"),
            {"cloud_name": CLOUD, "api_key": KEY, "api_secret": SECRET},
        )

    def test_connect_token_kwarg_used_as_api_key(self):
        with mock.patch("requests.get", return_value=FakeResponse({"resources": []})):
            result = self.conn.connect(CLOUD, token=KEY, api_secret=SECRET)
        self.assertEqual(result, f"Connected to Cloudinary (cloud name: {CLOUD}).")

    def test_connect_missing_cloud_name_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect("", api_key=KEY, api_secret=SECRET)
        self.assertTrue(result.startswith("ERROR: no cloud name"))
        self.assertIsNone(store.load("cloudinary"))
        get.assert_not_called()

    def test_connect_missing_key_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect(CLOUD, api_secret=SECRET)
        self.assertTrue(result.startswith("ERROR: no API key"))
        self.assertIsNone(store.load("cloudinary"))
        get.assert_not_called()

    def test_connect_missing_secret_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get") as get:
            result = self.conn.connect(CLOUD, api_key=KEY)
        self.assertTrue(result.startswith("ERROR: no API secret"))
        self.assertIsNone(store.load("cloudinary"))
        get.assert_not_called()

    def test_connect_401_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=401)) as get:
            result = self.conn.connect(CLOUD, api_key="bogus", api_secret="nope")
        self.assertTrue(result.startswith("ERROR: Cloudinary rejected the credentials"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("cloudinary"))
        get.assert_called_once()

    def test_connect_500_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({}, status=500)):
            result = self.conn.connect(CLOUD, api_key=KEY, api_secret=SECRET)
        self.assertTrue(result.startswith("ERROR: Cloudinary validation failed"))
        self.assertIn("500", result)
        self.assertIsNone(store.load("cloudinary"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(CLOUD, api_key=KEY, api_secret=SECRET)
        self.assertTrue(result.startswith("ERROR: could not reach Cloudinary"))
        self.assertIsNone(store.load("cloudinary"))

    def test_status_connected_shows_cloud_name_only(self):
        _seed_connected()
        status = self.conn.status()
        self.assertEqual(status, {"connected": True, "detail": f"linked to {CLOUD}"})
        self.assertNotIn(SECRET, str(status))
        self.assertNotIn(KEY, str(status))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "Cloudinary disconnected.")
        self.assertEqual(self.conn.disconnect(), "Cloudinary was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "cloudinary")
        self.assertEqual(self.conn.name, "Cloudinary")
        self.assertEqual(self.conn.description, "List and inspect Cloudinary media resources.")
        self.assertEqual(self.conn.auth_kind, "pat")


class CloudinaryOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-cloudinary-test-"))
        _patch_store(self, self.tmp)
        self.conn = CloudinaryConnector()
        _seed_connected()

    def test_list_resources(self):
        payload = {
            "resources": [
                {"public_id": "a/fox", "format": "jpg", "bytes": 1200},
                {"public_id": "a/cat", "format": "png", "bytes": 3400},
            ]
        }
        mapping = {(f"{BASE}/resources/image"): (payload, 200)}
        with mock.patch("requests.get", side_effect=_get_side_effect(mapping)) as get:
            result = self.conn.list_resources(limit=2)
        self.assertEqual(
            result,
            "a/fox (format: jpg, bytes: 1200)\na/cat (format: png, bytes: 3400)",
        )
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE}/resources/image")
        self.assertEqual(kwargs["auth"], (KEY, SECRET))
        self.assertEqual(kwargs["params"], {"max_results": 2})
        self.assertEqual(kwargs["timeout"], 30)

    def test_list_resources_video_type(self):
        mapping = {(f"{BASE}/resources/video"): ({"resources": []}, 200)}
        with mock.patch("requests.get", side_effect=_get_side_effect(mapping)) as get:
            self.assertEqual(self.conn.list_resources(resource_type="video"), "No resources found.")
        self.assertEqual(get.call_args[0][0], f"{BASE}/resources/video")

    def test_list_resources_invalid_type_raises(self):
        with mock.patch("requests.get") as get:
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_resources(resource_type="audio")
        self.assertIn("invalid resource type", str(ctx.exception))
        get.assert_not_called()

    def test_list_resources_limit_clamped(self):
        mapping = {
            (f"{BASE}/resources/image"): (
                {
                    "resources": [
                        {"public_id": f"p{i}", "format": "jpg", "bytes": i} for i in range(100)
                    ]
                },
                200,
            )
        }
        with mock.patch("requests.get", side_effect=_get_side_effect(mapping)) as get:
            result = self.conn.list_resources(limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(get.call_args.kwargs["params"], {"max_results": 100})

    def test_list_resources_min_limit(self):
        mapping = {(f"{BASE}/resources/image"): ({"resources": []}, 200)}
        with mock.patch("requests.get", side_effect=_get_side_effect(mapping)) as get:
            self.conn.list_resources(limit=0)
        self.assertEqual(get.call_args.kwargs["params"], {"max_results": 1})

    def test_list_resources_500_raises(self):
        mapping = {(f"{BASE}/resources/image"): ({}, 500)}
        with mock.patch("requests.get", side_effect=_get_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_resources()
        self.assertEqual(str(ctx.exception), "ERROR: Cloudinary API 500 on /resources/image.")

    def test_list_resources_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_resources()
        self.assertIn("ERROR: Cloudinary API request failed", str(ctx.exception))

    def test_list_resources_unreadable_body(self):
        resp = FakeResponse("not-json", status=200)
        resp.json = mock.Mock(side_effect=ValueError("bad"))
        with mock.patch("requests.get", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_resources()
        self.assertIn("unreadable response", str(ctx.exception))

    def test_resource_info(self):
        payload = {
            "public_id": "a/fox",
            "format": "jpg",
            "width": 640,
            "height": 480,
            "bytes": 1200,
            "url": "https://res.cloudinary.com/demo-cloud/image/upload/a/fox.jpg",
        }
        mapping = {(f"{BASE}/resources/image/upload/a/fox"): (payload, 200)}
        with mock.patch("requests.get", side_effect=_get_side_effect(mapping)) as get:
            result = self.conn.resource_info("a/fox")
        self.assertIn("Public ID: a/fox", result)
        self.assertIn("Format: jpg", result)
        self.assertIn("Dimensions: 640x480", result)
        self.assertIn("Bytes: 1200", result)
        self.assertIn("URL: https://res.cloudinary.com/demo-cloud/image/upload/a/fox.jpg", result)
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{BASE}/resources/image/upload/a/fox")
        self.assertEqual(kwargs["auth"], (KEY, SECRET))
        self.assertEqual(kwargs["timeout"], 30)

    def test_resource_info_video_type(self):
        mapping = {(f"{BASE}/resources/video/upload/a/clip"): ({"public_id": "a/clip"}, 200)}
        with mock.patch("requests.get", side_effect=_get_side_effect(mapping)) as get:
            self.conn.resource_info("a/clip", resource_type="video")
        self.assertEqual(get.call_args[0][0], f"{BASE}/resources/video/upload/a/clip")

    def test_resource_info_invalid_type_raises(self):
        with mock.patch("requests.get") as get:
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.resource_info("a/fox", resource_type="svg")
        self.assertIn("invalid resource type", str(ctx.exception))
        get.assert_not_called()

    def test_resource_info_404_raises(self):
        mapping = {(f"{BASE}/resources/image/upload/nope"): ({}, 404)}
        with mock.patch("requests.get", side_effect=_get_side_effect(mapping)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.resource_info("nope")
        self.assertIn("ERROR: Cloudinary API 404", str(ctx.exception))

    def test_operations_disconnected_raise(self):
        from zeline.connectors import store

        store.delete("cloudinary")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.list_resources()
        self.assertIn("zeline connect cloudinary", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.resource_info("a/fox")

    def test_secret_not_in_error_or_status(self):
        self.assertNotIn(SECRET, str(self.conn.status()))
        self.assertNotIn(KEY, str(self.conn.status()))
        with mock.patch("requests.get", return_value=FakeResponse({}, status=500)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.list_resources()
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertNotIn(KEY, str(ctx.exception))


class CloudinaryRegistryTests(unittest.TestCase):
    def test_cloudinary_registered(self):
        from zeline.connectors import get

        conn = get("cloudinary")
        self.assertIsInstance(conn, CloudinaryConnector)


if __name__ == "__main__":
    unittest.main()
