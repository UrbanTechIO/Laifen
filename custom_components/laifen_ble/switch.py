from __future__ import annotations
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .models import LaifenData, DEVICE_REGISTRY, laifen_device_info

_LOGGER = logging.getLogger(__name__)

class LaifenPowerSwitch(CoordinatorEntity, SwitchEntity):
    """Turn the brush motor on/off."""

    _attr_has_entity_name = True
    _attr_should_poll     = False

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_power"
        self._attr_name       = "Power"
        self._attr_icon       = "mdi:toothbrush-electric"
        self._attr_device_info = laifen_device_info(device)

    @property
    def is_on(self) -> bool:
        if self.device.result:
            return self.device.result.get("status") == "Running"
        return self._attr_is_on

    async def async_turn_on(self, **kwargs):
        success = await self.device.turn_on()
        _LOGGER.debug(f"[{self.device.address}] LaifenPowerSwitch.async_turn_on: device.turn_on() -> {success}")
        if success:
            self._attr_is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        success = await self.device.turn_off()
        _LOGGER.debug(f"[{self.device.address}] LaifenPowerSwitch.async_turn_off: device.turn_off() -> {success}")
        if success:
            self._attr_is_on = False
            self.async_write_ha_state()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class LaifenHighFrequencySwitch(CoordinatorEntity, SwitchEntity):
    """Enable/disable High Frequency mode (adds Mode 4, extends strength to 20 for Mode 4)."""

    _attr_has_entity_name = True
    _attr_should_poll     = False

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_high_frequency"
        self._attr_name       = "High Frequency"
        self._attr_icon       = "mdi:sine-wave"
        self._attr_device_info = laifen_device_info(device)

    @property
    def is_on(self) -> bool:
        return bool((self.device.result or {}).get("high_frequency", False))

    async def async_turn_on(self, **kwargs):
        # Per the Laifen app, High Frequency mode can only be enabled while
        # Deep Clean is off. Rather than erroring, mirror the app's
        # behavior: turn Deep Clean off first, then enable HF.
        if (self.device.result or {}).get("deep_clean", False):
            if await self.device.set_deep_clean(False):
                if self.device.result:
                    self.device.result["deep_clean"] = False
                self.coordinator.async_set_updated_data(self.device.result)
            else:
                raise HomeAssistantError(
                    "Could not turn off Deep Clean (required before enabling "
                    "High Frequency)."
                )
        if await self.device.set_high_frequency(True):
            if self.device.result:
                self.device.result["high_frequency"] = True
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_turn_off(self, **kwargs):
        if await self.device.set_high_frequency(False):
            if self.device.result:
                self.device.result["high_frequency"] = False
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class LaifenAirplaneSwitch(CoordinatorEntity, SwitchEntity):
    """Enable/disable Airplane mode (disables physical button, LED indicator on)."""

    _attr_has_entity_name = True
    _attr_should_poll     = False

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_airplane"
        self._attr_name       = "Airplane"
        self._attr_icon       = "mdi:airplane"
        self._attr_device_info = laifen_device_info(device)

    @property
    def is_on(self) -> bool:
        return bool((self.device.result or {}).get("airplane_mode", False))

    async def async_turn_on(self, **kwargs):
        if await self.device.set_airplane_mode(True):
            if self.device.result:
                self.device.result["airplane_mode"] = True
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_turn_off(self, **kwargs):
        if await self.device.set_airplane_mode(False):
            if self.device.result:
                self.device.result["airplane_mode"] = False
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class LaifenReminderSwitch(CoordinatorEntity, SwitchEntity):
    """Enable/disable the 30-second brushing reminder."""

    _attr_has_entity_name = True
    _attr_should_poll     = False

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_reminder_30s"
        self._attr_name       = "30s Reminder"
        self._attr_icon       = "mdi:timer-alert"
        self._attr_device_info = laifen_device_info(device)

    @property
    def is_on(self) -> bool:
        return bool((self.device.result or {}).get("reminder_30s", False))

    async def async_turn_on(self, **kwargs):
        if await self.device.set_reminder_30s(True):
            if self.device.result:
                self.device.result["reminder_30s"] = True
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_turn_off(self, **kwargs):
        if await self.device.set_reminder_30s(False):
            if self.device.result:
                self.device.result["reminder_30s"] = False
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class LaifenDeepCleanSwitch(CoordinatorEntity, SwitchEntity):
    """Enable/disable Deep Clean mode (Wave Pro). Must be off to use High Frequency."""

    _attr_has_entity_name = True
    _attr_should_poll     = False

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_deep_clean"
        self._attr_name       = "Deep Clean"
        self._attr_icon       = "mdi:toothbrush"
        self._attr_device_info = laifen_device_info(device)

    @property
    def available(self) -> bool:
        return self.device._proto_version == "v2pro"

    @property
    def is_on(self) -> bool:
        return bool((self.device.result or {}).get("deep_clean", False))

    async def async_turn_on(self, **kwargs):
        if (self.device.result or {}).get("high_frequency", False):
            if await self.device.set_high_frequency(False):
                if self.device.result:
                    self.device.result["high_frequency"] = False
                self.coordinator.async_set_updated_data(self.device.result)
            else:
                raise HomeAssistantError(
                    "Could not turn off High Frequency (required before "
                    "enabling Deep Clean)."
                )
        if await self.device.set_deep_clean(True):
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_turn_off(self, **kwargs):
        if await self.device.set_deep_clean(False):
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class LaifenAntiSplashSwitch(CoordinatorEntity, SwitchEntity):
    """Enable/disable Anti-Splash mode (Wave Pro)."""

    _attr_has_entity_name = True
    _attr_should_poll     = False

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_anti_splash"
        self._attr_name       = "Anti-Splash"
        self._attr_icon       = "mdi:water-off"
        self._attr_device_info = laifen_device_info(device)

    @property
    def available(self) -> bool:
        return self.device._proto_version == "v2pro"

    @property
    def is_on(self) -> bool:
        return bool((self.device.result or {}).get("anti_splash", False))

    async def async_turn_on(self, **kwargs):
        if await self.device.set_anti_splash(True):
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_turn_off(self, **kwargs):
        if await self.device.set_anti_splash(False):
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class LaifenPowerRampUpSwitch(CoordinatorEntity, SwitchEntity):
    """Enable/disable 3s Power Ramp-Up (Wave Pro)."""

    _attr_has_entity_name = True
    _attr_should_poll     = False

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_power_ramp_up"
        self._attr_name       = "3s Power Ramp-Up"
        self._attr_icon       = "mdi:chart-line"
        self._attr_device_info = laifen_device_info(device)

    @property
    def available(self) -> bool:
        return self.device._proto_version == "v2pro"

    @property
    def is_on(self) -> bool:
        return bool((self.device.result or {}).get("power_ramp_up", False))

    async def async_turn_on(self, **kwargs):
        if await self.device.set_power_ramp_up(True):
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_turn_off(self, **kwargs):
        if await self.device.set_power_ramp_up(False):
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class LaifenBristleProtectionSwitch(CoordinatorEntity, SwitchEntity):
    """Enable/disable Bristle Protection mode (Wave Pro)."""

    _attr_has_entity_name = True
    _attr_should_poll     = False

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_bristle_protection"
        self._attr_name       = "Bristle Protection"
        self._attr_icon       = "mdi:shield-check"
        self._attr_device_info = laifen_device_info(device)

    @property
    def available(self) -> bool:
        return self.device._proto_version == "v2pro"

    @property
    def is_on(self) -> bool:
        return bool((self.device.result or {}).get("bristle_protection", False))

    async def async_turn_on(self, **kwargs):
        if await self.device.set_bristle_protection(True):
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_turn_off(self, **kwargs):
        if await self.device.set_bristle_protection(False):
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class LaifenLiftToWakeSwitch(CoordinatorEntity, SwitchEntity):
    """Enable/disable Lift to Wake reminder (Wave Pro)."""

    _attr_has_entity_name = True
    _attr_should_poll     = False

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_lift_to_wake"
        self._attr_name       = "Lift to Wake Reminder"
        self._attr_icon       = "mdi:hand-wave"
        self._attr_device_info = laifen_device_info(device)

    @property
    def available(self) -> bool:
        return self.device._proto_version == "v2pro"

    @property
    def is_on(self) -> bool:
        return bool((self.device.result or {}).get("lift_to_wake", False))

    async def async_turn_on(self, **kwargs):
        if await self.device.set_lift_to_wake(True):
            self.coordinator.async_set_updated_data(self.device.result)

    async def async_turn_off(self, **kwargs):
        if await self.device.set_lift_to_wake(False):
            self.coordinator.async_set_updated_data(self.device.result)

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
                LaifenPowerSwitch(data.device, data.coordinator),
                LaifenHighFrequencySwitch(data.device, data.coordinator),
                LaifenAirplaneSwitch(data.device, data.coordinator),
                LaifenReminderSwitch(data.device, data.coordinator),
                LaifenDeepCleanSwitch(data.device, data.coordinator),
                LaifenAntiSplashSwitch(data.device, data.coordinator),
                LaifenPowerRampUpSwitch(data.device, data.coordinator),
                LaifenBristleProtectionSwitch(data.device, data.coordinator),
                LaifenLiftToWakeSwitch(data.device, data.coordinator),
            ]

    if entities:
        async_add_entities(entities)
    else:
        _LOGGER.debug("No valid Laifen switch entities to add.")
