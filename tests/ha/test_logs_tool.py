"""Tool-level tests for esphome_logs and esphome_job_status.

They run the real tools against Home Assistant's ``llm`` helper types. The
device side is the real ``async_stream_device_logs`` driven by a fake
aioesphomeapi client, so filtering, line caps, background jobs and early stop
are exercised end to end; the dashboard side is a fake build client.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("homeassistant")

from homeassistant.helpers import llm as ha_llm  # noqa: E402

import custom_components.esphome_mcp_bridge.llm as bridge  # noqa: E402
from esphome_mcp_client import CommandResult  # noqa: E402
from esphome_mcp_client import device_logs as device_logs_mod  # noqa: E402

ESC = "\x1b"


class FakeEntry:
    def __init__(self, data):
        self.data = data


class FakeConfigEntries:
    def __init__(self, esphome_entries):
        self._entries = esphome_entries

    def async_entries(self, domain):
        return self._entries if domain == "esphome" else []


class FakeHass:
    def __init__(self, esphome_entries=()):
        self.data: dict = {}
        self.config_entries = FakeConfigEntries(list(esphome_entries))
        self.tasks: list[asyncio.Task] = []

    def async_create_background_task(self, coro, name):
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task


class FakeDeviceClient:
    """Mirrors aioesphomeapi.APIClient's connect/subscribe_logs/disconnect."""

    instances: list[FakeDeviceClient] = []

    def __init__(self, messages, interval=0.0, forever=False):
        self.messages = messages
        self.interval = interval
        self.forever = forever
        self.subscribe_args = None
        self.unsubscribed = False
        FakeDeviceClient.instances.append(self)

    async def connect(self, login=False):
        return None

    def subscribe_logs(self, on_log, log_level=None, dump_config=None):
        self.subscribe_args = {"log_level": log_level, "dump_config": dump_config}

        async def deliver():
            for raw in self.messages:
                await asyncio.sleep(self.interval)
                if self.unsubscribed:
                    return
                on_log(type("Msg", (), {"message": raw.encode()})())
            i = 0
            while self.forever and not self.unsubscribed:
                await asyncio.sleep(0.01)
                on_log(type("Msg", (), {"message": f"[D][tick:001]: {i}".encode()})())
                i += 1

        asyncio.ensure_future(deliver())

        def unsub():
            self.unsubscribed = True

        return unsub

    async def disconnect(self):
        return None


class FakeDashboard:
    def __init__(self, devices):
        self._devices = devices

    async def inventory(self):
        return self._devices


class FakeBuildClient:
    def __init__(self, lines):
        self.lines = lines
        self.calls: list[dict] = []

    async def logs(self, configuration, port, **kwargs):
        self.calls.append({"configuration": configuration, "port": port, **kwargs})
        flt = kwargs["line_filter"]
        result = CommandResult(truncated=True)
        for raw in self.lines:
            for line in flt.feed(raw):
                result.lines.append(line)
                if kwargs.get("on_line"):
                    kwargs["on_line"](line)
        return result


def dev(letter, tag, msg):
    return f"{ESC}[0;36m[{letter}][{tag}:001]: {msg}{ESC}[0m"


RACE_DEVICE = {"name": "thingy-race", "configuration": "thingy-race.yaml", "address": "10.0.0.5"}
RACE_ENTRY = FakeEntry({"host": "10.0.0.5", "port": 6053, "noise_psk": "k", "password": None,
                        "device_name": "thingy-race"})


@pytest.fixture
def wire(monkeypatch):
    """Patch the dashboard/device seams; returns a configurator."""
    FakeDeviceClient.instances.clear()
    state: dict = {"captured_kwargs": []}

    def configure(*, device_messages=None, interval=0.0, forever=False,
                  dashboard_lines=None, devices=(RACE_DEVICE,)):
        async def fake_dashboard(hass, slug, user_id):
            return FakeDashboard(list(devices)), "5c53de3b_esphome"

        build = FakeBuildClient(dashboard_lines or [])
        state["build"] = build

        async def fake_build_client(hass, slug, user_id):
            return build, "5c53de3b_esphome"

        real_stream = device_logs_mod.async_stream_device_logs

        async def fake_stream(address, **kwargs):
            state["captured_kwargs"].append({"address": address, **kwargs})
            client = FakeDeviceClient(device_messages or [], interval, forever)
            return await real_stream(address, client_factory=lambda: client, **kwargs)

        monkeypatch.setattr(bridge, "_async_dashboard", fake_dashboard)
        monkeypatch.setattr(bridge, "_async_build_client", fake_build_client)
        monkeypatch.setattr(bridge, "async_stream_device_logs", fake_stream)
        return state

    return configure


def call(tool, **args):
    return ha_llm.ToolInput(tool_name=tool.name, tool_args=args)


CTX = ha_llm.LLMContext(platform="test", context=None, language=None, assistant=None, device_id=None)


async def logs(hass, **args):
    return await bridge.LogsTool().async_call(hass, call(bridge.LogsTool(), **args), CTX)


