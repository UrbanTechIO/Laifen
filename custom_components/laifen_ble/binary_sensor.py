from __future__ import annotations
import logging

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .models import LaifenData, DEVICE_REGISTRY, laifen_device_info

_LOGGER = logging.getLogger(__name__)


class LaifenBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """
    Read-only status indicator for a Wave Pro (LFTB02-S-412B, V2 Pro protocol)
    on/off feature.

    These mirror settings controlled from the Laifen app. The corresponding
    write commands have not yet been confirmed, so these are read-only for
    now — see laifen.py V2 Pro parser for the confirmed byte mappings.

    If the connected device doesn't report this key (e.g. a V1 LFTB01
    device), the entity reports as unavailable rather than showing a
    misleading Off state.
    """

    _attr_has_entity_name = True
    _attr_should_poll     = False

    def __init__(self, device, coordinator, key: str, icon: str):
        super().__init__(coordinator)
        self.device = device
        self._key = key
        self._attr_unique_id  = f"{device.address}_{key}"
        self._attr_translation_key = key
        self._attr_icon       = icon
        self._attr_device_info = laifen_device_info(device)

    @property
    def available(self) -> bool:
        result = self.device.result or {}
        return self._key in result

    @property
    def is_on(self) -> bool | None:
        result = self.device.result or {}
        value = result.get(self._key)
        if value is None:
            return None
        return bool(value)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class LaifenConnectionSensor(CoordinatorEntity, BinarySensorEntity):
    """
    BLE connection status sensor — Connected / Disconnected.

    Works for both V1 (LFTB01) and V2 Pro (Wave Pro).

    ON  = BLE link is currently established (client exists and is_connected)
    OFF = not connected (sleeping, out of range, or reconnecting)

    Device class CONNECTIVITY makes HA display "Connected" / "Disconnected"
    with the correct icon automatically, and integrates with the HA dashboard
    Connected/Disconnected UI styling.

    The entity updates whenever the coordinator pushes new data, which happens
    on every connect, disconnect, and successful notification — so latency is
    effectively immediate (same as the rest of the integration's entities).
    """

    _attr_has_entity_name = True
    _attr_should_poll     = False
    _attr_device_class    = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id   = f"{device.address}_connection"
        self._attr_translation_key = "connection"
        self._attr_device_info = laifen_device_info(device)

    @property
    def available(self) -> bool:
        # Always available — even when disconnected the entity itself is valid
        return True

    @property
    def is_on(self) -> bool:
        """True when the BLE client is connected."""
        client = self.device.client
        return bool(client and client.is_connected)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


# (key, icon) — key doubles as the translation_key; names live in translations/
WAVE_PRO_BINARY_SENSORS = [
    ("deep_clean",         "mdi:shimmer"),
    ("anti_splash",        "mdi:water-off"),
    ("power_ramp_up",      "mdi:chart-line-variant"),
    ("quick_spin_dry",     "mdi:fan"),
    ("over_pressure",      "mdi:gauge-full"),
    ("bristle_protection", "mdi:shield-check"),
    ("lift_to_wake",       "mdi:hand-back-right"),
]


class LaifenOverPressureActiveSensor(CoordinatorEntity, BinarySensorEntity):
    """
    Real-time "pressing too hard" sensor (Wave Pro).

    Updated at ~100ms intervals from the 0x82/0x0C telemetry packets during
    brushing — the same signal the brush uses to trigger its buzz/slowdown.
    Payload byte p2 != 0 means over-pressure is currently active.

    Uses device class PROBLEM so HA treats ON as a warning state (red/alert
    in the UI), and goes unavailable when not brushing (no telemetry stream).
    """

    _attr_has_entity_name = True
    _attr_should_poll     = False
    _attr_device_class    = BinarySensorDeviceClass.PROBLEM

    def __init__(self, device, coordinator):
        super().__init__(coordinator)
        self.device = device
        self._attr_unique_id  = f"{device.address}_over_pressure_active"
        self._attr_translation_key = "over_pressure_active"
        self._attr_icon       = "mdi:hand-back-right-outline"
        self._attr_device_info = laifen_device_info(device)

    @property
    def available(self) -> bool:
        # Only meaningful while brushing — unavailable otherwise
        result = self.device.result or {}
        return (
            self.device._proto_version == "v2pro"
            and result.get("status") == "Running"
        )

    @property
    def is_on(self) -> bool | None:
        return bool((self.device.result or {}).get("over_pressure_active", False))

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
            entities.append(
                LaifenConnectionSensor(data.device, data.coordinator)
            )
            for key, icon in WAVE_PRO_BINARY_SENSORS:
                entities.append(
                    LaifenBinarySensor(data.device, data.coordinator, key, icon)
                )
            entities.append(
                LaifenOverPressureActiveSensor(data.device, data.coordinator)
            )

    if entities:
        async_add_entities(entities)
    else:
        _LOGGER.debug("No valid Laifen binary_sensor entities to add.")
