"""Exceptions for the ESPHome MCP client library."""
from __future__ import annotations


class ESPHomeMCPError(Exception):
    """Base error for all client failures."""


class SupervisorError(ESPHomeMCPError):
    """Raised when a Home Assistant Supervisor API call fails."""


class AddonNotFoundError(SupervisorError):
    """Raised when a requested add-on slug is not installed."""


class DashboardError(ESPHomeMCPError):
    """Raised when an ESPHome dashboard request fails."""


class WsUnavailableError(DashboardError):
    """Raised when the multiplexed ``/ws`` API isn't available on this backend.

    Signals a classic dashboard (which lacks ``/ws``) so callers can fall back
    to the legacy per-operation WebSocket spawn endpoints. Raised only at
    connection time, before any output is streamed, so a fallback can't
    duplicate work.
    """


class DeviceLogError(ESPHomeMCPError):
    """Raised when streaming logs directly from a device fails."""
