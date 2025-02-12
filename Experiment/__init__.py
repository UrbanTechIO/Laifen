"""The Laifen integration."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

import async_timeout
from bleak import BleakError
from .laifen import Laifen

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import ADDRESS, BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEVICE_TIMEOUT, DOMAIN, UPDATE_SECONDS
from .models import LaifenData

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]
_LOGGER = logging.getLogger(__name__)

class LaifenCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, laifen: Laifen):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_SECONDS),
        )
        self.laifen = laifen

    async def _async_update_data(self):
        try:
            await self.laifen.check_connection()  # Check and reconnect if necessary
            await self.laifen.gatherdata()
            return self.laifen.result
        except BleakError as e:
            _LOGGER.error(f"Error updating data: {e}")
            return UpdateFailed(f"Error updating data: {e}")

    @callback
    def async_handle_notification(self, data):
        """Handle data from notifications."""
        self.async_set_updated_data(data)

    async def async_config_entry_first_refresh(self):
        """Perform the first refresh of the config entry."""
        while True:
            try:
                await self.async_refresh()
                break
            except UpdateFailed:
                _LOGGER.warning("Device not reachable, retrying in 10 seconds...")
                await asyncio.sleep(10)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Laifen from a config entry."""
    address: str = entry.data[CONF_MAC]
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    if entry.entry_id not in hass.data[DOMAIN]:
        hass.data[DOMAIN][entry.entry_id] = {}

    if address in hass.data[DOMAIN][entry.entry_id]:
        _LOGGER.warning(f"Device with address {address} is already set up.")
        return False

    coordinator = LaifenCoordinator(hass, None)
    laifen = Laifen(None, coordinator)
    coordinator.laifen = laifen
    hass.data[DOMAIN][entry.entry_id][address] = LaifenData(
        entry.title, laifen, coordinator
    )

    @callback
    async def _async_update_ble(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Update from a BLE callback."""
        await laifen.set_ble_device(service_info.device)

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_update_ble,
            BluetoothCallbackMatcher({ADDRESS: address}),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
    )

    async def connect_and_setup():
        while True:
            ble_device = bluetooth.async_ble_device_from_address(hass, address.upper(), True)
            if ble_device:
                laifen.ble_device = ble_device
                try:
                    async with async_timeout.timeout(DEVICE_TIMEOUT):
                        await coordinator.async_config_entry_first_refresh()
                        await laifen.start_notifications()  # Start notifications
                    break
                except asyncio.TimeoutError as ex:
                    _LOGGER.error("Unable to communicate with the device; Try moving the Bluetooth adapter closer to the device.")
            _LOGGER.warning("Device not found, retrying in 10 seconds...")
            await asyncio.sleep(10)

    hass.async_create_task(connect_and_setup())

    # Register the device
    from homeassistant.helpers.device_registry import async_get
    device_registry = async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, address)},
        manufacturer="Laifen",
        name="Laifen Toothbrush",
        model="Laifen BLE",
        sw_version="1.0.0",
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async def _async_stop(event: Event) -> None:
        """Close the connection."""
        await laifen.stop_notifications()  # Stop notifications
        await laifen.disconnect()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )
    return True

async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    data = hass.data[DOMAIN][entry.entry_id]
    for laifen_data in data.values():
        await laifen_data.coordinator.async_request_refresh()

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    for laifen_data in data.values():
        await laifen_data.device.stop_notifications()  # Stop notifications
        await laifen_data.device.disconnect()
    return True
