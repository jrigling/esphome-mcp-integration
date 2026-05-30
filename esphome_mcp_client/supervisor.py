"""Client for the Home Assistant Supervisor API.

Used to discover installed ESPHome add-ons (stable / beta / dev) and to
resolve the internal base URL of each add-on's dashboard so the dashboard
client can talk to it without going through ingress authentication.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

from .const import (
    CHANNEL_SUFFIXES,
    DEFAULT_DASHBOARD_PORT,
    ESPHOME_SLUG_MATCH,
    SUPERVISOR_BASE_URL,
)
from .exceptions import AddonNotFoundError, SupervisorError

_LOGGER = logging.getLogger(__name__)


@dataclass
class AddonInfo:
    """Summary of an installed ESPHome add-on."""

    slug: str
    name: str
    version: str | None
    version_latest: str | None
    state: str | None
    update_available: bool

    @property
    def is_running(self) -> bool:
        return self.state == "started"


class SupervisorClient:
    """Thin async wrapper over the Supervisor REST API.

    ``session`` is an externally owned :class:`aiohttp.ClientSession` (in Home
    Assistant, the shared session). ``token`` is the value of the
    ``SUPERVISOR_TOKEN`` environment variable injected into HA Core.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str,
        base_url: str = SUPERVISOR_BASE_URL,
    ) -> None:
        self._session = session
        self._token = token
        self._base_url = base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def _get(self, path: str) -> dict:
        """GET a Supervisor endpoint and return the unwrapped ``data`` object."""
        url = f"{self._base_url}{path}"
        try:
            async with self._session.get(
                url,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json()
        except aiohttp.ClientError as err:
            raise SupervisorError(f"Supervisor GET {path} failed: {err}") from err

        if payload.get("result") != "ok":
            raise SupervisorError(
                f"Supervisor GET {path} returned: {payload.get('message', payload)}"
            )
        return payload.get("data", {})

    async def list_esphome_addons(self) -> list[AddonInfo]:
        """Return all installed add-ons that look like ESPHome dashboards."""
        data = await self._get("/addons")
        addons = data.get("addons", [])
        result: list[AddonInfo] = []
        for addon in addons:
            slug = addon.get("slug", "")
            name = addon.get("name", "")
            if ESPHOME_SLUG_MATCH in slug.lower() or ESPHOME_SLUG_MATCH in name.lower():
                result.append(
                    AddonInfo(
                        slug=slug,
                        name=name,
                        version=addon.get("version"),
                        version_latest=addon.get("version_latest"),
                        state=addon.get("state"),
                        update_available=bool(addon.get("update_available")),
                    )
                )
        return result

    async def get_addon_info(self, slug: str) -> dict:
        """Return the full info object for a single add-on."""
        try:
            return await self._get(f"/addons/{slug}/info")
        except SupervisorError as err:
            # Supervisor returns a non-ok result (often 400/404) for unknown slugs.
            raise AddonNotFoundError(f"Add-on '{slug}' not found: {err}") from err

    async def async_default_slug(self) -> str:
        """Pick a sensible default add-on, preferring stable > beta > dev.

        Within a channel, a running add-on is preferred over a stopped one.
        """
        addons = await self.list_esphome_addons()
        if not addons:
            raise AddonNotFoundError("No ESPHome add-on is installed.")

        def rank(addon: AddonInfo) -> tuple[int, int]:
            channel = next(
                (i for i, suffix in enumerate(CHANNEL_SUFFIXES) if suffix and addon.slug.endswith(suffix)),
                0,  # no suffix -> stable -> highest priority
            )
            return (channel, 0 if addon.is_running else 1)

        return sorted(addons, key=rank)[0].slug

    async def get_dashboard_base_url(self, slug: str) -> str:
        """Resolve the internal HTTP base URL of an add-on's dashboard.

        Uses the add-on's container ``hostname`` and ``ingress_port`` reported
        by the Supervisor. Direct access to this port bypasses ingress auth,
        which is exactly what an in-cluster caller (HA Core) needs.
        """
        info = await self.get_addon_info(slug)
        hostname = info.get("hostname") or slug.replace("_", "-")
        port = info.get("ingress_port") or DEFAULT_DASHBOARD_PORT
        return f"http://{hostname}:{port}"
