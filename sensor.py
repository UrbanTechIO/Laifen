"""Platform for sensor integration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

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
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .laifen import Laifen

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=1)  # Set scan interval to 1 second


@dataclass
class LaifenSensorEntityDescription(SensorEntityDescription):
    """Provide a description of a Laifen sensor."""

    unique_id: str | None = None


SENSORS = (
    # LaifenSensorEntityDescription(
    #     key="status_raw",
    #     name="Status Raw",
    #     unique_id="laifen_status_raw",
    #     icon="mdi:toothbrush-electric",
    # ),
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
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    data = hass.data[DOMAIN][entry.entry_id]
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

        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, self.device.ble_device.address)},
            manufacturer="Laifen",
            name="Laifen Toothbrush",
        )

        self._attr_unique_id = (
            f"{self.device.ble_device.address}_{description.unique_id}"
        )

    @property
    def native_value(self) -> str | None:
        """Return sensor state."""
        if self.device.result is None:
            return self._last_valid_value  # Return the last valid value

        # if self.entity_description.key == "status_raw":
        #     value = self.device.result.get("status_raw")
        #     self._last_valid_value = value  # Update the last valid value
        #     return value
        if self.entity_description.key == "status":
            value = self.device.result.get("status")
            self._last_valid_value = "Running" if value == "1" else "Idle"
            return self._last_valid_value
        elif self.entity_description.key == "vibration_strength":
            value = self.device.result.get("vibration_strength")
            self._last_valid_value = value  # Cache the last valid value
            return value
        elif self.entity_description.key == "oscillation_range":
            value = self.device.result.get("oscillation_range")
            self._last_valid_value = value  # Cache the last valid value
            return value
        elif self.entity_description.key == "oscillation_speed":
            value = self.device.result.get("oscillation_speed")
            self._last_valid_value = value  # Cache the last valid value
            return value
        elif self.entity_description.key == "mode":
            value = self.device.result.get("mode")
            if value == "0":
                self._last_valid_value = "1"
            elif value == "1":
                self._last_valid_value = "2"
            elif value == "2":
                self._last_valid_value = "3"
            elif value == "3":
                self._last_valid_value = "4"
            else:
                self._last_valid_value = value  # Cache the last valid value
            return self._last_valid_value
        value = self.device.result[self.entity_description.key]
        self._last_valid_value = value  # Cache the last valid value
        return value

    async def async_update(self):
        """Update the sensor state."""
        await self.coordinator.async_request_refresh()
