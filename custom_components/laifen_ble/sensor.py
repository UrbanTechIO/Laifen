from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, SENSOR_TYPES
from .models import LaifenData, DEVICE_REGISTRY, DEVICE_SIGNAL

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=1)

class LaifenSensor(CoordinatorEntity, RestoreEntity, SensorEntity):
    def __init__(self, device, coordinator, description: SensorEntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self.device = device
        self._attr_unique_id = f"{device.address}_{description.key}"
        self._attr_name = f"{device.name} Toothbrush {description.name}"
        self._last_valid_value = None
        self._timer_task = None
        self._timer_state = 0
        self._attr_should_poll = False
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.device.address)},
            manufacturer="Laifen",
            name="Laifen Toothbrush",
            model="Laifen BLE",
            sw_version="1.0.0",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

        # Start periodic updates for timer logic
        from datetime import timedelta
        self.async_on_remove(
            async_track_time_interval(self.hass, self.async_update, timedelta(seconds=1))
        )

        # Restore state if device result is missing (e.g. HA just restarted)
        if not self.device.result:
            last_state = await self.async_get_last_state()
            if last_state and last_state.state and last_state.state != "unknown":
                try:
                    self._last_valid_value = float(last_state.state)
                    # _LOGGER.debug(f"Restored state for {self.entity_id}: {self._last_valid_value}")
                except ValueError:
                    _LOGGER.warning(f"Could not restore state for {self.entity_id}: {last_state.state}")

    async def _run_timer(self):
        """Increment the timer every second."""
        # _LOGGER.debug(f"Started _run_timer for {self.entity_id}")

        try:
            while True:
                self._timer_state += 1
                self.async_write_ha_state()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def _hold_timer(self):
        """Hold timer for 60 seconds, then reset if status stays Idle."""
        # _LOGGER.debug(f"Started _hold_timer for {self.entity_id}")

        try:
            for _ in range(60):
                await asyncio.sleep(1)
                if self.device.result and self.device.result.get("status") == "Running":
                    return  # Resume running
            self._timer_state = 0
            self.async_write_ha_state()
        except asyncio.CancelledError:
            pass

    async def async_update(self, *args):
        """Update the sensor state from the coordinator."""
        await self.coordinator.async_request_refresh()

        if self.device.result is not None:
            status = self.device.result.get("status")

            if self.entity_description.key == "timer":
                # _LOGGER.debug(f"Updating sensor: {self.entity_description.key}, device status: {status}")
                if status == "Running":
                    # _LOGGER.debug(f"Timer detected RUNNING status for {self.entity_id}")
                    if self._timer_task is None:
                        # _LOGGER.debug(f"Starting Timer for {self.entity_id}")
                        self._timer_task = asyncio.create_task(self._run_timer())
                elif status == "Idle":
                    # _LOGGER.debug(f"Timer detected IDLE status for {self.entity_id}")
                    if self._timer_task is not None:
                        # _LOGGER.debug(f"Stopping Timer for {self.entity_id}, holding value")
                        self._timer_task.cancel()
                        self._timer_task = None
                        asyncio.create_task(self._hold_timer())

    @property
    def native_value(self) -> str | int | float | None:
        key = self.entity_description.key

        # Timer is synthetic and not from coordinator/device result
        if key == "timer":
            return self._timer_state

        # Prefer coordinator data if available
        if self.coordinator.data:
            value = self.coordinator.data.get(key)
            if value is not None:
                self._last_valid_value = value
            return self._last_valid_value or 0

        # Fallback to last known value
        return self._last_valid_value or 0




async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    device_ids = entry.data.get("devices", [])
    entities = []

    for address in device_ids:
        # Try global registry first
        data = DEVICE_REGISTRY.get(entry.entry_id, {}).get(address)

        # Fallback to hass.data if dispatcher not yet triggered
        if not data:
            data = hass.data[DOMAIN][entry.entry_id].get(address)

        if isinstance(data, LaifenData):
            for description in SENSOR_TYPES:
                entities.append(
                    LaifenSensor(data.device, data.coordinator, description)
                )

    if entities:
        async_add_entities(entities)
    else:
        _LOGGER.warning("No valid Laifen sensor entities to add.")
