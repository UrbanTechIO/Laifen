"""The Laifen integration."""
from __future__ import annotations

import asyncio
import logging
import async_timeout
from bleak import BleakError
from .laifen import Laifen

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import ADDRESS, BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from datetime import timedelta

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
            # await self.laifen.check_connection()
            await self.laifen.gatherdata()
            return self.laifen.result
        except BleakError as e:
            _LOGGER.warning(f"Error updating data: {e}")
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
                _LOGGER.warning("Device not reachable, retrying in 30 seconds...")
                await asyncio.sleep(30)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Laifen for multiple devices, restoring stored devices and ensuring passive Bluetooth detection."""
    
    if DOMAIN not in hass.data:
        hass.data.setdefault(DOMAIN, {})

    stored_devices = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    addresses = entry.data.get("devices", []) or list(stored_devices.keys())

    if not addresses:
        _LOGGER.warning("No Laifen devices found. Setup aborted until a device is added.")
        raise ConfigEntryNotReady  

    ble_devices = [bluetooth.async_ble_device_from_address(hass, addr.upper(), True) for addr in addresses]
    ble_devices = [device for device in ble_devices if device]

    laifens = []
    hass.data[DOMAIN][entry.entry_id] = {"laifens": laifens}

    for addr, ble_device in zip(addresses, ble_devices):
        coordinator = LaifenCoordinator(hass, None)
        laifen = Laifen(ble_device, coordinator)
        coordinator.laifen = laifen
        laifens.append(laifen)

        if not await laifen.connect():
            _LOGGER.warning(f"Failed to connect to {laifen.ble_device.address}. Aborting setup.")
            continue

        if laifen.ble_device.address in hass.data[DOMAIN][entry.entry_id]:
            _LOGGER.warning(f"Device {laifen.ble_device.address} is already registered. Skipping duplicate setup.")
            continue  

        hass.data[DOMAIN][entry.entry_id][laifen.ble_device.address] = LaifenData(entry.title, laifen, laifen.coordinator)
        _LOGGER.warning(f"Successfully registered {laifen.ble_device.address} in Home Assistant.")

        await laifen.start_notifications()

    # ✅ Register Passive Bluetooth Callbacks for Wake-Ups and Data Updates
    for addr in addresses:
        entry.async_on_unload(
            bluetooth.async_register_callback(
                hass,
                lambda service_info, change: asyncio.create_task(_async_device_recovery(hass, entry, service_info)),  
                BluetoothCallbackMatcher({ADDRESS: addr}),
                bluetooth.BluetoothScanningMode.PASSIVE,
            )
        )
        entry.async_on_unload(
            bluetooth.async_register_callback(
                hass,
                lambda service_info, change: asyncio.create_task(_async_update_ble(service_info, change)),  
                BluetoothCallbackMatcher({ADDRESS: addr}),
                bluetooth.BluetoothScanningMode.PASSIVE,
            )
        )

    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )

    return True

async def _async_stop(hass: HomeAssistant, event: Event) -> None:
    """Close all connections on shutdown."""
    laifens = hass.data.get(DOMAIN, {}).get("laifens", [])  # ✅ Retrieve globally stored laifens
    for laifen in laifens:
        await laifen.stop_notifications()  
        await laifen.disconnect()

async def _async_update_ble(hass: HomeAssistant, entry: ConfigEntry, service_info, change):
    """Update BLE data."""
    laifens = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("laifens", [])  # ✅ Retrieve laifens from entry
    for laifen in laifens:
        if laifen.ble_device.address == service_info.device.address:
            await laifen.set_ble_device(service_info.device)


async def _async_device_recovery(hass: HomeAssistant, entry: ConfigEntry, service_info):
    """Recover Laifen devices when they wake up via passive Bluetooth events."""

    device_address = service_info.device.address

    if device_address in hass.data[DOMAIN][entry.entry_id]:
        _LOGGER.warning(f"Laifen {device_address} detected via Bluetooth callback! Restoring connection...")
        
        laifen_data = hass.data[DOMAIN][entry.entry_id][device_address]
        laifen = laifen_data.laifen if isinstance(laifen_data, LaifenData) else laifen_data

        if laifen.client.is_connected:
            _LOGGER.warning(f"{device_address} is already connected. Skipping recovery.")
            return

        # ✅ Attempt reconnection
        if await laifen.connect():
            await laifen.start_notifications()
            _LOGGER.warning(f"Successfully reconnected to {device_address}.")
        else:
            _LOGGER.warning(f"Failed to reconnect {device_address}. Retrying on next callback event.")

    else:
        _LOGGER.warning(f"Laifen {device_address} detected but not registered in HA. Attempting to restore...")
        
        # ✅ Dynamically register the toothbrush when detected for the first time
        new_coordinator = LaifenCoordinator(hass, None)
        new_laifen = Laifen(service_info.device, new_coordinator)
        new_coordinator.laifen = new_laifen  
        hass.data[DOMAIN][entry.entry_id][device_address] = LaifenData(entry.title, new_laifen, new_coordinator)

        if await new_laifen.connect():
            await new_laifen.start_notifications()
            _LOGGER.warning(f"Newly detected Laifen device {device_address} successfully restored.")


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    data = hass.data[DOMAIN][entry.entry_id]
    await data.coordinator.async_request_refresh()

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    await data.device.stop_notifications()  
    await data.device.disconnect()
    return True
