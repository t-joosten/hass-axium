"""Persistent TCP connection and state cache for an Axium amplifier.

A single :class:`AxiumController` owns the socket to the amplifier. It keeps the
connection open, sends commands, and parses the notifications the amplifier
emits whenever a zone changes state (the protocol re-uses command bytes as
notifications). Entities register a callback per zone and are pushed updates as
they arrive, giving a ``local_push`` integration.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import logging

from . import protocol
from .const import (
    BYTE_TO_SOURCE_NUMBER,
    CMD_MUTE,
    CMD_POWER,
    CMD_SOURCE,
    CMD_VOLUME,
    CMD_ZONE_NAME,
    POWER_OFF_VALUES,
    POWER_ON_VALUES,
    SOURCE_ID_MASK,
)

_LOGGER = logging.getLogger(__name__)

_RECONNECT_DELAY = 5.0
_MAX_RECONNECT_DELAY = 60.0
_CONNECT_TIMEOUT = 10.0


@dataclass
class ZoneState:
    """Cached state for a single amplifier zone."""

    power: bool | None = None
    muted: bool | None = None
    volume: float | None = None
    source: int | None = None  # physical source number (S1..S16)
    name: str | None = None
    available: bool = False


CallbackType = Callable[[], None]


class AxiumController:
    """Manage the connection to an Axium amplifier and dispatch updates."""

    def __init__(self, host: str, port: int) -> None:
        """Initialise the controller for ``host``/``port``."""
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._run_task: asyncio.Task | None = None
        self._closing = False
        self._connected = asyncio.Event()
        self._states: dict[int, ZoneState] = {}
        self._listeners: dict[int, list[CallbackType]] = {}

    @property
    def host(self) -> str:
        """Return the amplifier host."""
        return self._host

    @property
    def available(self) -> bool:
        """Return whether the socket is currently connected."""
        return self._connected.is_set()

    def zone_state(self, zone: int) -> ZoneState:
        """Return (creating if needed) the cached state for ``zone``."""
        return self._states.setdefault(zone, ZoneState())

    def register_listener(self, zone: int, callback: CallbackType) -> Callable[[], None]:
        """Register ``callback`` to be invoked when ``zone`` changes.

        Returns a function that unregisters the callback.
        """
        self._listeners.setdefault(zone, []).append(callback)

        def _remove() -> None:
            listeners = self._listeners.get(zone)
            if listeners and callback in listeners:
                listeners.remove(callback)

        return _remove

    async def async_start(self) -> None:
        """Start the background connection loop and wait for first connect."""
        self._closing = False
        self._run_task = asyncio.ensure_future(self._run())
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=_CONNECT_TIMEOUT)
        except asyncio.TimeoutError as err:
            await self.async_stop()
            raise ConnectionError(
                f"Timed out connecting to Axium amplifier at {self._host}:{self._port}"
            ) from err

    async def async_stop(self) -> None:
        """Close the connection and stop the background loop."""
        self._closing = True
        if self._run_task:
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass
            self._run_task = None
        await self._close_socket()

    async def _run(self) -> None:
        """Connect, read, and reconnect with backoff until stopped."""
        delay = _RECONNECT_DELAY
        while not self._closing:
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port),
                    timeout=_CONNECT_TIMEOUT,
                )
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.debug(
                    "Axium connection to %s:%s failed: %s; retrying in %.0fs",
                    self._host,
                    self._port,
                    err,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, _MAX_RECONNECT_DELAY)
                continue

            _LOGGER.info("Connected to Axium amplifier at %s:%s", self._host, self._port)
            delay = _RECONNECT_DELAY
            self._connected.set()
            self._notify_all()
            try:
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - log and reconnect
                _LOGGER.debug("Axium read loop ended: %s", err)
            finally:
                self._connected.clear()
                self._mark_unavailable()
                await self._close_socket()
                self._notify_all()

            if not self._closing:
                await asyncio.sleep(delay)

    async def _read_loop(self) -> None:
        """Read newline-terminated frames and dispatch them."""
        assert self._reader is not None
        while not self._closing:
            line = await self._reader.readline()
            if not line:
                raise ConnectionError("Connection closed by amplifier")
            frame = protocol.decode(line)
            if frame is not None and len(frame) >= 2:
                self._handle_frame(frame)

    def _handle_frame(self, frame: bytes) -> None:
        """Update the state cache from an incoming command/notification."""
        command = frame[0]
        zone = frame[1]
        data = frame[2:]
        state = self.zone_state(zone)
        changed = False

        if command == CMD_POWER and data:
            value = data[0]
            if value in POWER_ON_VALUES:
                state.power, changed = True, True
            elif value in POWER_OFF_VALUES:
                state.power, changed = False, True
        elif command == CMD_MUTE and data:
            state.muted = data[0] == 0x00
            changed = True
        elif command == CMD_VOLUME and data:
            state.volume = protocol.volume_to_level(data[0])
            changed = True
        elif command == CMD_SOURCE and data:
            source_byte = data[0] & SOURCE_ID_MASK
            state.source = BYTE_TO_SOURCE_NUMBER.get(source_byte)
            if data[0] & 0x80:
                state.power = True
            changed = True
        elif command == CMD_ZONE_NAME and data:
            state.name = data.decode("utf-8", errors="replace").rstrip("\x00") or None
            changed = True

        if changed:
            state.available = True
            self._notify(zone)

    def _notify(self, zone: int) -> None:
        """Invoke listeners registered for ``zone``."""
        for callback in list(self._listeners.get(zone, [])):
            callback()

    def _notify_all(self) -> None:
        """Invoke every registered listener (e.g. on (dis)connect)."""
        for zone in list(self._listeners):
            self._notify(zone)

    def _mark_unavailable(self) -> None:
        """Flag all known zones as unavailable."""
        for state in self._states.values():
            state.available = False

    async def _close_socket(self) -> None:
        """Close the writer if open."""
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
            self._reader = None

    async def async_send(self, command: int, zone: int, *data: int) -> None:
        """Send a command frame to the amplifier."""
        if self._writer is None or not self._connected.is_set():
            _LOGGER.warning(
                "Cannot send Axium command %02X to zone %s: not connected",
                command,
                zone,
            )
            return
        frame = protocol.encode(command, zone, *data)
        async with self._write_lock:
            try:
                self._writer.write(frame)
                await self._writer.drain()
            except OSError as err:
                _LOGGER.warning("Failed to send Axium command: %s", err)
                return
        _LOGGER.debug(
            "Sent to zone %s: %s", zone, protocol.describe(protocol.decode(frame) or b"")
        )

    async def async_request_zone_name(self, zone: int) -> None:
        """Ask the amplifier for a zone's name (command 0x38)."""
        from .const import CMD_ZONE_NAME_REQUEST

        await self.async_send(CMD_ZONE_NAME_REQUEST, zone)
