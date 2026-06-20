"""ESPHome MCP client library.

A transport-only client for the Home Assistant Supervisor and the ESPHome
dashboard / Device Builder API. Contains no Home Assistant dependencies so it
can be published to PyPI and consumed by the ``esphome_mcp_bridge`` custom
integration (or any async aiohttp app).
"""
from __future__ import annotations

from .dashboard import CommandResult, DashboardClient
from .device_logs import async_stream_device_logs
from .exceptions import (
    AddonNotFoundError,
    DashboardError,
    DeviceLogError,
    ESPHomeMCPError,
    SupervisorError,
)
from .supervisor import AddonInfo, SupervisorClient

__version__ = "0.1.3"

__all__ = [
    "AddonInfo",
    "AddonNotFoundError",
    "CommandResult",
    "DashboardClient",
    "DashboardError",
    "DeviceLogError",
    "ESPHomeMCPError",
    "SupervisorClient",
    "SupervisorError",
    "async_stream_device_logs",
    "__version__",
]
