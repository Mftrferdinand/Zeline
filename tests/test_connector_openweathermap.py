"""Tests for the OpenWeatherMap connector. All HTTP is mocked."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zeline.connectors import openweathermap as openweathermap_mod
from zeline.connectors.openweathermap import OpenWeatherMapConnector

API_BASE = "https://api.openweathermap.org/data/2.5"


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

    store.save("openweathermap", {"api_key": "KEY"})


class OpenWeatherMapConnectFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-owm-test-"))
        _patch_store(self, self.tmp)
        self.conn = OpenWeatherMapConnector()

    def test_connect_success_saves_key(self):
        from zeline.connectors import store

        payload = {"cod": 200, "name": "London"}
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.connect(api_key="KEY")
        self.assertEqual(result, "Connected to OpenWeatherMap.")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/weather")
        self.assertEqual(kwargs["params"]["q"], "London")
        self.assertEqual(kwargs["params"]["appid"], "KEY")
        self.assertEqual(kwargs["params"]["units"], "metric")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(store.load("openweathermap"), {"api_key": "KEY"})

    def test_connect_no_key_stores_nothing(self):
        from zeline.connectors import store

        self.assertEqual(self.conn.connect(api_key=""), "ERROR: no API key provided.")
        self.assertIsNone(store.load("openweathermap"))

    def test_connect_network_error_stores_nothing(self):
        import requests
        from zeline.connectors import store

        with mock.patch("requests.get", side_effect=requests.ConnectionError("down")):
            result = self.conn.connect(api_key="KEY")
        self.assertTrue(result.startswith("ERROR: could not reach api.openweathermap.org"))
        self.assertIsNone(store.load("openweathermap"))

    def test_connect_http_401_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({"cod": "401"}, status=401)):
            result = self.conn.connect(api_key="BAD")
        self.assertTrue(result.startswith("ERROR: OpenWeatherMap rejected the API key"))
        self.assertIn("401", result)
        self.assertIsNone(store.load("openweathermap"))

    def test_connect_bad_cod_stores_nothing(self):
        from zeline.connectors import store

        with mock.patch("requests.get", return_value=FakeResponse({"cod": "401"})):
            result = self.conn.connect(api_key="BAD")
        self.assertTrue(result.startswith("ERROR: OpenWeatherMap rejected the API key"))
        self.assertIsNone(store.load("openweathermap"))

    def test_connect_unreadable_body_stores_nothing(self):
        from zeline.connectors import store

        class BadJson(FakeResponse):
            def json(self):
                raise ValueError("nope")

        with mock.patch("requests.get", return_value=BadJson({})):
            result = self.conn.connect(api_key="KEY")
        self.assertTrue(result.startswith("ERROR: OpenWeatherMap returned an unreadable response"))
        self.assertIsNone(store.load("openweathermap"))

    def test_status_connected(self):
        _seed_connected()
        self.assertEqual(self.conn.status(), {"connected": True, "detail": "API key stored"})
        self.assertNotIn("KEY", str(self.conn.status()))

    def test_status_not_connected(self):
        self.assertEqual(self.conn.status(), {"connected": False, "detail": "not linked"})

    def test_disconnect(self):
        _seed_connected()
        self.assertEqual(self.conn.disconnect(), "OpenWeatherMap disconnected.")
        self.assertEqual(self.conn.disconnect(), "OpenWeatherMap was not connected.")

    def test_is_connected(self):
        self.assertFalse(self.conn.is_connected())
        _seed_connected()
        self.assertTrue(self.conn.is_connected())

    def test_connector_metadata(self):
        self.assertEqual(self.conn.id, "openweathermap")
        self.assertEqual(self.conn.name, "OpenWeatherMap")
        self.assertEqual(self.conn.auth_kind, "pat")


class OpenWeatherMapOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zeline-owm-test-"))
        _patch_store(self, self.tmp)
        self.conn = OpenWeatherMapConnector()
        _seed_connected()

    def test_current_weather(self):
        payload = {
            "name": "London",
            "main": {"temp": 12.5, "humidity": 81},
            "weather": [{"description": "light rain"}],
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.current_weather("London")
        self.assertEqual(result, "London: 12.5°C, light rain (humidity 81%)")
        args, kwargs = get.call_args
        self.assertEqual(args[0], f"{API_BASE}/weather")
        self.assertEqual(kwargs["params"]["q"], "London")
        self.assertEqual(kwargs["params"]["appid"], "KEY")
        self.assertEqual(kwargs["timeout"], 30)

    def test_current_weather_no_city(self):
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.current_weather("  ")
        self.assertEqual(str(ctx.exception), "ERROR: no city provided.")

    def test_current_weather_http_error(self):
        with mock.patch("requests.get", return_value=FakeResponse({}, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.current_weather("Nowhere")
        self.assertIn("ERROR: OpenWeatherMap API 404 on /weather.", str(ctx.exception))

    def test_current_weather_network_error(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.Timeout("slow")):
            with self.assertRaises(RuntimeError) as ctx:
                self.conn.current_weather("London")
        self.assertIn("ERROR: OpenWeatherMap API request failed", str(ctx.exception))

    def test_forecast(self):
        payload = {
            "list": [
                {
                    "dt_txt": "2026-10-08 12:00:00",
                    "main": {"temp": 13.1},
                    "weather": [{"description": "clouds"}],
                },
                {
                    "dt_txt": "2026-10-08 15:00:00",
                    "main": {"temp": 14.2},
                    "weather": [{"description": "clear sky"}],
                },
            ]
        }
        with mock.patch("requests.get", return_value=FakeResponse(payload)) as get:
            result = self.conn.forecast("London", limit=2)
        self.assertEqual(
            result,
            "2026-10-08 12:00:00: 13.1°C, clouds\n2026-10-08 15:00:00: 14.2°C, clear sky",
        )
        self.assertEqual(get.call_args.kwargs["params"]["cnt"], 2)

    def test_forecast_limit_clamped(self):
        entries = [
            {
                "dt_txt": f"2026-10-08 {i:02d}:00:00",
                "main": {"temp": 10.0 + i},
                "weather": [{"description": "clouds"}],
            }
            for i in range(120)
        ]
        with mock.patch("requests.get", return_value=FakeResponse({"list": entries})) as get:
            result = self.conn.forecast("London", limit=500)
        self.assertEqual(len(result.splitlines()), 100)
        self.assertEqual(get.call_args.kwargs["params"]["cnt"], 100)

    def test_forecast_empty(self):
        with mock.patch("requests.get", return_value=FakeResponse({"list": []})):
            self.assertEqual(self.conn.forecast("London"), "No forecast entries found for London.")

    def test_operation_disconnected_raises(self):
        from zeline.connectors import store

        store.delete("openweathermap")
        with self.assertRaises(RuntimeError) as ctx:
            self.conn.current_weather("London")
        self.assertIn("zeline connect openweathermap", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            self.conn.forecast("London")


class OpenWeatherMapRegistryTests(unittest.TestCase):
    def test_module_import_does_not_leak(self):
        self.assertEqual(openweathermap_mod.OpenWeatherMapConnector.id, "openweathermap")


if __name__ == "__main__":
    unittest.main()
