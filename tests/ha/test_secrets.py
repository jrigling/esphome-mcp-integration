"""Tests for the secrets insert logic and device-credential matching.

These live under tests/ha because the helpers are defined in the integration's
llm module, which imports Home Assistant. Auto-skips without HA installed.
"""
from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

import yaml  # noqa: E402

from custom_components.esphome_mcp_bridge.llm import (  # noqa: E402
    _insert_secret,
    _match_device,
    _SecretKeyExists,
)


def test_insert_creates_file_and_key(tmp_path):
    p = tmp_path / "secrets.yaml"
    _insert_secret(str(p), "wifi_password", "hunter2")
    assert yaml.safe_load(p.read_text())["wifi_password"] == "hunter2"


def test_insert_refuses_existing_key(tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text("api_key: abc\n")
    with pytest.raises(_SecretKeyExists):
        _insert_secret(str(p), "api_key", "different")
    # unchanged
    assert yaml.safe_load(p.read_text())["api_key"] == "abc"


def test_insert_preserves_existing_and_handles_specials(tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text("first: one")  # no trailing newline
    _insert_secret(str(p), "second", "a: b # tricky")
    assert yaml.safe_load(p.read_text()) == {"first": "one", "second": "a: b # tricky"}


def _creds():
    return [
        {"host": "1.1.1.1", "device_name": "kitchen", "port": 6053, "noise_psk": "k", "password": None},
        {"host": "2.2.2.2", "device_name": "garage", "port": 6053, "noise_psk": "g", "password": None},
    ]


def test_match_device_prefers_host():
    assert _match_device(_creds(), name="kitchen", address="2.2.2.2")["device_name"] == "garage"


def test_match_device_falls_back_to_name():
    assert _match_device(_creds(), name="kitchen", address=None)["host"] == "1.1.1.1"


def test_match_device_returns_none_when_unknown():
    assert _match_device(_creds(), name="unknown", address="9.9.9.9") is None
