import logging
import asyncio
import async_timeout
from bleak import BleakError, BleakClient, BleakScanner

_LOGGER = logging.getLogger(__name__)

SERVICE_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
RETRY_LIMIT = 5  # Set a retry limit for connection attempts


class Laifen:
    def __init__(self, ble_device, coordinator):
        self.ble_device = ble_device
        self.client = BleakClient(ble_device)
        self.coordinator = coordinator
        self.result = None
        self.lock = asyncio.Lock()  # Create a lock
        self._first_message = True  # Flag to ignore the first message
        _LOGGER.warning("Laifen instance created")

    async def scan_for_device(self):
        _LOGGER.warning("Scanning for devices...")
        scanner = BleakScanner()
        devices = await scanner.discover()
        for device in devices:
            _LOGGER.warning(f"Found device: {device.name}")
            if device.name and device.name.startswith("LFTB"):
                _LOGGER.warning("Found Laifen Device")
                self.ble_device = device
                self.client = BleakClient(device)
                return True
        return False

    async def connect(self):
        _LOGGER.warning("Attempting to connect...")
        if self.client.is_connected:
            _LOGGER.warning("Already connected to Laifen brush")
            return True
        attempts = 5
        for attempt in range(attempts):
            try:
                _LOGGER.warning("Connecting to Laifen brush...")
                await self.client.connect()
                await asyncio.sleep(2)  # Give some time to establish connection
                if self.client.is_connected:
                    _LOGGER.warning("Connected to Laifen brush")
                    return True
            except BleakError as e:
                _LOGGER.error(f"Failed to connect (attempt {attempt+1}/{attempts}): {e}")
                if "ATT error: 0x0e" in str(e):
                    _LOGGER.error("Encountered ATT error: 0x0e (Unlikely Error)")
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(3)  # Wait a bit before retrying
        _LOGGER.error("Unable to connect to Laifen brush after retries")
        return False

    async def send_command(self, command: bytes):
        """Send a HEX command to the Laifen device."""
        async with self.lock:
            if not self.client.is_connected:
                _LOGGER.warning("Device not connected. Attempting to reconnect...")
                if not await self.connect():
                    _LOGGER.error("Failed to reconnect, cannot send command.")
                    return False
            try:
                _LOGGER.info(f"Sending command: {command.hex()}")
                await self.client.write_gatt_char(CHARACTERISTIC_UUID, command)
                return True
            except BleakError as e:
                _LOGGER.error(f"Failed to send command: {e}")
                return False

    async def turn_on(self):
        """Turns the Laifen device on."""
        _LOGGER.info("Turning on the Laifen device...")
        return await self.send_command(bytes.fromhex("AA0F010101A4"))  # Example command for turning on

    async def turn_off(self):
        """Turns the Laifen device off."""
        _LOGGER.info("Turning off the Laifen device...")
        return await self.send_command(bytes.fromhex("AA0F010100A5"))  # Example command for turning off

    async def gatherdata(self):
        async with self.lock:
            _LOGGER.warning("Gathering data...")
            try:
                if not self.client.is_connected:
                    await self.connect()
                await self.start_notifications()
                async with async_timeout.timeout(10):  # Set a timeout of 10 seconds
                    data = await self.client.read_gatt_char(CHARACTERISTIC_UUID)
                    _LOGGER.warning(f"Gather data: {data}")
                    parsed_result = self.parse_data(data)
                    if not parsed_result["raw_data"].startswith("01020304050607"):
                        self.result = parsed_result
                        _LOGGER.warning(f"Parsed result: {self.result}")
                        self.coordinator.async_handle_notification(self.result)  # Update coordinator
            except (BleakError, asyncio.TimeoutError) as e:
                _LOGGER.error(f"Failed to gather data: {e}")
                self.result = self.result

    async def start_notifications(self):
        _LOGGER.warning("Starting notifications...")
        if not self.client.is_connected:
            _LOGGER.warning("Device not connected, scanning for Laifen brush...")
            if not await self.check_connection():
                _LOGGER.error("Laifen brush not found")
                return
        attempts = 5
        for attempt in range(attempts):
            try:
                await self.client.start_notify(CHARACTERISTIC_UUID, self.notification_handler)
                _LOGGER.warning("Started notifications")
                return
            except BleakError as e:
                if "Notifications are already enabled" in str(e):
                    _LOGGER.warning("Notifications are already enabled, skipping")
                    return
                _LOGGER.error(f"Failed to start notifications (attempt {attempt+1}/{attempts}): {e}")
                await asyncio.sleep(1)  # Wait a bit before retrying

    async def stop_notifications(self):
        async with self.lock:
            _LOGGER.warning("Stopping notifications...")
            try:
                await self.client.stop_notify(CHARACTERISTIC_UUID)
                _LOGGER.warning("Stopped notifications")
            except BleakError as e:
                _LOGGER.warning(f"Failed to stop notifications: {e}")
                raise

    def notification_handler(self, sender, data):
        _LOGGER.warning(f"Notification received from {sender}: {data}")
    
        # Convert data to hex string
        data_str = data.hex()
        
        # Check if the data starts with 'AA0A0215' and is exactly 52 characters long
        if not data_str.startswith("aa0a0215") or len(data_str) != 52:
            _LOGGER.warning(f"Ignoring invalid data: {data_str} (length: {len(data_str)}). Expected 52 characters starting with 'aa0a0215'.")
            return
    
        # Proceed with parsing valid data
        parsed_result = self.parse_data(data)
        
        # If valid data, update result and pass it to the coordinator
        self.result = parsed_result
        _LOGGER.warning(f"Parsed result: {self.result}")
        self.coordinator.async_handle_notification(self.result)  # Update coordinator

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
                "timer": None,
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
                "timer": None,
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
            "timer": status,  # Timer is derived from status in this example
        }


    async def set_ble_device(self, ble_device):
        _LOGGER.warning(f"Setting BLE device: {ble_device}")
        self.ble_device = ble_device
        self.client = BleakClient(ble_device)
        await self.connect()

    async def check_connection(self):
        async with self.lock:
            _LOGGER.warning("Checking connection...")
            if not self.client.is_connected:
                _LOGGER.warning("Device is not connected, attempting to reconnect...")
                if await self.scan_for_device():
                    _LOGGER.warning("Scan Returned Positive")
                    await self.connect()
                    _LOGGER.warning("Connected successfully, starting notifications...")
                    await self.start_notifications()
                    return True
            else:
                _LOGGER.warning("Device is already connected")
            return True
