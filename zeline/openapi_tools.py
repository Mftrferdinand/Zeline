"""Load operator-owned OpenAPI documents as namespaced agent tools.

A local ``.yaml``, ``.yml`` or ``.json`` document in ``~/.zeline/openapi/``
turns each HTTP operation into one provider-ready tool.  The loader is purposely
small: it supports OpenAPI 3 documents and local ``#/...`` references, keeps
credentials out of tool schemas, and reports a broken document without hiding
valid tools from other files.
"""
from __future__ import annotations

import json
import ipaddress
import base64
import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import requests
import yaml

from zeline import config

TOOL_PREFIX = "api_"
ALLOWED_PROFILES = frozenset({"workspace", "full"})
_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
REQUEST_TIMEOUT = 60
USER_AGENT = "zeline-openapi/1"
_SCHEMA_KEYS = frozenset({
    "type", "description", "enum", "default", "format", "minimum", "maximum",
    "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems",
    "nullable", "examples",
})


def enabled() -> bool:
    return bool(getattr(config, "OPENAPI_TOOLS", True))


def specs_dir() -> Path:
    return config.DATA_DIR / "openapi"


def ensure_dir() -> Path:
    directory = specs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


@dataclass(frozen=True)
class ApiParameter:
    name: str
    location: str
    required: bool
    schema: dict[str, Any]


@dataclass(frozen=True)
class OpenApiTool:
    name: str
    description: str
    method: str
    url: str
    bindings: tuple[ApiParameter, ...]
    body_schema: dict[str, Any] | None
    body_required: bool
    source: Path
    document: dict[str, Any]
    security: tuple[dict[str, list[str]], ...]

    @property
    def provider_parameters(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for parameter in self.bindings:
            properties[parameter.name] = dict(parameter.schema)
            if parameter.required:
                required.append(parameter.name)
        if self.body_schema is not None:
            properties["body"] = dict(self.body_schema)
            if self.body_required:
                required.append("body")
        result: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            result["required"] = required
        return result

    @property
    def parameters(self) -> dict[str, Any]:
        """Provider JSON schema, matching the existing custom-tool contract."""
        return self.provider_parameters

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.provider_parameters,
            },
        }


