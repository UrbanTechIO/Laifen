from __future__ import annotations

import asyncio
import logging
import async_timeout


from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import ADDRESS, BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from bleak import BleakError
from .laifen import Laifen
from datetime import timedelta

from .const import DEVICE_TIMEOUT, DOMAIN, UPDATE_SECONDS
from .models import LaifenData, DEVICE_REGISTRY, DEVICE_SIGNAL

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]
_LOGGER = logging.getLogger(__name__)

class MockBLEDevice:
    def __init__(self, address):
        self.address = address
        self.name = "Laifen"


LAST_KNOWN_STORE_VERSION = 1
LAST_KNOWN_FILENAME = f"{DOMAIN}_last_known.json"

class LaifenCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, laifen: Laifen, device_address: str):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_SECONDS),
        )
        self.laifen = laifen
        self.device_address = device_address
        self._store = Store(hass, LAST_KNOWN_STORE_VERSION, LAST_KNOWN_FILENAME)

    async def _async_update_data(self):
        try:
            await self.laifen.check_connection()
            await self.laifen.gatherdata()
            if not self.laifen.result:
                # _LOGGER.warning("No valid result from Laifen. Assuming device is idle or sleeping. Keeping last known values.")
                raise UpdateFailed("Device is sleeping — do not wipe data")
            
            await self._async_store_data(self.laifen.result)  # <- Save fresh state
            return self.laifen.result
        except BleakError as e:
            if any(term in str(e) for term in ["Characteristic", "not found", "disconnected", "sleep", "timed out"]):
                # _LOGGER.warning(f"Device likely asleep: {e}. Holding last known state.")
                # Do NOT raise UpdateFailed — coordinator holds last data
                cached = await self._async_restore_data()
                return cached or {}
            raise UpdateFailed(f"Unexpected BLE error: {e}")

    async def _async_store_data(self, data: dict):
        all_data = await self._store.async_load() or {}
        all_data[self.device_address] = data
        await self._store.async_save(all_data)

    async def _async_restore_data(self) -> dict | None:
        all_data = await self._store.async_load() or {}
        return all_data.get(self.device_address)

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
                # _LOGGER.warning("Device not reachable, retrying in 30 seconds...")
                await asyncio.sleep(30)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Laifen for multiple devices, restoring stored devices and ensuring passive Bluetooth detection."""
    
    if DOMAIN not in hass.data:
        hass.data.setdefault(DOMAIN, {})

    stored_devices = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    addresses = entry.data.get("devices", []) or list(stored_devices.keys())

    if not addresses:
        # _LOGGER.warning("No Laifen devices found. Setup aborted until a device is added.")
        raise ConfigEntryNotReady  

    ble_devices = []
    for addr in addresses:
        device = bluetooth.async_ble_device_from_address(hass, addr.upper(), True)
        if not device:
            _LOGGER.warning(f"[Startup] BLE device {addr} not found (likely sleeping). Will restore passively.")
        ble_devices.append(device)  # allow None to preserve order

    laifens = []
    if entry.entry_id not in hass.data[DOMAIN]:
        hass.data[DOMAIN][entry.entry_id] = {}

    hass.data[DOMAIN]["laifens"] = laifens



    for addr, ble_device in zip(addresses, ble_devices):
        if addr in hass.data[DOMAIN][entry.entry_id]:
            # Device already stored (likely from restore or wakeup)
            stored_data = hass.data[DOMAIN][entry.entry_id][addr]
            if isinstance(stored_data, LaifenData):
                laifen = stored_data.device
                coordinator = stored_data.coordinator
                # _LOGGER.warning(f"Restored Laifen {addr} from previous data.")
            else:
                # _LOGGER.error(f"Invalid stored object for {addr}, skipping...")
                continue
        else:
            # First-time setup for this device
            coordinator = LaifenCoordinator(hass, None, addr)
            laifen = Laifen(ble_device, coordinator) if ble_device else Laifen(MockBLEDevice(addr), coordinator)

            coordinator.laifen = laifen
            hass.data[DOMAIN][entry.entry_id][addr] = LaifenData(entry.title, laifen, coordinator)
            

            if entry.entry_id not in DEVICE_REGISTRY:
                DEVICE_REGISTRY[entry.entry_id] = {}
            DEVICE_REGISTRY[entry.entry_id][addr] = hass.data[DOMAIN][entry.entry_id][addr]

            async_dispatcher_send(hass, f"{DEVICE_SIGNAL}_{entry.entry_id}_{addr}")

            # _LOGGER.warning(f"Initialized new Laifen {addr}.")

        laifens.append(laifen)

        restored = await coordinator._async_restore_data()
        if restored:
            coordinator.data = restored
            coordinator.async_set_updated_data(restored)
            laifen.result = restored 

        if ble_device and await laifen.connect():
            await laifen.start_notifications()
            await coordinator.async_request_refresh()
        elif restored:
            # _LOGGER.warning(f"Device {addr} is sleeping. Restoring last known values.")
            coordinator.data = restored  # ✅ preload cached values
            coordinator.async_set_updated_data(restored)  # ✅ notify entities

        else:
            _LOGGER.warning(f"Device {addr} is unavailable and no cached data found. Entities may stay unavailable.")




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
                lambda service_info, change: asyncio.create_task(_async_update_ble(hass, entry, service_info, change)),
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

    # _LOGGER.warning("Laifen devices successfully disconnected.")

async def _async_update_ble(hass: HomeAssistant, entry: ConfigEntry, service_info, change):
    """Update BLE data."""
    laifens = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("laifens", [])  # ✅ Retrieve laifens from entry
    for laifen in laifens:
        if laifen.ble_device.address == service_info.device.address:
            await laifen.set_ble_device(service_info.device)


async def _async_device_recovery( hass: HomeAssistant, entry: ConfigEntry, service_info):
    """Recover Laifen devices when they wake up via passive Bluetooth events."""

    device_address = service_info.device.address
    # _LOGGER.debug(f"Bluetooth recovery callback fired for {device_address}")

    entry_devices: dict[str, LaifenData] = hass.data[DOMAIN][entry.entry_id]

    if device_address in entry_devices:
        # _LOGGER.warning(f"Laifen {device_address} detected via Bluetooth callback! Restoring connection...")

        laifen_data = entry_devices.get(device_address)
        if not isinstance(laifen_data, LaifenData):
            # _LOGGER.warning(f"Device {device_address} is stored incorrectly. Expected LaifenData but got {type(laifen_data)}.")
            return

        laifen = laifen_data.device
        laifen.coordinator = laifen_data.coordinator  # ✅ ensure linked

        if laifen.client.is_connected:
            # _LOGGER.warning(f"{device_address} is already connected. Skipping recovery.")
            return

        if await laifen.connect():
            await laifen.start_notifications()
            await laifen.coordinator.async_request_refresh()
            # _LOGGER.warning(f"Successfully reconnected to {device_address}.")
        else:
            _LOGGER.warning(f"Failed to reconnect {device_address}. Retrying on next callback event.")
    else:
        # ✅ Do not register new devices here
        _LOGGER.warning(f"Laifen {device_address} detected but not found in registered devices. Skipping recovery.")


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    data_dict = hass.data[DOMAIN].get(entry.entry_id, {})
    for dev_addr, data in data_dict.items():
        if isinstance(data, LaifenData):
            await data.coordinator.async_request_refresh()
        else:
            _LOGGER.warning(f"Skipping refresh for unexpected object {dev_addr}: {type(data)}")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and disconnect all devices."""
    entry_devices = hass.data[DOMAIN].get(entry.entry_id, {})
    
    for addr, data in entry_devices.items():
        if isinstance(data, LaifenData):
            try:
                await data.device.stop_notifications()
                await data.device.disconnect()
                # _LOGGER.debug(f"Disconnected Laifen device {addr}")
            except Exception as e:
                _LOGGER.warning(f"Error disconnecting {addr}: {e}")

    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True

