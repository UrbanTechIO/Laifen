from __future__ import annotations
import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    STRENGTH_MIN, STRENGTH_MIN_HF, STRENGTH_MAX_NORMAL, STRENGTH_MAX_HF,
    RANGE_MIN, RANGE_MAX,
    SPEED_MIN,  SPEED_MAX,
)
from .models import LaifenData, DEVICE_REGISTRY, laifen_device_info

_LOGGER = logging.getLogger(__name__)

class LaifenVibrationStrength(CoordinatorEntity, NumberEntity):
    """
    Vibration Strength slider.

    Range:
      - Mode 1/2/3 (any HF state): 1–10
      - Mode 4 with HF on:          11–20
    Reads the active mode's stored strength from the status packet.
    Writes to the currently active mode on value commit (release).
    """

    _attr_has_entity_name = True
    _attr_should_poll     = False
    _attr_mode            = NumberMode.SLIDER
    _attr_native_step     = 1

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_vibration_strength"
        self._attr_name       = "Vibration Strength"
        self._attr_icon       = "mdi:sine-wave"
        self._attr_device_info = laifen_device_info(device)

    def _mode_index(self) -> int:
        return (self.device.result or {}).get("mode_index", 0)

    def _hf_on(self) -> bool:
        return bool((self.device.result or {}).get("high_frequency", False))

    def _hf_active(self) -> bool:
        return self._mode_index() == 3 and self._hf_on()

    @property
    def native_min_value(self) -> float:
        return STRENGTH_MIN_HF if self._hf_active() else STRENGTH_MIN

    @property
    def native_max_value(self) -> float:
        # Only Mode 4 (index 3) with HF on gets the extended range
        if self._hf_active():
            return STRENGTH_MAX_HF
        return STRENGTH_MAX_NORMAL

    @property
    def native_value(self) -> float | None:
        result = self.device.result or {}
        val = result.get("active_strength", result.get("vibration_strength", 0))
        return min(max(float(val), self.native_min_value), self.native_max_value)

    async def async_set_native_value(self, value: float) -> None:
        int_val = int(round(value))
        int_val = max(int(self.native_min_value), min(int_val, int(self.native_max_value)))
        success = await self.device.set_vibration_strength(int_val)
        if success and self.device.result:
            mode = self._mode_index()
            key  = f"m{mode + 1}_strength"
            self.device.result[key]                  = int_val
            self.device.result["active_strength"]    = int_val
            self.device.result["vibration_strength"] = int_val
            self.coordinator.async_set_updated_data(self.device.result)
        else:
            _LOGGER.warning(f"Failed to set vibration strength to {int_val}")

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class LaifenOscillationRange(CoordinatorEntity, NumberEntity):
    """
    Oscillation Range slider (1–10, all modes).
    Reads the active mode's stored range. Writes on release.
    """

    _attr_has_entity_name    = True
    _attr_should_poll        = False
    _attr_mode               = NumberMode.SLIDER
    _attr_native_step        = 1
    _attr_native_min_value   = RANGE_MIN
    _attr_native_max_value   = RANGE_MAX

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_oscillation_range"
        self._attr_name       = "Oscillation Range"
        self._attr_icon       = "mdi:arrow-oscillating"
        self._attr_device_info = laifen_device_info(device)

    def _mode_index(self) -> int:
        return (self.device.result or {}).get("mode_index", 0)

    @property
    def native_value(self) -> float | None:
        result = self.device.result or {}
        val = result.get("active_range", result.get("oscillation_range", 0))
        return float(max(RANGE_MIN, min(int(val), RANGE_MAX)))

    async def async_set_native_value(self, value: float) -> None:
        int_val = int(round(value))
        int_val = max(RANGE_MIN, min(int_val, RANGE_MAX))
        success = await self.device.set_oscillation_range(int_val)
        if success and self.device.result:
            mode = self._mode_index()
            key  = f"m{mode + 1}_range"
            self.device.result[key]               = int_val
            self.device.result["active_range"]    = int_val
            self.device.result["oscillation_range"] = int_val
            self.coordinator.async_set_updated_data(self.device.result)
        else:
            _LOGGER.warning(f"Failed to set oscillation range to {int_val}")

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class LaifenOscillationSpeed(CoordinatorEntity, NumberEntity):
    """
    Oscillation Speed slider (1–10, all modes).
    Reads the active mode's stored speed. Writes on release.
    """

    _attr_has_entity_name    = True
    _attr_should_poll        = False
    _attr_mode               = NumberMode.SLIDER
    _attr_native_step        = 1
    _attr_native_min_value   = SPEED_MIN
    _attr_native_max_value   = SPEED_MAX

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_oscillation_speed"
        self._attr_name       = "Oscillation Speed"
        self._attr_icon       = "mdi:speedometer"
        self._attr_device_info = laifen_device_info(device)

    def _mode_index(self) -> int:
        return (self.device.result or {}).get("mode_index", 0)

    @property
    def native_value(self) -> float | None:
        result = self.device.result or {}
        val = result.get("active_speed", result.get("oscillation_speed", 0))
        return float(max(SPEED_MIN, min(int(val), SPEED_MAX)))

    async def async_set_native_value(self, value: float) -> None:
        int_val = int(round(value))
        int_val = max(SPEED_MIN, min(int_val, SPEED_MAX))
        success = await self.device.set_oscillation_speed(int_val)
        if success and self.device.result:
            mode = self._mode_index()
            key  = f"m{mode + 1}_speed"
            self.device.result[key]              = int_val
            self.device.result["active_speed"]   = int_val
            self.device.result["oscillation_speed"] = int_val
            self.coordinator.async_set_updated_data(self.device.result)
        else:
            _LOGGER.warning(f"Failed to set oscillation speed to {int_val}")

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class LaifenBrushingDuration(CoordinatorEntity, NumberEntity):
    """
    Brushing Duration adjustment (Wave Pro).

    UNCONFIRMED: device command (CMD_TB_BRUSHING_TIME=0x200) takes an index
    0-8, presumed to represent 1-5 minutes in 0.5-minute steps (index 0 =
    1 min, index 8 = 5 min). Displayed here directly in minutes for a more
    intuitive slider; converted to the 0-8 index internally.
    The relationship between this index and the raw seconds value reported
    in the status packet (p5) hasn't been verified, so the displayed value
    is optimistic-only (last value sent from HA), not read back from the
    device.
    """

    _attr_has_entity_name = True
    _attr_should_poll     = False
    _attr_mode            = NumberMode.SLIDER
    _attr_native_step     = 0.5
    _attr_native_min_value = 1
    _attr_native_max_value = 5
    _attr_native_unit_of_measurement = "min"

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_brushing_duration"
        self._attr_name       = "Brushing Duration Adjustment"
        self._attr_icon       = "mdi:timer-plus-outline"
        self._attr_device_info = laifen_device_info(device)

    @property
    def available(self) -> bool:
        return self.device._proto_version == "v2pro"

    @property
    def native_value(self) -> float | None:
        index = (self.device.result or {}).get("brushing_duration_index", 0)
        return 1 + index * 0.5

    async def async_set_native_value(self, value: float) -> None:
        # value is 1-5 min in 0.5 steps -> index 0-8
        int_val = max(0, min(8, int(round((value - 1) * 2))))
        success = await self.device.set_brushing_duration(int_val)
        if success:
            if self.device.result is not None:
                self.device.result["brushing_duration_index"] = int_val
            self.coordinator.async_set_updated_data(self.device.result)
        else:
            _LOGGER.warning(f"Failed to set brushing duration to index {int_val}")

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    device_ids = entry.data.get("devices", [])
    entities   = []

    for address in device_ids:
        data = DEVICE_REGISTRY.get(entry.entry_id, {}).get(address)
        if not data:
            data = hass.data[DOMAIN][entry.entry_id].get(address)
        if isinstance(data, LaifenData):
            entities += [
                LaifenVibrationStrength(data.device, data.coordinator),
                LaifenOscillationRange(data.device, data.coordinator),
                LaifenOscillationSpeed(data.device, data.coordinator),
                LaifenBrushingDuration(data.device, data.coordinator),
            ]

    if entities:
        async_add_entities(entities)
    else:
        _LOGGER.debug("No valid Laifen number entities to add.")
