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
        # async with self.lock:
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
        # async with self.lock:
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
                "vibration_strength": None,
                "oscillation_range": None,
                "oscillation_speed": None,
                "mode": None,
                "timer": None,
            }
        data_str = data.hex()
        if len(data_str) < 32:  # Ensure the string is long enough
            _LOGGER.error("Data string is too short")
            return {
                "raw_data": data_str,
                "status": None,
                "vibration_strength": None,
                "oscillation_range": None,
                "oscillation_speed": None,
                "mode": None,
                "timer": None,
            }
        status = data_str[47]
        mode = data_str[9]
        vibration_strength = data_str[11]
        oscillation_range = data_str[13]
        oscillation_speed = data_str[15]
        return {
            "raw_data": data_str,
            "status": status,
            "mode": mode,
            "vibration_strength": vibration_strength,
            "oscillation_range": oscillation_range,
            "oscillation_speed": oscillation_speed,
            "timer": status,
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
