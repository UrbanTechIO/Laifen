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
        translation_key="status",
        unique_id="laifen_status",
        icon="mdi:toothbrush-electric",
    ),
    LaifenSensorEntityDescription(
        key="mode",
        translation_key="mode",
        unique_id="laifen_mode",
        icon="mdi:dots-horizontal",
    ),
    LaifenSensorEntityDescription(
        key="battery_level",
        translation_key="battery_level",
        unique_id="laifen_battery_level",
        icon="mdi:battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="%",
    ),
    LaifenSensorEntityDescription(
        key="brushing_time",
        translation_key="brushing_time",
        unique_id="laifen_brushing_time",
        icon="mdi:timer",
    ),
    LaifenSensorEntityDescription(
        key="timer",
        translation_key="timer",
        unique_id="laifen_timer",
        icon="mdi:timer",
    ),
    LaifenSensorEntityDescription(
        key="active_strength",
        translation_key="active_strength",
        unique_id="laifen_active_strength",
        icon="mdi:sine-wave",
    ),
    LaifenSensorEntityDescription(
        key="active_range",
        translation_key="active_range",
        unique_id="laifen_active_range",
        icon="mdi:arrow-oscillating",
    ),
    LaifenSensorEntityDescription(
        key="active_speed",
        translation_key="active_speed",
        unique_id="laifen_active_speed",
        icon="mdi:speedometer",
    ),
    LaifenSensorEntityDescription(
        key="brushing_duration",
        translation_key="brushing_duration",
        unique_id="laifen_brushing_duration",
        icon="mdi:timer-sand",
        native_unit_of_measurement="s",
    ),
    LaifenSensorEntityDescription(
        key="over_pressure_level",
        translation_key="over_pressure_level",
        unique_id="laifen_over_pressure_level",
        icon="mdi:gauge",
    ),
]

# Mode options — Mode 4 is appended dynamically when HF is on
MODE_OPTIONS_BASE = ["Mode 1", "Mode 2", "Mode 3"]
MODE_OPTIONS_HF   = ["Mode 1", "Mode 2", "Mode 3", "Mode 4"]

# Over Pressure level options (Wave Pro)
OVER_PRESSURE_LEVEL_OPTIONS = ["Light", "Medium", "Hard"]

# Slider limits
STRENGTH_MIN      = 1
STRENGTH_MIN_HF     = 11   # Mode 4 (HF) strength range is 11-20, not 1-20
STRENGTH_MAX_NORMAL = 10
STRENGTH_MAX_HF     = 20   # only when Mode 4 is active
RANGE_MIN         = 1
RANGE_MAX         = 10
SPEED_MIN         = 1
SPEED_MAX         = 10
