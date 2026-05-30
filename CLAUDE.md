# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Two deliverables from one repo, mirroring the `python-jebao` / `homeassistant-jebao` split:

1. **`esphome_mcp_client/`** — a pure-Python async transport library (no Home Assistant imports), published to PyPI as **`esphome-mcp-client`**.
2. **`custom_components/esphome_mcp_bridge/`** — a Home Assistant custom integration (domain `esphome_mcp_bridge`) that registers a custom LLM API. It depends on the client via `manifest.json` `requirements` (exact `==` pin).

The integration exposes a full ESPHome dev cycle to AI agents via HA's MCP server: discover add-ons → inventory devices → read/create/write YAML → validate → compile → upload/run → logs → clean.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"              # install client lib + dev tooling
ruff check esphome_mcp_client tests  # lint
pytest                               # run all tests
pytest tests/test_dashboard.py -k inventory   # single test
python -m build && twine check dist/*         # verify packaging
```

Tests use `aioresponses` to mock HTTP; they cover the client library only (no HA test harness here).

## Architecture

`esphome_mcp_client` is transport-only and HA-agnostic so it can be unit-tested and reused:

- `supervisor.py` — `SupervisorClient`: discovers ESPHome add-ons via `GET /addons` (matches any slug/name containing `esphome`), ranks them stable > beta > dev (running preferred), and resolves each add-on's internal dashboard base URL (`http://<hostname>:<ingress_port>`) from `GET /addons/{slug}/info`. Falls back to port 6052.
- `dashboard.py` — `DashboardClient`: talks the **legacy** ESPHome dashboard protocol (supported by both the classic dashboard and the new Device Builder). REST for `/devices`, `/ping`, `/edit`, `/json-config`; WebSocket "spawn" protocol for `/compile|/validate|/clean|/upload|/run|/logs`.

The integration's `llm.py` wraps the client in `llm.Tool` subclasses. `async_setup_entry` registers `ESPHomeBuilderAPI` and ties the returned unregister callback to the entry via `entry.async_on_unload`. Installation is UI-only: a confirm-only, single-instance config flow (`config_flow.py`) — there is no YAML setup and no `async_setup`.

## Critical protocol facts (verified against ESPHome + Device Builder source)

- There is **no `/api/v1/jobs` REST endpoint** (an early spec assumed one). Compile/logs/etc. are **WebSocket**, spawn protocol:
  - client → `{"type": "spawn", "configuration": "x.yaml", "port": "OTA"}`
  - server → `{"event": "line", "data": "<chunk>"}` repeated, then `{"event": "exit", "code": <int>}`
- `/logs` never sends an `exit` frame for a healthy device, so `DashboardClient.logs()` is bounded by `max_seconds` / `max_lines` and sets `truncated`.
- ESPHome add-on slugs: `5c53de3b_esphome` (stable), `5c53de3b_esphome-beta`, `5c53de3b_esphome-dev`. Discovery matches the `esphome` substring rather than hard-coding the repo hash.

## HA LLM API contract (verified against HA `helpers/llm.py`)

- `llm.Tool` is a **plain class** — set `name` / `description` / `parameters` (a `vol.Schema`) as **class attributes**; do NOT call `super().__init__`. Implement `async_call(self, hass, tool_input, llm_context) -> dict`. Args are in `tool_input.tool_args`.
- `llm.API` is a `@dataclass(slots=True, kw_only=True)` with `hass`/`id`/`name`; subclass calls `super().__init__(hass=..., id=..., name=...)` and implements `async_get_api_instance` returning an `llm.APIInstance(api, api_prompt, llm_context, tools)`.

## Security invariants (do not weaken)

In `llm.py`: `_sanitize_filename()` rejects `..`, `/`, `\`; `_guard()` blocks `secrets.yaml`/`secrets.yml` and (for writes/builds) enforces `.yaml`/`.yml`. File tools are always joined under `ESPHOME_CONFIG_DIR` (`/config/esphome`). Supervisor auth uses `os.environ["SUPERVISOR_TOKEN"]` read at call time — never stored.

## Releasing

See [PYPI_SETUP.md](PYPI_SETUP.md). Bump `pyproject.toml` `version` + `esphome_mcp_client/__init__.py` `__version__` together; if the integration needs the new client, bump the `manifest.json` requirement pin and integration `version` too. A GitHub Release triggers `publish.yml`.

## Networking caveat

The dashboard base URL relies on the add-on being reachable by its Supervisor-reported `hostname:ingress_port` from the HA Core container. If an ESPHome add-on runs on host networking, this may need adjustment — verify at runtime against a real install.
