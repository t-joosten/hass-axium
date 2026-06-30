"""Config flow for the Axium amplifier integration."""

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

from .const import CONF_ZONES, DEFAULT_NAME, DEFAULT_PORT, DOMAIN

_CONNECT_TIMEOUT = 10.0


def _parse_zones(raw: str | list[int]) -> list[int]:
    """Parse a comma-separated zone string into a sorted unique int list."""
    if isinstance(raw, list):
        values = raw
    else:
        values = [part.strip() for part in str(raw).split(",") if part.strip()]
    zones: set[int] = set()
    for value in values:
        zone = int(value)
        if not 0 <= zone <= 95:
            raise ValueError(f"zone {zone} out of range 0..95")
        zones.add(zone)
    if not zones:
        raise ValueError("no zones specified")
    return sorted(zones)


async def _async_test_connection(host: str, port: int) -> None:
    """Open and close a TCP connection to validate host/port."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=_CONNECT_TIMEOUT
    )
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
            try:
                zones = _parse_zones(user_input[CONF_ZONES])
            except ValueError:
                errors[CONF_ZONES] = "invalid_zones"
            else:
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()
                try:
                    await _async_test_connection(host, port)
                except (OSError, asyncio.TimeoutError):
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(
                        title=user_input[CONF_NAME],
                        data={
                            CONF_HOST: host,
                            CONF_PORT: port,
                            CONF_NAME: user_input[CONF_NAME],
                            CONF_ZONES: zones,
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
                vol.Required(
                    CONF_ZONES, default=suggested.get(CONF_ZONES, "1")
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
    """Handle updating the configured zones."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Store the config entry."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the zone options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                zones = _parse_zones(user_input[CONF_ZONES])
            except ValueError:
                errors[CONF_ZONES] = "invalid_zones"
            else:
                return self.async_create_entry(data={CONF_ZONES: zones})

        current = self._config_entry.options.get(
            CONF_ZONES, self._config_entry.data.get(CONF_ZONES, [1])
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ZONES, default=",".join(str(z) for z in current)
                ): cv.string,
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
