"""Config and options flow for the Axium amplifier integration."""

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
    CMD_LINK_ZONES,
    CMD_REQUEST_DEVICE_INFO,
    CONF_GROUP_NAME,
    CONF_GROUP_ZONES,
    CONF_GROUPS,
    CONF_ZONES,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_ZONE_COUNT,
    DEVICE_INFO_LIST_ZONES,
    DEVICE_INFO_NO_EXPANSION_REPLY,
    DEVICE_INFO_REPLY_ON_PORT_ONLY,
    DOMAIN,
    LINK_REQUEST_GROUPED,
    NAME_KEY,
    RESP_DEVICE_INFO,
    ZONE_ALL,
    ZONE_KEY,
    ZONES_KEY,
)
from .controller import AxiumDeviceInfo, parse_device_info, parse_link_group
from .helpers import (
    format_zone_spec,
    get_groups,
    get_zones,
    groups_from_memberships,
    parse_zone_spec,
    zones_from_numbers,
)

_CONNECT_TIMEOUT = 10.0
_PROBE_TIMEOUT = 6.0
_GROUP_GRACE = 1.5


async def _async_probe_amplifier(host: str, port: int) -> AxiumDeviceInfo | None:
    """Connect, confirm an Axium amplifier is present, and discover its layout.

    Sends Request Device information (with the zone list) and a request for the
    amplifier's current zone groups. Returns the parsed device info — including
    discovered zones and link groups — when an amplifier replies, or ``None`` if
    nothing answers within the timeout. Raises ``OSError``/``asyncio.TimeoutError``
    if the connection cannot open.
    """
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=_CONNECT_TIMEOUT
    )
    loop = asyncio.get_running_loop()
    try:
        writer.write(
            protocol.encode(
                CMD_REQUEST_DEVICE_INFO,
                ZONE_ALL,
                DEVICE_INFO_NO_EXPANSION_REPLY
                | DEVICE_INFO_REPLY_ON_PORT_ONLY
                | DEVICE_INFO_LIST_ZONES,
            )
        )
        writer.write(protocol.encode(CMD_LINK_ZONES, ZONE_ALL, LINK_REQUEST_GROUPED))
        await writer.drain()

        device_info: AxiumDeviceInfo | None = None
        groups: list[list[int]] = []
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
                device_info = parse_device_info(frame[2:]) or AxiumDeviceInfo()
                # Once identified, wait only a short grace for group replies.
                end = min(end, loop.time() + _GROUP_GRACE)
            elif frame[0] == CMD_LINK_ZONES:
                members = parse_link_group(frame[2:])
                if members:
                    groups.append(members)

        if device_info is None:
            return None
        device_info.link_groups = groups
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
                # Create every zone the amplifier reports (or a sensible
                # default if it does not list them). Names are edited later.
                numbers = device_info.zones or list(
                    range(1, DEFAULT_ZONE_COUNT + 1)
                )
                zones = zones_from_numbers(numbers)
                valid = {item[ZONE_KEY] for item in zones}
                groups = groups_from_memberships(device_info.link_groups, valid)
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_ZONES: zones,
                        CONF_GROUPS: groups,
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
    """Menu-driven options flow to manage zone names and zone groups."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Load the working copies of zones and groups."""
        self._config_entry = config_entry
        self._zones: list[dict[str, Any]] = get_zones(config_entry)
        self._groups: list[dict[str, Any]] = get_groups(config_entry)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the main options menu."""
        menu_options = ["zones", "add_group"]
        if self._groups:
            menu_options.extend(["rename_group", "remove_group"])
        menu_options.append("save")
        return self.async_show_menu(step_id="init", menu_options=menu_options)

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the zone numbers and names."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._zones = parse_zone_spec(user_input[CONF_ZONES])
            except ValueError:
                errors[CONF_ZONES] = "invalid_zones"
            else:
                self._prune_groups()
                return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ZONES, default=format_zone_spec(self._zones)
                ): cv.string,
            }
        )
        return self.async_show_form(
            step_id="zones", data_schema=schema, errors=errors
        )

    async def async_step_add_group(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a new zone group."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_GROUP_NAME].strip()
            selected = user_input.get(CONF_GROUP_ZONES, [])
            existing = {group[NAME_KEY].casefold() for group in self._groups}
            if not name:
                errors[CONF_GROUP_NAME] = "invalid_group_name"
            elif name.casefold() in existing:
                errors[CONF_GROUP_NAME] = "duplicate_group"
            elif not selected:
                errors[CONF_GROUP_ZONES] = "no_group_zones"
            else:
                self._groups.append(
                    {
                        NAME_KEY: name,
                        ZONES_KEY: sorted(int(zone) for zone in selected),
                    }
                )
                return await self.async_step_init()

        # A zone may belong to only one group on the amplifier, so only offer
        # zones that are not already in a group.
        taken = {zone for group in self._groups for zone in group[ZONES_KEY]}
        zone_options = {
            str(item[ZONE_KEY]): item[NAME_KEY]
            for item in self._zones
            if item[ZONE_KEY] not in taken
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_GROUP_NAME): cv.string,
                vol.Required(CONF_GROUP_ZONES): cv.multi_select(zone_options),
            }
        )
        return self.async_show_form(
            step_id="add_group", data_schema=schema, errors=errors
        )

    async def async_step_rename_group(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rename an existing zone group."""
        errors: dict[str, str] = {}
        if user_input is not None:
            target = user_input[CONF_GROUPS]
            new_name = user_input[CONF_GROUP_NAME].strip()
            others = {
                group[NAME_KEY].casefold()
                for group in self._groups
                if group[NAME_KEY] != target
            }
            if not new_name:
                errors[CONF_GROUP_NAME] = "invalid_group_name"
            elif new_name.casefold() in others:
                errors[CONF_GROUP_NAME] = "duplicate_group"
            else:
                for group in self._groups:
                    if group[NAME_KEY] == target:
                        group[NAME_KEY] = new_name
                        break
                return await self.async_step_init()

        group_options = {group[NAME_KEY]: group[NAME_KEY] for group in self._groups}
        schema = vol.Schema(
            {
                vol.Required(CONF_GROUPS): vol.In(group_options),
                vol.Required(CONF_GROUP_NAME): cv.string,
            }
        )
        return self.async_show_form(
            step_id="rename_group", data_schema=schema, errors=errors
        )

    async def async_step_remove_group(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove one or more zone groups."""
        if user_input is not None:
            to_remove = set(user_input.get(CONF_GROUPS, []))
            self._groups = [
                group
                for group in self._groups
                if group[NAME_KEY] not in to_remove
            ]
            return await self.async_step_init()

        group_options = {group[NAME_KEY]: group[NAME_KEY] for group in self._groups}
        schema = vol.Schema(
            {vol.Required(CONF_GROUPS): cv.multi_select(group_options)}
        )
        return self.async_show_form(step_id="remove_group", data_schema=schema)

    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Persist the working copies as the entry options."""
        return self.async_create_entry(
            data={CONF_ZONES: self._zones, CONF_GROUPS: self._groups}
        )

    def _prune_groups(self) -> None:
        """Drop zones from groups that no longer exist, and empty groups."""
        valid = {item[ZONE_KEY] for item in self._zones}
        pruned: list[dict[str, Any]] = []
        for group in self._groups:
            zones = [zone for zone in group[ZONES_KEY] if zone in valid]
            if zones:
                pruned.append({NAME_KEY: group[NAME_KEY], ZONES_KEY: zones})
        self._groups = pruned
