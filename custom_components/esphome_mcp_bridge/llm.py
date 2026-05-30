"""ESPHome MCP Bridge LLM tools and API."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

ESPHOME_CONFIG_DIR = "/config/esphome"
ESPHOME_JOBS_URL = (
    "http://supervisor/core/api/hassio_ingress/5c53de3b_esphome/api/v1/jobs"
)
BLOCKED_FILES = frozenset({"secrets.yaml"})


def _sanitize_filename(filename: str) -> str:
    """Return the basename, rejecting any path traversal."""
    safe = Path(filename).name
    if not safe or safe != filename.replace("/", "").replace("\\", "").replace("..", ""):
        # Extra check: ensure no traversal sequences survived
        pass
    # Path.name already strips directory components; reject if the original had them
    if "/" in filename or "\\" in filename or ".." in filename:
        raise ValueError(f"Invalid filename: {filename!r}")
    return safe


def _read_file(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class ReadYamlTool(llm.Tool):
    """Read an ESPHome YAML configuration file."""

    def __init__(self) -> None:
        super().__init__(
            name="esphome_read_yaml",
            description=(
                "Read the contents of an ESPHome YAML configuration file from "
                "/config/esphome. Use this to inspect existing device configurations."
            ),
            parameters=vol.Schema(
                {
                    vol.Required("filename"): str,
                }
            ),
        )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        try:
            filename = _sanitize_filename(tool_input.tool_args["filename"])
        except ValueError as err:
            return {"error": str(err)}

        if filename in BLOCKED_FILES:
            return {"error": f"Access to '{filename}' is not permitted."}

        file_path = os.path.join(ESPHOME_CONFIG_DIR, filename)

        try:
            content = await hass.async_add_executor_job(_read_file, file_path)
            return {"filename": filename, "content": content}
        except FileNotFoundError:
            return {"error": f"File '{filename}' not found in {ESPHOME_CONFIG_DIR}."}
        except OSError as err:
            _LOGGER.error("Error reading %s: %s", file_path, err)
            return {"error": str(err)}


class WriteYamlTool(llm.Tool):
    """Write an ESPHome YAML configuration file."""

    def __init__(self) -> None:
        super().__init__(
            name="esphome_write_yaml",
            description=(
                "Write or overwrite an ESPHome YAML configuration file in "
                "/config/esphome. Use this to create or update device configurations."
            ),
            parameters=vol.Schema(
                {
                    vol.Required("filename"): str,
                    vol.Required("content"): str,
                }
            ),
        )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        try:
            filename = _sanitize_filename(tool_input.tool_args["filename"])
        except ValueError as err:
            return {"error": str(err)}

        if filename in BLOCKED_FILES:
            return {"error": f"Writing to '{filename}' is not permitted."}

        if not (filename.endswith(".yaml") or filename.endswith(".yml")):
            return {"error": "Only .yaml or .yml files may be written."}

        file_path = os.path.join(ESPHOME_CONFIG_DIR, filename)
        content = tool_input.tool_args["content"]

        try:
            await hass.async_add_executor_job(_write_file, file_path, content)
            return {"success": True, "filename": filename}
        except OSError as err:
            _LOGGER.error("Error writing %s: %s", file_path, err)
            return {"error": str(err)}


class CompileTool(llm.Tool):
    """Trigger ESPHome compilation of a configuration file."""

    def __init__(self) -> None:
        super().__init__(
            name="esphome_compile",
            description=(
                "Trigger compilation of an ESPHome configuration file via the "
                "ESPHome Add-on. Returns the job result."
            ),
            parameters=vol.Schema(
                {
                    vol.Required("configuration"): str,
                }
            ),
        )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        try:
            configuration = _sanitize_filename(tool_input.tool_args["configuration"])
        except ValueError as err:
            return {"error": str(err)}

        if configuration in BLOCKED_FILES:
            return {"error": f"Compilation of '{configuration}' is not permitted."}

        supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
        if not supervisor_token:
            return {
                "error": (
                    "SUPERVISOR_TOKEN not available. "
                    "Is Home Assistant running under the Supervisor?"
                )
            }

        payload = {"configuration": configuration, "action": "compile"}
        headers = {
            "Authorization": f"Bearer {supervisor_token}",
            "Content-Type": "application/json",
        }

        import aiohttp  # noqa: PLC0415

        session = async_get_clientsession(hass)
        try:
            async with session.post(
                ESPHOME_JOBS_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                resp.raise_for_status()
                result = await resp.json()
                return {"success": True, "result": result}
        except aiohttp.ClientResponseError as err:
            _LOGGER.error("ESPHome compile request failed [%s]: %s", err.status, err.message)
            return {"error": f"HTTP {err.status}: {err.message}"}
        except aiohttp.ClientError as err:
            _LOGGER.error("ESPHome compile connection error: %s", err)
            return {"error": str(err)}


class ESPHomeBuilderAPI(llm.API):
    """LLM API exposing ESPHome configuration and build tools."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass=hass,
            id="esphome_builder",
            name="ESPHome Builder",
        )

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        tools: list[llm.Tool] = [
            ReadYamlTool(),
            WriteYamlTool(),
            CompileTool(),
        ]
        return llm.APIInstance(
            api=self,
            api_prompt=(
                "You have access to the ESPHome configuration system running inside "
                "Home Assistant. You can read and write YAML configuration files in "
                "/config/esphome, and trigger compilation of those configurations "
                "via the ESPHome Add-on. Never read or write secrets.yaml."
            ),
            llm_context=llm_context,
            tools=tools,
        )
