"""ESPHome MCP Bridge: LLM tools and API.

Exposes a full ESPHome development cycle to AI agents connecting through Home
Assistant's MCP server:

  discover add-ons -> inventory devices -> read / create / write YAML ->
  validate -> compile -> upload (flash) / run -> stream logs -> clean

File operations (read/create/write) act directly on ``/config/esphome`` for
reliability. Build operations talk to the ESPHome dashboard / Device Builder
add-on (stable, beta, or dev) via the ``esphome_mcp_client`` library.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from esphome_mcp_client import (
    DashboardClient,
    ESPHomeMCPError,
    SupervisorClient,
)

from .const import (
    ALLOWED_EXTENSIONS,
    API_ID,
    API_NAME,
    BLOCKED_FILES,
    DOMAIN,
    ESPHOME_CONFIG_DIR,
)

_LOGGER = logging.getLogger(__name__)

# Reusable schema fragment: every build/inventory tool accepts an optional
# add-on slug so the agent can target stable vs. beta vs. dev explicitly.
_ADDON_SLUG = vol.Optional("addon_slug")


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _sanitize_filename(filename: str) -> str:
    """Reject path traversal and return the bare filename.

    Any directory component or ``..`` sequence is treated as hostile rather
    than silently stripped, so a caller can never escape ``/config/esphome``.
    """
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise ValueError(f"Invalid filename: {filename!r}")
    return filename


def _guard(filename: str, *, require_yaml: bool = False) -> str:
    """Sanitize, then block secrets and (optionally) non-YAML files."""
    safe = _sanitize_filename(filename)
    if safe in BLOCKED_FILES:
        raise ValueError(f"Access to '{safe}' is not permitted.")
    if require_yaml and not safe.endswith(ALLOWED_EXTENSIONS):
        raise ValueError("Only .yaml or .yml files are allowed.")
    return safe


def _read_file(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


async def _async_dashboard(
    hass: HomeAssistant, slug: str | None
) -> tuple[DashboardClient, str]:
    """Build a dashboard client for the chosen (or default) ESPHome add-on."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise ESPHomeMCPError(
            "SUPERVISOR_TOKEN is unavailable - is Home Assistant running under "
            "the Supervisor?"
        )
    session = async_get_clientsession(hass)
    supervisor = SupervisorClient(session, token)
    if not slug:
        slug = await supervisor.async_default_slug()

    cache: dict[str, str] = hass.data.setdefault(DOMAIN, {}).setdefault(
        "base_url_cache", {}
    )
    base_url = cache.get(slug)
    if base_url is None:
        base_url = await supervisor.get_dashboard_base_url(slug)
        cache[slug] = base_url
    return DashboardClient(session, base_url), slug


def _result_to_dict(slug: str, configuration: str, result: Any) -> dict[str, Any]:
    """Normalize a CommandResult into a JSON-serializable tool response."""
    return {
        "addon_slug": slug,
        "configuration": configuration,
        "success": result.success,
        "exit_code": result.exit_code,
        "truncated": result.truncated,
        "output": result.output,
    }


# --------------------------------------------------------------------------- #
# Discovery / inventory tools
# --------------------------------------------------------------------------- #
class ListAddonsTool(llm.Tool):
    """Discover which ESPHome add-ons are installed (stable/beta/dev)."""

    name = "esphome_list_addons"
    description = (
        "List the installed ESPHome dashboard add-ons (stable, beta, dev), with "
        "their slug, version, and running state, plus which one is used by "
        "default. Use the returned slug as 'addon_slug' on other tools to target "
        "a specific channel."
    )
    parameters = vol.Schema({})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> dict[str, Any]:
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            return {"error": "SUPERVISOR_TOKEN unavailable; not running under Supervisor."}
        session = async_get_clientsession(hass)
        supervisor = SupervisorClient(session, token)
        try:
            addons = await supervisor.list_esphome_addons()
            default = await supervisor.async_default_slug() if addons else None
        except ESPHomeMCPError as err:
            return {"error": str(err)}
        return {
            "default_slug": default,
            "addons": [
                {
                    "slug": a.slug,
                    "name": a.name,
                    "version": a.version,
                    "version_latest": a.version_latest,
                    "state": a.state,
                    "update_available": a.update_available,
                }
                for a in addons
            ],
        }


class ListDevicesTool(llm.Tool):
    """Inventory configured ESPHome devices with online status and versions."""

    name = "esphome_list_devices"
    description = (
        "List configured ESPHome devices: name, configuration filename, ESPHome "
        "version, target platform, loaded integrations, network address, and "
        "whether the device is currently online. Use this to take inventory "
        "before editing or debugging."
    )
    parameters = vol.Schema({_ADDON_SLUG: str})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> dict[str, Any]:
        try:
            client, slug = await _async_dashboard(hass, tool_input.tool_args.get("addon_slug"))
            devices = await client.inventory()
        except ESPHomeMCPError as err:
            return {"error": str(err)}
        return {"addon_slug": slug, "devices": devices}


