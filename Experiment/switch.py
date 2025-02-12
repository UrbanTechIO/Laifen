"""Platform for switch integration."""
from __future__ import annotations
import logging
import asyncio
from homeassistant import config_entries
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.helpers.restore_state import RestoreEntity

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
    switches = []
    for address, laifen_data in data.items():
        coordinator = laifen_data.coordinator
        device = laifen_data.device
        switches.append(
            LaifenPowerSwitch(coordinator, device)
        )
    async_add_entities(switches)

class LaifenPowerSwitch(CoordinatorEntity, SwitchEntity, RestoreEntity):
    """Representation of the Laifen power switch."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device: Laifen):
        """Initialize the switch."""
        super().__init__(coordinator)
        self.device = device
        self._attr_is_on = False  # Initial state

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.device.ble_device.address)},
            manufacturer="Laifen",
            name="Laifen Toothbrush",
            model="Laifen BLE",
            sw_version="1.0.0",
        )
        self._attr_unique_id = f"{self.device.ble_device.address}_power"
    async def async_added_to_hass(self):
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        _LOGGER.warning("Setting up listener for coordinator updates")
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
        asyncio.create_task(self.device.check_connection())  # Start the connection check task

        # Restore state
        if (old_state := await self.async_get_last_state()) is not None:
            self._attr_is_on = old_state.state == "on"

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
