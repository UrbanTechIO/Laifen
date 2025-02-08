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
    await coordinator.async_config_entry_first_refresh()    #added !
    _LOGGER.warning("Adding Laifen sensor entities")    #added !
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

        if self.entity_description.key == "status":
            value = self.device.result.get("status")
            self._last_valid_value = "Running" if value == "1" else "Idle"
            # return self._last_valid_value
        elif self.entity_description.key == "vibration_strength":
            value = self.device.result.get("vibration_strength")
            self._last_valid_value = value  # Cache the last valid value
            # return value
        elif self.entity_description.key == "oscillation_range":
            value = self.device.result.get("oscillation_range")
            self._last_valid_value = value  # Cache the last valid value
            # return value
        elif self.entity_description.key == "oscillation_speed":
            value = self.device.result.get("oscillation_speed")
            self._last_valid_value = value  # Cache the last valid value
            # return value
        elif self.entity_description.key == "mode":
            value = self.device.result.get("mode")
            self._last_valid_value = {
                "0": "1",
                "1": "2",
                "2": "3",
                "3": "4"
            }.get(value, value)
        elif self.entity_description.key == "timer":
            # _LOGGER.warning(f"Timer Value: {self._timer_state}")
            self._last_valid_value = self._timer_state
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
            # _LOGGER.warning("Timer async update YEeeeeeeee:")
            status = self.device.result.get("status")
            if status == "1" and self._timer_task is None:
                self._timer_task = asyncio.create_task(self._run_timer())
            elif status == "0" and self._timer_task is not None:
                self._timer_task.cancel()
                self._timer_task = None
                self._timer_state = 0
        # _LOGGER.warning("Timer async update Noooooooo:")
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