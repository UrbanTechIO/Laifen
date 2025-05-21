"""Platform for sensor integration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import asyncio

from homeassistant import config_entries
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, UPDATE_SECONDS
from .laifen import Laifen
from .models import LaifenData 


_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=1)

@dataclass
class LaifenSensorEntityDescription(SensorEntityDescription):
    """Provide a description of a Laifen sensor."""
    unique_id: str | None = None

SENSORS = (
    LaifenSensorEntityDescription(
        key="status",
        name="Status",
        unique_id="laifen_status",
        icon="mdi:toothbrush-electric",
    ),
    LaifenSensorEntityDescription(
        key="vibration_strength",
        name="Vibration Strength",
        unique_id="laifen_vibration_strength",
        icon="mdi:zodiac-aquarius",
    ),
    LaifenSensorEntityDescription(
        key="oscillation_range",
        name="Oscillation Range",
        unique_id="laifen_oscillation_range",
        icon="mdi:arrow-oscillating",
    ),
    LaifenSensorEntityDescription(
        key="oscillation_speed",
        name="Oscillation Speed",
        unique_id="laifen_oscillation_speed",
        icon="mdi:speedometer",
    ),
    LaifenSensorEntityDescription(
        key="mode",
        name="Mode",
        unique_id="laifen_mode",
        icon="mdi:dots-horizontal",
    ),
    LaifenSensorEntityDescription(
        key="battery_level",
        name="Battery Level",
        unique_id="laifen_battery_level",
        icon="mdi:battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="%",
    ),
    LaifenSensorEntityDescription(
        key="brushing_time",
        name="Brushing Time",
        unique_id="laifen_brushing_time",
        icon="mdi:timer",
    ),
    LaifenSensorEntityDescription(
        key="timer",
        name="Timer",
        unique_id="laifen_timer",
        icon="mdi:timer",
    ),
)

async def async_setup_entry(hass: HomeAssistant, entry: config_entries.ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the sensor platform for multiple devices."""
    if DOMAIN not in hass.data:
        _LOGGER.warning("Laifen domain not initialized, delaying setup...")
        await asyncio.sleep(3)

    devices = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).values()
    if not devices:
        _LOGGER.error(f"No Laifen devices registered under entry {entry.entry_id}. Aborting sensor setup.")
        return

    _LOGGER.warning("Setting up Laifen sensor entities")
    entities = []

    for device_entry in devices:
        if isinstance(device_entry, LaifenData):  # ✅ Ensure correct object type
            device = device_entry.device  # ✅ Retrieve actual Laifen instance
        else:
            _LOGGER.warning(f"Unexpected object type in hass.data: {device_entry}. Skipping...")
            continue  # ✅ Prevents setup issues from malformed data

        coordinator = device.coordinator  # ✅ Ensure coordinator is correctly assigned

        for description in SENSORS:  # ✅ Loop through sensor descriptions for each device
            entities.append(LaifenSensor(coordinator, device, description, unique_id=f"{device.ble_device.address}_{description.unique_id}"))

    async_add_entities(entities)  # ✅ Register entities properly


