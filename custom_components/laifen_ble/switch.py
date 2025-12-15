from __future__ import annotations
import logging
import asyncio

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN
from .models import LaifenData, DEVICE_REGISTRY, DEVICE_SIGNAL

_LOGGER = logging.getLogger(__name__)

class LaifenSwitch(CoordinatorEntity, SwitchEntity):
    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{self.device.address}_power"
        self._attr_should_poll = False

        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.device.address)},
            "name": "Laifen Toothbrush",
            "manufacturer": "Laifen",
            "model": "Laifen BLE",
            "sw_version": "1.0.0",
        }


    async def async_turn_on(self, **kwargs):
        success = await self.device.turn_on()
        if success:
            self._attr_is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        success = await self.device.turn_off()
        if success:
            self._attr_is_on = False
            self.async_write_ha_state()


    @property
    def is_on(self) -> bool:
        if self.device.result:
            return self.device.result.get("status") == "Running"
        return self._attr_is_on

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    device_ids = entry.data.get("devices", [])
    entities = []

    for address in device_ids:
        data = DEVICE_REGISTRY.get(entry.entry_id, {}).get(address)

        if not data:
            data = hass.data[DOMAIN][entry.entry_id].get(address)

        if isinstance(data, LaifenData):
            entities.append(LaifenSwitch(data.device, data.coordinator))

    if entities:
        async_add_entities(entities)
    else:
        _LOGGER.debug("No valid Laifen switch entities to add.")
