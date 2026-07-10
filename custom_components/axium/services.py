"""Services for managing Axium alarms from the frontend (or automations).

The alarm cards call these to add, edit, remove and enable/disable alarms
without opening the options dialog. Alarms live in the config entry's options;
these services upsert/remove entries and let the update listener decide whether
a reload is needed (field-only edits refresh in place).
"""

from __future__ import annotations

import asyncio
from functools import partial
import logging
import mimetypes
from urllib.parse import urlencode

import voluptuous as vol

from homeassistant.components import media_source
from homeassistant.components.media_player import async_process_play_media_url
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from . import dlna
from .const import (
    CMD_MUTE,
    CMD_POWER,
    CMD_SOURCE,
    CMD_VOLUME,
    CONF_ALARMS,
    DOMAIN,
    MUTE_ON,
    POWER_OFF,
    SOURCE_FLAG_TURN_ON,
    SOURCE_MEDIA_PLAYER_BYTE,
    UNIT_KEY,
    ZONE_KEY,
)
from .helpers import amp_zone_positions, get_alarms, get_presets, get_zones
from .protocol import level_to_volume

_LOGGER = logging.getLogger(__name__)

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
        vol.Optional("duration"): vol.All(vol.Coerce(int), vol.Range(0, 1440)),
        vol.Optional("media"): cv.string,
        vol.Optional("media_type"): cv.string,
        vol.Optional("media_title"): cv.string,
        vol.Optional("media_player"): cv.string,
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
        "duration": 0,
        "media": "",
        "media_type": "",
        "media_title": "",
        "media_player": "",
    }
    for key in (
        "time", "days", "zones", "source", "volume", "enabled", "duration",
        "media", "media_type", "media_title", "media_player",
    ):
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
        vol.Optional("message"): cv.string,
        vol.Optional("tts_engine"): cv.string,
        vol.Optional("language"): cv.string,
        vol.Optional("duration"): vol.All(vol.Coerce(float), vol.Range(0, 300)),
    }
)


def _tts_content_id(hass: HomeAssistant, call: ServiceCall) -> str | None:
    """Turn a spoken ``message`` into a TTS media-source id, or None if unused.

    The engine defaults to the first available ``tts.*`` entity (overridable via
    ``tts_engine``); an optional ``language`` overrides the engine's default so
    e.g. Google Translate can speak Dutch. The result flows through the same
    ``_resolve_media`` + push path as any other sound.
    """
    message = call.data.get("message")
    if not message:
        return None
    engine = call.data.get("tts_engine") or next(
        iter(hass.states.async_entity_ids("tts")), None
    )
    if not engine:
        raise ServiceValidationError(
            "play_notification: 'message' needs a text-to-speech engine, but "
            "none is set up. Install a TTS integration or pass 'tts_engine'."
        )
    params = {"message": message}
    if call.data.get("language"):
        params["language"] = call.data["language"]
    return f"media-source://tts/{engine}?{urlencode(params)}"

# One notification at a time per hub, so overlapping calls can't corrupt the
# save/restore (module-level: hass is a singleton; the locks survive reloads).
_NOTIFY_LOCKS: dict[str, asyncio.Lock] = {}


# A renderer is "done" only after this many consecutive non-playing samples, so
# a momentary buffering/paused blip between tracks doesn't end the wait early.
_DONE_STATES = frozenset({"idle", "off", "standby", "unavailable", "unknown"})


def _renderer_url_for_zone(controller, entry: ConfigEntry, zone: int) -> str | None:
    """Return a zone's amp DLNA AVTransport control URL, or None if unknown.

    Each amp serves one MediaRenderer per physical channel at
    ``http://<amp-ip>/upnp/av_transport_ctrl<index>`` (index = channel - 1). The
    channel comes from the per-amp zone position; the amp IP is the connected
    host for the primary, else the expansion unit's discovered IP (0xB9).
    """
    zones = get_zones(entry)
    position = amp_zone_positions(zones).get(zone)
    if position is None:
        return None
    unit_id = next((z.get(UNIT_KEY) for z in zones if z[ZONE_KEY] == zone), None)
    if unit_id is None or unit_id == controller.primary_unit_id:
        ip = controller.host
    else:
        unit = controller.unit(unit_id)
        ip = unit.ip if unit else None
    if not ip:
        return None
    return f"http://{ip}/upnp/av_transport_ctrl{position - 1}"


async def _resolve_media(
    hass: HomeAssistant, content_id: str, content_type: str | None
) -> tuple[str, str]:
    """Resolve a media_content_id to an absolute (amp-reachable) URL and mime.

    Media-source ids are resolved and the result signed via
    ``async_process_play_media_url`` so the amp can fetch it from HA without a
    session. The mime is taken from the content type if it looks like one, else
    guessed from the URL (the DIDL needs a real mime for picky renderers).
    """
    if media_source.is_media_source_id(content_id):
        item = await media_source.async_resolve_media(hass, content_id, None)
        content_id = item.url
        if not content_type:
            content_type = item.mime_type
    url = async_process_play_media_url(hass, content_id)
    mime = content_type if content_type and "/" in content_type else None
    if not mime:
        mime = mimetypes.guess_type(url.split("?", 1)[0])[0] or "audio/mpeg"
    return url, mime


