"""ESPHome MCP client library.

A transport-only client for the Home Assistant Supervisor and the ESPHome
dashboard / Device Builder API. Contains no Home Assistant dependencies so it
can be published to PyPI and consumed by the ``esphome_mcp_bridge`` custom
integration (or any async aiohttp app).
"""
from __future__ import annotations

from .dashboard import CommandResult, DashboardClient
from .exceptions import (
    AddonNotFoundError,
    DashboardError,
    ESPHomeMCPError,
    SupervisorError,
)
from .supervisor import AddonInfo, SupervisorClient

__version__ = "0.1.1"

__all__ = [
    "AddonInfo",
    "AddonNotFoundError",
    "CommandResult",
    "DashboardClient",
    "DashboardError",
    "ESPHomeMCPError",
    "SupervisorClient",
    "SupervisorError",
    "__version__",
]
