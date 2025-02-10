"""Platform for switch integration."""
import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant import config_entries
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN
from .laifen import Laifen

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the switch platform."""
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LaifenPowerSwitch(data.coordinator, data.device)])

class LaifenPowerSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of the Laifen power switch."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device: Laifen):
        """Initialize the switch."""
        super().__init__(coordinator)
        self.device = device
        self._attr_is_on = False  # Initial state

        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.device.ble_device.address)},
            "name": "Laifen Toothbrush",
            "manufacturer": "Laifen",
            "model": "Laifen BLE",
            "sw_version": "1.0.0",
        }
        self._attr_unique_id = f"{self.device.ble_device.address}_power"

    @property
    def is_on(self) -> bool:
        """Return true if the toothbrush is on."""
        return self.device.result.get("status") == "Running"

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the toothbrush on."""
        success = await self.device.turn_on()
        if success:
            self._attr_is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the toothbrush off."""
        success = await self.device.turn_off()
        if success:
            self._attr_is_on = False
            self.async_write_ha_state()
