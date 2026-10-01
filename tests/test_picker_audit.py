"""Offline regression coverage for Telegram picker responsiveness and validation."""
import importlib
import unittest
from unittest import mock


class PickerAuditTests(unittest.TestCase):
    def setUp(self):
        self.telegram = importlib.import_module("zeline.gateways.telegram")
        self.providers = [
            {"slug": "z", "name": "Zulu", "base_url": "https://z.invalid/v1", "api_key": "", "model": "cx/model"},
            {"slug": "a", "name": "Alpha", "base_url": "https://a.invalid/v1", "api_key": "", "model": "ag/model"},
        ]

    def test_root_picker_never_fetches_catalog(self):
        tg = self.telegram
        with mock.patch.object(tg, "_MODELS_CACHE", {}), \
             mock.patch.object(tg, "_MODEL_META_CACHE", {}), \
             mock.patch.object(tg, "_model_button_logos", return_value={}), \
             mock.patch.object(tg._HTTP, "get", side_effect=tg.requests.Timeout) as get:
            text, markup = tg._provider_picker_payload(self.providers, "z")
        get.assert_not_called()
        self.assertIn("Current: cx/model", text)
        rows = markup["inline_keyboard"]
        self.assertTrue(all(len(row) == 1 for row in rows))
        self.assertEqual([row[0]["callback_data"] for row in rows],
                         ["provider:1", "provider:0", "model:cancel"])
        self.assertTrue(all(row[0]["style"] == "primary" for row in rows[:-1]))
        self.assertNotIn("style", rows[-1][0])

    def test_root_picker_uses_cached_route_count_without_refresh(self):
        tg = self.telegram
        cache = {"https://z.invalid/v1": (0, ["cx/model", "ag/model", "ag/other"])}
        with mock.patch.object(tg, "_MODELS_CACHE", cache), \
             mock.patch.object(tg, "_model_button_logos", return_value={}), \
             mock.patch.object(tg._HTTP, "get", side_effect=tg.requests.Timeout) as get:
            _, markup = tg._provider_picker_payload(self.providers, "z")
        get.assert_not_called()
        self.assertEqual(markup["inline_keyboard"][1][0]["text"], "✓ Zulu (2)")

    def test_malformed_callbacks_do_not_select_or_discover_models(self):
        tg = self.telegram
        invalid = ["provider:-1", "routes:-1", "route:-1:0", "route:0:-1",
                   "route:0:0:extra", "model:-1:0", "model:0:-1", "model:-1",
                   "model:0:0:extra"]
        for data in invalid:
            with self.subTest(data=data), \
                 mock.patch.object(tg, "_configured_providers", return_value=self.providers), \
                 mock.patch.object(tg, "_model_button_logos", return_value={}), \
                 mock.patch.object(tg, "_discover_provider_models", return_value=["cx/model", "ag/model"]) as discover, \
                 mock.patch.object(tg, "_discover_models", return_value=["cx/model"]) as legacy, \
                 mock.patch.object(tg.config, "stored_config_copy", return_value={"provider": {}}), \
                 mock.patch.object(tg.config, "save_config") as save, \
                 mock.patch.object(tg, "_model_switch_text", return_value="Switched"), \
                 mock.patch.object(tg, "_edit_interactive") as edit, \
                 mock.patch.object(tg, "_api_call"):
                sessions = mock.Mock()
                tg._handle_callback("offline", {"id": "test", "data": data,
                                    "message": {"chat": {"id": 1}, "message_id": 2}}, sessions)
                discover.assert_not_called()
                legacy.assert_not_called()
                save.assert_not_called()
                sessions.switch_provider.assert_not_called()
                self.assertIn("expired", edit.call_args.args[3])


    def test_fetch_models_catalog_ttl_is_sixty_seconds(self):
        tg = self.telegram
        self.assertEqual(tg._MODELS_CACHE_TTL, 60.0)

    def test_fetch_models_catalog_forces_refresh_when_requested(self):
        tg = self.telegram
        tg._MODELS_CACHE.clear()
        tg._MODEL_META_CACHE.clear()
        base_url = "https://refresh.invalid/v1"

        class FakeResp:
            ok = True
            def __init__(self, data):
                self._data = data
            def json(self):
                return {"data": self._data}

        resp1 = FakeResp([{"id": "ag/gemini-3.7"}])
        resp2 = FakeResp([{"id": "ag/gemini-3.7"}, {"id": "cx/gpt-5.6"}])

        with mock.patch.object(tg._HTTP, "get", side_effect=[resp1, resp2]) as get:
            ids1, _ = tg._fetch_models_catalog(base_url, "token")
            self.assertEqual(ids1, ["ag/gemini-3.7"])
            self.assertEqual(get.call_count, 1)

            # Normal call within TTL still cached
            ids_cached, _ = tg._fetch_models_catalog(base_url, "token")
            self.assertEqual(ids_cached, ["ag/gemini-3.7"])
            self.assertEqual(get.call_count, 1)

            # force_refresh bypasses cache and updates live
            ids2, _ = tg._fetch_models_catalog(base_url, "token", force_refresh=True)
            self.assertEqual(ids2, ["ag/gemini-3.7", "cx/gpt-5.6"])
            self.assertEqual(get.call_count, 2)

    def test_discover_provider_models_supports_force_refresh(self):
        tg = self.telegram
        provider = {"slug": "p", "base_url": "https://prov.invalid/v1", "api_key": "k", "model": "old"}
        with mock.patch.object(tg, "_fetch_models_catalog", return_value=(["m1", "m2"], {})) as fetch:
            models = tg._discover_provider_models(provider, force_refresh=True)
            self.assertEqual(models, ["m1", "m2"])
            fetch.assert_called_once_with("https://prov.invalid/v1", "k", force_refresh=True)

    def test_handle_command_model_clears_or_refreshes_live_models(self):
        tg = self.telegram
        provider = {"slug": "9router", "name": "9Router", "base_url": "https://9r.invalid/v1", "api_key": "k", "model": "ag/gemini"}
        with mock.patch.object(tg, "_configured_providers", return_value=[provider]), \
             mock.patch.object(tg, "_fetch_models_catalog", return_value=(["ag/gemini"], {})) as fetch, \
             mock.patch.object(tg, "_api_call") as api:
            sessions = mock.Mock()
            handled = tg._handle_command_update(
                "https://tg.invalid", "/model", sessions, "telegram:123", 123,
                stop_event=mock.Mock(), tool_profile="full"
            )
            self.assertTrue(handled)
            fetch.assert_called_once_with("https://9r.invalid/v1", "k", force_refresh=True)


if __name__ == "__main__":
    unittest.main()
