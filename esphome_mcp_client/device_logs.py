"""Direct-to-device log streaming over the ESPHome native API.

Bypasses the ESPHome dashboard entirely - so it works whether the add-on runs
the classic dashboard or the new Device Builder - by connecting to a device's
native API (port 6053) with ``aioesphomeapi`` and subscribing to its log stream
for a bounded window.

``aioesphomeapi`` is imported lazily and kept out of the package's hard
dependencies: Home Assistant already ships a pinned copy, and pinning our own
could conflict with it. Inside HA it is always available.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from .command import CommandResult
from .exceptions import DeviceLogError
from .log_capture import async_capture
from .logtext import LEVELS, LogFilter

_LOGGER = logging.getLogger(__name__)

DEFAULT_NATIVE_PORT = 6053
DEFAULT_LOG_SECONDS = 30
DEFAULT_LOG_LINES = 500


async def async_stream_device_logs(
    address: str,
    *,
    port: int = DEFAULT_NATIVE_PORT,
    password: str | None = None,
    noise_psk: str | None = None,
    max_seconds: float = DEFAULT_LOG_SECONDS,
    max_lines: int = DEFAULT_LOG_LINES,
    line_filter: LogFilter | None = None,
    on_line: Callable[[str], None] | None = None,
    stop_event: asyncio.Event | None = None,
    client_factory: Callable[[], Any] | None = None,
) -> CommandResult:
    """Capture a bounded window of a device's native-API logs.

    Returns when ``max_seconds`` elapses, ``max_lines`` lines have been kept,
    or ``stop_event`` is set (``truncated`` is then ``True``).

    The subscription asks the device for logs at ``line_filter.min_level``
    (default: everything, very verbose). Leaving the level unset would send the
    protocol default ``0``, which is ``LOG_LEVEL_NONE`` - the device would send
    nothing. The device still caps it at the level its ``logger:`` was compiled
    with. The startup config dump is requested only when the filter includes it.

    ``client_factory`` exists for testing; production builds an
    ``aioesphomeapi.APIClient``.
    """
    flt = line_filter if line_filter is not None else LogFilter()
    if client_factory is None:
        try:
            from aioesphomeapi import APIClient
        except ImportError as err:  # pragma: no cover - present inside HA
            raise DeviceLogError(
                "aioesphomeapi is not installed; cannot stream device logs."
            ) from err

        def client_factory() -> Any:
            return APIClient(
                address,
                port,
                password,
                noise_psk=noise_psk,
                client_info="esphome-mcp-client",
            )

    client = client_factory()

    async def _stream(on_raw: Callable[[str], None]) -> CommandResult:
        def _on_log(msg: Any) -> None:
            raw = getattr(msg, "message", b"")
            text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
            for line in text.splitlines() or [text]:
                on_raw(line)

        await client.connect(login=True)
        unsubscribe = client.subscribe_logs(
            _on_log,
            log_level=min(flt.min_level, LEVELS["very_verbose"]),
            dump_config=flt.include_config_dump,
        )
        try:
            await asyncio.sleep(max_seconds)
        finally:
            try:
                unsubscribe()
            except Exception:  # noqa: BLE001 - best-effort cleanup  # pragma: no cover
                pass
        return CommandResult(truncated=True)

    try:
        result = await async_capture(
            _stream,
            line_filter=flt,
            max_lines=max_lines,
            on_line=on_line,
            stop_event=stop_event,
        )
    except DeviceLogError:
        raise
    except Exception as err:  # noqa: BLE001 - normalise any client failure
        raise DeviceLogError(f"device log stream for {address} failed: {err}") from err
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001 - best-effort cleanup  # pragma: no cover
            pass

    result.exit_code = 0
    return result
