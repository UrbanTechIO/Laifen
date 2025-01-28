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

PLATFORMS: list[Platform] = [Platform.SENSOR]
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
            return None  # Return None or handle it appropriately

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
                _LOGGER.warning("Device not reachable, retrying in 30 seconds...")
                await asyncio.sleep(30)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Laifen from a config entry."""
    address: str = entry.data[CONF_MAC]
    ble_device = bluetooth.async_ble_device_from_address(hass, address.upper(), True)
    if not ble_device:
        raise ConfigEntryNotReady(f"Could not find Laifen device with address {address}")

    coordinator = LaifenCoordinator(hass, None)
    laifen = Laifen(ble_device, coordinator)
    coordinator.laifen = laifen

    @callback
    def _async_update_ble(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Update from a ble callback."""
        laifen.set_ble_device(service_info.device)

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_update_ble,
            BluetoothCallbackMatcher({ADDRESS: address}),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
    )

    try:
        async with async_timeout.timeout(DEVICE_TIMEOUT):
            await coordinator.async_config_entry_first_refresh()
            await laifen.start_notifications()  # Start notifications
    except asyncio.TimeoutError as ex:
        raise ConfigEntryNotReady(
            "Unable to communicate with the device; "
            f"Try moving the Bluetooth adapter closer to {DOMAIN}"
        ) from ex

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = LaifenData(
        entry.title, laifen, coordinator
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
    await data.coordinator.async_request_refresh()

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    await data.device.stop_notifications()  # Stop notifications
    await data.device.disconnect()
    return True
