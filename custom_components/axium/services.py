"""Services for managing Axium alarms from the frontend (or automations).

The alarm cards call these to add, edit, remove and enable/disable alarms
without opening the options dialog. Alarms live in the config entry's options;
these services upsert/remove entries and let the update listener decide whether
a reload is needed (field-only edits refresh in place).
"""

from __future__ import annotations

import asyncio
from functools import partial

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    CMD_MUTE,
    CMD_POWER,
    CMD_SOURCE,
    CMD_VOLUME,
    CONF_ALARMS,
    DOMAIN,
    MUTE_OFF,
    MUTE_ON,
    POWER_OFF,
    POWER_ON,
    SOURCE_FLAG_TURN_ON,
    SOURCE_MEDIA_PLAYER_BYTE,
)
from .helpers import get_alarms, get_presets
from .protocol import level_to_volume

SERVICE_SET_ALARM = "set_alarm"
SERVICE_REMOVE_ALARM = "remove_alarm"
SERVICE_PLAY_NOTIFICATION = "play_notification"

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


_PLAY_NOTIFICATION_SCHEMA = vol.Schema(
    {
        vol.Optional("hub"): cv.string,
        vol.Optional("zones"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("presets"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("volume"): vol.All(vol.Coerce(int), vol.Range(0, 100)),
        vol.Optional("source"): vol.Coerce(int),
        vol.Optional("media_player"): cv.entity_id,
        vol.Optional("media_content_id"): cv.string,
        vol.Optional("media_content_type"): cv.string,
        vol.Optional("duration"): vol.All(vol.Coerce(float), vol.Range(0, 300)),
    }
)

# One notification at a time per hub, so overlapping calls can't corrupt the
# save/restore (module-level: hass is a singleton; the locks survive reloads).
_NOTIFY_LOCKS: dict[str, asyncio.Lock] = {}


async def _wait_media_done(hass: HomeAssistant, renderer: str) -> None:
    """Wait for a renderer to start, then finish, playing (bounded ~2min)."""
    for _ in range(20):  # allow ~4s for playback to start
        state = hass.states.get(renderer)
        if state and state.state == "playing":
            break
        await asyncio.sleep(0.2)
    for _ in range(300):  # then wait for it to stop (~120s cap)
        state = hass.states.get(renderer)
        if not state or state.state != "playing":
            return
        await asyncio.sleep(0.4)


async def _async_play_notification(hass: HomeAssistant, call: ServiceCall) -> None:
    """Play a sound on chosen zones, then restore exactly what they were doing.

    Snapshots each target zone's power/source/volume/mute, overrides them to the
    notification source at the notification volume, plays the sound (through a
    given renderer — e.g. the amp's DLNA player or a Music Assistant player), then
    restores every zone as it was. The amplifier can't mix audio, so this
    *overrides* the source for the duration rather than ducking under it; the
    notification's own (louder) volume plus the restore is the closest equivalent.
    """
    entry = _resolve_entry(hass, call.data.get("hub"))
    controller = hass.data[DOMAIN][entry.entry_id]

    # Target zones = explicit zones + the zones of each named preset.
    entity_ids = list(call.data.get("zones", []))
    if call.data.get("presets"):
        by_name = {p["name"]: p.get("zones", []) for p in get_presets(entry)}
        for name in call.data["presets"]:
            entity_ids.extend(by_name.get(name, []))
    zones: list[int] = []
    for eid in entity_ids:
        zone = controller.zone_for_entity_id(eid)
        if zone is not None and zone not in zones:
            zones.append(zone)
    if not zones:
        raise ServiceValidationError(
            "play_notification: no Axium zones resolved from 'zones'/'presets'."
        )

    # Notification source: explicit, else the detected internal Media Player.
    source = call.data.get("source")
    if source is None:
        detected = controller.media_sources()
        source = detected[0] if detected else SOURCE_MEDIA_PLAYER_BYTE
    level = call.data["volume"] / 100 if "volume" in call.data else None

    lock = _NOTIFY_LOCKS.setdefault(entry.entry_id, asyncio.Lock())
    async with lock:
        # Snapshot inside the lock so a queued call captures the restored state.
        snapshot = {}
        for zone in zones:
            state = controller.zone_state(zone)
            snapshot[zone] = (state.power, state.source, state.volume, state.muted)

        # Override: power on, unmute, select the source, set the volume.
        for zone in zones:
            await controller.async_send(CMD_POWER, zone, POWER_ON)
            await controller.async_send(CMD_MUTE, zone, MUTE_OFF)
            await controller.async_send(
                CMD_SOURCE, zone, source | SOURCE_FLAG_TURN_ON
            )
            if level is not None:
                await controller.async_send(
                    CMD_VOLUME, zone, level_to_volume(level)
                )
        for zone in zones:
            await controller.async_request_zone_state(zone)

        # Play the sound, then wait for it to finish.
        renderer = call.data.get("media_player")
        content_id = call.data.get("media_content_id")
        if renderer and content_id:
            await hass.services.async_call(
                "media_player",
                "play_media",
                {
                    "entity_id": renderer,
                    "media_content_id": content_id,
                    "media_content_type": call.data.get(
                        "media_content_type", "music"
                    ),
                },
                blocking=True,
            )
        duration = call.data.get("duration")
        if duration is not None:
            await asyncio.sleep(duration)
        elif renderer and content_id:
            await _wait_media_done(hass, renderer)
        else:
            await asyncio.sleep(5)

        # Restore each zone exactly as it was (source, volume, mute — or off).
        for zone in zones:
            power, prev_source, prev_level, muted = snapshot[zone]
            if not power:
                await controller.async_send(CMD_POWER, zone, POWER_OFF)
                continue
            if prev_source is not None:
                await controller.async_send(
                    CMD_SOURCE, zone, prev_source | SOURCE_FLAG_TURN_ON
                )
            if prev_level is not None:
                await controller.async_send(
                    CMD_VOLUME, zone, level_to_volume(prev_level)
                )
            if muted:
                await controller.async_send(CMD_MUTE, zone, MUTE_ON)
        for zone in zones:
            await controller.async_request_zone_state(zone)


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
    hass.services.async_register(
        DOMAIN,
        SERVICE_PLAY_NOTIFICATION,
        partial(_async_play_notification, hass),
        schema=_PLAY_NOTIFICATION_SCHEMA,
    )
