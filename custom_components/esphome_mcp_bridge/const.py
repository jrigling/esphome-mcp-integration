"""Constants for the ESPHome MCP Bridge integration."""
from __future__ import annotations

DOMAIN = "esphome_mcp_bridge"

# LLM API identifier exposed to the Home Assistant MCP server / agents.
API_ID = "esphome_builder"
API_NAME = "ESPHome Builder"

# Filesystem root that file tools are confined to.
ESPHOME_CONFIG_DIR = "/config/esphome"

# Files that must never be read, written, created, or built.
BLOCKED_FILES = frozenset({"secrets.yaml", "secrets.yml"})

# Allowed extensions for write/create.
ALLOWED_EXTENSIONS = (".yaml", ".yml")

# The ESPHome secrets file. The add-secret tool may insert keys here (only),
# but secrets are never read back or exposed through any tool.
SECRETS_FILE = "secrets.yaml"

# Permitted shape of a secret key (ESPHome uses snake_case identifiers).
SECRET_KEY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_-]*$"
