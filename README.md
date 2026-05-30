# ESPHome MCP Bridge

A Home Assistant Custom Integration that registers a custom LLM API, exposing tools that allow AI agents (such as Claude connecting via the official HA MCP Server) to interact with the local ESPHome Add-on.

## How It Works

This integration runs entirely inside Home Assistant Core, which gives it:

- **Native filesystem access** to `/config/esphome` to read and write YAML files.
- **Internal HTTP access** to the ESPHome Add-on via the Supervisor network proxy, without requiring external port exposure.

It registers a custom `ESPHome Builder` API with Home Assistant's LLM system. When an AI agent connects via the HA MCP Server and selects this API, it gains access to three tools:

| Tool | Description |
|------|-------------|
| `esphome_read_yaml` | Read an ESPHome YAML config file |
| `esphome_write_yaml` | Write or overwrite an ESPHome YAML config file |
| `esphome_compile` | Trigger compilation of a config via the ESPHome Add-on |

## Requirements

- Home Assistant with the Supervisor (HA OS or Supervised install)
- ESPHome Add-on installed (slug: `5c53de3b_esphome`)
- Home Assistant 2024.4.0 or newer

## Installation

### HACS

1. Add this repository as a custom HACS repository (Integration type).
2. Install **ESPHome MCP Bridge**.
3. Restart Home Assistant.

### Manual

Copy the `custom_components/esphome_mcp_bridge/` directory into your HA `config/custom_components/` folder, then restart.

## Configuration

Add the following to your `configuration.yaml`:

```yaml
esphome_mcp_bridge:
```

Restart Home Assistant. The `ESPHome Builder` API will appear in the LLM API selector.

## Security

- All file operations are restricted to `/config/esphome`.
- Directory traversal sequences (`../`) are blocked.
- `secrets.yaml` cannot be read, written, or compiled.
- Only `.yaml` and `.yml` files may be written.
- Compilation requests use the Supervisor token injected at runtime — no credentials are stored in the integration.

## Architecture

```
HA MCP Server  ──►  ESPHome Builder LLM API  ──►  /config/esphome (filesystem)
                           │
                           └──►  ESPHome Add-on (via Supervisor HTTP proxy)
```

The integration has no config flow and no entities. It purely registers an `llm.API` instance on startup.