# --------------------------------------------------------------------------- #
# Configuration file tools (filesystem)
# --------------------------------------------------------------------------- #
class ReadYamlTool(llm.Tool):
    """Read an ESPHome YAML configuration file."""

    name = "esphome_read_yaml"
    description = (
        "Read the contents of an ESPHome YAML configuration file from "
        "/config/esphome. Use this to inspect an existing device configuration."
    )
    parameters = vol.Schema({vol.Required("filename"): str})

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> dict[str, Any]:
        try:
            filename = _guard(tool_input.tool_args["filename"])
        except ValueError as err:
            return {"error": str(err)}
        path = os.path.join(ESPHOME_CONFIG_DIR, filename)
        try:
            content = await hass.async_add_executor_job(_read_file, path)
        except FileNotFoundError:
            return {"error": f"File '{filename}' not found in {ESPHOME_CONFIG_DIR}."}
        except OSError as err:
            return {"error": str(err)}
        return {"filename": filename, "content": content}


class CreateConfigTool(llm.Tool):
    """Create a NEW ESPHome configuration file (fails if it already exists)."""

    name = "esphome_create_config"
    description = (
        "Create a new ESPHome YAML configuration file in /config/esphome. Fails "
        "if a file with that name already exists - use esphome_write_yaml to "
        "modify an existing configuration."
    )
    parameters = vol.Schema(
        {
            vol.Required("filename"): str,
            vol.Required("content"): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> dict[str, Any]:
        try:
            filename = _guard(tool_input.tool_args["filename"], require_yaml=True)
        except ValueError as err:
            return {"error": str(err)}
        path = os.path.join(ESPHOME_CONFIG_DIR, filename)
        if await hass.async_add_executor_job(os.path.exists, path):
            return {"error": f"'{filename}' already exists; use esphome_write_yaml."}
        try:
            await hass.async_add_executor_job(
                _write_file, path, tool_input.tool_args["content"]
            )
        except OSError as err:
            return {"error": str(err)}
        return {"success": True, "filename": filename, "created": True}


class WriteYamlTool(llm.Tool):
    """Write (overwrite) an ESPHome YAML configuration file."""

    name = "esphome_write_yaml"
    description = (
        "Write or overwrite an ESPHome YAML configuration file in "
        "/config/esphome. Validate or compile afterward to confirm the change."
    )
    parameters = vol.Schema(
        {
            vol.Required("filename"): str,
            vol.Required("content"): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> dict[str, Any]:
        try:
            filename = _guard(tool_input.tool_args["filename"], require_yaml=True)
        except ValueError as err:
            return {"error": str(err)}
        path = os.path.join(ESPHOME_CONFIG_DIR, filename)
        try:
            await hass.async_add_executor_job(
                _write_file, path, tool_input.tool_args["content"]
            )
        except OSError as err:
            return {"error": str(err)}
        return {"success": True, "filename": filename}


# --------------------------------------------------------------------------- #
# Build-cycle tools (dashboard WebSocket spawn protocol)
# --------------------------------------------------------------------------- #
class _BuildTool(llm.Tool):
    """Base for tools that run a spawn command against one configuration."""

    # Subclasses set: name, description, _method (DashboardClient coroutine name).
    _method: str = ""
    parameters = vol.Schema(
        {
            vol.Required("configuration"): str,
            _ADDON_SLUG: str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> dict[str, Any]:
        args = tool_input.tool_args
        try:
            configuration = _guard(args["configuration"], require_yaml=True)
        except ValueError as err:
            return {"error": str(err)}
        try:
            client, slug = await _async_dashboard(hass, args.get("addon_slug"))
            result = await getattr(client, self._method)(configuration)
        except ESPHomeMCPError as err:
            return {"error": str(err)}
        return _result_to_dict(slug, configuration, result)


class ValidateTool(_BuildTool):
    """Validate an ESPHome configuration without building it."""

    name = "esphome_validate"
    description = (
        "Validate an ESPHome configuration file. Returns the validator output "
        "and exit code (0 = valid). Run this after editing, before compiling."
    )
    _method = "validate"


class CompileTool(_BuildTool):
    """Compile an ESPHome configuration into firmware."""

    name = "esphome_compile"
    description = (
        "Compile an ESPHome configuration into firmware. Returns the build log "
        "and exit code (0 = success). Compilation runs to completion before the "
        "result is returned."
    )
    _method = "compile"


class CleanTool(_BuildTool):
    """Clean cached build files for a configuration."""

    name = "esphome_clean"
    description = (
        "Delete cached build artifacts for an ESPHome configuration. Use this "
        "when a build behaves unexpectedly and you want a clean rebuild."
    )
    _method = "clean"


class _FlashTool(llm.Tool):
    """Base for tools that take an optional target port (upload/run/logs)."""

    _method: str = ""
    parameters = vol.Schema(
        {
            vol.Required("configuration"): str,
            vol.Optional("port", default="OTA"): str,
            _ADDON_SLUG: str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> dict[str, Any]:
        args = tool_input.tool_args
        try:
            configuration = _guard(args["configuration"], require_yaml=True)
        except ValueError as err:
            return {"error": str(err)}
        port = args.get("port", "OTA")
        try:
            client, slug = await _async_dashboard(hass, args.get("addon_slug"))
            result = await getattr(client, self._method)(configuration, port)
        except ESPHomeMCPError as err:
            return {"error": str(err)}
        response = _result_to_dict(slug, configuration, result)
        response["port"] = port
        return response


class UploadTool(_FlashTool):
    """Flash already-built (or freshly built) firmware to a device."""

    name = "esphome_upload"
    description = (
        "Upload (flash) firmware for an ESPHome configuration to a device. "
        "'port' defaults to 'OTA' (over-the-air); pass a device IP/hostname or "
        "serial port to target it explicitly. Returns the upload log and exit code."
    )
    _method = "upload"


class RunTool(_FlashTool):
    """Compile and upload in a single step."""

    name = "esphome_run"
    description = (
        "Compile an ESPHome configuration AND upload it to the device in one "
        "step (the equivalent of the dashboard 'Install' action). 'port' "
        "defaults to 'OTA'. Returns the combined build + upload log."
    )
    _method = "run"


class LogsTool(llm.Tool):
    """Capture a bounded window of live device logs for debugging."""

    name = "esphome_logs"
    description = (
        "Stream live logs from a running ESPHome device for debugging, capturing "
        "a bounded window (default ~30s or 500 lines). 'port' defaults to 'OTA'; "
        "pass a device IP/hostname for a specific target. Returns captured log "
        "lines; 'truncated' is true if the window limit was hit."
    )
    parameters = vol.Schema(
        {
            vol.Required("configuration"): str,
            vol.Optional("port", default="OTA"): str,
            vol.Optional("max_seconds", default=30): vol.All(
                vol.Coerce(float), vol.Range(min=1, max=120)
            ),
            _ADDON_SLUG: str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> dict[str, Any]:
        args = tool_input.tool_args
        try:
            configuration = _guard(args["configuration"], require_yaml=True)
        except ValueError as err:
            return {"error": str(err)}
        port = args.get("port", "OTA")
        max_seconds = args.get("max_seconds", 30)
        try:
            client, slug = await _async_dashboard(hass, args.get("addon_slug"))
            result = await client.logs(configuration, port, max_seconds=max_seconds)
        except ESPHomeMCPError as err:
            return {"error": str(err)}
        response = _result_to_dict(slug, configuration, result)
        response["port"] = port
        return response


# --------------------------------------------------------------------------- #
# The API
# --------------------------------------------------------------------------- #
_API_PROMPT = (
    "You can manage ESPHome devices running inside Home Assistant across a full "
    "development cycle: discover the installed ESPHome add-on channels, take "
    "inventory of devices, read/create/write YAML configurations in "
    "/config/esphome, then validate, compile, upload (flash), run, and stream "
    "logs via the ESPHome dashboard. A typical flow is: list devices -> read or "
    "create a config -> write changes -> validate -> compile -> run (flash) -> "
    "check logs. Never read, write, or build secrets.yaml. Compilation and "
    "uploads run to completion before returning; log streaming returns a bounded "
    "window. Prefer validating before compiling, and report exit codes and "
    "relevant log lines back to the user."
)


class ESPHomeBuilderAPI(llm.API):
    """LLM API exposing the ESPHome development cycle."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass=hass, id=API_ID, name=API_NAME)

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        tools: list[llm.Tool] = [
            ListAddonsTool(),
            ListDevicesTool(),
            ReadYamlTool(),
            CreateConfigTool(),
            WriteYamlTool(),
            ValidateTool(),
            CompileTool(),
            CleanTool(),
            UploadTool(),
            RunTool(),
            LogsTool(),
        ]
        return llm.APIInstance(
            api=self,
            api_prompt=_API_PROMPT,
            llm_context=llm_context,
            tools=tools,
        )
