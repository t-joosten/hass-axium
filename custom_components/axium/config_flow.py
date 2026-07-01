"""Config flow for the Axium amplifier integration.

Setup only asks for the connection details. Zones and sources are discovered
from the amplifier automatically; zones can be renamed from their device pages
and source names come from the amplifier.
"""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv, selector

from . import protocol
from .const import (
    CMD_REQUEST_DEVICE_INFO,
    CMD_SOURCE_NAME,
    CONF_ADVANCED,
    CONF_PRESETS,
    CONF_SOURCES,
    CONF_ZONES,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_ZONE_COUNT,
    DEVICE_INFO_LIST_ZONES,
    DOMAIN,
    RESP_DEVICE_INFO,
    ZONE_ALL,
)
from .controller import AxiumDeviceInfo, parse_device_info, parse_source_name
from .helpers import (
    default_sources,
    get_advanced,
    get_presets,
    sources_from_detection,
    zones_from_numbers,
)

_CONNECT_TIMEOUT = 10.0
_PROBE_TIMEOUT = 6.0
_STACK_GRACE = 1.5


async def _async_probe_amplifier(host: str, port: int) -> AxiumDeviceInfo | None:
    """Connect, confirm an Axium amplifier is present, and discover its layout.

    Requests device info (with the zone list) from the whole stack and all
    source names. Returns the parsed device info (with discovered zones and
    sources) when an amplifier replies, or ``None`` if nothing answers within
    the timeout. Raises ``OSError``/``asyncio.TimeoutError`` if the connection
    cannot open.
    """
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=_CONNECT_TIMEOUT
    )
    loop = asyncio.get_running_loop()
    try:
        writer.write(
            protocol.encode(CMD_REQUEST_DEVICE_INFO, ZONE_ALL, DEVICE_INFO_LIST_ZONES)
        )
        writer.write(protocol.encode(CMD_SOURCE_NAME, ZONE_ALL))  # request all names
        await writer.drain()

        device_info: AxiumDeviceInfo | None = None
        all_zones: set[int] = set()
        sources: list[dict] = []
        end = loop.time() + _PROBE_TIMEOUT
        while True:
            remaining = end - loop.time()
            if remaining <= 0:
                break
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if not line:  # connection closed by peer
                break
            frame = protocol.decode(line)
            if frame is None or len(frame) < 2:
                continue
            if frame[0] == RESP_DEVICE_INFO:
                info = parse_device_info(frame[2:]) or AxiumDeviceInfo()
                all_zones.update(info.zones)
                if device_info is None:
                    # First reply (the directly-connected amp) labels the hub;
                    # wait a short grace for other stack members and names.
                    device_info = info
                    end = min(end, loop.time() + _STACK_GRACE)
            elif frame[0] == CMD_SOURCE_NAME:
                parsed = parse_source_name(frame[2:])
                if parsed is not None:
                    sources.append(parsed)

        if device_info is None:
            return None
        device_info.zones = sorted(all_zones)
        device_info.sources = sources
        return device_info
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


class AxiumConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI configuration flow for an Axium amplifier."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            try:
                device_info = await _async_probe_amplifier(host, port)
            except (OSError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                if device_info is None:
                    errors["base"] = "no_amplifier"
            if not errors:
                # Discover every zone the amplifier (stack) reports — or a
                # sensible default — and the enabled sources with their names.
                numbers = device_info.zones or list(
                    range(1, DEFAULT_ZONE_COUNT + 1)
                )
                sources = (
                    sources_from_detection(device_info.sources) or default_sources()
                )
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_ZONES: zones_from_numbers(numbers),
                        CONF_SOURCES: sources,
                    },
                )

        suggested = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=suggested.get(CONF_HOST, "")
                ): cv.string,
                vol.Required(
                    CONF_PORT, default=suggested.get(CONF_PORT, DEFAULT_PORT)
                ): cv.port,
                vol.Required(
                    CONF_NAME, default=suggested.get(CONF_NAME, DEFAULT_NAME)
                ): cv.string,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AxiumOptionsFlow:
        """Return the options flow."""
        return AxiumOptionsFlow(config_entry)


class AxiumOptionsFlow(OptionsFlow):
    """Advanced-controls toggle and zone-preset management."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Store the config entry and a working copy of its options."""
        self._config_entry = config_entry
        self._options: dict[str, Any] = dict(config_entry.options)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose what to configure."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "add_preset", "remove_preset"],
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Toggle the advanced settings; reloads to add/remove the entities."""
        if user_input is not None:
            self._options[CONF_ADVANCED] = user_input[CONF_ADVANCED]
            return self.async_create_entry(data=self._options)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ADVANCED, default=get_advanced(self._config_entry)
                ): cv.boolean,
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_add_preset(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add (or replace by name) a zone preset."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            zones = user_input["zones"]
            if not name:
                errors["base"] = "preset_name_required"
            elif not zones:
                errors["base"] = "preset_zones_required"
            else:
                presets = [
                    p
                    for p in get_presets(self._config_entry)
                    if p["name"] != name
                ]
                presets.append({"name": name, "zones": zones})
                self._options[CONF_PRESETS] = presets
                return self.async_create_entry(data=self._options)

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): cv.string,
                vol.Required("zones"): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        integration=DOMAIN, domain="media_player", multiple=True
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="add_preset", data_schema=schema, errors=errors
        )

    async def async_step_remove_preset(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove one or more zone presets."""
        presets = get_presets(self._config_entry)
        names = [p["name"] for p in presets]
        if not names:
            return self.async_abort(reason="no_presets")
        if user_input is not None:
            remove = set(user_input.get("remove", []))
            self._options[CONF_PRESETS] = [
                p for p in presets if p["name"] not in remove
            ]
            return self.async_create_entry(data=self._options)

        schema = vol.Schema(
            {
                vol.Optional("remove", default=[]): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=names, multiple=True)
                ),
            }
        )
        return self.async_show_form(step_id="remove_preset", data_schema=schema)
