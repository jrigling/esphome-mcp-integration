"""Constants for the ESPHome MCP client library."""
from __future__ import annotations

# Internal Supervisor API root (reachable from any add-on / HA Core container).
SUPERVISOR_BASE_URL = "http://supervisor"

# Port the ESPHome dashboard listens on inside its container. Used as a
# fallback when the Supervisor does not report an ingress port.
DEFAULT_DASHBOARD_PORT = 6052

# Substring used to recognise an ESPHome add-on regardless of the repository
# slug prefix (stable is ``5c53de3b_esphome``; beta/dev append ``-beta`` /
# ``-dev``). Matching on the substring keeps discovery working even if the
# repository hash ever changes.
ESPHOME_SLUG_MATCH = "esphome"

# Channel suffixes, ordered by preference when auto-selecting a default add-on.
# Stable ("") is preferred over beta over dev.
CHANNEL_SUFFIXES = ("", "-beta", "-dev")
