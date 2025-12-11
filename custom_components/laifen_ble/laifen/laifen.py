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
        self.result = {}
        self.coordinator = coordinator
        self.lock = asyncio.Lock()  # Ensure concurrency safety
        self._first_message = True  # Ignore initial unwanted message
        self._reconnecting = asyncio.Lock()
        # _LOGGER.debug(f"Laifen instance created for {self.ble_device.address}")

    async def scan_for_devices(self):
        """Scan for Laifen toothbrush devices."""
        # _LOGGER.debug("Scanning for devices...")
        scanner = BleakScanner()
        devices = await scanner.discover()
        found_devices = [device for device in devices if device.name and (device.name.startswith("LFTB") or device.name.startswith("Laifen Toothbrush"))]
        if not found_devices:
            # _LOGGER.debug("No Laifen devices found during scan.")
            return None
        # _LOGGER.debug(f"Found Laifen devices: {[device.address for device in found_devices]}")
        return found_devices


    async def connect(self):
        if not self.client:
            self.client = BleakClient(self.ble_device)
            
        if self.client.is_connected:
            _LOGGER.debug(f"{self.ble_device.address} Already Connected!")
            return True

        max_attempts = 10
        for attempt in range(1, max_attempts + 1):
            try:
                if attempt == 1:
                    _LOGGER.debug(f"Starting connection attempts to {self.ble_device.address}")
                await asyncio.wait_for(self.client.connect(), timeout=10)
                await asyncio.sleep(2)

                # ✅ Log all characteristics for debug purposes
                # services = await self.client.get_services()
                # for service in services:
                #     _LOGGER.warning(f"Service {service.uuid}: {service.description}")
                #     for char in service.characteristics:
                #         _LOGGER.warning(
                #             f"  Characteristic {char.uuid} | Properties: {char.properties} | Handle: {char.handle}"
                #         )

                if self.client.is_connected:
                    self.client.set_disconnected_callback(self._handle_disconnect)
                    self.coordinator.device_asleep = False
                    # _LOGGER.warning(f"Connected to {self.ble_device.address}")
                    return True
            except asyncio.CancelledError:
                if self.coordinator:
                    _LOGGER.debug(f"Connection to {self.ble_device.address} was cancelled. Marking asleep.")
                    self.coordinator.device_asleep = True
                return False
            except (BleakError, asyncio.TimeoutError, TimeoutError) as e:
                _LOGGER.debug(f"Connection attempt {attempt} failed: {e}")
            await asyncio.sleep(2)

        _LOGGER.warning(f"Failed to connect to {self.ble_device.address} after {max_attempts} attempts. Marking asleep.")
        if self.coordinator:
            self.coordinator.device_asleep = True
        return False


    async def send_command(self, command: bytes):
        """Send a HEX command to the Laifen device."""
        async with self.lock:
            if self.client and self.client.is_connected:
                try:
                    # _LOGGER.debug(f"Sending command to {self.ble_device.address}: {command.hex()}")
                    await self.client.write_gatt_char(CHARACTERISTIC_UUID, command)
                    return True
                except BleakError as e:
                    # _LOGGER.warning(f"Failed to send command to {self.ble_device.address}: {e}")
                    return False

    async def turn_on(self):
        """Turn on the Laifen toothbrush."""
        _LOGGER.debug(f"Turning on {self.ble_device.address}...")
        return await self.send_command(bytes.fromhex("AA0F010101A4"))

    async def turn_off(self):
        """Turn off the Laifen toothbrush."""
        _LOGGER.debug(f"Turning off {self.ble_device.address}...")
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

            if data_str.startswith("aa0a021") and len(data_str) >= 50:
                parsed = self.parse_data(raw)
                if parsed:
                    self.result = parsed
                    # _LOGGER.warning(f"[gatherdata] Parsed result: {self.result}")
                    return
            else:
                _LOGGER.debug(f"Ignoring invalid data: {data_str} (length: {len(data_str)}). Expected 52 characters starting with 'aa0a021'.")
                
        except Exception as e:
            _LOGGER.debug(f"[gatherdata] Error reading data: {e}")
        
        # if self.result:
        #     _LOGGER.warning("Using existing result from notification.")
        # else:
        #     _LOGGER.warning("No result available from read or notification.")

    async def start_notifications(self):
        """Start BLE notifications for data updates."""
        # _LOGGER.warning(f"Starting notifications for {self.ble_device.address}...")

        if not self.client or not self.client.is_connected:
            # _LOGGER.warning(f"Cannot start notifications: {self.ble_device.address} is not connected.")
            # if self.coordinator:
            #     self.coordinator.device_asleep = True
            # return False
            return
        
        # def handle_disconnect(client):
        #     # _LOGGER.warning(f"{self.ble_device.address} disconnected unexpectedly.")
        #     if self.coordinator:
        #         self.coordinator.device_asleep = True
        #         # Schedule refresh to update HA with fallback data
        #         asyncio.create_task(self.coordinator.async_request_refresh())

        # self.client.set_disconnected_callback(self._handle_disconnect)

        for attempt in range(5):
            try:
                await self.client.start_notify(CHARACTERISTIC_UUID, self.notification_handler)
                # _LOGGER.warning(f"Started notifications for {self.ble_device.address}")
                return
            except BleakError as e:
                if "Notifications are already enabled" in str(e):
                    # _LOGGER.warning("Notifications already enabled, skipping")
                    # return True
                    return
                # _LOGGER.warning(f"Failed to start notifications (attempt {attempt+1}/5): {e}")
                await asyncio.sleep(1)

        _LOGGER.debug(f"Could not start notifications for {self.ble_device.address} after multiple retries.")

        # Fallback — treat device as asleep and defer to recovery next time
        if self.coordinator:
            self.coordinator.device_asleep = True
        return False

    async def stop_notifications(self):
        """Stop BLE notifications."""
        async with self.lock:
            if not self.client or not self.client.is_connected:
                # _LOGGER.warning(f"Cannot stop notifications; {self.ble_device.address} is not connected.")
                return
            # _LOGGER.warning(f"Stopping notifications for {self.ble_device.address}...")
            try:
                await self.client.stop_notify(CHARACTERISTIC_UUID)
                # _LOGGER.warning(f"Stopped notifications for {self.ble_device.address}")
            except BleakError as e:
                return
                # _LOGGER.warning(f"Failed to stop notifications for {self.ble_device.address}: {e}")

    def notification_handler(self, sender, data):
        if not self.coordinator:
            _LOGGER.debug("⚠️ self.coordinator is not assigned — cannot update HA entities!")
            return
        # else:
            # _LOGGER.debug(f"✅ Coordinator is assigned: {self.coordinator}")

            
        """Handle incoming notifications and update Home Assistant entities."""
        # _LOGGER.warning(f"Notification received from {sender}: {data}")

        # Convert data to hex string
        data_str = data.hex()
        # _LOGGER.warning(f"Converted Data to Hex from {sender}: {data_str}")

        # Enforce EXACT match to the known valid packet format
        if data_str.startswith("aa0a021") and len(data_str) >= 50:
            # _LOGGER.warning("Data is valid, Continue")
            # Proceed with parsing valid data
            parsed_result = self.parse_data(data)
            # If valid data, update result and pass it to the coordinator
            self.result = parsed_result
            # _LOGGER.warning(f"Parsed result: {self.result}")
            self.coordinator.device_asleep = False
            self.coordinator.async_set_updated_data(self.result)


        else:
            # _LOGGER.warning(f"No valid data: {data_str} (length: {len(data_str)}). Expected exactly 52 characters starting with 'aa0a021'. Continue Blocked")
            return


    def parse_data(self, data):
        """Parse BLE data into structured sensor attributes."""
        if data is None:
            # _LOGGER.warning(f"Received data is too short for parsing: {data.hex() if data else 'None'}")
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
            _LOGGER.debug(f"Unexpected error while parsing data: {e}")
            return {key: 0 if key != "raw_data" else data_str for key in ["raw_data", "status", "mode", "battery_level", "brushing_time", "vibration_strength", "oscillation_range", "oscillation_speed"]}
        
        # return parsed_result  # ✅ Returns the full dictionary
            
    async def set_ble_device(self, ble_device):
        """Forcefully set Bluetooth device and create a fresh client."""
        if self.client and self.client.is_connected:
            await self.client.disconnect()

        self.ble_device = ble_device
        self.address = ble_device.address
        self.client = BleakClient(self.ble_device)  # ✅ Always reset client
        self.client.set_disconnected_callback(self._handle_disconnect)

    
    async def disconnect(self):
        """Safely disconnect with better resource cleanup"""
        if self.client and self.client.is_connected:
            try:
                await self.stop_notifications()
                await self.client.disconnect()
                _LOGGER.debug(f"Disconnected {self.ble_device.address} cleanly")
            except BleakError as e:
                _LOGGER.debug(f"Error during disconnect: {e}")
            finally:
                self.client = None  # Ensure cleanup


    def _handle_disconnect(self, client):
        _LOGGER.debug(f"{self.ble_device.address} disconnected.")
        if self.coordinator:
            last_status = self.result.get("status", "Unknown")

            _LOGGER.debug(f"{self.ble_device.address} disconnected — will attempt reconnection.")
            self.coordinator.device_asleep = False
            asyncio.create_task(self._aggressive_reconnect())



    async def _aggressive_reconnect(self, max_attempts=10, initial_delay=1):
        async with self._reconnecting:  # ✅ Prevent overlapping loops
            attempt = 0
            while attempt < max_attempts:
                try:
                    if not self.client or not self.client.is_connected:
                        devices = await BleakScanner.discover()
                        for dev in devices:
                            if dev.address.lower() == self.address.lower():
                                await self.set_ble_device(dev)
                                break

                        await asyncio.sleep(initial_delay)
                        _LOGGER.debug(f"Reconnect attempt {attempt + 1}/{max_attempts} for {self.address}")

                        if not self.client:
                            self.client = BleakClient(self.ble_device)

                        if await self.connect():
                            await self.start_notifications()
                            await self.gatherdata()
                            _LOGGER.debug(f"Reconnected to {self.address}")
                            return True
                except Exception as e:
                    _LOGGER.debug(f"Reconnect attempt {attempt + 1} failed: {e}")
                attempt += 1

            cached = await self.coordinator._async_restore_data()
            self.coordinator.device_asleep = True
            self.coordinator.async_set_updated_data(cached or {})
            return False


