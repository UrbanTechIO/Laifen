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

from .const import DOMAIN
from .laifen import Laifen

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=1)  # Set scan interval to 1 second


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
        key="vibration_strength_mode_1",
        name="Vibration Strength Mode 1",
        unique_id="laifen_vibration_strength_mode_1",
        icon="mdi:zodiac-aquarius",
    ),
    LaifenSensorEntityDescription(
        key="oscillation_range_mode_1",
        name="Oscillation Range Mode 1",
        unique_id="laifen_oscillation_range_mode_1",
        icon="mdi:arrow-oscillating",
    ),
    LaifenSensorEntityDescription(
        key="oscillation_speed_mode_1",
        name="Oscillation Speed Mode 1",
        unique_id="laifen_oscillation_speed_mode_1",
        icon="mdi:speedometer",
    ),
    LaifenSensorEntityDescription(
        key="vibration_strength_mode_2",
        name="Vibration Strength Mode 2",
        unique_id="laifen_vibration_strength_mode_2",
        icon="mdi:zodiac-aquarius",
    ),
    LaifenSensorEntityDescription(
        key="oscillation_range_mode_2",
        name="Oscillation Range Mode 2",
        unique_id="laifen_oscillation_range_mode_2",
        icon="mdi:arrow-oscillating",
    ),
    LaifenSensorEntityDescription(
        key="oscillation_speed_mode_2",
        name="Oscillation Speed Mode 2",
        unique_id="laifen_oscillation_speed_mode_2",
        icon="mdi:speedometer",
    ),
    LaifenSensorEntityDescription(
        key="vibration_strength_mode_3",
        name="Vibration Strength Mode 3",
        unique_id="laifen_vibration_strength_mode_3",
        icon="mdi:zodiac-aquarius",
    ),
    LaifenSensorEntityDescription(
        key="oscillation_range_mode_3",
        name="Oscillation Range Mode 3",
        unique_id="laifen_oscillation_range_mode_3",
        icon="mdi:arrow-oscillating",
    ),
    LaifenSensorEntityDescription(
        key="oscillation_speed_mode_3",
        name="Oscillation Speed Mode 3",
        unique_id="laifen_oscillation_speed_mode_3",
        icon="mdi:speedometer",
    ),
    LaifenSensorEntityDescription(
        key="vibration_strength_mode_4",
        name="Vibration Strength Mode 4",
        unique_id="laifen_vibration_strength_mode_4",
        icon="mdi:zodiac-aquarius",
    ),
    LaifenSensorEntityDescription(
        key="oscillation_range_mode_4",
        name="Oscillation Range Mode 4",
        unique_id="laifen_oscillation_range_mode_4",
        icon="mdi:arrow-oscillating",
    ),
    LaifenSensorEntityDescription(
        key="oscillation_speed_mode_4",
        name="Oscillation Speed Mode 4",
        unique_id="laifen_oscillation_speed_mode_4",
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
    ),
    LaifenSensorEntityDescription(
        key="brushing_timer",
        name="Brushing Timer",
        unique_id="laifen_brushing_timer",
        icon="mdi:timer",
    ),
    LaifenSensorEntityDescription(
        key="timer",
        name="Timer",
        unique_id="laifen_timer",
        icon="mdi:timer",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = LaifenDataUpdateCoordinator(hass, data.device)
    await coordinator.async_config_entry_first_refresh()
    _LOGGER.warning("Adding Laifen sensor entities")
    async_add_entities(
        LaifenSensor(data.coordinator, data.device, description)
        for description in SENSORS
    )


class LaifenSensor(CoordinatorEntity, SensorEntity):
    """Implementation of the Laifen sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device, description):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self.device = device
        self._last_valid_value = None  # Store the last valid value
        self._timer_state = 0  # Initialize the timer state
        self._timer_task = None  # Initialize the timer task
        self._update_interval = SCAN_INTERVAL  # Store the update interval
        self._update_listener = None  # Initialize the update listener

        # Set device class and state class if defined in the description
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

        self._attr_unique_id = (
            f"{self.device.ble_device.address}_{description.unique_id}"
        )

    @property
    def native_value(self) -> str | None:
        """Return sensor state."""
        if self.device.result is None:
            return self._last_valid_value  # Return the last valid value

        key = self.entity_description.key
        if key == "status":
            value = self.device.result.get("status")
            self._last_valid_value = value  # Cache the last valid value
        elif key == "timer":
            self._last_valid_value = self._timer_state
        else:
            value = self.device.result.get(key)
            self._last_valid_value = value  # Cache the last valid value
        return self._last_valid_value

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        await super().async_added_to_hass()
        _LOGGER.warning("Setting up listener for coordinator updates")
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
        self._update_listener = async_track_time_interval(self.hass, self.async_update, self._update_interval)
        asyncio.create_task(self.device.check_connection())  # Start the connection check task

    async def async_update(self, *args):
        """Update the sensor state."""
        await self.coordinator.async_request_refresh()
        if self.device.result is not None:
            status = self.device.result.get("status")
            if status == "Running" and self._timer_task is None:
                self._timer_state = 0  # Reset timer when starting
                self._timer_task = asyncio.create_task(self._run_timer())
            elif status == "Idle" and self._timer_task is not None:
                self._timer_task.cancel()
                self._timer_task = None
                self._timer_state = 0  # Reset timer when stopped

    async def _run_timer(self):
        """Run the timer."""
        try:
            while True:
                self._timer_state += 1
                self.async_write_ha_state()  # Update the state in Home Assistant
                await asyncio.sleep(1)  # Add a 1-second delay
        except asyncio.CancelledError:
            pass


class LaifenDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Laifen data."""

    def __init__(self, hass: HomeAssistant, laifen: Laifen):
        """Initialize."""
        self.laifen = laifen
        super().__init__(
            hass,
            _LOGGER,
            name="Laifen",
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self):
        """Update data via library."""
        try:
            await self.laifen.gatherdata()
            return self.laifen.result
        except Exception as err:
            raise UpdateFailed(f"Error communicating with Laifen API: {err}")
