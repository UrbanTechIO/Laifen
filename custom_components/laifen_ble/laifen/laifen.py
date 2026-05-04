import logging
import asyncio
import json
import os
import struct
from bleak import BleakError, BleakClient, BleakScanner

_LOGGER = logging.getLogger(__name__)

SERVICE_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
RETRY_LIMIT = 10
STORAGE_PATH = "/config/.storage/laifen_ble_states.json"

# Protocol detection
PROTO_V2_MAGIC = 0x5A       # V2 packets start with 0x5A (firmware V1026+)

# V2 command codes
V2_CMD_STATUS   = 0x03      # Status/settings packet (43 bytes total)
V2_CMD_VERSION  = 0x04      # Firmware version string
V2_CMD_BRUSH    = 0x09      # Live oscillation data — emitted during brushing
V2_CMD_TIMER    = 0x0B      # Brushing timer tick
V2_CMD_SESSION  = 0x0D      # Post-session history record


class Laifen:
    def __init__(self, ble_device, coordinator):
        self.ble_device = ble_device
        self.has_connected_before = False
        self.address = ble_device.address
        self.name = ble_device.name or "Laifen"
        self.client = BleakClient(ble_device)
        self.result = {}
        self.coordinator = coordinator
        self.lock = asyncio.Lock()
        self._first_message = True
        self._reconnecting = asyncio.Lock()
        self._proto_version = None   # auto-detected: "v1" or "v2"
        self._brushing_active = False  # V2: set True by 0x09 packets, False by 0x0D

    async def scan_for_devices(self):
        """Scan for Laifen toothbrush devices."""
        scanner = BleakScanner()
        devices = await scanner.discover()
        found_devices = [
            device for device in devices
            if device.name and (
                device.name.startswith("LFTB") or
                device.name.startswith("Laifen Toothbrush")
            )
        ]
        if not found_devices:
            return None
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
                if self.client.is_connected:
                    self.client.set_disconnected_callback(self._handle_disconnect)
                    self.coordinator.device_asleep = False
                    return True
            except asyncio.CancelledError:
                if self.coordinator:
                    _LOGGER.debug(f"Connection to {self.ble_device.address} was cancelled. Marking asleep.")
                    self.coordinator.device_asleep = True
                return False
            except (BleakError, asyncio.TimeoutError, TimeoutError) as e:
                _LOGGER.debug(f"Connection attempt {attempt} failed: {e}")
            await asyncio.sleep(2)

        _LOGGER.warning(
            f"Failed to connect to {self.ble_device.address} after {max_attempts} attempts. Marking asleep."
        )
        if self.coordinator:
            self.coordinator.device_asleep = True
        return False

    async def send_command(self, command: bytes):
        """Send a HEX command to the Laifen device."""
        async with self.lock:
            if self.client and self.client.is_connected:
                try:
                    await self.client.write_gatt_char(CHARACTERISTIC_UUID, command)
                    return True
                except BleakError:
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
            return

        try:
            raw = await self.client.read_gatt_char(CHARACTERISTIC_UUID)
            data_str = raw.hex()
            _LOGGER.debug(f"Data received from {self.ble_device.address}: {data_str}")

            # V1 protocol
            if data_str.startswith("aa0a021") and len(data_str) >= 50:
                parsed = self._parse_v1(raw)
                if parsed:
                    self.result = parsed
                return

            # V2 protocol
            if len(raw) > 0 and raw[0] == PROTO_V2_MAGIC:
                parsed = self._parse_v2_packet(raw)
                if parsed:
                    self.result = parsed
                return

        except Exception as e:
            _LOGGER.debug(f"[gatherdata] Error reading data: {e}")

    async def start_notifications(self):
        """Start BLE notifications for data updates."""
        if not self.client or not self.client.is_connected:
            return

        for attempt in range(5):
            try:
                await self.client.start_notify(CHARACTERISTIC_UUID, self.notification_handler)
                return
            except BleakError as e:
                if "Notifications are already enabled" in str(e):
                    return
                await asyncio.sleep(1)

        _LOGGER.debug(
            f"Could not start notifications for {self.ble_device.address} after multiple retries."
        )
        if self.coordinator:
            self.coordinator.device_asleep = True
        return False

    async def stop_notifications(self):
        """Stop BLE notifications."""
        async with self.lock:
            if not self.client or not self.client.is_connected:
                return
            try:
                await self.client.stop_notify(CHARACTERISTIC_UUID)
            except BleakError:
                return

    def notification_handler(self, sender, data):
        if not self.coordinator:
            _LOGGER.debug("self.coordinator is not assigned — cannot update HA entities!")
            return

        data_str = data.hex()

        # ── V1 Protocol (AA-header) ────────────────────────────────────
        if data_str.startswith("aa0a021") and len(data_str) >= 50:
            self._proto_version = "v1"
            parsed = self._parse_v1(data)
            if parsed:
                self.result = parsed
                self.coordinator.device_asleep = False
                self.coordinator.async_set_updated_data(self.result)
            return

        # ── V2 Protocol (5A-header, firmware V1026+) ──────────────────
        if len(data) > 0 and data[0] == PROTO_V2_MAGIC:
            self._proto_version = "v2"
            parsed = self._parse_v2_packet(data)
            if parsed:
                self.result = parsed
                self.coordinator.device_asleep = False
                self.coordinator.async_set_updated_data(self.result)
            return

        _LOGGER.debug(
            f"[{self.ble_device.address}] Unrecognised packet "
            f"(proto={self._proto_version}): {data_str[:40]}... len={len(data_str)}"
        )

    # ──────────────────────────────────────────────────────────────────
    # V1 Parser  (original AA-based protocol)
    # ──────────────────────────────────────────────────────────────────

    def parse_data(self, data):
        """Public alias kept for backward compatibility."""
        return self._parse_v1(data)

    def _parse_v1(self, data):
        """Parse V1 BLE data (AA-header protocol)."""
        if data is None:
            return self._empty_result(data)

        data_str = data.hex()

        try:
            return {
                "raw_data":           data_str,
                "status":             "Running" if data_str[47] == "1" else "Idle",
                "mode":               str(int(data_str[9], 16) + 1),
                "battery_level":      int(data_str[36:38], 16) if data_str[36:38].isalnum() else 0,
                "brushing_time":      int(data_str[40:44], 16) / 60 if data_str[40:44].isalnum() else 0,
                "vibration_strength": int(data_str[10 + (int(data_str[9], 16) * 6) : 12 + (int(data_str[9], 16) * 6)], 16),
                "oscillation_range":  int(data_str[12 + (int(data_str[9], 16) * 6) : 14 + (int(data_str[9], 16) * 6)], 16),
                "oscillation_speed":  int(data_str[14 + (int(data_str[9], 16) * 6) : 16 + (int(data_str[9], 16) * 6)], 16),
            }
        except Exception as e:
            _LOGGER.debug(f"Unexpected error while parsing V1 data: {e}")
            return {
                key: 0 if key != "raw_data" else data_str
                for key in [
                    "raw_data", "status", "mode", "battery_level", "brushing_time",
                    "vibration_strength", "oscillation_range", "oscillation_speed",
                ]
            }

    # ──────────────────────────────────────────────────────────────────
    # V2 Parser  (5A-header protocol, firmware V1026+)
    # ──────────────────────────────────────────────────────────────────

    def _parse_v2_packet(self, data: bytes) -> dict | None:
        """
        Dispatch a V2 (0x5A-magic) BLE notification to the correct sub-parser.

        V2 frame layout:
          [0]      0x5A  magic
          [1]      frame type  (0x81 unicast-notify, 0xC1 broadcast-notify,
                                0x82/0xC2 ack-class, 0xF0 keep-alive)
          [2]      command code
          [3..4]   sequence number uint16 LE
          [5]      payload length
          [6..N]   payload  (N = 6 + payload_length - 1)
          [-1]     checksum
        """
        if len(data) < 7:
            return None

        cmd    = data[2]
        paylen = data[5]

        if len(data) < 6 + paylen + 1:
            _LOGGER.debug(
                f"V2 packet too short for cmd=0x{cmd:02X}: "
                f"got {len(data)}, need {6 + paylen + 1}"
            )
            return None

        payload = data[6 : 6 + paylen]

        if cmd == V2_CMD_STATUS:
            return self._parse_v2_status(data, payload)

        if cmd == V2_CMD_BRUSH:
            # Live oscillation data → device is actively brushing
            self._brushing_active = True
            return self._parse_v2_oscillation(data, payload)

        if cmd == V2_CMD_TIMER:
            # 1-second tick emitted while brushing; flag only, no new sensor data
            self._brushing_active = True
            return None

        if cmd == V2_CMD_SESSION:
            # Post-session history upload — brushing has just ended
            self._brushing_active = False
            return None

        # Version string, ACK, keep-alive — no state update needed
        return None

    def _parse_v2_status(self, raw: bytes, payload: bytes) -> dict | None:
        """
        Parse V2 cmd=0x03 status packet.

        Payload layout (0-indexed within the 36-byte payload):
          [2]        battery percentage (0–100)
          [3]        charging flag  0x01=charging, 0x00=on battery
          [5..6]     mode-0 speed  uint16 LE  (e.g. 0x0096 = 150)
          [7..8]     mode-1 speed  uint16 LE
          [9..10]    mode-2 speed  uint16 LE
          [11..12]   mode-3 speed  uint16 LE
          [13]       current mode index, 0-based
                       0 = idle (or mode-1 selected, not brushing)
                       1 = actively brushing in mode-2
                       2 = actively brushing in mode-3
          [15]       device-on flag (always 0x01 when BLE-connected)
          [21]       elapsed brushing seconds (non-zero only mid-session)
        """
        if len(payload) < 22:
            _LOGGER.debug(f"V2 status payload too short: {len(payload)} bytes")
            return None

        battery  = payload[2]
        charging = (payload[3] == 0x01)
        mode_idx = payload[13]   # 0-based

        # Four per-mode speed values stored as uint16 LE
        speeds = []
        for i in range(4):
            offset = 5 + i * 2
            if offset + 2 <= len(payload):
                speeds.append(struct.unpack_from("<H", payload, offset)[0])
            else:
                speeds.append(0)

        current_speed = speeds[mode_idx] if mode_idx < len(speeds) else 0

        # Running state determination:
        #  • self._brushing_active is set True by incoming V2_CMD_BRUSH (0x09) packets
        #    that stream every ~1 s during active brushing.
        #  • mode_idx > 0 is a reliable fallback: when the brush is running in mode 2+
        #    the firmware increments the mode index; idle always shows mode_idx == 0.
        #  • mode_idx == 0 CAN mean "mode-1 is selected and brushing" on some firmwares,
        #    but in practice the 0x09 stream covers that case via _brushing_active.
        running = self._brushing_active or (mode_idx > 0)

        # brushing_time is only meaningful mid-session (firmware sends 0 otherwise,
        # or occasionally echoes a default speed value — guard against that).
        brushing_time_sec = payload[21] if payload[21] < 0x60 else 0  # cap at 96 s sanity

        return {
            "raw_data":           raw.hex(),
            "status":             "Running" if running else "Idle",
            "mode":               str(mode_idx + 1),   # convert to 1-based for display
            "battery_level":      battery,
            "charging":           charging,
            "brushing_time":      brushing_time_sec / 60,   # minutes, matching V1 units
            "vibration_strength": current_speed,
            "oscillation_range":  speeds[0] if speeds else 0,
            "oscillation_speed":  current_speed,
        }

    def _parse_v2_oscillation(self, raw: bytes, payload: bytes) -> dict | None:
        """
        Parse V2 cmd=0x09 live-oscillation packet.

        These packets stream at ~1 Hz during active brushing and carry
        per-tooth-zone strength readings.  We surface payload[1] (the
        aggregate speed/strength byte) and carry over battery/mode from
        the last status packet.
        """
        battery  = self.result.get("battery_level", 0)
        charging = self.result.get("charging", False)
        mode     = self.result.get("mode", "1")

        # payload[1] = current speed/strength level for this reading
        current_speed = payload[1] if len(payload) > 1 else 0

        return {
            "raw_data":           raw.hex(),
            "status":             "Running",
            "mode":               mode,
            "battery_level":      battery,
            "charging":           charging,
            "brushing_time":      self.result.get("brushing_time", 0),
            "vibration_strength": current_speed,
            "oscillation_range":  current_speed,
            "oscillation_speed":  current_speed,
        }

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────

    def _empty_result(self, data):
        return {
            "raw_data":           data.hex() if data else "",
            "status":             "Unknown",
            "vibration_strength": 0,
            "oscillation_range":  0,
            "oscillation_speed":  0,
            "mode":               "Unknown",
            "battery_level":      0,
            "brushing_time":      0,
        }

    # ──────────────────────────────────────────────────────────────────
    # Connection management (unchanged from v1.x)
    # ──────────────────────────────────────────────────────────────────

    async def set_ble_device(self, ble_device):
        """Forcefully set Bluetooth device and create a fresh client."""
        if self.client and self.client.is_connected:
            await self.client.disconnect()

        self.ble_device = ble_device
        self.address = ble_device.address
        self.client = BleakClient(self.ble_device)
        self.client.set_disconnected_callback(self._handle_disconnect)

    async def disconnect(self):
        """Safely disconnect with better resource cleanup."""
        if self.client and self.client.is_connected:
            try:
                await self.stop_notifications()
                await self.client.disconnect()
                _LOGGER.debug(f"Disconnected {self.ble_device.address} cleanly")
            except BleakError as e:
                _LOGGER.debug(f"Error during disconnect: {e}")
            finally:
                self.client = None

    def _handle_disconnect(self, client):
        _LOGGER.debug(f"{self.ble_device.address} disconnected.")
        if self.coordinator:
            _LOGGER.debug(
                f"{self.ble_device.address} disconnected — will attempt reconnection."
            )
            self.coordinator.device_asleep = False
            asyncio.create_task(self._aggressive_reconnect())

    async def _aggressive_reconnect(self, max_attempts=10, initial_delay=1):
        async with self._reconnecting:
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
                        _LOGGER.debug(
                            f"Reconnect attempt {attempt + 1}/{max_attempts} for {self.address}"
                        )

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
