"""Client for the ESPHome dashboard / Device Builder API.

Speaks the *legacy* REST + WebSocket protocol that is supported by both the
classic ESPHome dashboard and the new ESPHome Device Builder (and is the
protocol Home Assistant's own ``esphome-dashboard-api`` uses):

- ``GET  /devices``            -> configured + importable devices
- ``GET  /ping``               -> {configuration: online_bool}
- ``GET  /edit?configuration`` -> raw YAML
- ``POST /edit?configuration`` -> write raw YAML
- ``GET  /json-config?configuration`` -> parsed YAML as JSON
- ``WS   /compile|/validate|/clean|/upload|/run|/logs`` (spawn protocol)

The WebSocket "spawn" protocol:

    client -> ``{"type": "spawn", "configuration": "kitchen.yaml", "port": "OTA"}``
    server -> ``{"event": "line", "data": "<stdout chunk>"}``  (repeated)
    server -> ``{"event": "exit", "code": <int>}``             (on completion)

``/logs`` never sends an ``exit`` frame for a healthy device, so log capture is
bounded by ``max_seconds`` / ``max_lines`` instead.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import aiohttp
from aiohttp import WSMsgType

from .exceptions import DashboardError

_LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_LINES = 2000
DEFAULT_BUILD_TIMEOUT = 600
DEFAULT_LOG_SECONDS = 30
DEFAULT_LOG_LINES = 500


@dataclass
class CommandResult:
    """Outcome of a spawn-protocol WebSocket command."""

    lines: list[str] = field(default_factory=list)
    exit_code: int | None = None
    truncated: bool = False

    @property
    def output(self) -> str:
        return "\n".join(self.lines)

    @property
    def success(self) -> bool:
        return self.exit_code == 0


# Endpoints the new ESPHome Device Builder does NOT expose over its legacy API
# (only /compile and /upload remain). Requesting these returns the SPA index
# (HTTP 200) instead of a WebSocket upgrade.
_DEVICE_BUILDER_MISSING = {"/validate", "/logs", "/clean", "/run"}


def _handshake_error(path: str, err: aiohttp.WSServerHandshakeError) -> str:
    """Turn a non-upgrade WebSocket handshake into an actionable message."""
    endpoint = path.lstrip("/")
    if err.status == 200 and path in _DEVICE_BUILDER_MISSING:
        return (
            f"The '{endpoint}' endpoint did not upgrade to a WebSocket (HTTP 200). "
            "This ESPHome add-on is running the new ESPHome Device Builder, whose "
            f"API does not expose '{endpoint}' (only compile and upload remain on "
            "the legacy interface). Workarounds: run a compile to surface "
            "configuration errors, and use the ESPHome dashboard UI for live logs."
        )
    if err.status == 200:
        return (
            f"The '{endpoint}' endpoint returned HTTP 200 without a WebSocket "
            "upgrade; this ESPHome backend may not support it."
        )
    return f"WS {path} handshake failed: {err.status} {err.message}"


class DashboardClient:
    """Async client bound to one ESPHome dashboard base URL.

    ``base_url`` and ``headers`` come from
    :meth:`SupervisorClient.open_dashboard_connection`: usually the Supervisor
    ingress proxy (``http://supervisor/ingress/<token>``) plus an
    ``ingress_session`` cookie, or a direct ``http://<hostname>:<port>`` URL
    with no headers for a classic dashboard.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._session = session
        self._http_base = base_url.rstrip("/")
        self._ws_base = "ws" + self._http_base[len("http"):]
        # Applied to every REST and WebSocket request. Carries the
        # ``ingress_session`` cookie when reaching the dashboard through the
        # Supervisor ingress proxy; empty for direct access.
        self._headers = dict(headers or {})

    # ---- REST -------------------------------------------------------------

    async def _get_json(self, path: str, params: dict | None = None) -> dict:
        url = f"{self._http_base}{path}"
        try:
            async with self._session.get(
                url,
                params=params,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientError as err:
            raise DashboardError(f"GET {path} failed: {err}") from err

    async def list_devices(self) -> dict:
        """Raw ``GET /devices`` payload: ``{configured: [...], importable: [...]}``."""
        return await self._get_json("/devices")

    async def version(self) -> str | None:
        """Return the dashboard's reported ESPHome version, or None.

        Useful for triaging API-drift reports: log which version a failing
        operation was talking to. Tolerates the endpoint being absent.
        """
        try:
            data = await self._get_json("/version")
        except DashboardError:
            return None
        return data.get("version")

    async def ping(self) -> dict[str, bool]:
        """``GET /ping`` -> mapping of configuration filename to online status."""
        try:
            return await self._get_json("/ping")
        except DashboardError:
            # Older/newer builds may not expose /ping; treat as "unknown".
            return {}

    async def inventory(self) -> list[dict]:
        """Merged device inventory with online status folded in.

        Each entry: ``name``, ``configuration``, ``deployed_version``,
        ``current_version``, ``target_platform``, ``loaded_integrations``,
        ``address``, ``web_port``, ``online``.
        """
        devices = await self.list_devices()
        if "configured" not in devices:
            # A missing 'configured' key means the /devices response shape has
            # drifted from the protocol we target - surface it loudly rather
            # than silently returning an empty inventory.
            _LOGGER.warning(
                "ESPHome /devices response missing 'configured' key (keys: %s); "
                "the dashboard API may have changed",
                sorted(devices),
            )
        online = await self.ping()
        result: list[dict] = []
        for dev in devices.get("configured", []):
            configuration = dev.get("configuration") or dev.get("filename")
            result.append(
                {
                    "name": dev.get("name"),
                    "friendly_name": dev.get("friendly_name"),
                    "configuration": configuration,
                    "deployed_version": dev.get("deployed_version"),
                    "current_version": dev.get("current_version")
                    or dev.get("esphome_version"),
                    "target_platform": dev.get("target_platform"),
                    "loaded_integrations": dev.get("loaded_integrations"),
                    "address": dev.get("address"),
                    "web_port": dev.get("web_port"),
                    "online": online.get(configuration, dev.get("online")),
                }
            )
        return result

    async def get_config(self, configuration: str) -> str:
        """Read raw YAML via ``GET /edit?configuration=``."""
        url = f"{self._http_base}/edit"
        try:
            async with self._session.get(
                url,
                params={"configuration": configuration},
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                return await resp.text()
        except aiohttp.ClientError as err:
            raise DashboardError(f"read '{configuration}' failed: {err}") from err

    async def write_config(self, configuration: str, content: str) -> None:
        """Write raw YAML via ``POST /edit?configuration=``."""
        url = f"{self._http_base}/edit"
        try:
            async with self._session.post(
                url,
                params={"configuration": configuration},
                data=content.encode("utf-8"),
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
        except aiohttp.ClientError as err:
            raise DashboardError(f"write '{configuration}' failed: {err}") from err

    async def get_json_config(self, configuration: str) -> dict:
        """Parsed YAML as JSON via ``GET /json-config?configuration=``."""
        return await self._get_json("/json-config", {"configuration": configuration})

    # ---- WebSocket spawn protocol ----------------------------------------

    async def _run_command(
        self,
        path: str,
        spawn: dict,
        *,
        max_seconds: float,
        max_lines: int,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        url = f"{self._ws_base}{path}"
        result = CommandResult()
        loop = asyncio.get_event_loop()
        deadline = loop.time() + max_seconds
        try:
            # Receive deadlines are enforced per-message below, so we rely on
            # ws_connect's default close timeout (portable across aiohttp 3.x).
            async with self._session.ws_connect(url, headers=self._headers) as ws:
                await ws.send_json(spawn)
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        result.truncated = True
                        break
                    try:
                        msg = await ws.receive(timeout=remaining)
                    except TimeoutError:
                        result.truncated = True
                        break
                    if msg.type in (
                        WSMsgType.CLOSE,
                        WSMsgType.CLOSING,
                        WSMsgType.CLOSED,
                        WSMsgType.ERROR,
                    ):
                        break
                    if msg.type != WSMsgType.TEXT:
                        continue
                    try:
                        data = json.loads(msg.data)
                    except (ValueError, TypeError):
                        result.lines.append(str(msg.data))
                        continue
                    event = data.get("event")
                    if event == "line":
                        line = data.get("data", "")
                        result.lines.append(line)
                        if on_line is not None:
                            on_line(line)
                        if len(result.lines) >= max_lines:
                            result.truncated = True
                            break
                    elif event == "exit":
                        result.exit_code = data.get("code")
                        break
        except aiohttp.WSServerHandshakeError as err:
            raise DashboardError(_handshake_error(path, err)) from err
        except aiohttp.ClientError as err:
            raise DashboardError(f"WS {path} failed: {err}") from err
        return result

    async def compile(
        self,
        configuration: str,
        *,
        max_seconds: float = DEFAULT_BUILD_TIMEOUT,
        max_lines: int = DEFAULT_MAX_LINES,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        return await self._run_command(
            "/compile",
            {"type": "spawn", "configuration": configuration},
            max_seconds=max_seconds,
            max_lines=max_lines,
            on_line=on_line,
        )

    async def validate(
        self,
        configuration: str,
        *,
        max_seconds: float = 120,
        max_lines: int = DEFAULT_MAX_LINES,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        return await self._run_command(
            "/validate",
            {"type": "spawn", "configuration": configuration},
            max_seconds=max_seconds,
            max_lines=max_lines,
            on_line=on_line,
        )

    async def clean(
        self,
        configuration: str,
        *,
        max_seconds: float = 120,
        max_lines: int = DEFAULT_MAX_LINES,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        return await self._run_command(
            "/clean",
            {"type": "spawn", "configuration": configuration},
            max_seconds=max_seconds,
            max_lines=max_lines,
            on_line=on_line,
        )

    async def upload(
        self,
        configuration: str,
        port: str = "OTA",
        *,
        max_seconds: float = DEFAULT_BUILD_TIMEOUT,
        max_lines: int = DEFAULT_MAX_LINES,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        return await self._run_command(
            "/upload",
            {"type": "spawn", "configuration": configuration, "port": port},
            max_seconds=max_seconds,
            max_lines=max_lines,
            on_line=on_line,
        )

    async def run(
        self,
        configuration: str,
        port: str = "OTA",
        *,
        max_seconds: float = DEFAULT_BUILD_TIMEOUT,
        max_lines: int = DEFAULT_MAX_LINES,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """Compile and upload in one step (``/run``)."""
        return await self._run_command(
            "/run",
            {"type": "spawn", "configuration": configuration, "port": port},
            max_seconds=max_seconds,
            max_lines=max_lines,
            on_line=on_line,
        )

    async def logs(
        self,
        configuration: str,
        port: str = "OTA",
        *,
        max_seconds: float = DEFAULT_LOG_SECONDS,
        max_lines: int = DEFAULT_LOG_LINES,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        """Capture a bounded window of live device logs for debugging.

        Returns after ``max_seconds`` or ``max_lines``, whichever comes first
        (``truncated`` will be ``True``). ``port`` may be ``"OTA"`` or a device
        address such as ``192.168.1.50``.
        """
        return await self._run_command(
            "/logs",
            {"type": "spawn", "configuration": configuration, "port": port},
            max_seconds=max_seconds,
            max_lines=max_lines,
            on_line=on_line,
        )
