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
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    selector,
)

from . import protocol
from .const import (
    CMD_REQUEST_DEVICE_INFO,
    CMD_SOURCE_NAME,
    CONF_ADVANCED,
    CONF_ALARMS,
    CONF_PRESETS,
    CONF_SOURCES,
    CONF_UNITS,
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
    ZONE_KEY,
)
from .controller import (
    UnitInfo,
    parse_device_info,
    parse_source_name,
)
from .helpers import (
    default_sources,
    get_advanced,
    get_alarms,
    get_presets,
    get_sources,
    get_zones,
    sources_from_detection,
    units_config,
    zones_from_numbers,
    zones_from_units,
)

_WEEKDAYS = [
    ("0", "Monday"),
    ("1", "Tuesday"),
    ("2", "Wednesday"),
    ("3", "Thursday"),
    ("4", "Friday"),
    ("5", "Saturday"),
    ("6", "Sunday"),
]

_CONNECT_TIMEOUT = 10.0
_PROBE_TIMEOUT = 6.0
_STACK_GRACE = 1.5


async def _async_probe_amplifier(
    host: str, port: int
) -> tuple[list[UnitInfo], int | None, list[dict]] | None:
    """Connect, confirm an Axium amplifier is present, and discover its layout.

    Requests device info (with the zone list) from the whole stack and all
    source names. Returns ``(units, primary_unit_id, sources)`` — one UnitInfo
    per amp in the stack (with its zones), which unit is primary, and the
    detected sources — or ``None`` if nothing answers within the timeout. Raises
    ``OSError``/``asyncio.TimeoutError`` if the connection cannot open.
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

        units: dict[int, UnitInfo] = {}
        primary_unit_id: int | None = None
        sources: list[dict] = []
        saw_amp = False
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
                info = parse_device_info(frame[2:])
                if info is None:
                    continue
                saw_amp = True
                if info.unit_id is None:
                    continue
                if primary_unit_id is None:
                    # First reply is the directly-connected/primary amp; wait a
                    # short grace for other stack members and source names.
                    primary_unit_id = info.unit_id
                    end = min(end, loop.time() + _STACK_GRACE)
                unit = units.setdefault(
                    info.unit_id, UnitInfo(unit_id=info.unit_id)
                )
                unit.model = info.model
                unit.model_code = info.model_code
                unit.firmware_major = info.firmware_major
                if info.zones:
                    unit.zones = sorted(set(unit.zones) | set(info.zones))
            elif frame[0] == CMD_SOURCE_NAME:
                parsed = parse_source_name(frame[2:])
                if parsed is not None:
                    sources.append(parsed)

        if not units:
            # An amp answered but reported no unit id: add it as a legacy single
            # amp (default zones, no per-unit config) rather than failing.
            return ([], None, sources) if saw_amp else None
        return list(units.values()), primary_unit_id, sources
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
            result = None
            try:
                result = await _async_probe_amplifier(host, port)
            except (OSError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                if result is None:
                    errors["base"] = "no_amplifier"
            if not errors:
                units, primary_unit_id, detected = result
                # Every zone the stack reports (with its owning amp), or a
                # sensible default; plus the enabled sources with their names.
                zones = zones_from_units(units) or zones_from_numbers(
                    list(range(1, DEFAULT_ZONE_COUNT + 1))
                )
                sources = sources_from_detection(detected) or default_sources()
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_ZONES: zones,
                        CONF_SOURCES: sources,
                        CONF_UNITS: units_config(units, primary_unit_id),
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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Rename the hub and/or change its connection details (host/port).

        The whole stack is reached over a single TCP connection (the primary amp
        relays to its expansion amps), so there is one host/port for the hub.
        Use this when the amplifier's IP or port changes (e.g. a new DHCP lease
        after a reboot), or to rename the hub — the discovered zones, sources and
        options are preserved.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            name = (user_input.get(CONF_NAME) or "").strip()
            result = None
            try:
                result = await _async_probe_amplifier(host, port)
            except (OSError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                if result is None:
                    errors["base"] = "no_amplifier"
            if not errors:
                units, primary_unit_id, _ = result
                updates = {CONF_HOST: host, CONF_PORT: port}
                if name:
                    updates[CONF_NAME] = name
                    # Keep the hub device's own name in step with the title,
                    # unless the user has renamed it via the device pencil.
                    dev_reg = dr.async_get(self.hass)
                    hub_dev = dev_reg.async_get_device(
                        identifiers={(DOMAIN, entry.entry_id)}
                    )
                    if hub_dev and not hub_dev.name_by_user:
                        dev_reg.async_update_device(hub_dev.id, name=name)
                # Re-detect the stack so a newly-stacked expansion amp's zones
                # and unit are added, preserving existing zone names. Grow only —
                # never drop zones because an expansion amp was powered off or
                # slow to answer during this re-probe (mirrors _handle_stack).
                zones = zones_from_units(units, get_zones(entry))
                existing = {z[ZONE_KEY] for z in get_zones(entry)}
                discovered = {z[ZONE_KEY] for z in zones}
                if zones and discovered >= existing:
                    updates[CONF_ZONES] = zones
                    updates[CONF_UNITS] = units_config(units, primary_unit_id)
                return self.async_update_reload_and_abort(
                    entry, data_updates=updates, title=name or entry.title
                )

        current = user_input or entry.data
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_NAME, default=current.get(CONF_NAME, entry.title)
                ): cv.string,
                vol.Required(
                    CONF_HOST, default=current.get(CONF_HOST, "")
                ): cv.string,
                vol.Required(
                    CONF_PORT, default=current.get(CONF_PORT, DEFAULT_PORT)
                ): cv.port,
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
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
            menu_options=[
                "settings",
                "add_preset",
                "remove_preset",
                "add_alarm",
                "remove_alarm",
            ],
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

    async def async_step_add_alarm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add (or replace by name) a wake-to-music alarm."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            zones = user_input["zones"]
            if not name:
                errors["base"] = "alarm_name_required"
            elif not zones:
                errors["base"] = "alarm_zones_required"
            else:
                current = get_alarms(self._config_entry)
                # Preserve the enabled/armed state when editing an existing
                # alarm (same name); a new alarm defaults to enabled.
                existing = next((a for a in current if a["name"] == name), None)
                alarms = [a for a in current if a["name"] != name]
                # A MediaSelector value is a dict {entity_id, media_content_id,
                # media_content_type, metadata}; pull the wake-media fields out.
                media_sel = user_input.get("media") or {}
                if not isinstance(media_sel, dict):
                    media_sel = {}
                media_meta = media_sel.get("metadata") or {}
                alarms.append(
                    {
                        "name": name,
                        "time": str(user_input["time"])[:5],
                        "days": [int(d) for d in user_input.get("days", [])],
                        "zones": zones,
                        "source": int(user_input["source"]),
                        "volume": int(user_input["volume"]),
                        "enabled": existing["enabled"] if existing else True,
                        "duration": int(user_input.get("duration", 0) or 0),
                        "media": media_sel.get("media_content_id", ""),
                        "media_type": media_sel.get("media_content_type", ""),
                        "media_title": media_meta.get("title", ""),
                        "media_player": media_sel.get("entity_id", ""),
                    }
                )
                self._options[CONF_ALARMS] = alarms
                return self.async_create_entry(data=self._options)

        source_options = [
            selector.SelectOptionDict(value=str(s[ID_KEY]), label=s[NAME_KEY])
            for s in get_sources(self._config_entry)
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): cv.string,
                vol.Required("time"): selector.TimeSelector(),
                vol.Optional("days", default=[d for d, _ in _WEEKDAYS]): (
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value=v, label=label)
                                for v, label in _WEEKDAYS
                            ],
                            multiple=True,
                        )
                    )
                ),
                vol.Required("zones"): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        integration=DOMAIN, domain="media_player", multiple=True
                    )
                ),
                vol.Required("source"): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=source_options)
                ),
                vol.Required("volume", default=30): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, step=1, unit_of_measurement="%"
                    )
                ),
                vol.Optional("duration", default=0): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1440,
                        step=1,
                        unit_of_measurement="min",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                # Optional "wake to Music Assistant" media (browse to pick a
                # song/album/playlist); overrides the fixed Source when set.
                vol.Optional("media"): selector.MediaSelector(),
            }
        )
        return self.async_show_form(
            step_id="add_alarm", data_schema=schema, errors=errors
        )

    async def async_step_remove_alarm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove one or more alarms."""
        alarms = get_alarms(self._config_entry)
        names = [a["name"] for a in alarms]
        if not names:
            return self.async_abort(reason="no_alarms")
        if user_input is not None:
            remove = set(user_input.get("remove", []))
            self._options[CONF_ALARMS] = [
                a for a in alarms if a["name"] not in remove
            ]
            return self.async_create_entry(data=self._options)

        schema = vol.Schema(
            {
                vol.Optional("remove", default=[]): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=names, multiple=True)
                ),
            }
        )
        return self.async_show_form(step_id="remove_alarm", data_schema=schema)
