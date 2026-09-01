"""Contract tests for OpenAPI-defined operator tools."""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))


class _FakeProviderResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.text = json.dumps(payload)
        self.status_code = status_code
        self.ok = status_code < 400
        self.encoding = "utf-8"


def fresh(home: Path):
    os.environ["ZELINE_HOME"] = str(home)
    for name in list(sys.modules):
        if name == "zeline" or name.startswith("zeline."):
            sys.modules.pop(name, None)
    try:
        config = importlib.import_module("zeline.config")
        openapi = importlib.import_module("zeline.openapi_tools")
    except ModuleNotFoundError as exc:
        raise AssertionError("OpenAPI tools module is missing") from exc
    return config, openapi


class OpenApiDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "zhome"
        self._saved = os.environ.get("ZELINE_HOME")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ZELINE_HOME", None)
        else:
            os.environ["ZELINE_HOME"] = self._saved
        for name in list(sys.modules):
            if name == "zeline" or name.startswith("zeline."):
                sys.modules.pop(name, None)
        self._tmp.cleanup()

    def test_an_openapi_operation_becomes_a_namespaced_tool(self):
        config, openapi = fresh(self.home)
        directory = openapi.ensure_dir()
        (directory / "pets.yaml").write_text(
            """openapi: 3.0.3
info:
  title: Pet service
  version: 1.0.0
servers:
  - url: https://api.example.test/v1
paths:
  /pets/{pet_id}:
    get:
      operationId: lookupPet
      summary: Look up one pet
      parameters:
        - name: pet_id
          in: path
          required: true
          schema: {type: string}
        - name: verbose
          in: query
          schema: {type: boolean}
""",
            encoding="utf-8",
        )

        tools, errors = openapi.discover()

        self.assertEqual(errors, [])
        self.assertEqual([tool.name for tool in tools], ["api_pets_lookupPet"])
        self.assertEqual(tools[0].description, "Look up one pet")
        parameters = tools[0].parameters
        self.assertEqual(parameters["required"], ["pet_id"])
        self.assertEqual(parameters["properties"]["pet_id"]["type"], "string")
        self.assertEqual(parameters["properties"]["verbose"]["type"], "boolean")

    def test_one_bad_document_does_not_hide_valid_tools(self):
        _, openapi = fresh(self.home)
        directory = openapi.ensure_dir()
        (directory / "broken.yaml").write_text("openapi: [", encoding="utf-8")
        (directory / "good.yaml").write_text(
            """openapi: 3.0.3
info: {title: Good, version: 1.0.0}
servers: [{url: https://api.example.test}]
paths: {/health: {get: {operationId: health}}}
""",
            encoding="utf-8",
        )

        tools, errors = openapi.discover()

        self.assertEqual([tool.name for tool in tools], ["api_good_health"])
        self.assertEqual(len(errors), 1)
        self.assertIn("broken.yaml", errors[0])

    def test_server_urls_cannot_embed_credentials_or_fixed_query_secrets(self):
        _, openapi = fresh(self.home)
        directory = openapi.ensure_dir()
        for filename, url in (
            ("userinfo.yaml", "https://user:pass@api.example.test"),
            ("query.yaml", "https://api.example.test?token=hidden"),
        ):
            (directory / filename).write_text(
                f"""openapi: 3.0.3
info: {{title: Unsafe, version: 1.0.0}}
servers: [{{url: {url}}}]
paths: {{/one: {{get: {{operationId: one}}}}}}
""",
                encoding="utf-8",
            )

        tools, errors = openapi.discover()

        self.assertEqual(tools, [])
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("credential" in error or "query" in error for error in errors))

    def test_path_keys_cannot_override_the_declared_server(self):
        _, openapi = fresh(self.home)
        directory = openapi.ensure_dir()
        for index, route in enumerate(("https://other.example.test/pwn", "/ok?token=hidden")):
            (directory / f"route-{index}.yaml").write_text(
                f"""openapi: 3.0.3
info: {{title: Unsafe route, version: 1.0.0}}
servers: [{{url: https://api.example.test}}]
paths:
  {route}:
    get: {{operationId: unsafe}}
""",
                encoding="utf-8",
            )

        tools, errors = openapi.discover()

        self.assertEqual(tools, [])
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("path" in error.lower() for error in errors))

    def test_auth_shaped_parameters_must_use_security_schemes(self):
        _, openapi = fresh(self.home)
        directory = openapi.ensure_dir()
        (directory / "unsafe.yaml").write_text(
            """openapi: 3.0.3
info: {title: Unsafe, version: 1.0.0}
servers: [{url: https://api.example.test}]
paths:
  /private:
    get:
      operationId: private
      parameters:
        - {name: Authorization, in: header, required: true, schema: {type: string}}
""",
            encoding="utf-8",
        )

        tools, errors = openapi.discover()

        self.assertEqual(tools, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("securitySchemes", errors[0])

    def test_local_references_and_parameter_defaults_are_applied(self):
        _, openapi = fresh(self.home)
        directory = openapi.ensure_dir()
        (directory / "refs.yaml").write_text(
            """openapi: 3.0.3
info: {title: Refs, version: 1.0.0}
servers: [{url: https://api.example.test}]
components:
  parameters:
    Limit:
      name: limit
      in: query
      schema: {type: integer, default: 25}
  schemas:
    Payload:
      type: object
      required: [name]
      properties:
        name: {type: string}
paths:
  /items:
    post:
      operationId: createItem
      parameters: [{$ref: '#/components/parameters/Limit'}]
      requestBody:
        required: true
        content:
          application/json:
            schema: {$ref: '#/components/schemas/Payload'}
""",
            encoding="utf-8",
        )
        registry = openapi.OpenApiRegistry("full")
        tool = registry.tools["api_refs_createItem"]
        self.assertEqual(tool.parameters["properties"]["limit"]["default"], 25)
        self.assertEqual(tool.parameters["properties"]["body"]["required"], ["name"])
        response = mock.Mock(status_code=201, reason="Created", text="ok")
        response.headers = {"Content-Type": "text/plain"}

        with mock.patch.object(openapi, "_is_internal_host", return_value=False), mock.patch.object(
            openapi.requests, "request", return_value=response
        ) as request:
            result = registry.call(tool.name, {"body": {"name": "one"}})

        self.assertIn("Status: 201 Created", result)
        self.assertEqual(request.call_args.kwargs["params"], {"limit": "25"})

    def test_operator_executor_advertises_openapi_tools_but_safe_does_not(self):
        _, openapi = fresh(self.home)
        directory = openapi.ensure_dir()
        (directory / "status.yaml").write_text(
            """openapi: 3.0.3
info: {title: Status, version: 1.0.0}
servers: [{url: https://status.example.test}]
paths:
  /health:
    get:
      operationId: health
      summary: Read service health
""",
            encoding="utf-8",
        )
        tools_module = importlib.import_module("zeline.tools")

        full = tools_module.ToolExecutor("cli:local", "full", self.home)
        safe = tools_module.ToolExecutor("telegram:public", "safe", self.home)
        full_names = [item["function"]["name"] for item in full.all_schemas]
        safe_names = [item["function"]["name"] for item in safe.all_schemas]

        self.assertIn("api_status_health", full_names)
        self.assertNotIn("api_status_health", safe_names)

    def test_dispatch_assembles_path_query_header_cookie_and_json_body(self):
        _, openapi = fresh(self.home)
        directory = openapi.ensure_dir()
        (directory / "orders.yaml").write_text(
            """openapi: 3.0.3
info: {title: Orders, version: 1.0.0}
servers: [{url: https://api.example.test/v2}]
paths:
  /orders/{order_id}:
    post:
      operationId: updateOrder
      parameters:
        - {name: order_id, in: path, required: true, schema: {type: string}}
        - {name: dry_run, in: query, schema: {type: boolean}}
        - {name: X-Trace, in: header, schema: {type: string}}
        - {name: session, in: cookie, schema: {type: string}}
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [status]
              properties:
                status: {type: string}
""",
            encoding="utf-8",
        )
        registry = openapi.OpenApiRegistry("full")
        response = mock.Mock(status_code=200, reason="OK", text='{"ok":true}')
        response.headers = {"Content-Type": "application/json"}

        with mock.patch.object(openapi, "_is_internal_host", return_value=False), mock.patch.object(
            openapi.requests, "request", return_value=response
        ) as request:
            result = registry.call(
                "api_orders_updateOrder",
                {
                    "order_id": "A/B",
                    "dry_run": True,
                    "X-Trace": "trace-7",
                    "session": "cookie-9",
                    "body": {"status": "ready"},
                },
            )

        self.assertIn('"ok":true', result)
        request.assert_called_once_with(
            "POST",
            "https://api.example.test/v2/orders/A%2FB",
            params={"dry_run": "true"},
            headers={"User-Agent": openapi.USER_AGENT, "X-Trace": "trace-7"},
            cookies={"session": "cookie-9"},
            json={"status": "ready"},
            timeout=openapi.REQUEST_TIMEOUT,
            allow_redirects=False,
        )

    def test_tool_executor_dispatches_an_openapi_call(self):
        _, openapi = fresh(self.home)
        directory = openapi.ensure_dir()
        (directory / "ping.yaml").write_text(
            """openapi: 3.0.3
info: {title: Ping, version: 1.0.0}
servers: [{url: https://api.example.test}]
paths:
  /ping:
    get: {operationId: ping, summary: Ping the service}
""",
            encoding="utf-8",
        )
        tools_module = importlib.import_module("zeline.tools")
        executor = tools_module.ToolExecutor("cli:local", "full", self.home)
        response = mock.Mock(status_code=204, reason="No Content", text="")
        response.headers = {"Content-Type": ""}

        with mock.patch.object(openapi, "_is_internal_host", return_value=False), mock.patch.object(
            openapi.requests, "request", return_value=response
        ):
            result = executor.run("api_ping_ping", {})

        self.assertIn("Status: 204 No Content", result)

    def test_real_agent_turn_advertises_calls_and_consumes_an_openapi_tool(self):
        _, openapi = fresh(self.home)
        directory = openapi.ensure_dir()
        (directory / "ping.yaml").write_text(
            """openapi: 3.0.3
info: {title: Ping, version: 1.0.0}
servers: [{url: https://api.example.test}]
paths:
  /ping:
    get: {operationId: ping, summary: Ping the service}
""",
            encoding="utf-8",
        )
        config = importlib.import_module("zeline.config")
        setattr(config, "STREAM_RESPONSES", False)
        setattr(config, "API_KEY", "TOKEN-PLACEHOLDER-9")
        setattr(config, "BASE_URL", "https://provider.example.test/v1")
        setattr(config, "MODEL", "test-model")
        agent_module = importlib.import_module("zeline.agent")
        first = _FakeProviderResponse({
            "choices": [{"message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-openapi-1",
                    "type": "function",
                    "function": {"name": "api_ping_ping", "arguments": "{}"},
                }],
            }}],
        })
        final = _FakeProviderResponse({
            "choices": [{"message": {"role": "assistant", "content": "API says pong."}}],
        })
        response = mock.Mock(status_code=200, reason="OK", text="pong")
        response.headers = {"Content-Type": "text/plain"}
        agent = agent_module.Zeline(identity="cli:openapi", tool_profile="full", workspace=str(self.home))

        with mock.patch.object(agent_module.requests, "post", side_effect=[first, final]) as provider, mock.patch.object(
            openapi, "_is_internal_host", return_value=False
        ), mock.patch.object(openapi.requests, "request", return_value=response):
            reply = agent.send("ping the configured API")

        self.assertEqual(reply, "API says pong.")
        first_tools = provider.call_args_list[0].kwargs["json"]["tools"]
        # Lazy schemas may put the operation inside tool_search's catalogue
        # instead of paying to send its full shape. Either way the provider must
        # receive the name, and a direct listed call must still dispatch.
        self.assertIn("api_ping_ping", json.dumps(first_tools))
        second_messages = provider.call_args_list[1].kwargs["json"]["messages"]
        tool_message = next(item for item in second_messages if item.get("tool_call_id") == "call-openapi-1")
        self.assertIn("pong", tool_message["content"])

    def test_security_credentials_come_from_environment_not_the_model_schema(self):
        _, openapi = fresh(self.home)
        directory = openapi.ensure_dir()
        source = directory / "secure.yaml"
        source.write_text(
            """openapi: 3.0.3
info: {title: Secure, version: 1.0.0}
servers: [{url: https://api.example.test}]
components:
  securitySchemes:
    serviceToken: {type: apiKey, in: header, name: X-Service-Key}
security: [{serviceToken: []}]
paths:
  /private:
    get: {operationId: readPrivate, summary: Read private data}
""",
            encoding="utf-8",
        )
        registry = openapi.OpenApiRegistry("full")
        tool = registry.tools["api_secure_readPrivate"]
        env_name = openapi.credential_env_name(source, "serviceToken")
        self.assertNotIn("serviceToken", str(tool.schema()))
        self.assertNotIn("X-Service-Key", str(tool.schema()))

        missing = registry.call(tool.name, {})
        self.assertIn(env_name, missing)
        self.assertNotIn("serviceToken", tool.parameters["properties"])

        response = mock.Mock(status_code=200, reason="OK", text="done")
        response.headers = {"Content-Type": "text/plain"}
        with mock.patch.dict(os.environ, {env_name: "TOKEN-PLACEHOLDER-9"}), mock.patch.object(
            openapi, "_is_internal_host", return_value=False
        ), mock.patch.object(openapi.requests, "request", return_value=response) as request:
            result = registry.call(tool.name, {})

        self.assertIn("done", result)
        self.assertEqual(request.call_args.kwargs["headers"]["X-Service-Key"], "TOKEN-PLACEHOLDER-9")
        self.assertNotIn("TOKEN-PLACEHOLDER-9", result)

    def test_failed_security_alternative_does_not_leak_into_the_next_one(self):
        _, openapi = fresh(self.home)
        directory = openapi.ensure_dir()
        source = directory / "alternatives.yaml"
        source.write_text(
            """openapi: 3.0.3
info: {title: Alternatives, version: 1.0.0}
servers: [{url: https://api.example.test}]
components:
  securitySchemes:
    firstKey: {type: apiKey, in: header, name: X-First}
    unsupported: {type: oauth2, flows: {}}
    secondKey: {type: apiKey, in: header, name: X-Second}
security:
  - {firstKey: [], unsupported: []}
  - {secondKey: []}
paths: {/private: {get: {operationId: private}}}
""",
            encoding="utf-8",
        )
        registry = openapi.OpenApiRegistry("full")
        response = mock.Mock(status_code=200, reason="OK", text="done")
        response.headers = {"Content-Type": "text/plain"}
        credentials = {
            openapi.credential_env_name(source, "firstKey"): "FIRST-PLACEHOLDER-1",
            openapi.credential_env_name(source, "unsupported"): "UNUSED-PLACEHOLDER-2",
            openapi.credential_env_name(source, "secondKey"): "SECOND-PLACEHOLDER-3",
        }

        with mock.patch.dict(os.environ, credentials), mock.patch.object(
            openapi, "_is_internal_host", return_value=False
        ), mock.patch.object(openapi.requests, "request", return_value=response) as request:
            result = registry.call("api_alternatives_private", {})

        self.assertIn("done", result)
        sent = request.call_args.kwargs["headers"]
        self.assertNotIn("X-First", sent)
        self.assertEqual(sent["X-Second"], "SECOND-PLACEHOLDER-3")

    def test_redirect_to_internal_address_is_blocked_before_following(self):
        _, openapi = fresh(self.home)
        directory = openapi.ensure_dir()
        (directory / "redirect.yaml").write_text(
            """openapi: 3.0.3
info: {title: Redirect, version: 1.0.0}
servers: [{url: https://api.example.test}]
paths: {/start: {get: {operationId: start}}}
""",
            encoding="utf-8",
        )
        registry = openapi.OpenApiRegistry("full")
        redirect = mock.Mock(status_code=302, reason="Found", text="")
        redirect.headers = {"Location": "http://127.0.0.1/private"}

        with mock.patch.object(openapi, "_is_internal_host", side_effect=[False, True]), mock.patch.object(
            openapi.requests, "request", return_value=redirect
        ) as request:
            result = registry.call("api_redirect_start", {})

        self.assertIn("redirect", result.lower())
        self.assertIn("blocked", result.lower())
        self.assertEqual(request.call_count, 1)
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_owner_can_disable_openapi_tools_in_config(self):
        config, openapi = fresh(self.home)
        directory = openapi.ensure_dir()
        (directory / "one.yaml").write_text(
            """openapi: 3.0.3
info: {title: One, version: 1.0.0}
servers: [{url: https://api.example.test}]
paths: {/one: {get: {operationId: one}}}
""",
            encoding="utf-8",
        )
        saved = config.config_copy()
        saved["tools"]["openapi_tools"] = False
        config.save_config(saved)

        self.assertFalse(openapi.enabled())
        self.assertEqual(openapi.discover(), ([], []))

    def test_cli_adds_and_lists_a_valid_spec(self):
        _, openapi = fresh(self.home)
        cli = importlib.import_module("zeline.cli")
        source = Path(self._tmp.name) / "weather.yaml"
        source.write_text(
            """openapi: 3.0.3
info: {title: Weather, version: 1.0.0}
servers: [{url: https://weather.example.test}]
paths:
  /current:
    get: {operationId: currentWeather, summary: Read current weather}
""",
            encoding="utf-8",
        )

        self.assertEqual(cli.cmd_openapi("add", path=str(source)), 0)
        self.assertTrue((openapi.specs_dir() / "weather.yaml").is_file())
        self.assertEqual(cli.cmd_openapi("list"), 0)
        parser = cli.build_parser()
        parsed = parser.parse_args(["tools", "openapi-add", str(source)])
        self.assertEqual(parsed.tools_command, "openapi-add")
        self.assertEqual(parsed.path, str(source))


if __name__ == "__main__":
    unittest.main()
