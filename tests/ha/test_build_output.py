"""Tool-level tests for trimmed validate / build output (#9)."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("homeassistant")

from homeassistant.helpers import llm as ha_llm  # noqa: E402

import custom_components.esphome_mcp_bridge.llm as bridge  # noqa: E402
from esphome_mcp_client import CommandResult  # noqa: E402
from tests.test_output import BUILD_OK, VALIDATE_FAILED, VALIDATE_OK  # noqa: E402

CTX = ha_llm.LLMContext(platform="test", context=None, language=None, assistant=None, device_id=None)


class FakeHass:
    def __init__(self):
        self.data: dict = {}

    def async_create_background_task(self, coro, name):
        return asyncio.ensure_future(coro)


class FakeBuildClient:
    def __init__(self, lines, exit_code):
        self.lines = lines
        self.exit_code = exit_code

    async def _run(self, *args, on_line=None, **kwargs):
        for line in self.lines:
            if on_line:
                on_line(line)
        return CommandResult(lines=list(self.lines), exit_code=self.exit_code)

    validate = compile = upload = run = clean = _run


@pytest.fixture
def build(monkeypatch):
    def configure(lines, exit_code=0):
        client = FakeBuildClient(lines, exit_code)

        async def fake_build_client(hass, slug, user_id):
            return client, "5c53de3b_esphome"

        monkeypatch.setattr(bridge, "_async_build_client", fake_build_client)
        return client

    return configure


async def run_tool(tool_cls, hass=None, **args):
    tool = tool_cls()
    return await tool.async_call(
        hass or FakeHass(), ha_llm.ToolInput(tool_name=tool.name, tool_args=args), CTX
    )


async def test_validate_success_is_trimmed(build):
    build(VALIDATE_OK, 0)
    resp = await run_tool(bridge.ValidateTool, configuration="decklights.yaml")
    assert resp["success"] is True
    assert resp["output"].splitlines() == [
        "INFO ESPHome 2026.10.0",
        "INFO Reading configuration /config/esphome/decklights.yaml...",
        "WARNING WiFi AP is configured but neither captive_portal nor web_server is enabled.",
        "INFO Configuration is valid!",
    ]
    assert resp["omitted_lines"] == 7
    assert "verbose=true" in resp["note"]


async def test_validate_verbose_returns_everything_without_ansi(build):
    build(VALIDATE_OK, 0)
    resp = await run_tool(bridge.ValidateTool, configuration="decklights.yaml", verbose=True)
    assert resp["omitted_lines"] == 0
    assert "note" not in resp
    assert "  name: decklights" in resp["output"].splitlines()
    assert "\\033" not in resp["output"]


async def test_validate_failure_keeps_diagnostics(build):
    build(VALIDATE_FAILED, 2)
    resp = await run_tool(bridge.ValidateTool, configuration="race.yaml")
    assert resp["success"] is False
    assert "Failed config" in resp["output"]
    assert "Did you mean [lambda]?" in resp["output"]
    assert resp["omitted_lines"] == 0


async def test_compile_output_is_trimmed(build):
    build(BUILD_OK, 0)
    resp = await run_tool(bridge.CompileTool, configuration="decklights.yaml")
    assert "INFO Successfully compiled program." in resp["output"]
    assert "-- Minimal build - OFF" not in resp["output"]
    assert resp["omitted_lines"] > 0


async def test_upload_output_is_trimmed_and_keeps_port(build):
    build(BUILD_OK, 0)
    resp = await run_tool(bridge.UploadTool, configuration="decklights.yaml", port="10.0.0.9")
    assert resp["port"] == "10.0.0.9"
    assert "esptool.py v4.12.0" not in resp["output"]


async def test_failed_compile_keeps_tail(build):
    lines = [f"[{i}/300] Building CXX object x{i}.o" for i in range(100)] + ["FAILED: race.cpp.obj"]
    build(lines, 1)
    resp = await run_tool(bridge.CompileTool, configuration="race.yaml")
    assert resp["output"].splitlines()[-1] == "FAILED: race.cpp.obj"
    assert len(resp["output"].splitlines()) == 40


async def test_background_job_status_is_trimmed_unless_verbose(build):
    build(BUILD_OK, 0)
    hass = FakeHass()
    started = await run_tool(bridge.CompileTool, hass, configuration="decklights.yaml", background=True)
    done = await run_tool(bridge.JobStatusTool, hass, job_id=started["job_id"], wait_seconds=5)
    assert done["status"] == "done"
    assert "-- Minimal build - OFF" not in done["output"]
    assert done["omitted_lines"] > 0
    assert done["line_count"] == len(BUILD_OK)  # raw count is still reported

    full = await run_tool(bridge.JobStatusTool, hass, job_id=started["job_id"], verbose=True)
    assert "-- Minimal build - OFF" in full["output"]
    assert full["omitted_lines"] == 0


async def test_log_jobs_are_not_trimmed():
    hass = FakeHass()
    job = bridge._job_registry(hass).create("logs", "race.yaml", None, stoppable=True)
    job.lines.extend(["-- not build noise here", "[D][race:001]: x"])
    job.status = "done"
    resp = await run_tool(bridge.JobStatusTool, hass, job_id=job.id)
    assert resp["output"].splitlines() == ["-- not build noise here", "[D][race:001]: x"]
    assert "omitted_lines" not in resp