class LaifenSensor(CoordinatorEntity, SensorEntity):
    """Implementation of the Laifen sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device, description, unique_id):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self.device = device
        self._last_valid_value = None
        self._timer_state = 0
        self._timer_task = None
        self._update_interval = SCAN_INTERVAL
        self._update_listener = None
        self._attr_unique_id = unique_id

        if hasattr(description, "device_class"):
            self._attr_device_class = description.device_class
        if hasattr(description, "state_class"):
            self._attr_state_class = description.state_class

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.device.ble_device.address)},
            manufacturer="Laifen",
            name="Laifen Toothbrush",
            model="Laifen BLE",
            sw_version="1.0.0",
        )

    @property
    def native_value(self) -> str | None:
        """Return sensor state, ensuring last known values remain when the device disconnects."""
        key = self.entity_description.key

        # ✅ If device is disconnected, hold last valid value instead of allowing "Unavailable"
        if self.device.result is None:
            _LOGGER.warning(f"{self.entity_id} disconnected. Holding last known value: {self._last_valid_value}")
            return self._last_valid_value  # ✅ Forces last value instead of "Unavailable"

        # ✅ Retrieve latest sensor value
        value = self.device.result.get(key)

        # ✅ Explicitly store previous values per sensor type
        if key in ["status", "vibration_strength", "oscillation_range", "oscillation_speed", "mode", "battery_level"]:
            self._last_valid_value = value  # ✅ Keep valid data before disconnect
        elif key == "brushing_time":
            if value is not None:
                value = round(float(value), 1)  # ✅ Keep 1 decimal place
                self._last_valid_value = f"{value} min"
        elif key == "timer":
            value = self._timer_state
            _LOGGER.warning(f"Timer Value is {value}")
            self._last_valid_value = value

        # ✅ Prevent returning `None` if no new data is available
        self._last_valid_value = value if value is not None else self._last_valid_value
        
        _LOGGER.warning(f"{self.entity_id} holding last known value: {self._last_valid_value}")
        return self._last_valid_value  # ✅ Forces retention of valid values


    async def async_added_to_hass(self):
        """Restore last known value after HA restart."""
        await super().async_added_to_hass()
        if (last_state := self.hass.states.get(self.entity_id)) is not None:
            _LOGGER.warning(f"Restoring last known state for {self.entity_id}: {last_state.state}")
            self._last_valid_value = last_state.state  # ✅ Restore previous sensor value
            self.async_write_ha_state()

        
        if not self.device.client.is_connected:
            _LOGGER.warning(f"Delaying state updates for {self.device.ble_device.address} until connection stabilizes.")
            await asyncio.sleep(2)  # ✅ Small delay before coordinator starts listening

        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
        self._update_listener = async_track_time_interval(self.hass, self.async_update, self._update_interval)
        if self.device.has_connected_before:  # ✅ Ensures it's only called after device was online before
            _LOGGER.warning(f"Device {self.device.ble_device.address} has previously been connected—checking status.")
            asyncio.create_task(self.device.check_connection())
        else:
            _LOGGER.warning(f"Skipping connection check for {self.device.ble_device.address}—first-time setup detected.")


    async def async_update(self, *args):
        """Update the sensor state."""
        await self.coordinator.async_request_refresh()
        if self.device.result is not None:
            status = self.device.result.get("status")
            if status == "Running" :
                if self._timer_task is None:
                    _LOGGER.warning(f"Starting Timer")
                    self._timer_task = asyncio.create_task(self._run_timer())
            elif status == "Idle":
                if self._timer_task is not None:
                    _LOGGER.warning(f"Stopping Timer - Holding Value for 60 seconds")
                    self._timer_task.cancel()
                    self._timer_task = None
                    asyncio.create_task(self._hold_timer())

    async def _hold_timer(self):
        """Hold timer value for 60 seconds before resetting to 0."""
        try:
            for _ in range(60):
                await asyncio.sleep(1)
                if self.device.result.get("status") == "Running":
                    _LOGGER.warning("Status changed back to Running - Holding timer value.")
                    return
            _LOGGER.warning("Timer Held for 60 seconds - Resetting to 0.")
            self._timer_state = 0
            self.async_write_ha_state()
        except asyncio.CancelledError:
            pass

    async def _run_timer(self):
        """Increment brushing timer every second while running."""
        try:
            while True:
                if self.device.result.get("status") == "Idle":
                    _LOGGER.warning("Status changed to Idle - Stopping Timer.")
                    return  # ✅ Exit loop when Idle
                
                self._timer_state += 1
                _LOGGER.warning(f"Timer Value Updated: {self._timer_state}")
                self.async_write_ha_state()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            _LOGGER.warning("Timer task was cancelled.")

