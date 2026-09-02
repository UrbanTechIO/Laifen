from __future__ import annotations
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_OPTIONS_BASE, MODE_OPTIONS_HF, OVER_PRESSURE_LEVEL_OPTIONS
from .models import LaifenData, DEVICE_REGISTRY, laifen_device_info
from .laifen.laifen import build_v1_settings

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
        # getattr(..., 0) fallback: defends against the entity rendering
        # state before the device object has fully initialized (e.g. right
        # after a reload, before any status packet has been parsed) — avoids
        # an AttributeError crash-loop in the coordinator update path, which
        # previously fired every second and bloated the HA database/log.
        mode_index = (self.device.result or {}).get(
            "mode_index", getattr(self.device, "_current_mode_index", 0)
        )
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
        Select a mode.

        V1: CONFIRMED via three HCI snoop captures (2026-08-09) — the app
        switches modes purely by sending the combined settings command
        (build_v1_settings) with the new mode's group byte and that mode's
        strength/range/speed. There is no separate mode-select command.
        Sending settings with an out-of-range value for the target mode
        (e.g. Mode 4 needs strength 11-20, not 1-10) causes the device to
        reject the write and revert to another mode — this was the cause of
        "selecting Mode 4 reverts to Mode 1".

        V2 Pro: switchMode has no known side effects; send it directly.
        """
        try:
            mode_index = int(option.split()[-1]) - 1
        except (ValueError, IndexError):
            _LOGGER.warning(f"Invalid mode option: {option}")
            return

        base = f"m{mode_index + 1}"
        result = self.device.result or {}
        strength = result.get(f"{base}_strength", 5)
        rng      = result.get(f"{base}_range",    5)
        speed    = result.get(f"{base}_speed",    5)

        # Mode 4 (index 3) uses the extended 11-20 strength range; all other
        # modes use 1-10.
        if mode_index == 3:
            strength = max(11, min(int(strength), 20))
        else:
            strength = max(1, min(int(strength), 10))
        rng   = max(1, min(int(rng),   10))
        speed = max(1, min(int(speed), 10))

        if self.device._proto_version == "v2pro":
            success = await self.device.set_mode(mode_index)
            if not success:
                _LOGGER.warning(f"Failed to send mode-select for {option}")
                return
        else:
            # V1: one combined write does the mode switch AND sets values.
            success = await self.device.send_command(
                build_v1_settings(mode_index, strength, rng, speed)
            )
            if not success:
                _LOGGER.warning(f"Failed to switch to {option}")
                return

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
            # Over-pressure level select is V2 Pro-only.
            if not data.device.is_v1_device:
                entities.append(LaifenOverPressureLevelSelect(data.device, data.coordinator))

    if entities:
        async_add_entities(entities)
    else:
        _LOGGER.debug("No valid Laifen select entities to add.")
