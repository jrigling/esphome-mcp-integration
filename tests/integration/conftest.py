"""Fixtures for integration smoke tests against a real ESPHome dashboard.

These tests launch the actual ``esphome dashboard`` server and exercise our
client against it, so they catch protocol drift if ESPHome changes the
dashboard API. They auto-skip when ESPHome is not installed, so the normal
unit-test run is unaffected.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

import pytest

SMOKE_YAML = "esphome:\n  name: smoke\nesp32:\n  board: esp32dev\n"
READY_TIMEOUT = 120


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def dashboard() -> str:
    """Launch ``esphome dashboard`` on a temp config dir; yield its base URL."""
    esphome = shutil.which("esphome")
    if not esphome:
        pytest.skip("esphome not installed; skipping integration smoke tests")

    tmp = tempfile.mkdtemp(prefix="esphome-smoke-")
    with open(os.path.join(tmp, "smoke.yaml"), "w", encoding="utf-8") as fh:
        fh.write(SMOKE_YAML)

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [esphome, "dashboard", tmp, "--port", str(port), "--address", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + READY_TIMEOUT
        while True:
            if proc.poll() is not None:
                raise RuntimeError("esphome dashboard exited before becoming ready")
            try:
                with urllib.request.urlopen(f"{base}/devices", timeout=2) as resp:
                    if resp.status == 200:
                        break
            except OSError:
                pass
            if time.time() > deadline:
                raise RuntimeError("esphome dashboard did not become ready in time")
            time.sleep(1)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)
