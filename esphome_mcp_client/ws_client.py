"""Client for the ESPHome Device Builder multiplexed ``/ws`` command API.

The new ESPHome Device Builder replaced the classic dashboard's per-operation
WebSocket "spawn" endpoints (``/compile``, ``/validate``, ``/clean``, ``/run``,
``/logs`` — each its own socket) with a **single multiplexed ``/ws`` endpoint**.
Clients send ``CommandMessage`` frames and receive correlated replies:

    client -> ``{"command": "firmware/compile", "message_id": "1", "args": {...}}``
    server -> ``{"message_id": "1", "event": "output", "data": "<line>"}``   (repeated)
    server -> ``{"message_id": "1", "event": "result", "data": {...}}``       (terminal)
    server -> ``{"message_id": "1", "result": <payload>}``                    (command done)
    server -> ``{"message_id": "1", "error_code": "...", "details": "..."}``  (on failure)

Frames carry no ``type`` discriminator; they're told apart by their fields
(``error_code`` / ``event`` / ``server_version`` / else ``result``). A top-level
``ResultMessage`` (or ``ErrorMessage``) for our ``message_id`` is the universal
"command finished" signal — the server sends one after every handler returns.

Two shapes of operation:

- **Firmware jobs** (``compile`` / ``upload`` / ``clean``): the submit command
  returns a ``FirmwareJob`` immediately (it only queues); output is obtained by
  following it with ``firmware/follow_job`` (which replays history then tails
  ``output`` events until a terminal ``result`` event).
- **Per-connection streams** (``validate`` / ``logs``): a single command streams
  ``output`` events, then a ``result`` event carrying the exit code.

Reached through the same Supervisor ingress proxy as :class:`DashboardClient`;
on the trusted ingress site the ``/ws`` connection is pre-authenticated, so no
password / Noise handshake is needed.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
from collections.abc import Callable
from typing import Any

import aiohttp
from aiohttp import WSMsgType

from .dashboard import (
    DEFAULT_BUILD_TIMEOUT,
    DEFAULT_LOG_LINES,
    DEFAULT_LOG_SECONDS,
    DEFAULT_MAX_LINES,
    CommandResult,
)
from .exceptions import DashboardError, WsUnavailableError

_LOGGER = logging.getLogger(__name__)

# Streaming frame event names (StreamEvent in the Device Builder).
_EVENT_OUTPUT = "output"
_EVENT_RESULT = "result"


class WsClient:
    """Async client for one Device Builder ``/ws`` endpoint.

    ``base_url`` and ``headers`` are the same values :class:`DashboardClient`
    uses (from ``SupervisorClient.open_dashboard_connection``): usually the
    Supervisor ingress proxy URL plus an ``ingress_session`` cookie.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._session = session
        http_base = base_url.rstrip("/")
        self._ws_url = "ws" + http_base[len("http"):] + "/ws"
        self._headers = dict(headers or {})
        self._ids = itertools.count(1)

    # ---- framing ----------------------------------------------------------

    async def _send(self, ws: aiohttp.ClientWebSocketResponse, command: str, args: dict) -> str:
        message_id = str(next(self._ids))
        await ws.send_json({"command": command, "message_id": message_id, "args": args})
        return message_id

    async def _pump(
        self,
        ws: aiohttp.ClientWebSocketResponse,
        message_id: str,
        result: CommandResult,
        *,
        label: str,
        deadline: float,
        max_lines: int,
        on_line: Callable[[str], None] | None,
    ) -> Any:
        """Read frames addressed to ``message_id`` until this command finishes.

        Appends ``output`` lines to ``result`` (and captures the exit code from
        the terminal ``result`` event). Returns the top-level ``ResultMessage``
        payload, or ``None`` if the command was truncated / the socket closed.
        Raises :class:`DashboardError` on an ``ErrorMessage``.
        """
        loop = asyncio.get_event_loop()
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                result.truncated = True
                return None
            try:
                msg = await ws.receive(timeout=remaining)
            except TimeoutError:
                result.truncated = True
                return None
            if msg.type in (
                WSMsgType.CLOSE,
                WSMsgType.CLOSING,
                WSMsgType.CLOSED,
                WSMsgType.ERROR,
            ):
                return None
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except (ValueError, TypeError):
                continue
            # Ignore the connection's ServerInfo greeting and any multiplexed
            # frames belonging to a different command.
            if data.get("message_id") != message_id:
                continue
            if "error_code" in data:
                raise DashboardError(
                    f"{label} failed: {data['error_code']}: {data.get('details', '')}"
                )
            event = data.get("event")
            if event is not None:
                if event == _EVENT_OUTPUT:
                    line = data.get("data")
                    if isinstance(line, str):
                        result.lines.append(line)
                        if on_line is not None:
                            on_line(line)
                        if len(result.lines) >= max_lines:
                            result.truncated = True
                            return None
                elif event == _EVENT_RESULT:
                    payload = data.get("data") or {}
                    result.exit_code = payload.get("exit_code", payload.get("code"))
                continue
            # No event and no error_code -> the terminal ResultMessage.
            return data.get("result")

    # ---- connection helpers ----------------------------------------------

    def _connect(self) -> Any:
        return self._session.ws_connect(self._ws_url, headers=self._headers)

    def _wrap_conn_errors(self, err: aiohttp.ClientError) -> DashboardError:
        if isinstance(err, aiohttp.WSServerHandshakeError):
            return WsUnavailableError(
                f"/ws did not upgrade ({err.status}); this ESPHome backend may not "
                "expose the multiplexed API (a classic dashboard rather than the "
                "Device Builder)."
            )
        return DashboardError(f"/ws connection failed: {err}")

    # ---- firmware jobs (compile / upload / clean) -------------------------

    async def _run_job(
        self,
        submit_command: str,
        args: dict,
        *,
        max_seconds: float,
        max_lines: int,
        on_line: Callable[[str], None] | None,
    ) -> CommandResult:
        """Submit a firmware job, then follow it to completion."""
        result = CommandResult()
        deadline = asyncio.get_event_loop().time() + max_seconds
        try:
            async with self._connect() as ws:
                submit_id = await self._send(ws, submit_command, args)
                job = await self._pump(
                    ws,
                    submit_id,
                    CommandResult(),
                    label=submit_command,
                    deadline=deadline,
                    max_lines=max_lines,
                    on_line=None,
                )
                if not isinstance(job, dict) or not job.get("job_id"):
                    raise DashboardError(f"{submit_command} did not return a job id")
                follow_id = await self._send(
                    ws, "firmware/follow_job", {"job_id": job["job_id"]}
                )
                await self._pump(
                    ws,
                    follow_id,
                    result,
                    label="firmware/follow_job",
                    deadline=deadline,
                    max_lines=max_lines,
                    on_line=on_line,
                )
        except aiohttp.ClientError as err:
            raise self._wrap_conn_errors(err) from err
        return result

    async def compile(
        self,
        configuration: str,
        *,
        max_seconds: float = DEFAULT_BUILD_TIMEOUT,
        max_lines: int = DEFAULT_MAX_LINES,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        return await self._run_job(
            "firmware/compile",
            {"configuration": configuration},
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
        return await self._run_job(
            "firmware/upload",
            {"configuration": configuration, "port": port},
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
        return await self._run_job(
            "firmware/clean",
            {"configuration": configuration},
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
        """Compile then upload (the Device Builder has no single ``run``)."""
        compiled = await self.compile(
            configuration, max_seconds=max_seconds, max_lines=max_lines, on_line=on_line
        )
        if not compiled.success:
            return compiled
        uploaded = await self.upload(
            configuration,
            port,
            max_seconds=max_seconds,
            max_lines=max_lines,
            on_line=on_line,
        )
        return CommandResult(
            lines=compiled.lines + uploaded.lines,
            exit_code=uploaded.exit_code,
            truncated=compiled.truncated or uploaded.truncated,
        )

    # ---- per-connection streams (validate / logs) -------------------------

    async def _run_stream(
        self,
        command: str,
        args: dict,
        *,
        max_seconds: float,
        max_lines: int,
        on_line: Callable[[str], None] | None,
    ) -> CommandResult:
        result = CommandResult()
        deadline = asyncio.get_event_loop().time() + max_seconds
        try:
            async with self._connect() as ws:
                message_id = await self._send(ws, command, args)
                await self._pump(
                    ws,
                    message_id,
                    result,
                    label=command,
                    deadline=deadline,
                    max_lines=max_lines,
                    on_line=on_line,
                )
        except aiohttp.ClientError as err:
            raise self._wrap_conn_errors(err) from err
        return result

    async def validate(
        self,
        configuration: str,
        *,
        max_seconds: float = 120,
        max_lines: int = DEFAULT_MAX_LINES,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        return await self._run_stream(
            "devices/validate",
            {"configuration": configuration},
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
        """Capture a bounded window of live device logs.

        A healthy device streams forever, so this returns after ``max_seconds``
        or ``max_lines`` (``truncated`` will be ``True``); closing the socket
        cancels the server-side stream.
        """
        return await self._run_stream(
            "devices/logs",
            {"configuration": configuration, "port": port},
            max_seconds=max_seconds,
            max_lines=max_lines,
            on_line=on_line,
        )
