"""Constants for the Laifen integration."""

from dataclasses import dataclass
from homeassistant.components.sensor import (
    SensorEntityDescription,
    SensorDeviceClass,
    SensorStateClass,
)

@dataclass
class LaifenSensorEntityDescription(SensorEntityDescription):
    unique_id: str | None = None

DOMAIN = "laifen_ble"
UPDATE_SECONDS = 1
DEVICE_TIMEOUT = 15

SENSOR_TYPES = [
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
]