async def status(hass, **args):
    return await bridge.JobStatusTool().async_call(hass, call(bridge.JobStatusTool(), **args), CTX)


# ---- device mode: the #10 fix and filtering ----------------------------------


async def test_device_capture_returns_lines_and_requests_very_verbose(wire):
    state = wire(device_messages=[dev("C", "logger", "Logger:"), dev("I", "race", "Lap 1")])
    hass = FakeHass([RACE_ENTRY])
    resp = await logs(hass, configuration="thingy-race.yaml", max_seconds=1)
    assert resp["mode"] == "device"
    assert resp["address"] == "10.0.0.5"
    assert resp["output"] == "[I][race:001]: Lap 1"  # config dump skipped by default
    assert resp["success"] is True
    assert resp["lines_received"] == 2
    assert resp["lines_returned"] == 1
    assert resp["ended_by"] == "window"
    assert FakeDeviceClient.instances[0].subscribe_args == {"log_level": 7, "dump_config": False}
    assert state["captured_kwargs"][0]["max_seconds"] == 1


async def test_level_tags_search_and_context_are_applied(wire):
    messages = [dev("D", "race", "tick"), dev("W", "esp32_ble_client", "Disconnected"),
                dev("I", "race", "Lap 2"), dev("I", "wifi", "ok"), dev("W", "race", "Crash")]
    wire(device_messages=messages)
    hass = FakeHass([RACE_ENTRY])
    resp = await logs(hass, configuration="thingy-race.yaml", max_seconds=1,
                      level="info", tags=["race", "esp32_ble*"], search="crash", context=1)
    assert resp["output"].splitlines() == ["[I][race:001]: Lap 2", "[W][race:001]: Crash"]
    assert resp["filters"] == {"level": "info", "include_config_dump": False,
                               "tags": ["race", "esp32_ble*"], "search": "crash", "context": 1}
    # The level filter is also pushed down to the device subscription.
    assert FakeDeviceClient.instances[0].subscribe_args["log_level"] == 3


async def test_include_config_dump_requests_it_from_the_device(wire):
    wire(device_messages=[dev("C", "logger", "Logger:")])
    hass = FakeHass([RACE_ENTRY])
    resp = await logs(hass, configuration="thingy-race.yaml", max_seconds=1, include_config_dump=True)
    assert resp["output"] == "[C][logger:001]: Logger:"
    assert FakeDeviceClient.instances[0].subscribe_args["dump_config"] is True


async def test_idle_device_gets_an_explanatory_note(wire):
    wire(device_messages=[])
    resp = await logs(FakeHass([RACE_ENTRY]), configuration="thingy-race.yaml", max_seconds=1)
    assert resp["output"] == ""
    assert "sent no log lines" in resp["note"]


async def test_idle_with_level_filter_explains_the_level(wire):
    # Seen live: woodfire logs only at debug, so level=info yields nothing at all.
    wire(device_messages=[])
    resp = await logs(FakeHass([RACE_ENTRY]), configuration="thingy-race.yaml",
                      max_seconds=1, level="info")
    assert "level 'info'" in resp["note"]
    assert "idle" not in resp["note"]


async def test_nothing_matched_note(wire):
    wire(device_messages=[dev("D", "wifi", "x")])
    resp = await logs(FakeHass([RACE_ENTRY]), configuration="thingy-race.yaml",
                      max_seconds=1, tags=["race"])
    assert "none matched" in resp["note"]


async def test_max_lines_counts_kept_lines(wire):
    wire(device_messages=[dev("D", "wifi", "n")] * 50 + [dev("I", "race", f"L{i}") for i in range(5)])
    resp = await logs(FakeHass([RACE_ENTRY]), configuration="thingy-race.yaml",
                      max_seconds=5, max_lines=2, tags=["race"])
    assert resp["output"].splitlines() == ["[I][race:001]: L0", "[I][race:001]: L1"]
    assert resp["truncated"] is True
    assert resp["ended_by"] == "max_lines"


# ---- argument validation and the sync cap (#11) -------------------------------


async def test_sync_capture_is_capped_with_a_note(wire):
    # One kept line ends the capture at once, so the 30 s cap is never waited out.
    state = wire(device_messages=[dev("I", "race", "x")])
    resp = await logs(FakeHass([RACE_ENTRY]), configuration="thingy-race.yaml",
                      max_seconds=90, max_lines=1)
    assert state["captured_kwargs"][0]["max_seconds"] == bridge.SYNC_LOG_MAX_SECONDS
    assert "reduced from 90" in resp["note"]
    assert "background=true" in resp["note"]


async def test_default_window_is_the_sync_cap(wire):
    state = wire(device_messages=[])
    hass = FakeHass([RACE_ENTRY])
    # Shorten the real wait: the default comes from the schema.
    task = asyncio.ensure_future(logs(hass, configuration="thingy-race.yaml"))
    while not state["captured_kwargs"]:
        await asyncio.sleep(0.01)
    assert state["captured_kwargs"][0]["max_seconds"] == bridge.SYNC_LOG_MAX_SECONDS
    task.cancel()


