"""Select platform for Axium preset (scene) recall."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .controller import AxiumController
from .helpers import primary_amp_identifier

STANDARD = "Standard"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the preset selector on the amplifier device."""
    controller: AxiumController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AxiumPresetSelect(controller, entry)])


class AxiumPresetSelect(SelectEntity):
    """Recall an amplifier preset/scene."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Preset"
    _attr_icon = "mdi:playlist-star"

    def __init__(self, controller: AxiumController, entry: ConfigEntry) -> None:
        """Initialise the preset select."""
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_preset"
        # Preset/scene recall is amp hardware — it lives on the primary amp device.
        self._attr_device_info = DeviceInfo(
            identifiers={primary_amp_identifier(entry.entry_id)}
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to diagnostic updates."""
        self.async_on_remove(
            self._controller.register_diagnostic_listener(self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        """Write state on change."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return whether the amplifier connection is up."""
        return self._controller.available

    @property
    def options(self) -> list[str]:
        """Return the available presets (Standard plus any named presets)."""
        names = self._controller.preset_names
        return [STANDARD] + [names[index] for index in sorted(names)]

    @property
    def current_option(self) -> str | None:
        """Return the active preset."""
        index = self._controller.preset_current
        if index == 0:
            return STANDARD
        return self._controller.preset_names.get(index)

    async def async_select_option(self, option: str) -> None:
        """Recall the selected preset."""
        if option == STANDARD:
            await self._controller.async_select_preset(0)
            return
        for index, name in self._controller.preset_names.items():
            if name == option:
                await self._controller.async_select_preset(index)
                return