def _pointer(document: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise ValueError(f"only local OpenAPI references are supported: {ref}")
    value: Any = document
    for raw in ref[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"unresolved OpenAPI reference: {ref}")
        value = value[key]
    return value


def _dereference(value: Any, document: dict[str, Any], *, depth: int = 0) -> Any:
    if depth > 20:
        raise ValueError("OpenAPI reference nesting exceeds 20 levels")
    if isinstance(value, dict) and "$ref" in value:
        resolved = _pointer(document, str(value["$ref"]))
        if not isinstance(resolved, dict):
            raise ValueError(f"OpenAPI reference is not an object: {value['$ref']}")
        merged = dict(resolved)
        merged.update({key: item for key, item in value.items() if key != "$ref"})
        return _dereference(merged, document, depth=depth + 1)
    return value


def _schema(value: Any, document: dict[str, Any], *, depth: int = 0) -> dict[str, Any]:
    if depth > 20:
        raise ValueError("OpenAPI schema nesting exceeds 20 levels")
    raw = _dereference(value or {}, document, depth=depth)
    if not isinstance(raw, dict):
        return {"type": "string"}
    result = {key: raw[key] for key in _SCHEMA_KEYS if key in raw}
    if "type" not in result:
        result["type"] = "object" if "properties" in raw else "string"
    if isinstance(raw.get("properties"), dict):
        result["properties"] = {
            str(name): _schema(child, document, depth=depth + 1)
            for name, child in raw["properties"].items()
        }
    if isinstance(raw.get("required"), list):
        result["required"] = [str(name) for name in raw["required"]]
    if "items" in raw:
        result["items"] = _schema(raw["items"], document, depth=depth + 1)
    if isinstance(raw.get("additionalProperties"), dict):
        result["additionalProperties"] = _schema(
            raw["additionalProperties"], document, depth=depth + 1
        )
    elif isinstance(raw.get("additionalProperties"), bool):
        result["additionalProperties"] = raw["additionalProperties"]
    return result


def _safe_name(value: str, fallback: str) -> str:
    name = _NAME_RE.sub("_", str(value or "")).strip("_-") or fallback
    return name[:64]


def credential_env_name(source: Path, scheme: str) -> str:
    """Environment variable holding one document's security credential."""
    provider = re.sub(r"[^A-Za-z0-9]+", "_", source.stem).strip("_").upper() or "API"
    security = re.sub(r"[^A-Za-z0-9]+", "_", scheme).strip("_").upper() or "AUTH"
    return f"ZELINE_OPENAPI_{provider}_{security}"


def _apply_security(
    tool: OpenApiTool,
    headers: dict[str, str],
    params: dict[str, Any],
    cookies: dict[str, Any],
) -> tuple[list[str], str | None]:
    """Apply the first security alternative whose local credentials are present."""
    if not tool.security:
        return [], None
    schemes = tool.document.get("components", {}).get("securitySchemes", {})
    if not isinstance(schemes, dict):
        return [], "OpenAPI securitySchemes is missing or invalid"
    missing_sets: list[list[str]] = []
    unsupported: list[str] = []
    for requirement in tool.security:
        if not requirement:
            return [], None
        pending: list[tuple[dict[str, Any], str]] = []
        missing: list[str] = []
        bad = False
        for scheme_name in requirement:
            raw = schemes.get(scheme_name)
            try:
                scheme = _dereference(raw, tool.document)
            except ValueError as exc:
                unsupported.append(str(exc))
                bad = True
                continue
            if not isinstance(scheme, dict):
                unsupported.append(f"security scheme '{scheme_name}' is invalid")
                bad = True
                continue
            env_name = credential_env_name(tool.source, scheme_name)
            secret = os.environ.get(env_name, "")
            if not secret:
                missing.append(env_name)
                continue
            pending.append((scheme, secret))
        if bad:
            continue
        if missing:
            missing_sets.append(missing)
            continue

        # Build one alternative in isolation. A requirement may contain several
        # schemes (logical AND), and a later unsupported scheme must not leave
        # credentials from an abandoned alternative in the real request.
        candidate_headers = dict(headers)
        candidate_params = dict(params)
        candidate_cookies = dict(cookies)
        destinations = {
            "header": candidate_headers,
            "query": candidate_params,
            "cookie": candidate_cookies,
        }
        secrets: list[str] = []
        for scheme, secret in pending:
            kind = str(scheme.get("type", ""))
            if kind == "apiKey":
                location = str(scheme.get("in", ""))
                key = str(scheme.get("name", ""))
                if not key or location not in destinations:
                    unsupported.append("apiKey security needs a header/query/cookie name")
                    bad = True
                    break
                destinations[location][key] = secret
            elif kind == "http" and str(scheme.get("scheme", "")).lower() == "bearer":
                candidate_headers["Authorization"] = f"Bearer {secret}"
            elif kind == "http" and str(scheme.get("scheme", "")).lower() == "basic":
                encoded = base64.b64encode(secret.encode("utf-8")).decode("ascii")
                candidate_headers["Authorization"] = f"Basic {encoded}"
            else:
                unsupported.append(
                    f"unsupported security type: {kind or '(empty)'} {scheme.get('scheme', '')}".strip()
                )
                bad = True
                break
            secrets.append(secret)
        if not bad:
            headers.clear()
            headers.update(candidate_headers)
            params.clear()
            params.update(candidate_params)
            cookies.clear()
            cookies.update(candidate_cookies)
            return secrets, None
    if missing_sets:
        choices = [" + ".join(names) for names in missing_sets]
        return [], "missing OpenAPI credential; set " + " or ".join(choices) + " in ~/.zeline/.env"
    detail = "; ".join(dict.fromkeys(unsupported)) or "no usable security alternative"
    return [], detail


def _server_url(servers: Any) -> str:
    if not isinstance(servers, list) or not servers or not isinstance(servers[0], dict):
        raise ValueError("OpenAPI document has no server URL")
    raw = str(servers[0].get("url", "")).strip()
    variables = servers[0].get("variables", {})
    if isinstance(variables, dict):
        for name, details in variables.items():
            default = details.get("default") if isinstance(details, dict) else None
            if default is None:
                raise ValueError(f"server variable '{name}' has no default")
            raw = raw.replace("{" + str(name) + "}", str(default))
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid OpenAPI server URL: {raw}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("OpenAPI server URL must not embed credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("OpenAPI server URL must not contain a fixed query or fragment")
    return raw.rstrip("/") + "/"


def _is_internal_host(host: str) -> bool:
    """Reject loopback, private, link-local and otherwise non-public targets."""
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return True
    if not addresses:
        return True
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if not address.is_global:
            return True
    return False


def _wire_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return [_wire_value(item) for item in value]
    if isinstance(value, (dict, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _operation_parameters(
    path_item: dict[str, Any], operation: dict[str, Any], document: dict[str, Any]
) -> tuple[ApiParameter, ...]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in (path_item.get("parameters", []), operation.get("parameters", [])):
        if not isinstance(candidate, list):
            continue
        for item in candidate:
            item = _dereference(item, document)
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            location = str(item.get("in", "")).strip().lower()
            if not name or location not in {"path", "query", "header", "cookie"}:
                raise ValueError(f"invalid OpenAPI parameter: {item!r}")
            lowered = name.casefold().replace("_", "-")
            if (
                (location == "header" and lowered in {"authorization", "proxy-authorization", "x-api-key", "api-key"})
                or (location in {"query", "cookie"} and lowered in {"api-key", "apikey", "access-token", "token"})
            ):
                raise ValueError(
                    f"credential parameter '{name}' must be declared in components.securitySchemes"
                )
            merged[(name, location)] = item

    by_name: dict[str, str] = {}
    result: list[ApiParameter] = []
    for (name, location), item in merged.items():
        previous = by_name.get(name)
        if previous is not None and previous != location:
            raise ValueError(
                f"parameter '{name}' appears in both {previous} and {location}; rename one"
            )
        by_name[name] = location
        schema = _schema(item.get("schema", {}), document)
        description = str(item.get("description", "")).strip()
        if description and "description" not in schema:
            schema["description"] = description
        result.append(ApiParameter(
            name=name,
            location=location,
            required=bool(item.get("required")) or location == "path",
            schema=schema,
        ))
    return tuple(result)


def _request_body(operation: dict[str, Any], document: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    body = operation.get("requestBody")
    if body is None:
        return None, False
    body = _dereference(body, document)
    if not isinstance(body, dict):
        raise ValueError("requestBody must be an object")
    content = body.get("content", {})
    if not isinstance(content, dict) or "application/json" not in content:
        raise ValueError("only application/json request bodies are supported")
    media = content["application/json"]
    if not isinstance(media, dict) or "schema" not in media:
        raise ValueError("application/json request body has no schema")
    return _schema(media["schema"], document), bool(body.get("required"))


def _parse_document(path: Path, document: Any) -> list[OpenApiTool]:
    if not isinstance(document, dict):
        raise ValueError("OpenAPI document must be an object")
    version = str(document.get("openapi", ""))
    if not version.startswith("3."):
        raise ValueError("only OpenAPI 3.x documents are supported")
    paths = document.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise ValueError("OpenAPI document has no paths")
    default_servers = document.get("servers")
    root_security = document.get("security", [])
    provider = _safe_name(path.stem, "api")
    tools: list[OpenApiTool] = []
    for route, raw_path_item in paths.items():
        route = str(route)
        if not route.startswith("/") or urlparse(route).scheme or "?" in route or "#" in route:
            raise ValueError(f"OpenAPI path must be a relative path beginning with '/': {route}")
        path_item = _dereference(raw_path_item, document)
        if not isinstance(path_item, dict):
            continue
        for method in _HTTP_METHODS:
            raw_operation = path_item.get(method)
            if not isinstance(raw_operation, dict):
                continue
            operation = _dereference(raw_operation, document)
            operation_id = _safe_name(
                str(operation.get("operationId", "")),
                f"{method}_{_safe_name(str(route), 'root')}",
            )
            name = _safe_name(f"{TOOL_PREFIX}{provider}_{operation_id}", f"{TOOL_PREFIX}{provider}")
            summary = str(operation.get("summary") or operation.get("description") or "").strip()
            description = summary or f"Call {method.upper()} {route}"
            servers = operation.get("servers") or path_item.get("servers") or default_servers
            base = _server_url(servers)
            url = urljoin(base, str(route).lstrip("/"))
            parameters = _operation_parameters(path_item, operation, document)
            body_schema, body_required = _request_body(operation, document)
            security_raw = operation.get("security", root_security)
            security = tuple(
                {str(key): [str(scope) for scope in value] for key, value in item.items()}
                for item in security_raw
                if isinstance(item, dict)
            ) if isinstance(security_raw, list) else ()
            tools.append(OpenApiTool(
                name=name,
                description=description,
                method=method.upper(),
                url=url,
                bindings=parameters,
                body_schema=body_schema,
                body_required=body_required,
                source=path,
                document=document,
                security=security,
            ))
    if not tools:
        raise ValueError("OpenAPI document has no supported HTTP operations")
    return tools


def _load(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


def discover(directory: Path | None = None) -> tuple[list[OpenApiTool], list[str]]:
    if not enabled():
        return [], []
    root = Path(directory) if directory is not None else specs_dir()
    if not root.is_dir():
        return [], []
    tools: list[OpenApiTool] = []
    errors: list[str] = []
    seen: dict[str, Path] = {}
    candidates = sorted(
        path for path in root.iterdir()
        if path.is_file() and not path.name.startswith("_")
        and path.suffix.lower() in {".yaml", ".yml", ".json"}
    )
    for path in candidates:
        try:
            parsed = _parse_document(path, _load(path))
        except (OSError, ValueError, TypeError, json.JSONDecodeError, yaml.YAMLError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        for tool in parsed:
            if tool.name in seen:
                errors.append(f"{path.name}: tool name '{tool.name}' already defined in {seen[tool.name].name}")
                continue
            seen[tool.name] = path
            tools.append(tool)
    return tools, errors


class OpenApiRegistry:
    """Hold parsed API operations for one agent security profile."""

    def __init__(self, profile: str, directory: Path | None = None):
        self.tools: dict[str, OpenApiTool] = {}
        self.errors: list[str] = []
        if profile not in ALLOWED_PROFILES:
            return
        found, self.errors = discover(directory)
        self.tools = {tool.name: tool for tool in found}

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self.tools.values()]

    def has_tool(self, name: str) -> bool:
        return name in self.tools

    def call(self, name: str, arguments: dict[str, Any]) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return f"ERROR: OpenAPI tool '{name}' is not registered."
        supplied = arguments if isinstance(arguments, dict) else {}
        missing = [
            parameter.name for parameter in tool.bindings
            if parameter.required and parameter.name not in supplied
        ]
        if tool.body_required and "body" not in supplied:
            missing.append("body")
        if missing:
            return "ERROR: missing required argument(s): " + ", ".join(missing)

        url = tool.url
        params: dict[str, Any] = {}
        headers = {"User-Agent": USER_AGENT}
        cookies: dict[str, Any] = {}
        for parameter in tool.bindings:
            if parameter.name in supplied:
                value = supplied[parameter.name]
            elif "default" in parameter.schema:
                value = parameter.schema["default"]
            else:
                continue
            if parameter.location == "path":
                url = url.replace(
                    "{" + parameter.name + "}",
                    quote(str(value), safe=""),
                )
            elif parameter.location == "query":
                params[parameter.name] = _wire_value(value)
            elif parameter.location == "header":
                headers[parameter.name] = str(_wire_value(value))
            elif parameter.location == "cookie":
                cookies[parameter.name] = _wire_value(value)

        secrets, security_error = _apply_security(tool, headers, params, cookies)
        if security_error:
            return f"ERROR: {security_error}."
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"} or not host:
            return "ERROR: OpenAPI operation resolved to an invalid URL."
        if _is_internal_host(host):
            return "ERROR: OpenAPI operation points to an internal address and is blocked."
        method = tool.method
        body = supplied.get("body") if tool.body_schema is not None else None
        initial = urlparse(url)
        initial_origin = (initial.scheme, initial.hostname, initial.port)
        response = None
        for redirect_count in range(6):
            try:
                response = requests.request(
                    method,
                    url,
                    params=params if redirect_count == 0 else {},
                    headers=headers,
                    cookies=cookies,
                    json=body,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                return f"ERROR OpenAPI request: {exc.__class__.__name__}: {exc}"
            location = response.headers.get("Location", "")
            if response.status_code not in {301, 302, 303, 307, 308} or not location:
                break
            if redirect_count == 5:
                return "ERROR: OpenAPI request exceeded 5 redirects."
            redirected = urljoin(url, location)
            target = urlparse(redirected)
            target_origin = (target.scheme, target.hostname, target.port)
            if target_origin != initial_origin:
                return "ERROR: cross-origin OpenAPI redirect is blocked."
            if not target.hostname or _is_internal_host(target.hostname):
                return "ERROR: OpenAPI redirect points to an internal address and is blocked."
            url = redirected
            if response.status_code == 303 or (
                response.status_code in {301, 302} and method not in {"GET", "HEAD"}
            ):
                method = "GET"
                body = None
        assert response is not None
        text = response.text or ""
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        if len(text) > 8_000:
            text = text[:8_000] + "\n... [truncated]"
        content_type = response.headers.get("Content-Type", "")
        return (
            f"Status: {response.status_code} {response.reason}\n"
            f"Content-Type: {content_type}\n\n{text}"
        ).strip()
