import logging
import asyncio
import async_timeout
from bleak import BleakError, BleakClient, BleakScanner

_LOGGER = logging.getLogger(__name__)

SERVICE_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
RETRY_LIMIT = 10  # Set a retry limit for connection attempts


class Laifen:
    def __init__(self, ble_device, coordinator):
        self.ble_device = ble_device
        self.has_connected_before = False  # ✅ Tracks connection history
        self.client = BleakClient(ble_device)
        self.coordinator = coordinator
        self.result = None
        self.lock = asyncio.Lock()  # Ensure concurrency safety
        self._first_message = True  # Ignore initial unwanted message
        _LOGGER.warning(f"Laifen instance created for {self.ble_device.address}")

    async def scan_for_devices(self):
        """Scan for Laifen toothbrush devices."""
        _LOGGER.warning("Scanning for devices...")
        scanner = BleakScanner()
        devices = await scanner.discover()
        found_devices = [device for device in devices if device.name and device.name.startswith("LFTB")]

        if not found_devices:
            _LOGGER.warning("No Laifen devices found during scan.")
            return None

        _LOGGER.warning(f"Found Laifen devices: {[device.address for device in found_devices]}")
        return found_devices

    async def connect(self):
        """Attempt to connect to the toothbrush device."""
        # _LOGGER.warning(f"Attempting to connect to {self.ble_device.address}...")
        if self.client.is_connected:
            self.has_connected_before = True
            return True
        #     _LOGGER.warning(f"{self.ble_device.address} appears stuck in a connected state. Disconnecting first...")
        #     await self.client.disconnect()
        #     await asyncio.sleep(2)  # ✅ Small delay before retrying connection

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
        """Retrieve sensor data from the toothbrush."""
        _LOGGER.warning(f"Gathering data from {self.ble_device.address}...")
        
        if not self.client.is_connected:
            _LOGGER.warning(f"{self.ble_device.address} disconnected, attempting to reconnect...")
            if not await self.connect():
                _LOGGER.error(f"Failed to reconnect to {self.ble_device.address}, aborting data gathering.")
                return
            await self.start_notifications()

        try:
            async with async_timeout.timeout(20):
                data = await self.client.read_gatt_char(CHARACTERISTIC_UUID)
                if data:
                    _LOGGER.warning(f"Data received from {self.ble_device.address}: {data.hex()}")  # ✅ Debug log
                    parsed_result = self.parse_data(data)
                    if parsed_result.get("status") is not None:
                        _LOGGER.warning(f"Parsed result: {parsed_result}")  # ✅ Debug log for verification
                        self.result = parsed_result
                        self.coordinator.async_handle_notification(self.result) # Update coordinator
                else:
                    _LOGGER.warning(f"No data received from {self.ble_device.address}. Retrying...")
                    await asyncio.sleep(2)
                    await self.gatherdata()  # ✅ Recursive retry
        except (BleakError, asyncio.TimeoutError) as e:
            _LOGGER.error(f"Failed to gather data from {self.ble_device.address}: {e}")

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
        """Handle incoming notifications."""
        _LOGGER.warning(f"Notification received from {sender}: {data}")

        # Convert data to hex string
        data_str = data.hex()

        # Check if the data starts with 'AA0A0215' and is exactly 52 characters long
        if not data_str.startswith("aa0a0215") or len(data_str) < 50:
            _LOGGER.warning(f"Ignoring invalid data: {data_str} (length: {len(data_str)}). Expected 52 characters starting with 'aa0a0215'.")
            return

        # Proceed with parsing valid data
        parsed_result = self.parse_data(data)

        # If valid data, update result and pass it to the coordinator
        self.result = parsed_result
        _LOGGER.warning(f"Parsed result: {self.result}")
        self.coordinator.async_handle_notification(self.result)

    def parse_data(self, data):
        if data is None:
            _LOGGER.warning("No Data Received")
            return {
                "raw_data": "",
                "status": None,
                "vibration_strength": None,
                "oscillation_range": None,
                "oscillation_speed": None,
                "mode": None,
                "battery_level": None,
                "brushing_time": None,
                # "timer": None,
            }
    
        data_str = data.hex()
        
        # Continue parsing if the data is valid (already filtered outside this function)
        try:
            status = "Running" if data_str[47] == "1" else "Idle"  # Byte 47 for status
            mode = str(int(data_str[9], 16) + 1)  # Byte 9 for mode (add 1 for human-readable mode)
            battery_level = int(data_str[36:38], 16)  # Bytes 36-37 for battery level (hex to decimal)
            brushing_time = int(data_str[40:44], 16) / 60  # Convert seconds to minutes, Bytes 40-43 for brushing timer (hex to decimal)
    
            # Extract values for the current mode
            mode_index = int(data_str[9], 16)  # Byte 9 for mode index (0-based)
            vibration_strength = int(data_str[10 + (mode_index * 6):12 + (mode_index * 6)], 16)  # Vibration strength for current mode
            oscillation_range = int(data_str[12 + (mode_index * 6):14 + (mode_index * 6)], 16)  # Oscillation range for current mode
            oscillation_speed = int(data_str[14 + (mode_index * 6):16 + (mode_index * 6)], 16)  # Oscillation speed for current mode
        except Exception as e:
            _LOGGER.error(f"Error parsing data: {e}")
            return {
                "raw_data": data_str,
                "status": None,
                "vibration_strength": None,
                "oscillation_range": None,
                "oscillation_speed": None,
                "mode": None,
                "battery_level": None,
                "brushing_time": None,
                # "timer": None,
            }
    
        return {
            "raw_data": data_str,
            "status": status,
            "vibration_strength": vibration_strength,
            "oscillation_range": oscillation_range,
            "oscillation_speed": oscillation_speed,
            "mode": mode,
            "battery_level": battery_level,
            "brushing_time": brushing_time,
            # "timer": "",  # Timer is derived from status in this example
        }

    async def set_ble_device(self, ble_device):
        """Set Bluetooth device and reconnect."""
        _LOGGER.warning(f"Setting BLE device: {ble_device.address}")
        self.ble_device = ble_device
        self.client = BleakClient(ble_device)
        if await self.connect():
            await self.start_notifications()
        # await self.connect()

    async def check_connection(self):
        """Ensure continuous connection after sleep or sudden disconnect."""
        async with self.lock:
            _LOGGER.warning(f"Checking connection status for {self.ble_device.address}...")

            if self.client.is_connected:
                _LOGGER.warning(f"{self.ble_device.address} is already connected. Skipping reconnection.")
                return  # ✅ Skip unnecessary reconnect attempts

            # if self.client.is_connecting:
            #     _LOGGER.warning(f"{self.ble_device.address} is already attempting to connect. Waiting...")
            #     return  # ✅ Avoid interfering with an ongoing connection attempt

            _LOGGER.warning(f"{self.ble_device.address} is disconnected. Starting availability monitoring...")
            await self.monitor_device_availability()  # ✅ Start continuous scan

            _LOGGER.warning(f"{self.ble_device.address} is disconnected. Attempting to reconnect...")
            if await self.scan_for_device():
                await self.connect()
                await asyncio.sleep(2)  # ✅ Allow connection stabilization

                if self.client.is_connected:
                    await self.start_notifications()  # ✅ Ensures notifications only start after stable connection


    async def monitor_device_availability(self):
        """Continuously scan for the Laifen device and reconnect when found."""
        _LOGGER.warning(f"Monitoring availability of {self.ble_device.address}...")

        while True:
            found_devices = await self.scan_for_devices()
            
            if found_devices:
                matching_device = next((dev for dev in found_devices if dev.address == self.ble_device.address), None)
                if matching_device:
                    _LOGGER.warning(f"{self.ble_device.address} found! Attempting reconnection...")
                    await self.set_ble_device(matching_device)  # ✅ Set device & connect
                    return  # ✅ Stop scanning once found
            
            _LOGGER.warning(f"{self.ble_device.address} not found, retrying in 5 seconds...")
            await asyncio.sleep(5)  # ✅ Avoid excessive scanning loops

    
    async def disconnect(self):
        """Safely disconnect the Laifen BLE device."""
        if self.client.is_connected:
            await self.client.disconnect()
            _LOGGER.warning(f"Laifen device {self.ble_device.address} disconnected successfully.")