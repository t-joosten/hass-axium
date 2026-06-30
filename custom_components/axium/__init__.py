"""The Axium amplifier integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import (
    DEFAULT_PORT,
    DOMAIN,
    LINK_OPTIONS_DEFAULT,
    ZONE_KEY,
    ZONES_KEY,
)
from .controller import AxiumController, AxiumDeviceInfo
from .helpers import get_groups, get_zones

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Axium from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)

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

    try:
        await controller.async_start()
    except (ConnectionError, OSError) as err:
        raise ConfigEntryNotReady(
            f"Unable to connect to Axium amplifier at {host}:{port}"
        ) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _async_apply_group_links(entry, controller)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_apply_group_links(
    entry: ConfigEntry, controller: AxiumController
) -> None:
    """Make the amplifier's zone links match the configured groups.

    Each group (2+ zones) is linked on the amplifier so it keeps the zones in
    sync. Zones that are not in any group are sent as a single-zone link to
    clear any stale grouping. This converges the amp's link state to the
    integration's configuration on every (re)load.
    """
    groups = get_groups(entry)
    grouped: set[int] = set()
    for group in groups:
        members = sorted(set(group[ZONES_KEY]))
        if len(members) >= 2:
            grouped.update(members)
            await controller.async_link_zones(members, LINK_OPTIONS_DEFAULT)

    for item in get_zones(entry):
        zone = item[ZONE_KEY]
        if zone not in grouped:
            await controller.async_link_zones([zone], LINK_OPTIONS_DEFAULT)


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
