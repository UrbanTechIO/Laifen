import logging
import asyncio  # Import asyncio
import async_timeout
from bleak import BleakError, BleakClient

_LOGGER = logging.getLogger(__name__)

SERVICE_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"

class Laifen:
    def __init__(self, ble_device, coordinator):
        self.ble_device = ble_device
        self.client = BleakClient(ble_device)
        self.coordinator = coordinator
        self.result = None

    async def connect(self):
        try:
            _LOGGER.debug("Connecting to Laifen brush...")
            await self.client.connect()
            _LOGGER.debug("Connected to Laifen brush")
        except BleakError as e:
            _LOGGER.error(f"Failed to connect: {e}")
            raise

    async def gatherdata(self):
        try:
            if not self.client.is_connected:
                await self.connect()
            async with async_timeout.timeout(10):  # Set a timeout of 10 seconds
                data = await self.client.read_gatt_char(CHARACTERISTIC_UUID)
                _LOGGER.debug(f"Raw data: {data}")
                parsed_result = self.parse_data(data)
                if not parsed_result["raw_data"].startswith("01020304050607"):
                    self.result = parsed_result
                    _LOGGER.debug(f"Parsed result: {self.result}")
                    self.coordinator.async_handle_notification(self.result)  # Update coordinator
        except (BleakError, asyncio.TimeoutError) as e:
            _LOGGER.error(f"Failed to gather data: {e}")
            self.result = None  # Set result to None or handle it appropriately

    async def start_notifications(self):
        try:
            if not self.client.is_connected:
                await self.connect()
            await self.client.start_notify(CHARACTERISTIC_UUID, self.notification_handler)
            _LOGGER.debug("Started notifications")
        except BleakError as e:
            if "Notifications are already enabled" in str(e):
                _LOGGER.debug("Notifications are already enabled")
            else:
                _LOGGER.error(f"Failed to start notifications: {e}")
                raise

    async def stop_notifications(self):
        try:
            await self.client.stop_notify(CHARACTERISTIC_UUID)
            _LOGGER.debug("Stopped notifications")
        except BleakError as e:
            _LOGGER.error(f"Failed to stop notifications: {e}")
            raise

    def notification_handler(self, sender, data):
        _LOGGER.debug(f"Notification received from {sender}: {data}")
        parsed_result = self.parse_data(data)
        if not parsed_result["raw_data"].startswith("01020304050607"):
            self.result = parsed_result
            _LOGGER.debug(f"Parsed result: {self.result}")
            self.coordinator.async_handle_notification(self.result)  # Update coordinator

    def parse_data(self, data):
        # Implement the parsing logic based on the data format
        if data is None:
            return {
                # "status_raw": None,
                "raw_data": "",
                "status": None,
                "vibration_strength": None,
                "oscillation_range": None,
                "oscillation_speed": None,
                "mode": None,
            }
        data_str = data.hex()
        if len(data_str) < 32:  # Ensure the string is long enough
            _LOGGER.error("Data string is too short")
            return {
                # "status_raw": data_str,
                "raw_data": data_str,
                "status": None,
                "vibration_strength": None,
                "oscillation_range": None,
                "oscillation_speed": None,
                "mode": None,
            }
        status = data_str[47]
        mode = data_str[9]
        vibration_strength = data_str[11]
        oscillation_range = data_str[13]
        oscillation_speed = data_str[15]
        return {
            # "status_raw": data_str,  # Store the raw value as a hex string
            "raw_data": data_str,
            "status": status,
            "mode": mode,
            "vibration_strength": vibration_strength,
            "oscillation_range": oscillation_range,
            "oscillation_speed": oscillation_speed,
        }

    def set_ble_device(self, ble_device):
        self.ble_device = ble_device
        self.client = BleakClient(ble_device)

    async def check_connection(self):
        """Check the connection status and reconnect if necessary."""
        if not self.client.is_connected:
            _LOGGER.debug("Device disconnected, attempting to reconnect...")
            await self.connect()
            await self.start_notifications()
            _LOGGER.debug("Reconnected and notifications started")
