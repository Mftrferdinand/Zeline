"""Tests for the Hetzner Cloud connector (all HTTP mocked, no network)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from zeline.connectors.hetzner import HetznerConnector


def _conn() -> HetznerConnector:
    return HetznerConnector()


def _resp(status_code: int, payload=None, json_raises: bool = False) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    if json_raises:
        resp.json.side_effect = ValueError("bad json")
    else:
        resp.json.return_value = payload if payload is not None else {}
    return resp


_SERVER = {
    "id": 4711,
    "name": "web-01",
    "status": "running",
    "server_type": {"name": "cx22"},
    "public_net": {"ipv4": {"ip": "138.201.1.2"}},
}

_CONNECT_PATH = "zeline.connectors.hetzner.requests.get"
_STORE = "zeline.connectors.hetzner.store"


# -- connect --------------------------------------------------------------

@patch(_STORE)
@patch(_CONNECT_PATH)
def test_connect_success_saves(mock_get, mock_store):
    mock_get.return_value = _resp(200, {"meta": {"pagination": {"total_entries": 5}}})
    result = _conn().connect(api_token="secret-token")
    assert not result.startswith("ERROR:")
    assert "Hetzner" in result
    mock_store.save.assert_called_once()
    saved_id, saved_creds = mock_store.save.call_args[0]
    assert saved_id == "hetzner"
    assert saved_creds == {"api_token": "secret-token"}


@patch(_STORE)
@patch(_CONNECT_PATH)
def test_connect_token_alias(mock_get, mock_store):
    mock_get.return_value = _resp(200)
    result = _conn().connect(token="alias-token")
    assert not result.startswith("ERROR:")
    mock_store.save.assert_called_once_with("hetzner", {"api_token": "alias-token"})


@patch(_STORE)
@patch(_CONNECT_PATH)
def test_connect_401_no_save(mock_get, mock_store):
    mock_get.return_value = _resp(401)
    result = _conn().connect(api_token="bad-token")
    assert result.startswith("ERROR:")
    mock_store.save.assert_not_called()


@patch(_STORE)
@patch(_CONNECT_PATH)
def test_connect_request_exception(mock_get, mock_store):
    mock_get.side_effect = requests.RequestException("network down")
    result = _conn().connect(api_token="secret-token")
    assert result.startswith("ERROR:")
    mock_store.save.assert_not_called()


@patch(_STORE)
@patch(_CONNECT_PATH)
def test_connect_200_non_json_no_crash(mock_get, mock_store):
    mock_get.return_value = _resp(200, json_raises=True)
    result = _conn().connect(api_token="secret-token")
    assert not result.startswith("ERROR:")
    mock_store.save.assert_called_once()


@patch(_STORE)
def test_connect_no_token(mock_store):
    result = _conn().connect(api_token="")
    assert result.startswith("ERROR:")
    mock_store.save.assert_not_called()


# -- disconnect / status ---------------------------------------------------

@patch(_STORE)
def test_disconnect_connected(mock_store):
    mock_store.delete.return_value = True
    assert "disconnected" in _conn().disconnect().lower()
    mock_store.delete.assert_called_once_with("hetzner")


@patch(_STORE)
def test_disconnect_not_connected(mock_store):
    mock_store.delete.return_value = False
    assert _conn().disconnect() == "Hetzner Cloud was not connected."


@patch(_STORE)
def test_status_connected_hides_secret(mock_store):
    mock_store.load.return_value = {"api_token": "super-secret-value"}
    st = _conn().status()
    assert st["connected"] is True
    assert "super-secret-value" not in repr(st)


@patch(_STORE)
def test_status_not_connected(mock_store):
    mock_store.load.return_value = None
    st = _conn().status()
    assert st["connected"] is False


# -- operations ------------------------------------------------------------

@patch(_STORE)
def test_list_servers_requires_connection(mock_store):
    mock_store.load.return_value = None
    with pytest.raises(RuntimeError, match="ERROR:"):
        _conn().list_servers()


@patch(_STORE)
@patch("zeline.connectors.hetzner.requests.request")
def test_list_servers_success(mock_request, mock_store):
    mock_store.load.return_value = {"api_token": "secret-token"}
    mock_request.return_value = _resp(200, {"servers": [_SERVER]})
    out = _conn().list_servers(limit=10)
    assert "4711" in out
    assert "web-01" in out
    assert "running" in out
    assert "cx22" in out
    assert "138.201.1.2" in out
    _, kwargs = mock_request.call_args
    assert kwargs["params"] == {"per_page": 10}


@patch(_STORE)
@patch("zeline.connectors.hetzner.requests.request")
def test_list_servers_limit_clamped(mock_request, mock_store):
    mock_store.load.return_value = {"api_token": "secret-token"}
    mock_request.return_value = _resp(200, {"servers": []})
    assert _conn().list_servers(limit=500) == "No servers found."
    _, kwargs = mock_request.call_args
    assert kwargs["params"] == {"per_page": 100}


@patch(_STORE)
@patch("zeline.connectors.hetzner.requests.request")
def test_list_servers_missing_fields_defensive(mock_request, mock_store):
    mock_store.load.return_value = {"api_token": "secret-token"}
    mock_request.return_value = _resp(200, {"servers": [{"id": 1}, "not-a-dict"]})
    out = _conn().list_servers()
    assert "1" in out  # sparse dict renders, non-dict skipped
    assert "No servers found." not in out


@patch(_STORE)
@patch("zeline.connectors.hetzner.requests.request")
def test_list_servers_500_raises(mock_request, mock_store):
    mock_store.load.return_value = {"api_token": "secret-token"}
    mock_request.return_value = _resp(500)
    with pytest.raises(RuntimeError, match="ERROR:"):
        _conn().list_servers()


@patch(_STORE)
@patch("zeline.connectors.hetzner.requests.request")
def test_list_servers_request_exception(mock_request, mock_store):
    mock_store.load.return_value = {"api_token": "secret-token"}
    mock_request.side_effect = requests.RequestException("timeout")
    with pytest.raises(RuntimeError, match="ERROR:"):
        _conn().list_servers()
