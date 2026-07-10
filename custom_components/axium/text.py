"""Text platform for Axium source names (inline, editable on the device)."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, ID_KEY, NAME_KEY, SOURCE_BYTE_TO_NAME
from .controller import AxiumController
from .helpers import get_sources, primary_amp_identifier


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one editable name field per source, on the amplifier device."""
    controller: AxiumController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AxiumSourceName(controller, entry, item[ID_KEY], item[NAME_KEY])
        for item in get_sources(entry)
    )


class AxiumSourceName(TextEntity):
    """The friendly name of one amplifier source, editable inline."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.TEXT
    # Axium firmware historically limited zone/source names to ~15 characters
    # (15 UTF-8 bytes); firmware 3.0.0 on AX-Mini4/AX-1250 extended this. 15 is
    # the safe cross-model default. See Axium's Software/Firmware Notes.
    _attr_native_max = 15
    _attr_icon = "mdi:import"

    def __init__(
        self,
        controller: AxiumController,
        entry: ConfigEntry,
        source_id: int,
        seed_name: str,
    ) -> None:
        """Initialise the source-name text entity."""
        self._controller = controller
        self._source_id = source_id
        self._seed_name = seed_name
        # Label the field by the physical input (e.g. "Source 1", "AirPlay").
        self._attr_name = SOURCE_BYTE_TO_NAME.get(source_id, f"Source {source_id}")
        self._attr_unique_id = f"{entry.entry_id}_source_{source_id}_name"
        # Source names are amp hardware — they live on the primary amp device.
        self._attr_device_info = DeviceInfo(
            identifiers={primary_amp_identifier(entry.entry_id)}
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to amp-wide updates (source names arrive as diagnostics)."""
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
    def native_value(self) -> str:
        """Return the current source name."""
        return self._controller.source_name(self._source_id) or self._seed_name

    async def async_set_value(self, value: str) -> None:
        """Rename the source on the amplifier."""
        await self._controller.async_set_source_name(self._source_id, value)