@pytest.mark.parametrize(
    "bad",
    [
        {"level": "loud"},
        {"context": 99},
        {"max_seconds": 0},
        {"max_seconds": 601},
        {"tags": "race"},
        {"bogus": 1},
    ],
)
async def test_invalid_arguments_are_rejected(wire, bad):
    wire()
    resp = await logs(FakeHass(), configuration="thingy-race.yaml", **bad)
    assert resp["error"].startswith("Invalid arguments")


async def test_invalid_regex_is_rejected(wire):
    wire()
    resp = await logs(FakeHass(), configuration="thingy-race.yaml", search="/lap (/")
    assert "Invalid search regex" in resp["error"]


async def test_secrets_and_paths_still_guarded(wire):
    wire()
    resp = await logs(FakeHass(), configuration="secrets.yaml")
    assert "error" in resp


# ---- dashboard fallback -------------------------------------------------------


async def test_dashboard_fallback_filters_and_passes_no_states(wire):
    lines = [r"[10:00:00.000]\033[0;35m[C][logger:250]: Logger:\033[0m",
             r"[10:00:01.000]\033[0;32m[I][race:045]: Lap 1\033[0m"]
    state = wire(dashboard_lines=lines)
    resp = await logs(FakeHass([]), configuration="thingy-race.yaml", max_seconds=1, no_states=True)
    assert resp["mode"] == "dashboard"
    assert resp["output"] == "[10:00:01.000][I][race:045]: Lap 1"
    call_kwargs = state["build"].calls[0]
    assert call_kwargs["no_states"] is True
    assert call_kwargs["max_seconds"] == 1


# ---- background capture and stop (#11) ----------------------------------------


async def test_background_capture_then_poll(wire):
    wire(device_messages=[dev("I", "race", "Lap 1"), dev("I", "race", "Lap 2")])
    hass = FakeHass([RACE_ENTRY])
    started = await logs(hass, configuration="thingy-race.yaml", background=True, max_seconds=1)
    assert started["status"] == "running"
    assert started["kind"] == "logs"
    assert "esphome_job_status" in started["note"]

    done = await status(hass, job_id=started["job_id"], wait_seconds=5)
    assert done["status"] == "done"
    assert done["success"] is True
    assert done["output"].splitlines() == ["[I][race:001]: Lap 1", "[I][race:001]: Lap 2"]
    assert done["mode"] == "device"
    assert done["lines_returned"] == 2


async def test_background_allows_long_windows(wire):
    state = wire(device_messages=[], forever=True)
    hass = FakeHass([RACE_ENTRY])
    started = await logs(hass, configuration="thingy-race.yaml", background=True, max_seconds=300)
    assert started["max_seconds"] == 300
    await status(hass, job_id=started["job_id"], stop=True)
    assert state["captured_kwargs"][0]["max_seconds"] == 300


async def test_stop_ends_a_background_capture_early(wire):
    wire(device_messages=[dev("I", "race", "start")], forever=True)
    hass = FakeHass([RACE_ENTRY])
    started = await logs(hass, configuration="thingy-race.yaml", background=True, max_seconds=600)
    running = await status(hass, job_id=started["job_id"], wait_seconds=0.2)
    assert running["status"] == "running"
    assert running["line_count"] >= 1

    loop = asyncio.get_running_loop()
    begin = loop.time()
    stopped = await status(hass, job_id=started["job_id"], stop=True)
    assert loop.time() - begin < bridge.JOB_STOP_GRACE_SECONDS
    assert stopped["status"] == "done"
    assert stopped["ended_by"] == "stopped"
    assert stopped["output_truncated"] is False  # nothing was cut from the output
    assert stopped["success"] is True
    assert stopped["output"].splitlines()[0] == "[I][race:001]: start"
    assert FakeDeviceClient.instances[0].unsubscribed is True


async def test_stop_is_refused_for_build_jobs(wire):
    hass = FakeHass()
    job = bridge._job_registry(hass).create("compile", "thingy-race.yaml", None)
    resp = await status(hass, job_id=job.id, stop=True)
    assert "only log captures" in resp["error"]


async def test_wait_seconds_is_clamped(wire, monkeypatch):
    hass = FakeHass()
    job = bridge._job_registry(hass).create("compile", "thingy-race.yaml", None)
    waited: list[float] = []

    async def fast_sleep(seconds):
        waited.append(seconds)
        job.status = "done"

    monkeypatch.setattr(bridge.asyncio, "sleep", fast_sleep)
    clock = iter(range(0, 10_000))
    monkeypatch.setattr(bridge.time, "monotonic", lambda: next(clock))
    resp = await status(hass, job_id=job.id, wait_seconds=500)
    assert resp["status"] == "done"
    assert waited and waited[0] <= bridge.JOB_STATUS_MAX_WAIT


async def test_job_status_rejects_unknown_job():
    resp = await status(FakeHass(), job_id="nope")
    assert "No job" in resp["error"]
