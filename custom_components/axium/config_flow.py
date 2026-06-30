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
from homeassistant.helpers import config_validation as cv

from . import protocol
from .const import (
    CMD_REQUEST_DEVICE_INFO,
    CMD_SOURCE_NAME,
    CONF_SOURCES,
    CONF_ZONES,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_ZONE_COUNT,
    DEVICE_INFO_LIST_ZONES,
    DOMAIN,
    ID_KEY,
    NAME_KEY,
    RESP_DEVICE_INFO,
    ZONE_ALL,
)
from .controller import (
    AxiumController,
    AxiumDeviceInfo,
    parse_device_info,
    parse_source_name,
)
from .helpers import (
    default_sources,
    get_sources,
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
    """Rename sources. One field per source; names are written to the amp.

    Zones are renamed from their device pages, so only sources are managed here.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Store the config entry."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show one name field per source; save and write changes to the amp."""
        current = get_sources(self._config_entry)

        if user_input is not None:
            controller: AxiumController | None = self.hass.data.get(DOMAIN, {}).get(
                self._config_entry.entry_id
            )
            new_sources: list[dict[str, Any]] = []
            for item in current:
                old_name = item[NAME_KEY]
                new_name = (user_input.get(old_name) or "").strip() or old_name
                new_sources.append({ID_KEY: item[ID_KEY], NAME_KEY: new_name})
                if controller is not None and new_name != old_name:
                    await controller.async_set_source_name(item[ID_KEY], new_name)
            return self.async_create_entry(data={CONF_SOURCES: new_sources})

        # Field key = current name (so the label is readable); value = the name.
        schema = vol.Schema(
            {
                vol.Optional(item[NAME_KEY], default=item[NAME_KEY]): cv.string
                for item in current
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
