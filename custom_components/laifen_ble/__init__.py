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

from bleak import BleakError, BleakClient, BleakScanner
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
        self.lock = asyncio.Lock()
        self._first_message = True
        self.device_asleep = False
        self._reconnecting = asyncio.Lock()
        self._store = Store(hass, LAST_KNOWN_STORE_VERSION, LAST_KNOWN_FILENAME)


    async def _async_update_data(self):
        if self.device_asleep:
            _LOGGER.debug(f"{self.device_address} is asleep. Skipping update.")
            restored = await self._async_restore_data()
            self.async_set_updated_data(restored or {})
            return restored or {}
        
        if not self.laifen:
            _LOGGER.debug(f"{self.device_address}: coordinator has no device yet; restoring cached data.")
            restored = await self._async_restore_data()
            self.async_set_updated_data(restored or {})
            return restored or {}


        try:
            # Add timeout for the entire operation
            async with async_timeout.timeout(30):
                await self.laifen.gatherdata()
                
                if self.laifen.result:
                    # Always check connection, even if not Running
                    if not self.laifen.client or not self.laifen.client.is_connected:
                        _LOGGER.debug(f"{self.device_address} appears disconnected — attempting immediate reconnect.")
                        if not await self.laifen._aggressive_reconnect(max_attempts=5):
                            _LOGGER.warning("Reconnection failed for %s, marking asleep", self.device_address)
                            self.device_asleep = True
                            return await self._async_restore_data() or {}

                    await self._async_store_data(self.laifen.result)
                    return self.laifen.result

                else:
                    # No new data - use cached but don't mark as asleep yet
                    cached = await self._async_restore_data()
                    if cached:
                        self.async_set_updated_data(cached)
                        return cached
                    
                    # Only mark as asleep if we have no data at all
                    self.device_asleep = True
                    cached = await self._async_restore_data()
                    self.async_set_updated_data(cached or {})
                    return cached or {}

                    
        except (BleakError, asyncio.TimeoutError) as e:
            _LOGGER.debug(f"BLE error during update: {e}. Will retry before marking asleep.")
            # Don't immediately mark as asleep - will happen automatically if retries fail
            cached = await self._async_restore_data()
            self.async_set_updated_data(cached or {})
            return cached or {}

    async def _async_store_data(self, data: dict):
        all_data = await self._store.async_load() or {}
        all_data[self.device_address] = data
        await self._store.async_save(all_data)

    async def _async_restore_data(self) -> dict | None:
        all_data = await self._store.async_load() or {}
        return all_data.get(self.device_address)

    @callback
    def async_handle_notification(self, data):
        # Keep entities live and also persist for future sleeps/restarts
        self.async_set_updated_data(data)
        # Persist in background
        self.hass.async_create_task(self._async_store_data(data))


    async def async_config_entry_first_refresh(self):
        max_attempts = 10
        attempts = 0
        while attempts < max_attempts:
            try:
                await self.async_refresh()
                break
            except UpdateFailed:
                attempts += 1
                # _LOGGER.debug("Device not reachable, retrying in 30 seconds...")
                await asyncio.sleep(30)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Laifen for multiple devices, restoring stored devices and ensuring passive Bluetooth detection."""

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entry.entry_id, {})  # don't overwrite on reload
    entry_data = hass.data[DOMAIN][entry.entry_id]
    laifens = hass.data[DOMAIN].setdefault("laifens", [])

    stored_devices = entry_data
    addresses = entry.data.get("devices", []) or list(stored_devices.keys())

    if not addresses:
        _LOGGER.warning("No Laifen devices found. Setup aborted until a device is added.")
        raise ConfigEntryNotReady

    ble_devices = []
    for addr in addresses:
        device = bluetooth.async_ble_device_from_address(hass, addr.upper(), True)
        if not device:
            _LOGGER.debug(f"[Startup] BLE device {addr} not found (likely sleeping). Will restore passively.")
        ble_devices.append(device)

    for addr, ble_device in zip(addresses, ble_devices):
        restored = None  # ✅ Always define restored

        if addr in entry_data and isinstance(entry_data[addr], LaifenData):
            # Device already initialized
            laifen = entry_data[addr].device
            coordinator = entry_data[addr].coordinator
            has_cached_data = True  # Because we have previous data for this device
            restored = await coordinator._async_restore_data()
            _LOGGER.debug(f"Restored Laifen {addr} from previous data.")
        else:
            # First time initialization
            coordinator = LaifenCoordinator(hass, None, addr)
            laifen = Laifen(ble_device or MockBLEDevice(addr), coordinator)
            coordinator.laifen = laifen
            entry_data[addr] = LaifenData(entry.title, laifen, coordinator)
            DEVICE_REGISTRY.setdefault(entry.entry_id, {})[addr] = entry_data[addr]
            async_dispatcher_send(hass, f"{DEVICE_SIGNAL}_{entry.entry_id}_{addr}")

            restored = await coordinator._async_restore_data()
            has_cached_data = restored is not None

            if has_cached_data:
                coordinator.data = restored
                coordinator.async_set_updated_data(restored)
                laifen.result = restored
                _LOGGER.warning(f"Restored Laifen {addr} from saved state.")
            else:
                laifen.result = {}
                _LOGGER.warning(f"Initialized new Laifen {addr}.")


        laifens.append(laifen)
        laifen.device_asleep = False


        if ble_device and await laifen.connect():
            await laifen.start_notifications()
            await coordinator.async_request_refresh()
        elif restored:
            # _LOGGER.warning(f"Device {addr} is sleeping. Restoring last known values.")
            coordinator.data = restored
            coordinator.async_set_updated_data(restored)
        else:
            _LOGGER.debug(f"Device {addr} is unavailable and no cached data found. Entities may state unavailable.")

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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )

    return True


async def _async_stop(hass: HomeAssistant, event: Event) -> None:
    """Close all connections on shutdown."""
    laifens = hass.data.get(DOMAIN, {}).get("laifens", [])
    for laifen in laifens:
        await laifen.stop_notifications()
        await laifen.disconnect()

    # _LOGGER.warning("Laifen devices successfully disconnected.")


async def _async_device_recovery(hass: HomeAssistant, entry: ConfigEntry, service_info):
    """Recover Laifen devices when they wake up via passive Bluetooth events."""

    device_address = service_info.device.address
    _LOGGER.debug(f"Bluetooth recovery callback fired for {device_address}")

    entry_devices: dict[str, LaifenData] = hass.data[DOMAIN][entry.entry_id]

    if device_address in entry_devices:
        _LOGGER.debug(f"Laifen {device_address} detected via Bluetooth callback! Restoring connection...")

        laifen_data = entry_devices.get(device_address)
        if not isinstance(laifen_data, LaifenData):
            # _LOGGER.debug(f"Device {device_address} is stored incorrectly. Expected LaifenData but got {type(laifen_data)}.")
            return

        laifen = laifen_data.device
        # laifen.coordinator = laifen_data.coordinator  # ✅ ensure linked
        coordinator = laifen_data.coordinator  # ✅ ensure linked

        if laifen.client and laifen.client.is_connected:
            _LOGGER.debug(f"{device_address} is already connected. Skipping recovery.")
            return

        _LOGGER.debug(f"Old device for {device_address}: {laifen.ble_device}")
        await laifen.set_ble_device(service_info.device)
        _LOGGER.debug(f"Updated device for {device_address}: {laifen.ble_device}")

        # If the passive scan gave an incomplete device, refresh it from a full scan
        if not getattr(service_info.device, "details", None):
            devices = await BleakScanner.discover()
            for dev in devices:
                if dev.address.lower() == device_address.lower():
                    await laifen.set_ble_device(dev)
                    break

        # Force new BleakClient to avoid stale connection object
        laifen.client = BleakClient(laifen.ble_device)

        
        if await laifen.connect():
            await laifen.start_notifications()
            await laifen.coordinator.async_request_refresh()
            _LOGGER.debug(f"Successfully reconnected to {device_address}.")
        else:
            _LOGGER.debug(f"Failed to reconnect {device_address}. Retrying on next callback event.")
    else:
        # ✅ Do not register new devices here
        _LOGGER.debug(f"Laifen {device_address} detected but not found in registered devices. Skipping recovery.")


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    data_dict = hass.data[DOMAIN].get(entry.entry_id, {})
    for dev_addr, data in data_dict.items():
        if isinstance(data, LaifenData):
            await data.coordinator.async_request_refresh()
        else:
            _LOGGER.debug(f"Skipping refresh for unexpected object {dev_addr}: {type(data)}")


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
                _LOGGER.debug(f"Error disconnecting {addr}: {e}")

    hass.data[DOMAIN].pop(entry.entry_id, None)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
