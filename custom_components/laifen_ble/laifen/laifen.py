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
                if "No backend with an available connection slot" in str(e):
                    _LOGGER.error("No available connection slot. Retrying...")
                if attempt == attempts - 1:
                    raise
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
        if self._first_message:
            _LOGGER.warning("Ignoring first message (subscription confirmation)")
            self._first_message = False
            return

        parsed_result = self.parse_data(data)
        if not parsed_result["raw_data"].startswith("01020304050607"):
            self.result = parsed_result
            _LOGGER.warning(f"Parsed result: {self.result}")
            self.coordinator.async_handle_notification(self.result)  # Update coordinator
        else:
            _LOGGER.warning("Ignoring invalid data")

    def parse_data(self, data):
        if data is None:
            _LOGGER.warning("No Data Received")
            return {
                "raw_data": "",
                "status": None,
                "vibration_strength_mode_1": None,
                "oscillation_range_mode_1": None,
                "oscillation_speed_mode_1": None,
                "vibration_strength_mode_2": None,
                "oscillation_range_mode_2": None,
                "oscillation_speed_mode_2": None,
                "vibration_strength_mode_3": None,
                "oscillation_range_mode_3": None,
                "oscillation_speed_mode_3": None,
                "vibration_strength_mode_4": None,
                "oscillation_range_mode_4": None,
                "oscillation_speed_mode_4": None,
                "mode": None,
                "battery_level": None,
                "brushing_timer": None,
                "timer": None,
            }

        data_str = data.hex()
        if len(data_str) < 32:  # Ensure the string is long enough
            _LOGGER.error("Data string is too short")
            return {
                "raw_data": data_str,
                "status": None,
                "vibration_strength_mode_1": None,
                "oscillation_range_mode_1": None,
                "oscillation_speed_mode_1": None,
                "vibration_strength_mode_2": None,
                "oscillation_range_mode_2": None,
                "oscillation_speed_mode_2": None,
                "vibration_strength_mode_3": None,
                "oscillation_range_mode_3": None,
                "oscillation_speed_mode_3": None,
                "vibration_strength_mode_4": None,
                "oscillation_range_mode_4": None,
                "oscillation_speed_mode_4": None,
                "mode": None,
                "battery_level": None,
                "brushing_timer": None,
                "timer": None,
            }

        # Parse the data string
        status = "Running" if data_str[47] == "1" else "Idle"  # Byte 47 for status
        mode = str(int(data_str[9], 16) + 1)  # Byte 9 for mode (add 1 for human-readable mode)
        battery_level = int(data_str[36:38], 16)  # Bytes 35-36 for battery percentage
        brushing_timer = int(data_str[40:44], 16)  # Bytes 35-36 for battery percentage

        # Mode 1
        vibration_strength_mode_1 = int(data_str[10:12], 16)  # Bytes 10-11 (hex to decimal)
        oscillation_range_mode_1 = int(data_str[12:14], 16)  # Bytes 12-13 (hex to decimal)
        oscillation_speed_mode_1 = int(data_str[14:16], 16)  # Bytes 14-15 (hex to decimal)

        # Mode 2
        vibration_strength_mode_2 = int(data_str[16:18], 16)  # Bytes 16-17 (hex to decimal)
        oscillation_range_mode_2 = int(data_str[18:20], 16)  # Bytes 18-19 (hex to decimal)
        oscillation_speed_mode_2 = int(data_str[20:22], 16)  # Bytes 20-21 (hex to decimal)

        # Mode 3
        vibration_strength_mode_3 = int(data_str[22:24], 16)  # Bytes 22-23 (hex to decimal)
        oscillation_range_mode_3 = int(data_str[24:26], 16)  # Bytes 24-25 (hex to decimal)
        oscillation_speed_mode_3 = int(data_str[26:28], 16)  # Bytes 26-27 (hex to decimal)

        # Mode 4
        vibration_strength_mode_4 = int(data_str[28:30], 16)  # Bytes 28-29 (hex to decimal)
        oscillation_range_mode_4 = int(data_str[30:32], 16)  # Bytes 30-31 (hex to decimal)
        oscillation_speed_mode_4 = int(data_str[32:34], 16)  # Bytes 32-33 (hex to decimal)

        return {
            "raw_data": data_str,
            "status": status,
            "vibration_strength_mode_1": vibration_strength_mode_1,
            "oscillation_range_mode_1": oscillation_range_mode_1,
            "oscillation_speed_mode_1": oscillation_speed_mode_1,
            "vibration_strength_mode_2": vibration_strength_mode_2,
            "oscillation_range_mode_2": oscillation_range_mode_2,
            "oscillation_speed_mode_2": oscillation_speed_mode_2,
            "vibration_strength_mode_3": vibration_strength_mode_3,
            "oscillation_range_mode_3": oscillation_range_mode_3,
            "oscillation_speed_mode_3": oscillation_speed_mode_3,
            "vibration_strength_mode_4": vibration_strength_mode_4,
            "oscillation_range_mode_4": oscillation_range_mode_4,
            "oscillation_speed_mode_4": oscillation_speed_mode_4,
            "mode": mode,
            "battery_level": battery_level,
            "brushing_timer": brushing_timer,
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