async def _wait_dlna_done(
    hass: HomeAssistant,
    urls: list[str],
    start_timeout: float = 15.0,
    max_wait: float = 300.0,
) -> None:
    """Wait for pushed renderers to start, then for all of them to finish.

    Polls each renderer's AVTransport state directly (no HA entity exists for
    them). Mirrors ``_wait_media_done``: a grace period to start, then end only
    after a run of samples where none is still active.
    """
    waited = 0.0
    while waited < start_timeout:
        states = [await dlna.async_transport_state(hass, u) for u in urls]
        if any(s in dlna.ACTIVE_STATES for s in states):
            break
        await asyncio.sleep(0.4)
        waited += 0.4
    else:
        return  # never started

    idle_streak = 0
    waited = 0.0
    while waited < max_wait:
        states = [await dlna.async_transport_state(hass, u) for u in urls]
        if any(s in dlna.ACTIVE_STATES for s in states):
            idle_streak = 0
        else:
            idle_streak += 1
            if idle_streak >= 2:
                return
        await asyncio.sleep(0.5)
        waited += 0.5


async def _wait_media_done(
    hass: HomeAssistant,
    renderer: str,
    start_timeout: float = 15.0,
    max_wait: float = 300.0,
) -> None:
    """Wait for a renderer to start, then finish, playing.

    Gives generous time to *start* (network renderers can buffer for several
    seconds) and only treats it as finished after a run of confirmed non-playing
    samples, so a brief buffering/paused blip mid-playback isn't mistaken for the
    end (which would restore the zones and cut the sound off).
    """
    waited = 0.0
    while waited < start_timeout:
        state = hass.states.get(renderer)
        if state and state.state == "playing":
            break
        await asyncio.sleep(0.3)
        waited += 0.3
    else:
        return  # never started — nothing to wait for

    idle_streak = 0
    waited = 0.0
    while waited < max_wait:
        state = hass.states.get(renderer)
        if state is None or state.state in _DONE_STATES:
            idle_streak += 1
            if idle_streak >= 3:
                return
        else:
            idle_streak = 0  # playing / paused / buffering — still going
        await asyncio.sleep(0.4)
        waited += 0.4


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

    # A spoken 'message' becomes a TTS media-source id (validated up front so a
    # missing engine fails before any zone is overridden); an explicit
    # media_content_id is used only when no message is given.
    renderer = call.data.get("media_player")
    content_id = _tts_content_id(hass, call) or call.data.get("media_content_id")

    lock = _NOTIFY_LOCKS.setdefault(entry.entry_id, asyncio.Lock())
    async with lock:
        # Snapshot inside the lock so a queued call captures the restored state.
        snapshot = {}
        for zone in zones:
            state = controller.zone_state(zone)
            snapshot[zone] = (state.power, state.source, state.volume, state.muted)

        pushed_urls: list[str] = []
        try:
            # Override: power on, unmute, select the source, set the volume.
            for zone in zones:
                await controller.async_activate_zone(
                    zone, source, level, unmute=True
                )
            for zone in zones:
                await controller.async_request_zone_state(zone)

            played_via_ha = False
            if content_id and renderer and hass.states.get(renderer) is not None:
                # Optional override: route through a given HA renderer / MA player.
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
                played_via_ha = True
            elif content_id:
                # Default: push the sound straight to each zone's amp renderer —
                # works for every zone with no DLNA discovery needed.
                media_url, mime = await _resolve_media(
                    hass, content_id, call.data.get("media_content_type")
                )
                for zone in zones:
                    url = _renderer_url_for_zone(controller, entry, zone)
                    if url is None:
                        _LOGGER.warning(
                            "axium.play_notification: renderer URL unknown for "
                            "zone %s (amp IP not discovered) — no audio there",
                            zone,
                        )
                        continue
                    try:
                        await dlna.async_push(hass, url, media_url, mime=mime)
                        pushed_urls.append(url)
                    except Exception as err:  # noqa: BLE001 - keep the rest going
                        _LOGGER.warning(
                            "axium.play_notification: push to zone %s (%s) "
                            "failed: %s",
                            zone,
                            url,
                            err,
                        )
            else:
                _LOGGER.warning(
                    "axium.play_notification: no media_content_id — overriding "
                    "the zones without audio, then restoring"
                )

            duration = call.data.get("duration")
            if duration is not None:
                await asyncio.sleep(duration)
            elif played_via_ha:
                await _wait_media_done(hass, renderer)
            elif pushed_urls:
                await _wait_dlna_done(hass, pushed_urls)
            else:
                await asyncio.sleep(5)
        finally:
            # Silence any renderers we pushed to before switching sources back.
            for url in pushed_urls:
                await dlna.async_stop(hass, url)
            # Always restore each zone, even if playback errored, so a bad or
            # missing renderer can never leave a zone stuck on the notification.
            for zone in zones:
                power, prev_source, prev_level, muted = snapshot[zone]
                # Only an explicitly-off zone (power is False) is powered back
                # off; an unknown power (None) is left on rather than silenced.
                was_off = power is False
                # Restore source/volume/mute for every zone — including an
                # originally-off one — so its cached state is correct at the next
                # power-on. The turn-on bit is applied only when it should stay on.
                if prev_source is not None:
                    byte = (
                        prev_source if was_off
                        else prev_source | SOURCE_FLAG_TURN_ON
                    )
                    await controller.async_send(CMD_SOURCE, zone, byte)
                if prev_level is not None:
                    await controller.async_send(
                        CMD_VOLUME, zone, level_to_volume(prev_level)
                    )
                if muted:
                    await controller.async_send(CMD_MUTE, zone, MUTE_ON)
                if was_off:
                    await controller.async_send(CMD_POWER, zone, POWER_OFF)
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
