"""Restart an amp's Music Assistant stream that silently died.

Music Assistant streams to each amp's DLNA renderer. When that link breaks (a
network blip, an HA/MA restart, a lost UPnP subscription) MA can keep
reporting its amp player as ``playing`` for hours while the amp has nothing —
the rooms are on the Media Player source but silent, and nothing in HA looks
wrong. Seen on hardware 2026-10-04/05.

Every check, for each amp that has a room listening to its stream and whose MA
player claims to be playing, ask the amp itself (UPnP ``GetTransportInfo`` on
all its renderers — they alias one stream, and which index MA uses varies). If
none is playing for ``DEAD_CHECKS`` checks in a row, restart the stream the way
that works: ``media_stop``, wait until MA really stopped (a ``media_play`` sent
too early is dropped), then ``media_play``. Rate-limited per player, and quiet
while/just after a notification (which legitimately takes the renderer over).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from . import dlna
from .const import DATA_NOTIFYING, MEDIA_SOURCE_BYTES, ZONE_KEY
from .controller import AxiumController
from .helpers import get_zones
from .services import _amp_ma_player_for_zone, _renderer_url_for_zone

_LOGGER = logging.getLogger(__name__)

CHECK_INTERVAL = timedelta(seconds=30)
DEAD_CHECKS = 2  # consecutive "amp has nothing" checks before acting
COOLDOWN = 300.0  # seconds between recoveries of the same player
QUIET_AFTER_NOTIFY = 60.0  # seconds to stay out of the way after a notification
RENDERERS_PER_AMP = 8
_STOP_WAIT = 15.0  # max seconds to wait for MA to leave "playing" after a stop


def amp_renderer_urls(zone_url: str) -> list[str]:
    """All AVTransport control URLs of the amp that ``zone_url`` belongs to."""
    base = zone_url.rsplit("av_transport_ctrl", 1)[0] + "av_transport_ctrl"
    return [f"{base}{i}" for i in range(RENDERERS_PER_AMP)]


def stream_is_dead(states: list[str | None]) -> bool:
    """True when the amp answered and none of its renderers is playing.

    All-unreachable (every state None) is NOT "dead": we can't tell, and a
    restart wouldn't help an amp we can't reach.
    """
    if all(state is None for state in states):
        return False
    return not any(state in dlna.ACTIVE_STATES for state in states)


class StreamWatchdog:
    """Per-entry watchdog; call ``async_check`` periodically."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, controller: AxiumController
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._controller = controller
        self._dead: dict[str, int] = {}  # player -> consecutive dead checks
        self._last_fix: dict[str, float] = {}  # player -> monotonic time
        self._last_notify = float("-inf")
        self._running = False

    def _listening(self) -> dict[str, str]:
        """MA player -> one renderer URL, for amps with a room on their stream."""
        out: dict[str, str] = {}
        for item in get_zones(self._entry):
            zone = item[ZONE_KEY]
            state = self._controller.zone_state(zone)
            if not (state.power and state.source in MEDIA_SOURCE_BYTES):
                continue
            player = _amp_ma_player_for_zone(self._hass, self._entry, zone)
            if not player or player in out:
                continue
            url = _renderer_url_for_zone(self._controller, self._entry, zone)
            if url:
                out[player] = url
        return out

    async def async_check(self, _now: datetime | None = None) -> None:
        """One watchdog pass (never raises)."""
        if self._running or not self._controller.available:
            return
        if self._hass.data.get(DATA_NOTIFYING, {}).get(self._entry.entry_id):
            self._last_notify = time.monotonic()
            self._dead.clear()
            return
        if time.monotonic() - self._last_notify < QUIET_AFTER_NOTIFY:
            return
        self._running = True
        try:
            await self._check()
        except Exception:  # noqa: BLE001 - a watchdog must never break the entry
            _LOGGER.exception("Axium stream watchdog check failed")
        finally:
            self._running = False

    async def _check(self) -> None:
        listening = self._listening()
        for player in list(self._dead):
            if player not in listening:
                self._dead.pop(player)
        for player, url in listening.items():
            st = self._hass.states.get(player)
            if st is None or st.state != "playing":
                self._dead.pop(player, None)
                continue
            states = await asyncio.gather(
                *(dlna.async_transport_state(self._hass, u) for u in amp_renderer_urls(url))
            )
            if not stream_is_dead(list(states)):
                self._dead.pop(player, None)
                continue
            self._dead[player] = self._dead.get(player, 0) + 1
            if self._dead[player] < DEAD_CHECKS:
                continue
            now = time.monotonic()
            if now - self._last_fix.get(player, float("-inf")) < COOLDOWN:
                continue
            self._dead.pop(player, None)
            self._last_fix[player] = now
            self._hass.async_create_task(self._recover(player))

    async def _recover(self, player: str) -> None:
        """Stop, wait until MA really stopped, then play — restarts the stream."""
        _LOGGER.warning(
            "Axium: %s reports playing but the amp receives no stream — restarting it",
            player,
        )
        try:
            await self._hass.services.async_call(
                "media_player", "media_stop", {"entity_id": player}, blocking=True
            )
            waited = 0.0
            while waited < _STOP_WAIT:
                st = self._hass.states.get(player)
                if st is None or st.state != "playing":
                    break
                await asyncio.sleep(0.5)
                waited += 0.5
            await asyncio.sleep(1)
            await self._hass.services.async_call(
                "media_player", "media_play", {"entity_id": player}, blocking=True
            )
        except Exception as err:  # noqa: BLE001 - e.g. Spotify Connect can't resume
            _LOGGER.warning("Axium: restarting the stream on %s failed: %s", player, err)


def async_setup_stream_watchdog(
    hass: HomeAssistant, entry: ConfigEntry, controller: AxiumController
) -> Callable[[], None]:
    """Start the watchdog; returns the unsubscribe callback."""
    watchdog = StreamWatchdog(hass, entry, controller)
    return async_track_time_interval(hass, watchdog.async_check, CHECK_INTERVAL)
