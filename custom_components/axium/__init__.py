"""The Axium amplifier integration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import DEFAULT_PORT, DOMAIN
from .controller import AxiumController, AxiumDeviceInfo

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.TEXT,
]


_CARD_URL = "/axium/axium-source-card.js"
_CARD_PATH = "lovelace/axium-source-card.js"


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve and register the bundled Lovelace card (best effort, once).

    Served via a view with an explicit ``application/javascript`` content type
    so it always loads as an ES module — some platforms (notably Windows) would
    otherwise serve ``.js`` as ``text/plain``, which browsers refuse to run.

    The frontend imports the card from a **version-stamped path**
    (``/axium/axium-source-card-<version>.js``) rather than a ``?v=`` query. A
    brand-new path is one no browser HTTP cache or service worker has seen
    before, so every release is guaranteed to load fresh — a plain query string
    can be ignored by a service worker's cache key and serve a stale/broken copy.
    The unversioned ``/axium/axium-source-card.js`` stays valid for anyone who
    added it as a manual dashboard resource.
    """
    if hass.data.get(f"{DOMAIN}_card_registered"):
        return
    try:
        from aiohttp import web

        from homeassistant.components.frontend import add_extra_js_url
        from homeassistant.components.http import HomeAssistantView
        from homeassistant.loader import async_get_integration

        integration = await async_get_integration(hass, DOMAIN)
        version = str(integration.version)

        card_path = Path(__file__).parent / _CARD_PATH
        card_bytes = await hass.async_add_executor_job(card_path.read_bytes)

        class AxiumCardView(HomeAssistantView):
            """Serve the Axium dashboard card with a correct JS content type."""

            url = _CARD_URL
            extra_urls = ["/axium/axium-source-card-{version}.js"]
            name = "axium:card"
            requires_auth = False

            async def get(
                self, request: web.Request, version: str | None = None
            ) -> web.Response:
                """Return the card JavaScript.

                A version-stamped path is content-addressed, so it may be cached
                forever; the unversioned path uses a short max-age.
                """
                cache = (
                    "public, max-age=31536000, immutable"
                    if version is not None
                    else "public, max-age=300"
                )
                return web.Response(
                    body=card_bytes,
                    content_type="application/javascript",
                    charset="utf-8",
                    headers={"Cache-Control": cache},
                )

        hass.http.register_view(AxiumCardView())

        card_url = f"/axium/axium-source-card-{version}.js"
        # Prefer a managed Lovelace resource: the card picker *waits* for
        # resources, so the card renders in the "Add card" gallery. A
        # fire-and-forget add_extra_js_url import races the picker (it isn't
        # awaited) and shows a perpetual spinner. Fall back to add_extra_js_url
        # only when resources aren't writable (YAML-mode dashboards).
        if not await _async_register_card_resource(hass, card_url):
            add_extra_js_url(hass, card_url)
        hass.data[f"{DOMAIN}_card_registered"] = True
    except Exception as err:  # noqa: BLE001 - card is optional, never block setup
        # Surfaced at warning level: without the card the dashboard still works,
        # but a silent failure here is exactly what makes it hard to diagnose.
        _LOGGER.warning("Could not auto-register the Axium dashboard card: %s", err)


async def _async_register_card_resource(hass: HomeAssistant, url: str) -> bool:
    """Register/refresh a managed Lovelace *module* resource for the card.

    Returns ``True`` when a storage-mode resource was created/updated (the card
    then loads as a resource the card picker awaits), ``False`` when resources
    are read-only (YAML-mode dashboards) so the caller falls back to
    ``add_extra_js_url``.

    Exactly one axium resource is kept, pointed at the current version-stamped
    URL; any stale/duplicate axium resources are updated or removed so old
    version paths don't linger.
    """
    try:
        from homeassistant.components.lovelace.resources import (
            ResourceStorageCollection,
        )
    except ImportError:
        return False

    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if not isinstance(resources, ResourceStorageCollection):
        return False

    if not resources.loaded:
        await resources.async_load()

    ours = [
        item
        for item in resources.async_items()
        if "/axium/axium-source-card" in item.get("url", "")
    ]

    if any(item.get("url") == url for item in ours):
        # Already correct — drop any duplicates pointing at the same/old card.
        keep = next(item for item in ours if item.get("url") == url)
        stale = [item for item in ours if item.get("id") != keep.get("id")]
    elif ours:
        await resources.async_update_item(
            ours[0]["id"], {"res_type": "module", "url": url}
        )
        stale = ours[1:]
    else:
        await resources.async_create_item({"res_type": "module", "url": url})
        stale = []

    for item in stale:
        await resources.async_delete_item(item["id"])
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Axium from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)

    await _async_register_card(hass)

    controller = AxiumController(host, port)

    # Register the amplifier as a hub device so each zone/group device nests
    # under it via their `via_device` reference. Model/firmware are filled in
    # automatically once the amplifier identifies itself (command 0x14).
    device_registry = dr.async_get(hass)
    hub = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Axium",
        name=entry.title,
        model="Amplifier",
        configuration_url=f"http://{host}",
    )

    @callback
    def _update_hub_device(info: AxiumDeviceInfo) -> None:
        """Enrich the hub device with the reported model and firmware."""
        updates: dict[str, str] = {}
        if info.model or info.device_type:
            updates["model"] = info.model or info.device_type  # type: ignore[assignment]
        if info.firmware_major is not None:
            updates["sw_version"] = f"v{info.firmware_major}"
        if updates:
            device_registry.async_update_device(hub.id, **updates)

    controller.set_device_info_callback(_update_hub_device)

    @callback
    def _update_hub_extended(firmware: str | None, mac: str | None) -> None:
        """Enrich the hub with the full firmware version and MAC address."""
        kwargs: dict = {}
        if firmware:
            kwargs["sw_version"] = firmware
        if mac:
            kwargs["merge_connections"] = {
                (dr.CONNECTION_NETWORK_MAC, dr.format_mac(mac))
            }
        if kwargs:
            device_registry.async_update_device(hub.id, **kwargs)

    controller.set_extended_info_callback(_update_hub_extended)

    try:
        await controller.async_start()
    except (ConnectionError, OSError) as err:
        raise ConfigEntryNotReady(
            f"Unable to connect to Axium amplifier at {host}:{port}"
        ) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        controller: AxiumController = hass.data[DOMAIN].pop(entry.entry_id)
        await controller.async_stop()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options (zones) change."""
    await hass.config_entries.async_reload(entry.entry_id)
