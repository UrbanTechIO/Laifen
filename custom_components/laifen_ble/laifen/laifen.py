import logging
import asyncio
import json
import os
from bleak import BleakError, BleakClient, BleakScanner

_LOGGER = logging.getLogger(__name__)

SERVICE_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
RETRY_LIMIT = 10  # Set a retry limit for connection attempts
STORAGE_PATH = "/config/.storage/laifen_ble_states.json"

class Laifen:
    def __init__(self, ble_device, coordinator):
        self.ble_device = ble_device
        self.has_connected_before = False  # ✅ Tracks connection history
        self.address = ble_device.address
        self.name = ble_device.name or "Laifen"
        self.client = BleakClient(ble_device)
        self.coordinator = coordinator
        self.lock = asyncio.Lock()  # Ensure concurrency safety
        self._first_message = True  # Ignore initial unwanted message
        _LOGGER.warning(f"Laifen instance created for {self.ble_device.address}")

    async def scan_for_devices(self):
        """Scan for Laifen toothbrush devices."""
        # _LOGGER.warning("Scanning for devices...")
        scanner = BleakScanner()
        devices = await scanner.discover()
        found_devices = [device for device in devices if device.name and device.name.startswith("LFTB")]
        if not found_devices:
            # _LOGGER.warning("No Laifen devices found during scan.")
            return None
        _LOGGER.warning(f"Found Laifen devices: {[device.address for device in found_devices]}")
        return found_devices

    async def connect(self):
        """Attempt to connect to the toothbrush device."""
        # _LOGGER.warning(f"Attempting to connect to {self.ble_device.address}...")
        if self.client.is_connected:
            self.has_connected_before = True
            return True

        for attempt in range(RETRY_LIMIT):
            try:
                _LOGGER.warning(f"Connecting attempt {attempt + 1}/{RETRY_LIMIT}...")
                await asyncio.wait_for(self.client.connect(), timeout=60)  # Increase timeout
                if self.client.is_connected:
                    _LOGGER.warning(f"Connected successfully to {self.ble_device.address}")
                    return True
            except BleakError as e:
                _LOGGER.warning(f"Connection failed (attempt {attempt+1}/{RETRY_LIMIT}): {e}")
                await asyncio.sleep(5)  # Wait before retrying

        _LOGGER.warning(f"Unable to connect to {self.ble_device.address} after retries")
        return False

    async def send_command(self, command: bytes):
        """Send a HEX command to the Laifen device."""
        async with self.lock:
            if not self.client.is_connected:
                _LOGGER.warning(f"{self.ble_device.address} not connected. Attempting reconnection...")
                if not await self.connect():
                    _LOGGER.warning(f"Failed to reconnect {self.ble_device.address}, cannot send command.")
                    return False

            try:
                _LOGGER.info(f"Sending command to {self.ble_device.address}: {command.hex()}")
                await self.client.write_gatt_char(CHARACTERISTIC_UUID, command)
                return True
            except BleakError as e:
                _LOGGER.warning(f"Failed to send command to {self.ble_device.address}: {e}")
                return False

    async def turn_on(self):
        """Turn on the Laifen toothbrush."""
        _LOGGER.info(f"Turning on {self.ble_device.address}...")
        return await self.send_command(bytes.fromhex("AA0F010101A4"))

    async def turn_off(self):
        """Turn off the Laifen toothbrush."""
        _LOGGER.info(f"Turning off {self.ble_device.address}...")
        return await self.send_command(bytes.fromhex("AA0F010100A5"))

    async def gatherdata(self):
        _LOGGER.debug(f"Gathering data from {self.ble_device.address}...")

        if not self.client or not self.client.is_connected:
            # _LOGGER.warning(f"[gatherdata] Not connected to {self.ble_device.address}")
            return

        try:
            raw = await self.client.read_gatt_char(CHARACTERISTIC_UUID)
            data_str = raw.hex()
            _LOGGER.debug(f"Data received from {self.ble_device.address}: {data_str}")

            if data_str.startswith("aa0a0215") and len(data_str) >= 50:
                parsed = self.parse_data(raw)
                if parsed:
                    self.result = parsed
                    _LOGGER.warning(f"[gatherdata] Parsed result: {self.result}")
                else:
                    _LOGGER.warning("[gatherdata] Parsed result is None. Keeping previous result.")
            else:
                _LOGGER.warning(f"Ignoring invalid data: {data_str} (length: {len(data_str)}). Expected 52 characters starting with 'aa0a0215'.")

        except Exception as e:
            _LOGGER.warning(f"[gatherdata] Error reading data: {e}")

    async def start_notifications(self):
        """Start BLE notifications for data updates."""
        _LOGGER.warning(f"Starting notifications for {self.ble_device.address}...")

        if not self.client.is_connected:
            _LOGGER.warning(f"Failed to reconnect {self.ble_device.address}, skipping notifications.")
            return

        for attempt in range(5):
            try:
                await self.client.start_notify(CHARACTERISTIC_UUID, self.notification_handler)
                _LOGGER.warning(f"Started notifications for {self.ble_device.address}")
                return
            except BleakError as e:
                if "Notifications are already enabled" in str(e):
                    _LOGGER.warning("Notifications already enabled, skipping")
                    return
                _LOGGER.warning(f"Failed to start notifications (attempt {attempt+1}/5): {e}")
                await asyncio.sleep(1)
        _LOGGER.error(f"Could not start notifications for {self.ble_device.address} after multiple retries.")
        return False

    async def stop_notifications(self):
        """Stop BLE notifications."""
        async with self.lock:
            if not self.client.is_connected:
                _LOGGER.warning(f"Cannot stop notifications; {self.ble_device.address} is not connected.")
                return
            _LOGGER.warning(f"Stopping notifications for {self.ble_device.address}...")
            try:
                await self.client.stop_notify(CHARACTERISTIC_UUID)
                _LOGGER.warning(f"Stopped notifications for {self.ble_device.address}")
            except BleakError as e:
                _LOGGER.warning(f"Failed to stop notifications for {self.ble_device.address}: {e}")

    def notification_handler(self, sender, data):
        """Handle incoming notifications and update Home Assistant entities."""
        _LOGGER.warning(f"Notification received from {sender}: {data}")

        # Convert data to hex string
        data_str = data.hex()

        # Enforce EXACT match to the known valid packet format
        if not data_str.startswith("aa0a0215") or len(data_str) != 52:
            _LOGGER.warning(f"Ignoring invalid data: {data_str} (length: {len(data_str)}). Expected exactly 52 characters starting with 'aa0a0215'.")
            return


        # Proceed with parsing valid data
        parsed_result = self.parse_data(data)

        # If valid data, update result and pass it to the coordinator
        self.result = parsed_result
        _LOGGER.warning(f"Parsed result: {self.result}")
        self.coordinator.async_set_updated_data(self.result)


    def parse_data(self, data):
        """Parse BLE data into structured sensor attributes."""
        if data is None:
            _LOGGER.warning(f"Received data is too short for parsing: {data.hex() if data else 'None'}")
            return {
                "raw_data": data.hex() if data else "",
                "status": "Unknown",
                "vibration_strength": 0,
                "oscillation_range": 0,
                "oscillation_speed": 0,
                "mode": "Unknown",
                "battery_level": 0,
                "brushing_time": 0,
            }

        data_str = data.hex()


        try:
            return { 
                "raw_data": data_str,
                "status": "Running" if data_str[47] == "1" else "Idle",
                "mode": str(int(data_str[9], 16) + 1),
                "battery_level": int(data_str[36:38], 16) if data_str[36:38].isalnum() else 0,
                "brushing_time": int(data_str[40:44], 16) / 60 if data_str[40:44].isalnum() else 0,
                "vibration_strength": int(data_str[10 + (int(data_str[9], 16) * 6):12 + (int(data_str[9], 16) * 6)], 16),
                "oscillation_range": int(data_str[12 + (int(data_str[9], 16) * 6):14 + (int(data_str[9], 16) * 6)], 16),
                "oscillation_speed": int(data_str[14 + (int(data_str[9], 16) * 6):16 + (int(data_str[9], 16) * 6)], 16),
            }

        except Exception as e:
            _LOGGER.error(f"Unexpected error while parsing data: {e}")
            return {key: 0 if key != "raw_data" else data_str for key in ["raw_data", "status", "mode", "battery_level", "brushing_time", "vibration_strength", "oscillation_range", "oscillation_speed"]}
        
        # return parsed_result  # ✅ Returns the full dictionary
            
    async def set_ble_device(self, ble_device):
        """Set Bluetooth device and reconnect."""
        _LOGGER.warning(f"Setting BLE device: {ble_device.address}")
        self.ble_device = ble_device
        self.client = BleakClient(ble_device)
        if await self.connect():
            await self.start_notifications()
            await self.check_connection()
        else:
            _LOGGER.warning(f"Device {self.ble_device.address} not connectable — starting availability monitoring")
            asyncio.create_task(self.monitor_device_availability())

    async def check_connection(self):
        """Ensure continuous connection after sleep or sudden disconnect."""
        async with self.lock:
            # _LOGGER.warning(f"Checking connection status for {self.ble_device.address}...")
            if self.client.is_connected:
                _LOGGER.warning(f"{self.ble_device.address} is already connected. Skipping reconnection.")
                return  # ✅ Skip unnecessary reconnect attempts


            # _LOGGER.warning(f"{self.ble_device.address} is disconnected. Starting availability monitoring...")
            asyncio.create_task(self.monitor_device_availability())

            # _LOGGER.warning(f"{self.ble_device.address} is disconnected. Attempting to reconnect...")
            if await self.scan_for_devices():
                await self.connect()
                await asyncio.sleep(2)  # ✅ Allow connection stabilization

                if self.client.is_connected:
                    await self.start_notifications()  # ✅ Ensures notifications only start after stable connection


    async def monitor_device_availability(self):
        """Continuously scan for the Laifen device and reconnect when found."""
        # _LOGGER.warning(f"Monitoring availability of {self.ble_device.address}...")
        while True:
            found_devices = await self.scan_for_devices()       
            if found_devices:
                matching_device = next((dev for dev in found_devices if dev.address == self.ble_device.address), None)
                if matching_device:
                    _LOGGER.warning(f"{self.ble_device.address} found! Attempting reconnection...")
                    await self.set_ble_device(matching_device)  # ✅ Set device & connect
                    return  # ✅ Stop scanning once found
            
            # _LOGGER.warning(f"{self.ble_device.address} not found, retrying in 5 seconds...")
            for delay in [5, 10, 15, 30, 60]:
                await asyncio.sleep(delay)
                found_devices = await self.scan_for_devices()
                if found_devices:
                    matching_device = next((dev for dev in found_devices if dev.address == self.ble_device.address), None)
                    if matching_device:
                        _LOGGER.warning(f"{self.ble_device.address} found! Attempting reconnection...")
                        await self.set_ble_device(matching_device)
                        return
                # _LOGGER.warning(f"{self.ble_device.address} not found, retrying in {delay} seconds...")
    
    async def disconnect(self):
        """Safely disconnect the Laifen BLE device and clean up Bluetooth resources."""
        if self.client.is_connected:
            _LOGGER.warning(f"Disconnecting Laifen device {self.ble_device.address}...")
            try:
                await self.client.disconnect()
                _LOGGER.warning(f"Laifen device {self.ble_device.address} disconnected successfully.")
            except BleakError as e:
                _LOGGER.warning(f"Error disconnecting {self.ble_device.address}: {e}")

        # ✅ Ensure cleanup of the BLE client before Home Assistant shutdown
        self.client = None
