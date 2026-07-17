from __future__ import annotations
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_OPTIONS_BASE, MODE_OPTIONS_HF, OVER_PRESSURE_LEVEL_OPTIONS
from .models import LaifenData, DEVICE_REGISTRY, laifen_device_info

_LOGGER = logging.getLogger(__name__)


class LaifenModeSelect(CoordinatorEntity, SelectEntity):
    """
    Mode selector dropdown.

    - Shows Mode 1/2/3 normally.
    - Shows Mode 1/2/3/4 when High Frequency is on.
    - When HF is turned off while Mode 4 is selected, auto-selects Mode 3.
    - Changing mode updates the three sliders via coordinator data update.
    """

    _attr_has_entity_name = True
    _attr_should_poll     = False

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_mode_select"
        self._attr_translation_key = "mode"
        self._attr_icon       = "mdi:view-list"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.address)},
            "manufacturer": "Laifen",
            "name":         "Laifen Toothbrush",
            "model":        "Laifen BLE",
            "sw_version":   "1.0.0",
        }
        self._attr_options    = MODE_OPTIONS_BASE[:]

    # ── Options list — dynamic based on HF state ──────────────────────

    def _hf_on(self) -> bool:
        return bool((self.device.result or {}).get("high_frequency", False))

    def _refresh_options(self):
        self._attr_options = MODE_OPTIONS_HF[:] if self._hf_on() else MODE_OPTIONS_BASE[:]

    # ── Current selection ─────────────────────────────────────────────

    @property
    def current_option(self) -> str | None:
        self._refresh_options()
        # Read active mode from the status packet (mode_nibble from data_str[9])
        # This reflects what the physical button has selected on the device.
        mode_index = (self.device.result or {}).get("mode_index",
                      self.device._current_mode_index)
        label = f"Mode {mode_index + 1}"
        if label not in self._attr_options:
            return self._attr_options[-1]
        return label

    @property
    def options(self) -> list[str]:
        self._refresh_options()
        return self._attr_options

    async def async_select_option(self, option: str) -> None:
        """
        Select a mode and immediately re-write that mode's cached slider
        values. This mirrors what the Laifen app does: after sending the
        mode-select command (which has a side effect of writing its checksum
        into the target mode's strength byte), the app immediately sends
        the correct strength/range/speed to overwrite the garbage.
        """
        try:
            mode_index = int(option.split()[-1]) - 1
        except (ValueError, IndexError):
            _LOGGER.warning(f"Invalid mode option: {option}")
            return

        # Step 1: send mode-select
        success = await self.device.set_mode(mode_index)
        if not success:
            _LOGGER.warning(f"Failed to send mode-select for {option}")
            return

        base = f"m{mode_index + 1}"
        result = self.device.result or {}
        strength = result.get(f"{base}_strength", 5)
        rng      = result.get(f"{base}_range",    5)
        speed    = result.get(f"{base}_speed",    5)

        if self.device._proto_version == "v2pro":
            # V2 Pro's switchMode (LEN=1) has no known corruption side
            # effect to fix up — just update HA state to reflect the new
            # mode and its cached slider values.
            strength = max(1, min(int(strength), 20 if mode_index == 3 else 10))
            rng      = max(1, min(int(rng),   10))
            speed    = max(1, min(int(speed), 10))
        else:
            # Step 2 (V1 only): immediately re-write the correct slider
            # values for this mode to overwrite the checksum corruption
            # the mode-select command caused.
            strength = max(1, min(int(strength), 20 if mode_index == 3 else 10))
            rng      = max(1, min(int(rng),  10))
            speed    = max(1, min(int(speed), 10))

            await self.device.set_vibration_strength(strength)
            await self.device.set_oscillation_range(rng)
            await self.device.set_oscillation_speed(speed)

        # Update HA state
        self.device._current_mode_index = mode_index
        if self.device.result:
            self.device.result["mode_index"]      = mode_index
            self.device.result["mode"]            = str(mode_index + 1)
            self.device.result["active_strength"] = strength
            self.device.result["active_range"]    = rng
            self.device.result["active_speed"]    = speed

        self.coordinator.async_set_updated_data(self.device.result)

    # ── Handle HF toggle: if Mode 4 selected and HF turns off → Mode 3 ──

    async def _handle_hf_off(self):
        current = (self.device.result or {}).get("mode_index", 0)
        if current == 3:
            # Auto-switch to Mode 3 (index 2)
            await self.async_select_option("Mode 3")

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        def _on_coordinator_update():
            # Detect HF turning off while on Mode 4
            if not self._hf_on():
                current_idx = (self.device.result or {}).get("mode_index", 0)
                if current_idx == 3:
                    self.hass.async_create_task(self._handle_hf_off())
            self.async_write_ha_state()

        self.async_on_remove(
            self.coordinator.async_add_listener(_on_coordinator_update)
        )


class LaifenOverPressureLevelSelect(CoordinatorEntity, SelectEntity):
    """
    Over Pressure sensitivity level (Wave Pro): Light / Medium / Hard.

    Selecting a level enables Over Pressure detection at that sensitivity
    (CMD_TB_PRESS_REMINDER=0x20B). There is currently no "Off" option here —
    use the "Over Pressure" binary sensor's source switch if you want to
    fully disable it (not yet exposed as a switch).
    """

    _attr_has_entity_name = True
    _attr_should_poll     = False
    _attr_options         = OVER_PRESSURE_LEVEL_OPTIONS[:]

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_over_pressure_level_select"
        self._attr_translation_key = "over_pressure_level"
        self._attr_icon       = "mdi:gauge"
        self._attr_device_info = laifen_device_info(device)

    @property
    def available(self) -> bool:
        return self.device._proto_version == "v2pro"

    @property
    def current_option(self) -> str | None:
        level = (self.device.result or {}).get("over_pressure_level")
        if level in OVER_PRESSURE_LEVEL_OPTIONS:
            return level
        return None

    async def async_select_option(self, option: str) -> None:
        if await self.device.set_over_pressure_level(option):
            if self.device.result:
                self.device.result["over_pressure_level"] = option
                self.device.result["over_pressure"] = True
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
            entities.append(LaifenModeSelect(data.device, data.coordinator))
            entities.append(LaifenOverPressureLevelSelect(data.device, data.coordinator))

    if entities:
        async_add_entities(entities)
    else:
        _LOGGER.debug("No valid Laifen select entities to add.")
