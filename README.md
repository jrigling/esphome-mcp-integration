# ESPHome MCP Bridge

Drive a **full ESPHome development cycle from an AI agent** (e.g. Claude Code
via Home Assistant's MCP server). This repo ships a Home Assistant custom
integration that registers a custom LLM API exposing ESPHome tools, backed by a
small reusable PyPI client library.

```
AI agent ── MCP ──► Home Assistant ──► ESPHome Builder LLM API
                                          │
                          ┌───────────────┴───────────────┐
                          ▼                                ▼
                 /config/esphome (files)        ESPHome dashboard add-on
                                                (stable / beta / dev) via
                                                Supervisor internal network
```

## Why this works without sidecars

The integration runs **inside Home Assistant Core**, so it has:

- **Native filesystem access** to `/config/esphome` for reading, creating, and
  writing YAML — reliable and independent of which add-on is installed.
- **Internal network access** to the ESPHome dashboard add-on through the
  Supervisor, reaching the dashboard's container directly (no ingress auth, no
  exposed ports) for validate / compile / upload / run / logs.

No changes to the ESPHome add-on and no external containers required.

## The development-cycle tools

The `ESPHome Builder` LLM API exposes these tools:

| Tool | Purpose |
|------|---------|
| `esphome_list_addons` | Discover installed ESPHome channels (stable/beta/dev), versions, state |
| `esphome_list_devices` | Inventory devices: name, version, platform, integrations, **online** status |
| `esphome_read_yaml` | Read a config from `/config/esphome` |
| `esphome_create_config` | Create a **new** config (fails if it exists) |
| `esphome_write_yaml` | Overwrite an existing config |
| `esphome_validate` | Validate a config without building |
| `esphome_compile` | Compile firmware (runs to completion, returns log + exit code) |
| `esphome_upload` | Flash firmware to a device (`port` defaults to `OTA`) |
| `esphome_run` | Compile **and** flash in one step (dashboard "Install") |
| `esphome_logs` | Capture a bounded window of live device logs for debugging |
| `esphome_clean` | Remove cached build artifacts |

A typical agent flow: **list devices → read/create config → write → validate →
compile → run → check logs**.

Each build tool accepts an optional `addon_slug` to target a specific channel;
omit it to use the default (stable preferred over beta over dev).

## Multi-channel discovery

`esphome_list_addons` queries the Supervisor for every installed add-on whose
slug or name matches `esphome` (`5c53de3b_esphome`, `…_esphome-beta`,
`…_esphome-dev`). The bridge resolves each add-on's internal hostname and port
from the Supervisor, so it adapts automatically rather than hard-coding a URL.

## Requirements

- Home Assistant with the Supervisor (HA OS or Supervised install)
- An ESPHome dashboard add-on installed (classic dashboard **or** the new
  Device Builder — both speak the protocol this bridge uses)
- Home Assistant 2024.4.0+ (LLM API helpers)

## Installation

### HACS (integration)

1. Add this repository as a custom HACS repository (type: *Integration*).
2. Install **ESPHome MCP Bridge** and restart Home Assistant.
3. Add to `configuration.yaml`:
   ```yaml
   esphome_mcp_bridge:
   ```
4. Restart. The `ESPHome Builder` API appears in the LLM/Assist API selector
   and to MCP clients.

### Manual

Copy `custom_components/esphome_mcp_bridge/` into your HA
`config/custom_components/`, add `esphome_mcp_bridge:` to `configuration.yaml`,
and restart.

> Home Assistant installs the `esphome-mcp-client` PyPI dependency
> automatically (declared in the integration manifest).

## Security

- File tools are confined to `/config/esphome`; directory traversal (`..`, `/`,
  `\`) is rejected outright.
- `secrets.yaml` / `secrets.yml` are blocked from read, write, create, and build.
- Writes/creates accept only `.yaml` / `.yml`.
- Supervisor requests use the `SUPERVISOR_TOKEN` injected at runtime — no
  credentials are stored.

## Repository layout

- `esphome_mcp_client/` — reusable async transport library → published to PyPI
  as **`esphome-mcp-client`** (see [PYPI_SETUP.md](PYPI_SETUP.md)).
- `custom_components/esphome_mcp_bridge/` — the Home Assistant integration
  (LLM tool definitions + API registration).
- `tests/` — unit tests for the client library.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check esphome_mcp_client tests
pytest
```

## License

MIT © Justin Rigling
