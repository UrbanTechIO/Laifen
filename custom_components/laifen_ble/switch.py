"""Platform for switch integration."""
import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant import config_entries
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN, UPDATE_SECONDS
from .laifen import Laifen
from .models import LaifenData

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: config_entries.ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the switch platform for multiple Laifen devices."""
    if DOMAIN not in hass.data:
        _LOGGER.warning("Laifen domain not initialized, delaying setup...")
        await asyncio.sleep(3)

    devices = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).values()
    if not devices:
        _LOGGER.error(f"No Laifen devices registered under entry {entry.entry_id}. Aborting sensor setup.")
        return  

    _LOGGER.warning("Setting up Laifen switch entities")
    entities = []

    for device_entry in devices:
        if isinstance(device_entry, LaifenData):  # ✅ Ensure correct object type
            device = device_entry.device  # ✅ Extract the actual Laifen instance
        else:
            _LOGGER.warning(f"Unexpected object type in hass.data: {device_entry}. Skipping...")
            continue  # ✅ Prevents invalid objects from breaking setup

        coordinator = device.coordinator  # ✅ Ensure coordinator is correctly assigned
        entities.append(LaifenPowerSwitch(coordinator, device, unique_id=device.ble_device.address))  # ✅ Correct reference


    async_add_entities(entities)  # ✅ Register entities properly


class LaifenPowerSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of the Laifen power switch."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device: Laifen, unique_id: str):
        """Initialize the switch."""
        super().__init__(coordinator)
        self.device = device
        self._attr_is_on = False  

        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.device.ble_device.address)},
            "name": f"Laifen Toothbrush ({self.device.ble_device.address})",
            "manufacturer": "Laifen",
            "model": "Laifen BLE",
            "sw_version": "1.0.0",
        }

        self._attr_unique_id = unique_id

    @property
    def is_on(self) -> bool:
        """Return true if the toothbrush is on."""
        if not self.device.result:
            _LOGGER.warning(f"Device {self.device.ble_device.address} returned no data.")
            return False  
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

    async def async_added_to_hass(self):
        """Restore power state when HA restarts."""
        await super().async_added_to_hass()
        if (last_state := self.hass.states.get(self.entity_id)) is not None:  # ✅ Correct method
            _LOGGER.warning(f"Restoring switch state for {self.entity_id}: {last_state.state}")
            self._attr_is_on = last_state.state == "on"
        else:
            _LOGGER.warning(f"No previous state found for {self.entity_id}. Defaulting to off.")

