# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Home Assistant Custom Integration (domain: `esphome_mcp_bridge`) that registers a custom LLM API. The API exposes tools for AI agents (connecting via HA's MCP Server) to read/write ESPHome YAML configs and trigger compilation via the ESPHome Add-on.

No config flow, no entities, no UI — this integration purely registers an `llm.API` on startup via `async_setup`.

## Key Files

- `custom_components/esphome_mcp_bridge/manifest.json` — HA integration manifest; depends on `hassio`
- `custom_components/esphome_mcp_bridge/__init__.py` — calls `llm.async_register_api(hass, ESPHomeBuilderAPI(hass))` in `async_setup`
- `custom_components/esphome_mcp_bridge/llm.py` — all tool and API classes

## HA LLM API Pattern

Tools subclass `llm.Tool` and pass `name`, `description`, `parameters` to `super().__init__()` (Tool is a dataclass in HA 2024+). The `async_call` signature is:

```python
async def async_call(self, hass, tool_input: llm.ToolInput, llm_context: llm.LLMContext) -> dict
```

`tool_input.tool_args` holds the validated dict of arguments.

The custom API class subclasses `llm.API` and implements `async_get_api_instance`, which returns an `llm.APIInstance` with the tool list and a system prompt string.

## Security Invariants

These must not be weakened:
- `_sanitize_filename()` in `llm.py` blocks `..`, `/`, and `\` in filenames before any file I/O
- `secrets.yaml` is in `BLOCKED_FILES` — blocked from read, write, and compile
- Only `.yaml`/`.yml` extensions are accepted for writes
- File operations are always joined to `ESPHOME_CONFIG_DIR` (`/config/esphome`)

## Supervisor API Call

The compile tool POSTs to `ESPHOME_JOBS_URL` with `Authorization: Bearer $SUPERVISOR_TOKEN`. The token is read at call time via `os.environ.get("SUPERVISOR_TOKEN")` — never stored. Uses `async_get_clientsession(hass)` (HA's shared aiohttp session) rather than creating a new session.

## Installation for Testing

Copy `custom_components/esphome_mcp_bridge/` into a running HA instance's `config/custom_components/`, add `esphome_mcp_bridge:` to `configuration.yaml`, and restart. The `ESPHome Builder` API then appears in HA's LLM API selector.

## Version

Current: `0.1.0` in `manifest.json`. Follow semver; bump for any user-visible change.
