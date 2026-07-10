"""Binary sensor platform for Axium clipping/overload alerts."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .controller import AxiumController
from .helpers import primary_amp_identifier


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the clipping alert on the amplifier device."""
    controller: AxiumController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AxiumClipping(controller, entry)])


class AxiumClipping(BinarySensorEntity):
    """Indicates when an analogue input is clipping (overloading)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Clipping"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, controller: AxiumController, entry: ConfigEntry) -> None:
        """Initialise the clipping sensor."""
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_clipping"
        # Clipping is an amp-hardware diagnostic — it lives on the primary amp
        # device, not the logical hub container.
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
    def is_on(self) -> bool:
        """Return whether an input is currently clipping."""
        return self._controller.clipping

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """Expose which source is clipping, if any."""
        source = self._controller.clipping_source
        return {"source": source} if source is not None else {}
