"""The Axium amplifier integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.util import slugify

from .const import (
    CMD_POWER,
    CMD_VOLUME,
    CONF_ALARMS,
    CONF_PRESETS,
    CONF_UNITS,
    CONF_ZONES,
    DATA_ALARMS_ENABLED,
    DATA_PREV_OPTIONS,
    DATA_SLEEP_DEADLINES,
    DEFAULT_PORT,
    DOMAIN,
    POWER_OFF,
    SIGNAL_ALARM_UPDATE,
    SOURCE_MEDIA_PLAYER_BYTE,
    UNIT_KEY,
    ZONE_KEY,
)
from .controller import AxiumController, AxiumDeviceInfo, UnitInfo
from .helpers import (
    amp_zone_positions,
    get_alarms,
    get_units,
    get_zones,
    units_config,
    zone_device_model,
    zones_from_units,
)
from .protocol import level_to_volume
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

_ALARM_FADE_SECONDS = 30
_ALARM_FADE_STEPS = 6

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


# Bump the suffix when the entity-id scheme changes; guards the one-time rename.
_ENTITY_ID_MIGRATION = "entity_ids_prefixed_v1"


def _zone_refs_migrated(entry: ConfigEntry, id_map: dict[str, str]) -> dict:
    """Return entry.options with preset/alarm zone entity_ids remapped."""
    options = dict(entry.options)
    for key in (CONF_PRESETS, CONF_ALARMS):
        items = options.get(key)
        if not items:
            continue
        options[key] = [
            {**item, "zones": [id_map.get(z, z) for z in item.get("zones", [])]}
            if item.get("zones")
            else item
            for item in items
        ]
    return options


async def _async_migrate_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """One-time: namespace every Axium entity_id as ``axium_<primary unit id>_…``.

    Runs before the platforms load, so entities come up with the new ids, and
    rewrites the zone entity_ids stored inside presets/alarms so they keep
    pointing at the right zones. Guarded by a flag so it runs once and never
    fights a later manual rename. Skips (retries next load) until the primary
    unit id is known, and never overwrites an id that is already taken.
    """
    if entry.data.get(_ENTITY_ID_MIGRATION):
        return
    primary = next((u for u in get_units(entry) if u.get("primary")), None)
    if primary is None:
        return
    prefix = f"axium_{int(primary[UNIT_KEY]) & 0xFFFF:04x}_"
    registry = er.async_get(hass)
    id_map: dict[str, str] = {}
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        uid = ent.unique_id
        suffix = uid[len(entry.entry_id) :] if uid.startswith(entry.entry_id) else uid
        new_entity_id = f"{ent.domain}.{prefix}{slugify(suffix)}"
        if ent.entity_id == new_entity_id:
            continue
        if registry.async_get(new_entity_id) is not None:
            _LOGGER.warning(
                "Axium: not renaming %s -> %s (target already exists)",
                ent.entity_id,
                new_entity_id,
            )
            continue
        registry.async_update_entity(ent.entity_id, new_entity_id=new_entity_id)
        id_map[ent.entity_id] = new_entity_id
    updates: dict = {"data": {**entry.data, _ENTITY_ID_MIGRATION: True}}
    if id_map:
        new_options = _zone_refs_migrated(entry, id_map)
        if new_options != dict(entry.options):
            updates["options"] = new_options
        _LOGGER.info(
            "Axium: renamed %d entity ids to the '%s' prefix", len(id_map), prefix
        )
    hass.config_entries.async_update_entry(entry, **updates)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Axium from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)

    await _async_register_card(hass)
    await _async_migrate_entity_ids(hass, entry)

    controller = AxiumController(host, port)

    # Register the amplifier as a hub device so each zone/group device nests
    # under it via their `via_device` reference. Model/firmware are filled in
    # automatically once the amplifier identifies itself (command 0x14).
    # Each amplifier in the stack is its own device. The primary/connected amp
    # is the hub device; expansion amps are separate devices nested under it via
    # `via_device`. Zones nest under their owning amp.
    device_registry = dr.async_get(hass)
    hub = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Axium",
        name=entry.title,
        model="Hub",
        configuration_url=f"http://{host}",
    )
    # The hub is a logical container now; the amp's MAC/connections belong on the
    # primary amp device. Clear any left on the hub from before the hub/amp split,
    # so the primary amp can claim the MAC — otherwise `async_update_device` raises
    # a DeviceConnectionCollisionError on every extended-info reply (flapping the
    # connection until the callback guard catches it).
    if hub.connections:
        device_registry.async_update_device(hub.id, new_connections=set())
    # The primary amp is its OWN device ("…_amp_primary"), nested under the hub,
    # just like the expansion amps — so the hub ("Axium Hub") and the primary amp
    # ("Main") can carry independent names. Its identifier has no "_unit_" so the
    # dashboard still treats it as the master stream. Model/firmware/temp land on
    # it (via `_amp_identifier`), leaving the hub a pure logical container.
    primary_amp = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_amp_primary")},
        via_device=(DOMAIN, entry.entry_id),
        manufacturer="Axium",
        name="Axium 1",
        model="Amplifier",
        configuration_url=f"http://{host}",
    )
    # Follow the "Axium 1" default even for an existing device (async_get_or_create
    # doesn't rename an existing one, and the split first shipped it as "Main"); a
    # user's own name_by_user still wins for display.
    if primary_amp.name != "Axium 1" and not primary_amp.name_by_user:
        device_registry.async_update_device(primary_amp.id, name="Axium 1")
    # One-time hub/amp split migration: before the split the hub device carried the
    # AMP's user name (name_by_user, e.g. "Axium 1"). Move it onto the new primary
    # amp device and clear it off the hub, so the hub shows its own name (entry
    # title, e.g. "Axium Hub") and the amp keeps the name you gave it. Guarded by a
    # flag so a later genuine hub rename is never clobbered.
    if not entry.data.get("_hub_amp_split_named"):
        if hub.name_by_user and not primary_amp.name_by_user:
            device_registry.async_update_device(
                primary_amp.id, name_by_user=hub.name_by_user
            )
        if hub.name_by_user:
            device_registry.async_update_device(hub.id, name_by_user=None)
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "_hub_amp_split_named": True}
        )
    # NOTE: we deliberately do NOT force the config-entry title from the hub
    # device's name here. Doing so reverted a user's own entry-title edit back to
    # the device name on every reload ("Axium Hub" → "Axium 1"). The title and the
    # hub device name are now independent HA names — each sticks on its own.
    for unit in get_units(entry):
        if unit.get("primary"):
            continue
        uid = unit[UNIT_KEY]
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{entry.entry_id}_unit_{uid}")},
            via_device=(DOMAIN, entry.entry_id),
            manufacturer="Axium",
            model="Amplifier",
            name=f"{entry.title} amp {uid:#06x}",
        )

    def _amp_identifier(unit_id: int | None) -> tuple[str, str]:
        """Device identifier for the amp hosting a unit. The primary amp is its
        own device ("…_amp_primary"), separate from the logical hub (entry id)."""
        if unit_id is None or unit_id == controller.primary_unit_id:
            return (DOMAIN, f"{entry.entry_id}_amp_primary")
        return (DOMAIN, f"{entry.entry_id}_unit_{unit_id}")

    def _enrich_zone_models(unit_id: int | None, model_code: int | None) -> None:
        """Set a unit's zone devices' model to their physical channel (+ type).

        Runs when device info (with the device-type code) arrives, since that can
        land after the zone devices are created; it's idempotent (only writes on a
        real change), so re-runs on every device-info reply are cheap.
        """
        if model_code is None:
            return
        zones = get_zones(entry)
        positions = amp_zone_positions(zones)
        for item in zones:
            z_unit = item.get(UNIT_KEY)
            # This unit's zones; legacy zones (no unit) belong to the primary.
            if z_unit != unit_id and not (
                z_unit is None and unit_id == controller.primary_unit_id
            ):
                continue
            zone = item[ZONE_KEY]
            dev = device_registry.async_get_device(
                identifiers={(DOMAIN, f"{entry.entry_id}_zone_{zone}")}
            )
            if dev is None:
                continue
            model = zone_device_model(model_code, positions.get(zone, zone))
            if dev.model != model:
                device_registry.async_update_device(dev.id, model=model)

    @callback
    def _update_amp_device(info: AxiumDeviceInfo) -> None:
        """Enrich an amp device with its reported model and firmware."""
        device = device_registry.async_get_device(
            identifiers={_amp_identifier(info.unit_id)}
        )
        if device is None:
            return
        updates: dict[str, str] = {}
        if info.model or info.device_type:
            updates["model"] = info.model or info.device_type  # type: ignore[assignment]
        if info.firmware_major is not None:
            updates["sw_version"] = f"v{info.firmware_major}"
        if updates:
            device_registry.async_update_device(device.id, **updates)
        _enrich_zone_models(info.unit_id, info.model_code)

    controller.set_device_info_callback(_update_amp_device)

    @callback
    def _update_unit_extended(unit: UnitInfo) -> None:
        """Enrich an amp device with its full firmware, model and MAC."""
        device = device_registry.async_get_device(
            identifiers={_amp_identifier(unit.unit_id)}
        )
        if device is None:
            return
        kwargs: dict = {}
        if unit.firmware:
            kwargs["sw_version"] = unit.firmware
        if unit.model:
            kwargs["model"] = unit.model
        if unit.manufacture_date:
            kwargs["hw_version"] = unit.manufacture_date
        if unit.mac:
            kwargs["merge_connections"] = {
                (dr.CONNECTION_NETWORK_MAC, dr.format_mac(unit.mac))
            }
        if kwargs:
            device_registry.async_update_device(device.id, **kwargs)

    controller.set_extended_info_callback(_update_unit_extended)

    @callback
    def _handle_stack(units: list[UnitInfo], primary_unit_id: int | None) -> None:
        """Auto-detect a newly-stacked expansion amp and add its zones/unit."""
        discovered_zones = {z for u in units for z in u.zones}
        cfg_zones = {z[ZONE_KEY] for z in get_zones(entry)}
        cfg_units = {u[UNIT_KEY] for u in get_units(entry)}
        if not cfg_units and primary_unit_id is not None:
            # Legacy single-amp config without unit info: the primary is implicit,
            # so a lone primary doesn't look like a change (no needless reload).
            cfg_units = {primary_unit_id}
        discovered_units = {u.unit_id for u in units}
        # Only react to growth (an amp added); never drop zones on a partial read.
        if discovered_zones <= cfg_zones and discovered_units <= cfg_units:
            return
        if not discovered_zones >= cfg_zones:
            return
        _LOGGER.info(
            "Axium: expansion detected — %d zones across %d amp(s); reloading",
            len(discovered_zones),
            len(units),
        )
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ZONES: zones_from_units(units, get_zones(entry)),
                CONF_UNITS: units_config(units, primary_unit_id),
            },
        )
        hass.async_create_task(hass.config_entries.async_reload(entry.entry_id))

    controller.set_stack_callback(_handle_stack)

    try:
        await controller.async_start()
    except (ConnectionError, OSError) as err:
        raise ConfigEntryNotReady(
            f"Unable to connect to Axium amplifier at {host}:{port}"
        ) from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller
    hass.data.setdefault(DATA_ALARMS_ENABLED, {}).setdefault(entry.entry_id, True)
    hass.data.setdefault(DATA_PREV_OPTIONS, {})[entry.entry_id] = dict(entry.options)

    async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_setup_alarms(hass, entry, controller)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    @callback
    def _handle_device_rename(event: Event) -> None:
        """Mirror an HA device rename (the pencil) to the right place.

        Renaming a zone's device only updates the local registry; the amplifier
        stores its own zone names (used on the front panel and by other
        controllers), so mirror the new name onto the amp. Renaming the hub
        device instead updates the config-entry title, so the integrations page
        reflects the chosen hub name. We never write the registry back here, so
        this cannot loop.
        """
        if event.data.get("action") != "update":
            return
        if "name_by_user" not in (event.data.get("changes") or {}):
            return
        device = device_registry.async_get(event.data["device_id"])
        if device is None:
            return
        new_name = device.name_by_user or device.name
        # Hub device rename -> sync the config-entry title (the hub name). The hub
        # is a logical container now, so no amp network name is pushed from here.
        if (DOMAIN, entry.entry_id) in device.identifiers:
            if new_name and new_name != entry.title:
                hass.config_entries.async_update_entry(entry, title=new_name)
            return
        # Primary amp device rename -> push its own network name to the primary unit.
        if (DOMAIN, f"{entry.entry_id}_amp_primary") in device.identifiers:
            if new_name:
                hass.async_create_task(controller.async_set_amp_name(new_name))
            return
        # Expansion amp device rename -> push its own network name to that unit.
        unit_prefix = f"{entry.entry_id}_unit_"
        for domain_id, identifier in device.identifiers:
            if domain_id == DOMAIN and identifier.startswith(unit_prefix):
                try:
                    uid = int(identifier[len(unit_prefix):])
                except ValueError:
                    return
                if new_name:
                    hass.async_create_task(
                        controller.async_set_amp_name(new_name, uid)
                    )
                return
        prefix = f"{entry.entry_id}_zone_"
        zone: int | None = None
        for domain_id, identifier in device.identifiers:
            if domain_id == DOMAIN and identifier.startswith(prefix):
                try:
                    zone = int(identifier[len(prefix):])
                except ValueError:
                    zone = None
                break
        if zone is None:
            return
        name = device.name_by_user or device.name
        if name:
            hass.async_create_task(controller.async_set_zone_name(zone, name))

    entry.async_on_unload(
        hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, _handle_device_rename)
    )

    async def _poll_zones(_now: datetime | None = None) -> None:
        """Periodically re-read zones so on-amp changes reach HA and the cards."""
        await controller.async_poll_zones()

    entry.async_on_unload(
        async_track_time_interval(hass, _poll_zones, timedelta(seconds=30))
    )
    return True


@callback
def _master_stream_player(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """The Music Assistant player named after the PRIMARY AMP device.

    Wake media plays on the primary amp's Media Player stream. Matched by that
    device's name (the user renames the MA player to e.g. "Axium 1"). Since the
    hub/amp split the primary amp is its own "…_amp_primary" device, not the hub;
    fall back to the hub identifier for a pre-split entry.
    """
    reg = dr.async_get(hass)
    amp = reg.async_get_device(
        identifiers={(DOMAIN, f"{entry.entry_id}_amp_primary")}
    ) or reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    name = amp and (amp.name_by_user or amp.name)
    if not name:
        return None
    want = name.strip().lower()
    registry = er.async_get(hass)
    for ent in registry.entities.values():
        if ent.domain != "media_player" or ent.platform != "music_assistant":
            continue
        state = hass.states.get(ent.entity_id)
        fn = state and (state.attributes.get("friendly_name") or "").strip().lower()
        if fn == want:
            return ent.entity_id
    return None


def _async_setup_alarms(
    hass: HomeAssistant, entry: ConfigEntry, controller: AxiumController
) -> None:
    """Fire configured wake-to-music alarms each minute they are due."""

    async def _fire(alarm: dict) -> None:
        zones = [
            zone
            for eid in alarm["zones"]
            if (zone := controller.zone_for_entity_id(eid)) is not None
        ]
        if not zones:
            return
        media = (alarm.get("media") or "").strip()
        # A Music Assistant wake plays on the internal Media Player source; a
        # classic alarm uses its configured (analog/media) source.
        source = SOURCE_MEDIA_PLAYER_BYTE if media else alarm["source"]
        target = alarm["volume"] / 100
        start = min(target, 0.1)
        # Power on, select the source (turn-on flag, as the media_player does)
        # and start quiet.
        for zone in zones:
            await controller.async_activate_zone(zone, source, start)
        # Wake to a Music Assistant playlist: start it on the master stream player
        # (stack-wide, so the activated zones hear it) before fading the volume up.
        if media:
            player = (alarm.get("media_player") or "").strip() or _master_stream_player(
                hass, entry
            )
            if player and hass.states.get(player) is not None:
                await hass.services.async_call(
                    "media_player",
                    "play_media",
                    {
                        "entity_id": player,
                        "media_content_id": media,
                        "media_content_type": alarm.get("media_type") or "playlist",
                        # Replace whatever's playing so the wake media starts now
                        # (without this, MA may enqueue behind current playback).
                        "enqueue": "replace",
                    },
                    blocking=True,
                )
            else:
                _LOGGER.warning(
                    "Axium alarm '%s': no Music Assistant stream player found for "
                    "the wake media (rename the amp's MA player to the amp name)",
                    alarm.get("name"),
                )
        # Gently fade up to the target volume (wake-to-music).
        for step in range(1, _ALARM_FADE_STEPS + 1):
            level = start + (target - start) * step / _ALARM_FADE_STEPS
            for zone in zones:
                await controller.async_send(
                    CMD_VOLUME, zone, level_to_volume(level)
                )
            await asyncio.sleep(_ALARM_FADE_SECONDS / _ALARM_FADE_STEPS)
        # Optional auto turn-off: power the woken zones back off after `duration`
        # minutes (0 = leave them on). A background task so it can't block others.
        duration = alarm.get("duration") or 0
        if duration > 0:

            async def _auto_off(zone_ids: list[int]) -> None:
                await asyncio.sleep(duration * 60)
                for zone in zone_ids:
                    await controller.async_send(CMD_POWER, zone, POWER_OFF)
                for zone in zone_ids:  # read back so HA reflects the power-off
                    await controller.async_request_zone_state(zone)

            hass.async_create_task(_auto_off(list(zones)))

    @callback
    def _tick(now: datetime) -> None:
        if not hass.data.get(DATA_ALARMS_ENABLED, {}).get(entry.entry_id, True):
            return
        hhmm = now.strftime("%H:%M")
        weekday = now.weekday()  # Monday = 0 .. Sunday = 6
        for alarm in get_alarms(entry):
            if not alarm["enabled"] or alarm["time"] != hhmm:
                continue
            if alarm["days"] and weekday not in alarm["days"]:
                continue
            hass.async_create_task(_fire(alarm))

    entry.async_on_unload(async_track_time_change(hass, _tick, second=0))


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        controller: AxiumController = hass.data[DOMAIN].pop(entry.entry_id)
        await controller.async_stop()
        # Drop this entry's per-entry runtime state so a reload can't surface a
        # stale sleep-timer deadline (the timer tasks are already cancelled).
        # The alarms-enabled flag is intentionally kept so arm/disarm survives.
        hass.data.get(DATA_SLEEP_DEADLINES, {}).pop(entry.entry_id, None)
        hass.data.get(DATA_PREV_OPTIONS, {}).pop(entry.entry_id, None)
    return unload_ok


def _alarm_names(options: dict) -> list[str]:
    """Sorted alarm names from a raw options dict (for change detection)."""
    raw = options.get(CONF_ALARMS, [])
    if not isinstance(raw, list):
        return []
    return sorted(
        str(a.get("name", "")) for a in raw if isinstance(a, dict) and a.get("name")
    )


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload on option changes, except pure alarm field edits (refresh instead).

    Editing an existing alarm's time/days/enabled (e.g. from the card) doesn't
    change the set of entities, so we skip the disruptive full reload and just
    nudge the alarm sensors to re-read.
    """
    prev = hass.data.get(DATA_PREV_OPTIONS, {}).get(entry.entry_id, {})
    cur = dict(entry.options)
    hass.data.setdefault(DATA_PREV_OPTIONS, {})[entry.entry_id] = cur

    same_alarm_set = _alarm_names(prev) == _alarm_names(cur)
    other_prev = {k: v for k, v in prev.items() if k != CONF_ALARMS}
    other_cur = {k: v for k, v in cur.items() if k != CONF_ALARMS}
    if same_alarm_set and other_prev == other_cur:
        async_dispatcher_send(hass, f"{SIGNAL_ALARM_UPDATE}_{entry.entry_id}")
        return
    await hass.config_entries.async_reload(entry.entry_id)
