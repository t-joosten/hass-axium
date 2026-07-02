"""Services for managing Axium alarms from the frontend (or automations).

The alarm cards call these to add, edit, remove and enable/disable alarms
without opening the options dialog. Alarms live in the config entry's options;
these services upsert/remove entries and let the update listener decide whether
a reload is needed (field-only edits refresh in place).
"""

from __future__ import annotations

from functools import partial

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import CONF_ALARMS, DOMAIN
from .helpers import get_alarms

SERVICE_SET_ALARM = "set_alarm"
SERVICE_REMOVE_ALARM = "remove_alarm"

_SET_ALARM_SCHEMA = vol.Schema(
    {
        vol.Optional("hub"): cv.string,
        vol.Required("name"): cv.string,
        vol.Optional("time"): cv.string,
        vol.Optional("days"): vol.All(cv.ensure_list, [vol.All(int, vol.Range(0, 6))]),
        vol.Optional("zones"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("source"): vol.Coerce(int),
        vol.Optional("volume"): vol.All(vol.Coerce(int), vol.Range(0, 100)),
        vol.Optional("enabled"): cv.boolean,
    }
)

_REMOVE_ALARM_SCHEMA = vol.Schema(
    {
        vol.Optional("hub"): cv.string,
        vol.Required("name"): cv.string,
    }
)


def _resolve_entry(hass: HomeAssistant, hub: str | None) -> ConfigEntry:
    """Find the target config entry (by id, or the only one)."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if hub:
        for entry in entries:
            if entry.entry_id == hub:
                return entry
        raise ServiceValidationError(f"Unknown Axium amplifier: {hub}")
    if len(entries) == 1:
        return entries[0]
    raise ServiceValidationError(
        "Multiple Axium amplifiers configured; specify 'hub'."
    )


async def _async_set_alarm(hass: HomeAssistant, call: ServiceCall) -> None:
    """Add or update an alarm by name."""
    entry = _resolve_entry(hass, call.data.get("hub"))
    name = call.data["name"].strip()
    existing = next(
        (a for a in get_alarms(entry) if a["name"] == name), None
    )
    alarm = dict(existing) if existing else {
        "name": name,
        "time": "07:00",
        "days": [],
        "zones": [],
        "source": 0,
        "volume": 30,
        "enabled": True,
    }
    for key in ("time", "days", "zones", "source", "volume", "enabled"):
        if key in call.data:
            alarm[key] = call.data[key]
    if "time" in alarm:
        alarm["time"] = str(alarm["time"])[:5]
    others = [a for a in get_alarms(entry) if a["name"] != name]
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_ALARMS: [*others, alarm]}
    )


async def _async_remove_alarm(hass: HomeAssistant, call: ServiceCall) -> None:
    """Remove an alarm by name."""
    entry = _resolve_entry(hass, call.data.get("hub"))
    name = call.data["name"].strip()
    remaining = [a for a in get_alarms(entry) if a["name"] != name]
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_ALARMS: remaining}
    )


def async_register_services(hass: HomeAssistant) -> None:
    """Register the alarm services once."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_ALARM):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_ALARM,
        partial(_async_set_alarm, hass),
        schema=_SET_ALARM_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_ALARM,
        partial(_async_remove_alarm, hass),
        schema=_REMOVE_ALARM_SCHEMA,
    )
