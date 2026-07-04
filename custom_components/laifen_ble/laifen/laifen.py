import logging
import asyncio
import struct
from bleak import BleakError, BleakClient, BleakScanner
from bleak_retry_connector import establish_connection

_LOGGER = logging.getLogger(__name__)

SERVICE_UUID        = "0000ff01-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
STORAGE_PATH        = "/config/.storage/laifen_ble_states.json"

# V2 protocol (5A-header) constants — speculative, unvalidated (LFTB02-S-7857)
PROTO_V2_MAGIC = 0x5A
V2_CMD_STATUS  = 0x03
V2_CMD_VERSION = 0x04
V2_CMD_BRUSH   = 0x09
V2_CMD_TIMER   = 0x0B
V2_CMD_SESSION = 0x0D

# V2 Pro protocol (5A-header) constants — CONFIRMED 2026-06-12 (Wave Pro, LFTB02-S-412B)
# Packet format: 5A [TYPE] [SUBCMD] [SEQ] 00 [LEN] [...payload (LEN bytes)...] [checksum]
#   TYPE: 0xC1=periodic broadcast / poll, 0x81=change-triggered status,
#         0xC2=setting-change ACK, 0xF0=ready/sync signal,
#         0x82=real-time telemetry while brushing (subcmd 0x0C/0x0D)
#   SUBCMD: 0x03=main status (36-byte payload), 0x09=modes table (not yet decoded),
#           0x0C/0x0D=brushing telemetry (not yet decoded)
V2PRO_TYPE_POLL      = 0xC1
V2PRO_TYPE_STATUS    = 0x81
V2PRO_TYPE_ACK       = 0xC2
V2PRO_TYPE_READY     = 0xF0
V2PRO_TYPE_TELEMETRY = 0x82
V2PRO_SUBCMD_STATUS  = 0x03

# Confirmed payload byte offsets for V2PRO_SUBCMD_STATUS (36-byte payload)
V2PRO_OVER_PRESSURE_LEVELS = {
    (0, 0xDC): "Light",
    (1, 0x4A): "Medium",
    (1, 0xB8): "Hard",
}


def _xor_checksum(data: list[int]) -> int:
    cs = 0
    for b in data:
        cs ^= b
    return cs


def build_command(param: int, b3: int, value: int) -> bytes:
    """
    Build a V1 write command: AA 04 [param] [b3] [value] [xor_cs]

    Confirmed format from laifen_12.txt:
      ACK = AA-04-[param]-01-00-cs  (device echoes param at position [2])
      So write format is: AA 04 [param] 01 [value] [cs]

    Params: 0x02=Strength, 0x03=Range, 0x04=Speed, 0x01=Mode
    b3 is always 0x01.
    """
    data = [0xAA, 0x04, param, b3, value]
    cs = 0
    for b in data:
        cs ^= b
    data.append(cs)
    return bytes(data)


def build_v2pro_command(cmd: int, payload: list[int]) -> bytes:
    """
    Build a V2 Pro (Wave Pro / LFTB02-S-412B) write command.

    CONFIRMED 2026-06-12 from decompiled Laifen app source
    (TbDataCommandManager / PublicDataCommandManager) and verified live
    on the device (power on/off, mode select all worked first try):

      AA [hi(cmd)] [lo(cmd)] 00 00 [len(payload)] [...payload...] [xor_cs]

    cmd is the 16-bit command ID (e.g. 0x0108=power, 0x0109=mode).
    The XOR checksum covers all preceding bytes.
    """
    data = [0xAA, (cmd >> 8) & 0xFF, cmd & 0xFF, 0x00, 0x00, len(payload)]
    data.extend(payload)
    cs = 0
    for b in data:
        cs ^= b
    data.append(cs)
    return bytes(data)


class Laifen:
    def __init__(self, ble_device, coordinator):
        self.ble_device      = ble_device
        self.address         = ble_device.address
        self.name            = ble_device.name or "Laifen"
        self.client          = BleakClient(ble_device)
        self.result          = {}
        self.coordinator     = coordinator
        self.lock            = asyncio.Lock()
        self._first_message  = True
        self._reconnecting   = asyncio.Lock()
        self._proto_version  = None        # "v1" or "v2"
        self._brushing_active = False      # V2 only

        # ── State tracking ────────────────────────────────────────────────
        # mode_index: byte[4] echoes the last-written value for ANY command,
        # so it is unreliable after slider writes. We track it explicitly.
        self._current_mode_index: int = 0

    # ──────────────────────────────────────────────────────────────────
    # Connection management
    # ──────────────────────────────────────────────────────────────────

    async def scan_for_devices(self):
        scanner = BleakScanner()
        devices = await scanner.discover()
        found = [
            d for d in devices
            if d.name and (d.name.startswith("LFTB") or d.name.startswith("Laifen Toothbrush"))
        ]
        return found or None

    async def connect(self):
        if self.client and self.client.is_connected:
            return True

        try:
            self.client = await establish_connection(
                BleakClient,
                self.ble_device,
                self.name,
                disconnected_callback=self._handle_disconnect,
                max_attempts=10,
            )
            if self.coordinator:
                self.coordinator.device_asleep = False
            try:
                char = self.client.services.get_characteristic(CHARACTERISTIC_UUID)
                if char:
                    _LOGGER.debug(
                        f"[{self.address}] connect: resolved characteristic "
                        f"{CHARACTERISTIC_UUID} -> handle={char.handle}, "
                        f"properties={char.properties}, service={char.service_uuid}"
                    )
                else:
                    _LOGGER.warning(f"[{self.address}] connect: characteristic {CHARACTERISTIC_UUID} NOT FOUND in services")
                _LOGGER.debug(f"[{self.address}] connect: all services/characteristics:")
                for svc in self.client.services:
                    for c in svc.characteristics:
                        _LOGGER.debug(f"[{self.address}]   svc={svc.uuid} char={c.uuid} handle={c.handle} props={c.properties}")
            except Exception as e:
                _LOGGER.debug(f"[{self.address}] connect: failed to enumerate services: {e!r}")
            return True
        except asyncio.CancelledError:
            if self.coordinator:
                self.coordinator.device_asleep = True
            return False
        except (BleakError, asyncio.TimeoutError, TimeoutError) as e:
            _LOGGER.warning(f"Failed to connect to {self.ble_device.address}: {e}")
            if self.coordinator:
                self.coordinator.device_asleep = True
            return False

    async def send_command(self, command: bytes):
        cmd_hex = command.hex()
        async with self.lock:
            if not self.client:
                _LOGGER.warning(f"[{self.address}] send_command({cmd_hex}): no client object")
                return False
            if not self.client.is_connected:
                _LOGGER.warning(f"[{self.address}] send_command({cmd_hex}): client not connected")
                return False
            try:
                _LOGGER.debug(f"[{self.address}] send_command: writing {cmd_hex} (proto={self._proto_version}) to {CHARACTERISTIC_UUID}, response=True")
                await self.client.write_gatt_char(CHARACTERISTIC_UUID, command, response=True)
                _LOGGER.debug(f"[{self.address}] send_command: write of {cmd_hex} completed OK")
                return True
            except BleakError as e:
                _LOGGER.warning(f"[{self.address}] send_command({cmd_hex}): BleakError: {e!r}")
                return False
            except Exception as e:
                _LOGGER.warning(f"[{self.address}] send_command({cmd_hex}): unexpected error: {e!r}")
                return False

    async def set_ble_device(self, ble_device):
        if self.client and self.client.is_connected:
            await self.client.disconnect()
        self.ble_device = ble_device
        self.address    = ble_device.address
        self.client     = BleakClient(self.ble_device)
        self.client.set_disconnected_callback(self._handle_disconnect)

    async def disconnect(self):
        if self.client and self.client.is_connected:
            try:
                await self.stop_notifications()
                await self.client.disconnect()
                _LOGGER.debug(f"Disconnected {self.ble_device.address}")
            except BleakError as e:
                _LOGGER.debug(f"Error during disconnect: {e}")
            finally:
                self.client = None

    def _handle_disconnect(self, client):
        _LOGGER.debug(f"{self.ble_device.address} disconnected.")
        if self.coordinator:
            self.coordinator.device_asleep = False
            # Push a coordinator update immediately so the Connection binary
            # sensor (and any other entities) reflect the disconnected state
            # without waiting for the next coordinator tick.
            self.coordinator.async_set_updated_data(self.result or {})
            asyncio.create_task(self._aggressive_reconnect())

    async def _aggressive_reconnect(self, max_attempts=10, initial_delay=1):
        async with self._reconnecting:
            # Another caller may have already reconnected us while we were
            # waiting for the lock (e.g. _handle_disconnect's background
            # task racing with the coordinator's per-tick connection check).
            # If we're already connected, there's nothing to do — and
            # falling through to the loop below would do nothing for
            # max_attempts iterations and incorrectly mark the device
            # asleep even though the connection is healthy.
            if self.client and self.client.is_connected:
                self.coordinator.device_asleep = False
                return True

            for attempt in range(max_attempts):
                try:
                    if not self.client or not self.client.is_connected:
                        devices = await BleakScanner.discover()
                        for dev in devices:
                            if dev.address.lower() == self.address.lower():
                                await self.set_ble_device(dev)
                                break
                        await asyncio.sleep(initial_delay)
                        _LOGGER.debug(f"Reconnect attempt {attempt+1}/{max_attempts} for {self.address}")
                        if not self.client:
                            self.client = BleakClient(self.ble_device)
                        if await self.connect():
                            await self.start_notifications()
                            if self._proto_version != "v2pro":
                                await self.gatherdata()
                            _LOGGER.debug(f"Reconnected to {self.address}")
                            # Push update so Connection sensor flips to ON
                            self.coordinator.async_set_updated_data(self.result or {})
                            return True
                except Exception as e:
                    _LOGGER.debug(f"Reconnect attempt {attempt+1} failed: {e}")

            cached = await self.coordinator._async_restore_data()
            self.coordinator.device_asleep = True
            self.coordinator.async_set_updated_data(cached or {})
            return False

    # ──────────────────────────────────────────────────────────────────
    # Notifications
    # ──────────────────────────────────────────────────────────────────

    async def start_notifications(self):
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
        if self.coordinator:
            self.coordinator.device_asleep = True

    async def stop_notifications(self):
        async with self.lock:
            if not self.client or not self.client.is_connected:
                return
            try:
                await self.client.stop_notify(CHARACTERISTIC_UUID)
            except BleakError:
                return

    async def gatherdata(self):
        if not self.client or not self.client.is_connected:
            return
        try:
            raw      = await self.client.read_gatt_char(CHARACTERISTIC_UUID)
            data_str = raw.hex()
            if data_str.startswith("aa0a021") and len(data_str) >= 50:
                parsed = self._parse_v1(raw)
                if parsed:
                    self.result = parsed
            elif len(raw) >= 2 and raw[0] == PROTO_V2_MAGIC and raw[1] in (
                V2PRO_TYPE_POLL, V2PRO_TYPE_STATUS, V2PRO_TYPE_ACK, V2PRO_TYPE_READY
            ):
                parsed = self._parse_v2pro_packet(raw)
                if parsed:
                    self.result = parsed
            elif len(raw) > 0 and raw[0] == PROTO_V2_MAGIC:
                parsed = self._parse_v2_packet(raw)
                if parsed:
                    self.result = parsed
        except Exception as e:
            _LOGGER.debug(f"[gatherdata] Error: {e}")

    def notification_handler(self, sender, data):
        if not self.coordinator:
            return

        data_str = data.hex()

        _LOGGER.debug(
            f"[{self.address}] notification_handler: {len(data)} bytes, "
            f"data={data.hex()}, proto_before={self._proto_version}"
        )

        # V1 protocol
        if data_str.startswith("aa0a021") and len(data_str) >= 50:
            self._proto_version = "v1"
            parsed = self._parse_v1(data)
            if parsed:
                self.result = parsed
                self.coordinator.device_asleep = False
                self.coordinator.async_set_updated_data(self.result)
            _LOGGER.debug(f"[{self.address}] -> matched V1, proto_after=v1")
            return

        # V2 Pro protocol (Wave Pro / LFTB02-S-412B) — checked before the
        # speculative V2 path since both share the 0x5A magic byte.
        # Includes 0x82 (high-frequency brushing telemetry) so these
        # packets are recognized-but-ignored rather than falling through
        # to the speculative-V2 branch below, which would incorrectly
        # downgrade _proto_version from "v2pro" to "v2" mid-session
        # (CONFIRMED 2026-06-13: this caused turn_off() to send the wrong
        # protocol's command while the brush was running).
        if len(data) >= 2 and data[0] == PROTO_V2_MAGIC and data[1] in (
            V2PRO_TYPE_POLL, V2PRO_TYPE_STATUS, V2PRO_TYPE_ACK,
            V2PRO_TYPE_READY, V2PRO_TYPE_TELEMETRY,
        ):
            self._proto_version = "v2pro"
            parsed = self._parse_v2pro_packet(data)
            if parsed:
                self.coordinator.device_asleep = False
                # Only push an update to HA if something we actually track
                # changed. Status packets (0xC1-03 periodic poll, 0x81-03
                # change notifications) can arrive multiple times per
                # second — pushing a coordinator update on every single one
                # (most of which are identical) creates a lot of unnecessary
                # HA-side churn while the connection is already under load
                # from brushing telemetry.
                if parsed != self.result:
                    self.result = parsed
                    self.coordinator.async_set_updated_data(self.result)
            _LOGGER.debug(f"[{self.address}] -> matched V2Pro, proto_after=v2pro")
            return

        # V2 protocol (speculative, unvalidated — LFTB02-S-7857). A device
        # already confirmed as v2pro must never be downgraded to this
        # speculative protocol by an unrecognized 0x5A packet.
        if len(data) > 0 and data[0] == PROTO_V2_MAGIC and self._proto_version != "v2pro":
            self._proto_version = "v2"
            parsed = self._parse_v2_packet(data)
            if parsed:
                self.result = parsed
                self.coordinator.device_asleep = False
                self.coordinator.async_set_updated_data(self.result)
            _LOGGER.debug(f"[{self.address}] -> matched V2-speculative, proto_after=v2")
            return

        _LOGGER.debug(f"[{self.address}] -> matched NOTHING, proto unchanged ({self._proto_version})")

        _LOGGER.debug(f"Unrecognised packet: {data_str[:40]}")

    # ──────────────────────────────────────────────────────────────────
    # V1 Parser  (AA-header protocol — LFTB01)
    # ──────────────────────────────────────────────────────────────────

    def parse_data(self, data):
        """Backward-compatibility alias."""
        return self._parse_v1(data)

    def _parse_v1(self, data: bytes) -> dict | None:
        """
        Parse V1 BLE status packet (AA-header, 26 bytes).

        CONFIRMED layout from laifen_12.txt systematic test
        (Laifen app controlling all sliders while brush running):

          [4]     active mode index (0-3). Changes ONLY when mode switches
                  (physical button or mode-select command). Slider writes
                  do NOT change this byte.
          [5]     ACTIVE mode — Vibration Strength (1-10, or 1-20 on Mode 4)
          [6]     ACTIVE mode — Oscillation Range  (1-10)
          [7]     ACTIVE mode — Oscillation Speed  (1-10)
          [8]     Mode 2 — Vibration Strength (stored, doesn't change when
                  mode 1 is active)
          [9]     Mode 2 — Oscillation Range
          [10]    Mode 2 — Oscillation Speed
          [11]    Mode 3 — Vibration Strength
          [12]    Mode 3 — Oscillation Range
          [13]    Mode 3 — Oscillation Speed
          [14]    Mode 4 — Vibration Strength
          [15]    Mode 4 — Oscillation Range
          [16]    Mode 4 — Oscillation Speed
          [17]    Airplane mode flag  (0=off, 1=on)
          [18]    Battery level (%)
          [22]    High Frequency flag (0=off, 1=on)
          [23]    Running flag: 0x01 = Running, 0x00 = Idle
                  (data_str[47]=='1' is the same check)

        Note: bytes[5,6,7] always show the ACTIVE mode's live values.
        bytes[8..16] show the STORED values for modes 2/3/4 — these only
        update when the app or HA writes to those modes while they are active.
        """
        if data is None:
            return self._empty_result(data)

        data_str = data.hex()

        try:
            # Mode index — byte[4], reliable at all times
            mode_index = data[4] if len(data) > 4 and 0 <= data[4] <= 3 else self._current_mode_index
            self._current_mode_index = mode_index

            # Active mode values — bytes[5,6,7]
            active_strength = data[5] if len(data) > 5 else 0
            active_range    = data[6] if len(data) > 6 else 0
            active_speed    = data[7] if len(data) > 7 else 0

            # Per-mode stored values — bytes[8..16]
            # Mode 1 is always in [5,6,7] (active), so we synthesise m1_* from those
            # when mode 1 is active; otherwise preserve cached m1 from prior result.
            prev = self.result or {}

            if mode_index == 0:
                m1_str, m1_range, m1_speed = active_strength, active_range, active_speed
            else:
                m1_str   = prev.get("m1_strength", 0)
                m1_range = prev.get("m1_range",    0)
                m1_speed = prev.get("m1_speed",    0)

            # Modes 2/3/4 are directly readable from packet
            m2_str   = data[8]  if len(data) > 8  else prev.get("m2_strength", 0)
            m2_range = data[9]  if len(data) > 9  else prev.get("m2_range",    0)
            m2_speed = data[10] if len(data) > 10 else prev.get("m2_speed",    0)
            m3_str   = data[11] if len(data) > 11 else prev.get("m3_strength", 0)
            m3_range = data[12] if len(data) > 12 else prev.get("m3_range",    0)
            m3_speed = data[13] if len(data) > 13 else prev.get("m3_speed",    0)
            m4_str   = data[14] if len(data) > 14 else prev.get("m4_strength", 0)
            m4_range = data[15] if len(data) > 15 else prev.get("m4_range",    0)
            m4_speed = data[16] if len(data) > 16 else prev.get("m4_speed",    0)

            # When mode 2/3/4 is active, bytes[8..16] still hold ALL stored values
            # but bytes[5,6,7] hold the live active ones. Keep stored values in sync
            # for the active mode using the live bytes[5,6,7]:
            if mode_index == 1:
                m2_str, m2_range, m2_speed = active_strength, active_range, active_speed
            elif mode_index == 2:
                m3_str, m3_range, m3_speed = active_strength, active_range, active_speed
            elif mode_index == 3:
                m4_str, m4_range, m4_speed = active_strength, active_range, active_speed

            # Feature flags
            airplane_mode  = bool(data[17]) if len(data) > 17 else False
            battery_level  = data[18]       if len(data) > 18 else 0
            high_frequency = bool(data[22]) if len(data) > 22 else False

            # Running status — byte[23] low nibble, confirmed from laifen_12
            status = "Running" if (len(data) > 23 and data[23] == 0x01) else "Idle"

            return {
                "raw_data":           data_str,
                "status":             status,
                "mode":               str(mode_index + 1),
                "mode_index":         mode_index,
                "battery_level":      battery_level,
                "brushing_time":      0,
                "vibration_strength": active_strength,
                "oscillation_range":  active_range,
                "oscillation_speed":  active_speed,
                "active_strength":    active_strength,
                "active_range":       active_range,
                "active_speed":       active_speed,
                "m1_strength": m1_str,   "m1_range": m1_range,   "m1_speed": m1_speed,
                "m2_strength": m2_str,   "m2_range": m2_range,   "m2_speed": m2_speed,
                "m3_strength": m3_str,   "m3_range": m3_range,   "m3_speed": m3_speed,
                "m4_strength": m4_str,   "m4_range": m4_range,   "m4_speed": m4_speed,
                "airplane_mode":      airplane_mode,
                "high_frequency":     high_frequency,
                "reminder_30s":       prev.get("reminder_30s", False),
            }
        except Exception as e:
            _LOGGER.debug(f"V1 parse error: {e}")
            return self._empty_result(data)

    # ──────────────────────────────────────────────────────────────────
    # V2 Parser  (5A-header protocol — LFTB02, firmware V1026+)
    # ──────────────────────────────────────────────────────────────────

    def _parse_v2_packet(self, data: bytes) -> dict | None:
        if len(data) < 7 or data[0] != PROTO_V2_MAGIC:
            return None
        cmd    = data[2]
        paylen = data[5]
        if len(data) < 6 + paylen + 1:
            return None
        payload = data[6:6 + paylen]

        if cmd == V2_CMD_STATUS:
            return self._parse_v2_status(data, payload)
        if cmd == V2_CMD_BRUSH:
            self._brushing_active = True
            return self._parse_v2_oscillation(data, payload)
        if cmd == V2_CMD_TIMER:
            self._brushing_active = True
        if cmd == V2_CMD_SESSION:
            self._brushing_active = False
        return None

    def _parse_v2_status(self, raw: bytes, payload: bytes) -> dict | None:
        if len(payload) < 22:
            return None

        battery  = payload[2]
        charging = (payload[3] == 0x01)
        mode_idx = payload[13]
        running  = self._brushing_active or (mode_idx > 0)

        speeds = [
            struct.unpack_from("<H", payload, 5 + i * 2)[0]
            for i in range(4)
            if 5 + i * 2 + 2 <= len(payload)
        ]
        current_speed = speeds[mode_idx] if mode_idx < len(speeds) else 0
        brushing_sec  = payload[21] if payload[21] < 0x60 else 0

        return {
            "raw_data":           raw.hex(),
            "status":             "Running" if running else "Idle",
            "mode":               str(mode_idx + 1),
            "mode_index":         mode_idx,
            "battery_level":      battery,
            "charging":           charging,
            "brushing_time":      brushing_sec / 60,
            "vibration_strength": current_speed,
            "oscillation_range":  speeds[0] if speeds else 0,
            "oscillation_speed":  current_speed,
            "m1_strength":  speeds[0] if len(speeds) > 0 else 0,
            "m1_range":     0,
            "m1_speed":     current_speed,
            "m2_strength":  speeds[1] if len(speeds) > 1 else 0,
            "m2_range":     0,
            "m2_speed":     0,
            "m3_strength":  speeds[2] if len(speeds) > 2 else 0,
            "m3_range":     0,
            "m3_speed":     0,
            "m4_strength":  speeds[3] if len(speeds) > 3 else 0,
            "m4_range":     0,
            "m4_speed":     0,
            "airplane_mode":    False,
            "high_frequency":   False,
            "reminder_30s":     False,
        }

    def _parse_v2_oscillation(self, raw: bytes, payload: bytes) -> dict | None:
        battery = self.result.get("battery_level", 0)
        mode    = self.result.get("mode", "1")
        speed   = payload[1] if len(payload) > 1 else 0
        return {
            "raw_data":           raw.hex(),
            "status":             "Running",
            "mode":               mode,
            "mode_index":         self.result.get("mode_index", 0),
            "battery_level":      battery,
            "brushing_time":      self.result.get("brushing_time", 0),
            "vibration_strength": speed,
            "oscillation_range":  speed,
            "oscillation_speed":  speed,
            "m1_strength":  self.result.get("m1_strength", 0),
            "m1_range":     self.result.get("m1_range", 0),
            "m1_speed":     self.result.get("m1_speed", 0),
            "m2_strength":  self.result.get("m2_strength", 0),
            "m2_range":     self.result.get("m2_range", 0),
            "m2_speed":     self.result.get("m2_speed", 0),
            "m3_strength":  self.result.get("m3_strength", 0),
            "m3_range":     self.result.get("m3_range", 0),
            "m3_speed":     self.result.get("m3_speed", 0),
            "m4_strength":  self.result.get("m4_strength", 0),
            "m4_range":     self.result.get("m4_range", 0),
            "m4_speed":     self.result.get("m4_speed", 0),
            "airplane_mode":    self.result.get("airplane_mode", False),
            "high_frequency":   self.result.get("high_frequency", False),
            "reminder_30s":     self.result.get("reminder_30s", False),
        }

    # ──────────────────────────────────────────────────────────────────
    # V2 Pro Parser  (5A-header protocol — Wave Pro, LFTB02-S-412B)
    # CONFIRMED 2026-06-12 from systematic single-toggle captures.
    # ──────────────────────────────────────────────────────────────────

    def _parse_v2pro_packet(self, data: bytes) -> dict | None:
        """
        Dispatch a Wave Pro packet: 5A [TYPE] [SUBCMD] [SEQ] 00 [LEN] [payload] [cs]

        Decoded packet types:
          TYPE 0xC1/0x81, SUBCMD 0x03 — main status (36-byte payload)
          TYPE 0x82, SUBCMD 0x0C      — real-time pressure telemetry (4 bytes)
            p0=3, p1=1 (fixed), p2=over-pressure flag (0=normal, !=0=hard press),
            p3=raw pressure value. Confirmed via nRF Connect capture 2026-06-13.

        Other subcommands (0x09 modes table, 0x0D motion telemetry) and
        ACK/ready packets (TYPE 0xC2/0xF0) are acknowledged but not decoded.
        """
        if len(data) < 6:
            return None

        ptype  = data[1]
        subcmd = data[2]
        paylen = data[5]

        if len(data) < 6 + paylen + 1:
            return None

        payload = data[6:6 + paylen]

        if ptype in (V2PRO_TYPE_POLL, V2PRO_TYPE_STATUS) and subcmd == V2PRO_SUBCMD_STATUS:
            return self._parse_v2pro_status(data, payload)

        # Real-time pressure sensor telemetry — TYPE 0x82, SUBCMD 0x0C, 4-byte payload.
        # p2 is the over-pressure flag: 0=normal, non-zero=pressing too hard.
        # Return a partial update so the binary_sensor can react immediately
        # without waiting for the next full 0x81-03 status packet (~5s cadence).
        if ptype == V2PRO_TYPE_TELEMETRY and subcmd == 0x0C and len(payload) >= 3:
            pressing_hard = payload[2] != 0
            _LOGGER.debug(
                f"[{self.address}] 0x0C pressure: flag={payload[2]} "
                f"raw_pressure={payload[3] if len(payload) > 3 else '?'} "
                f"-> over_pressure_active={pressing_hard}"
            )
            if self.result is not None:
                self.result["over_pressure_active"] = pressing_hard
            return self.result  # trigger coordinator update with current result

        # ACK (0xC2), ready (0xF0), modes table (subcmd 0x09), 0x0D motion
        # telemetry — not decoded, next full status packet carries value changes.
        return None

    def _parse_v2pro_status(self, raw: bytes, payload: bytes) -> dict | None:
        """
        Parse the Wave Pro main status payload (36 bytes, p0..p35).

        Confirmed fields (from earlier systematic single-toggle captures,
        restored 2026-06-13 now that writes are confirmed working):
          p2  = battery %
          p5  = brushing duration, in seconds (per-mode; modes share one
                value in practice)
          p13 = running status: 0=Idle, 1=Running, 2=just-stopped
          p15 = Deep Clean            (ACK 0x07 / CMD 0x207)
          p16 = Anti-Splash           (ACK 0x10 / CMD 0x210)
          p24 = 3s Power Ramp-Up      (ACK 0x11 / CMD 0x211)
          p25 = Quick Spin-dry        (ACK 0x12 / CMD 0x212)
          p26 = Over Pressure enabled (ACK 0x0B / CMD 0x20B)
          p27,p28 = Over Pressure level: Light=(0,0xDC) Medium=(1,0x4A) Hard=(1,0xB8)
          p30 = Bristle Protection    (ACK 0x13 / CMD 0x213)
          p32 = Lift to Wake reminder (ACK 0x08 / CMD 0x208)

        Not yet confirmed: active mode index (preserved as a placeholder),
        and 30s Reminder / High Frequency, which aren't known to appear in
        this packet — those remain optimistic-only.
        """
        if len(payload) < 33:
            _LOGGER.debug(
                f"[{self.address}] _parse_v2pro_status: payload too short "
                f"({len(payload)} bytes), raw={raw.hex()}"
            )
            return None

        prev = self.result or {}

        battery      = payload[2]
        # Duration is stored per-mode as 16-bit little-endian seconds.
        # Mode 0's duration sits at payload[5:7]; all modes are normally set
        # to the same value by the app. Reading the full 16-bit value (not
        # just the low byte) is required for durations >255s (e.g. 300s=0x012C).
        duration_sec = payload[5] | (payload[6] << 8)
        status       = "Running" if payload[13] == 1 else "Idle"

        op_key   = (payload[27], payload[28])
        op_level = V2PRO_OVER_PRESSURE_LEVELS.get(op_key, "Unknown")

        _LOGGER.debug(
            f"[{self.address}] _parse_v2pro_status: raw={raw.hex()} "
            f"p2(batt)={payload[2]} p5(dur)={payload[5]} p13(run)={payload[13]} "
            f"p15(deepclean)={payload[15]} p16(antisplash)={payload[16]} "
            f"p24(rampup)={payload[24]} p25(spin)={payload[25]} "
            f"p26(overpress)={payload[26]} p27/28(level)={payload[27]}/{payload[28]} "
            f"p30(bristle)={payload[30]} p32(wake)={payload[32]} -> status={status}"
        )

        return {
            "status":              status,
            "battery_level":       battery,
            "brushing_duration":   duration_sec,  # seconds — also exposed as brushing_duration_sec
            "deep_clean":          bool(payload[15]),
            "anti_splash":         bool(payload[16]),
            "power_ramp_up":       bool(payload[24]),
            "quick_spin_dry":      bool(payload[25]),
            "over_pressure":       bool(payload[26]),
            "over_pressure_level": op_level,
            "bristle_protection":  bool(payload[30]),
            "lift_to_wake":        bool(payload[32]),
            # Preserved placeholders — these are set optimistically by their
            # respective switches/selects/sliders after a successful write,
            # and must survive subsequent status notifications since this
            # function replaces self.result wholesale.
            "mode":              prev.get("mode", "1"),
            "mode_index":        prev.get("mode_index", 0),
            "airplane_mode":     prev.get("airplane_mode", False),
            "high_frequency":    prev.get("high_frequency", False),
            "reminder_30s":      prev.get("reminder_30s", False),
            # Per-mode strength/range/speed — UNCONFIRMED defaults (5,5,5).
            # The 0x09 "modes table" packet contains real per-mode/per-zone
            # values but hasn't been decoded yet, so sliders start at a
            # guessed mid-range value and track whatever is set from HA.
            "m1_strength": prev.get("m1_strength", 5),
            "m1_range":    prev.get("m1_range", 5),
            "m1_speed":    prev.get("m1_speed", 5),
            "m2_strength": prev.get("m2_strength", 5),
            "m2_range":    prev.get("m2_range", 5),
            "m2_speed":    prev.get("m2_speed", 5),
            "m3_strength": prev.get("m3_strength", 5),
            "m3_range":    prev.get("m3_range", 5),
            "m3_speed":    prev.get("m3_speed", 5),
            "m4_strength": prev.get("m4_strength", 5),
            "m4_range":    prev.get("m4_range", 5),
            "m4_speed":    prev.get("m4_speed", 5),
            "active_strength": prev.get("active_strength", prev.get(f"m{prev.get('mode_index', 0) + 1}_strength", 5)),
            "active_range":    prev.get("active_range",    prev.get(f"m{prev.get('mode_index', 0) + 1}_range", 5)),
            "active_speed":    prev.get("active_speed",    prev.get(f"m{prev.get('mode_index', 0) + 1}_speed", 5)),
            # brushing_duration_sec: sourced from p5. The slider writes it
            # optimistically so the UI updates immediately; the next status
            # packet from the device will confirm or correct it.
            "brushing_duration_sec": duration_sec,
            # Real-time pressure flag — updated by 0x0C telemetry packets at
            # ~100ms during brushing. Must survive the full-status dict replacement.
            "over_pressure_active": prev.get("over_pressure_active", False),
        }

    # ──────────────────────────────────────────────────────────────────
    # Write commands — V1 protocol (LFTB01)
    # ──────────────────────────────────────────────────────────────────

    async def turn_on(self):
        _LOGGER.debug(f"[{self.address}] turn_on: proto_version={self._proto_version}")
        if self._proto_version == "v2pro":
            return await self.send_command(build_v2pro_command(0x0108, [0x01]))
        return await self.send_command(bytes.fromhex("AA0F010101A4"))

    async def turn_off(self):
        _LOGGER.debug(f"[{self.address}] turn_off: proto_version={self._proto_version}")
        if self._proto_version == "v2pro":
            return await self.send_command(build_v2pro_command(0x0108, [0x00]))
        return await self.send_command(bytes.fromhex("AA0F010100A5"))

    async def set_mode(self, mode_index: int) -> bool:
        """
        Select a brushing mode.

        V1 (LFTB01): mode-select command (param=0x01, b3=0x01).
          Side effect: overwrites target mode's strength byte with checksum.
          Caller must immediately re-write strength/range/speed to fix this
          (handled in select.py for V1 devices).

        V2 Pro (Wave Pro): CMD_MODE=0x109, "switchMode", LEN=1
          AA 01 09 00 00 01 [mode] [cs]
          Source-confirmed (Tb92ControlActivity.switchMode). Same
          AA-[hi]-[lo]-00-00-[len]-... pattern as power on/off, airplane
          mode etc. — all of which use the SAME control characteristic
          (0000ff02) and are now confirmed safe. No known side effects to
          fix up afterwards.
        """
        if self._proto_version == "v2pro":
            success = await self.send_command(build_v2pro_command(0x0109, [mode_index]))
            if success:
                self._current_mode_index = mode_index
            return success

        cmd = build_command(0x01, 0x01, mode_index)
        success = await self.send_command(cmd)
        if success:
            self._current_mode_index = mode_index
        return success

    async def _set_v2pro_mode_params(self, strength: int | None = None,
                                      range_: int | None = None,
                                      speed: int | None = None) -> bool:
        """
        V2 Pro: CMD_MODE=0x109, "setMode(mode, strength, range, speed)", LEN=4
          AA 01 09 00 00 04 [mode] [strength] [range] [speed] [cs]

        UNCONFIRMED: command ID and parameter ORDER are taken from the
        decompiled setMode(int mode, int strength, int range, int speed)
        signature, but the live effect on the device hasn't been verified.
        Sends the currently-active mode index plus the cached per-mode
        strength/range/speed (from self.result), with the changed slider
        substituted in.
        """
        result      = self.result or {}
        mode_index  = result.get("mode_index", 0)
        base        = f"m{mode_index + 1}"
        cur_strength = result.get(f"{base}_strength", 5)
        cur_range    = result.get(f"{base}_range", 5)
        cur_speed    = result.get(f"{base}_speed", 5)

        if strength is not None:
            cur_strength = strength
        if range_ is not None:
            cur_range = range_
        if speed is not None:
            cur_speed = speed

        return await self.send_command(
            build_v2pro_command(0x0109, [mode_index, cur_strength, cur_range, cur_speed])
        )

    async def set_vibration_strength(self, value: int) -> bool:
        """Set vibration strength for the currently active mode. param=0x02 (V1) / setMode (V2 Pro)."""
        if self._proto_version == "v2pro":
            return await self._set_v2pro_mode_params(strength=value)
        return await self.send_command(build_command(0x02, 0x01, value))

    async def set_oscillation_range(self, value: int) -> bool:
        """Set oscillation range for the currently active mode. param=0x03 (V1) / setMode (V2 Pro)."""
        if self._proto_version == "v2pro":
            return await self._set_v2pro_mode_params(range_=value)
        return await self.send_command(build_command(0x03, 0x01, value))

    async def set_oscillation_speed(self, value: int) -> bool:
        """Set oscillation speed for the currently active mode. param=0x04 (V1) / setMode (V2 Pro)."""
        if self._proto_version == "v2pro":
            return await self._set_v2pro_mode_params(speed=value)
        return await self.send_command(build_command(0x04, 0x01, value))

    async def set_high_frequency(self, enabled: bool) -> bool:
        """
        Enable or disable High Frequency mode.

        V2 Pro: CMD_HIGH_FRE_ONOFF=0x203, LEN=1 -> AA 02 03 00 00 01 [val] [cs]
          Source-confirmed (Tb92ControlActivity.setHighFrequency).
          Note: per the Laifen app, this can only be enabled while Deep
          Clean is OFF — enforced in the switch entity, not here.

        V1: AA 0E 01 01 [val] [cs]
        """
        val = 0x01 if enabled else 0x00
        if self._proto_version == "v2pro":
            return await self.send_command(build_v2pro_command(0x0203, [val]))

        data = [0xAA, 0x0E, 0x01, 0x01, val]
        cs   = 0
        for b in data: cs ^= b
        return await self.send_command(bytes(data + [cs]))

    async def set_airplane_mode(self, enabled: bool) -> bool:
        """
        Enable or disable Airplane mode.

        V2 Pro (Wave Pro): CMD_TB_AIRPLANE_MODE=0x202, LEN=1
          AA 02 02 00 00 01 [val] [cs]
          Source-confirmed (Tb92ControlActivity.setFly -> setAirplaneModeData
          -> getCMD_TB_AIRPLANE_MODE), same toggle pattern as power on/off
          which is now confirmed working (response=True).

        V1 (LFTB01): AA 07 01 01 [val] [cs]
        """
        val = 0x01 if enabled else 0x00
        if self._proto_version == "v2pro":
            return await self.send_command(build_v2pro_command(0x0202, [val]))

        data = [0xAA, 0x07, 0x01, 0x01, val]
        cs   = 0
        for b in data: cs ^= b
        return await self.send_command(bytes(data + [cs]))

    async def set_reminder_30s(self, enabled: bool) -> bool:
        """
        Enable or disable 30s reminder.

        V2 Pro: UNCONFIRMED — using CMD_VIB_REMINDER=0x10C, LEN=1
          (AA 01 0C 00 00 01 [val] [cs]), found in
          PublicDataCommandManager.setVibration(Z) which seemed like the
          closest match by name, but this has NOT been live-tested.
          Recommend testing this one carefully and reporting back.

        V1: AA 0B 01 01 [val] [cs]
        """
        val = 0x01 if enabled else 0x00
        if self._proto_version == "v2pro":
            return await self.send_command(build_v2pro_command(0x010C, [val]))

        data = [0xAA, 0x0B, 0x01, 0x01, val]
        cs   = 0
        for b in data: cs ^= b
        return await self.send_command(bytes(data + [cs]))

    # ──────────────────────────────────────────────────────────────────
    # Write commands — V2 Pro only (Wave Pro / LFTB02-S-412B)
    # All confirmed via decompiled app source (TbDataCommandManager /
    # Tb92ControlActivity), same AA-02-XX-00-00-01-[val]-[cs] toggle
    # pattern as Airplane mode (CMD 0x202, confirmed working live).
    # Readback positions confirmed in _parse_v2pro_status.
    # ──────────────────────────────────────────────────────────────────

    async def set_deep_clean(self, enabled: bool) -> bool:
        """CMD_TB_POWER_COMPEN=0x207 (setDeepCleanMode->setPowerCompenData). Readback: p15."""
        if self._proto_version != "v2pro":
            _LOGGER.debug(f"[{self.address}] set_deep_clean: not implemented for {self._proto_version}")
            return False
        return await self.send_command(build_v2pro_command(0x0207, [0x01 if enabled else 0x00]))

    async def set_anti_splash(self, enabled: bool) -> bool:
        """CMD_TB_PRESSDEVICE_ON=0x210 (setPressureOpen). Readback: p16."""
        if self._proto_version != "v2pro":
            _LOGGER.debug(f"[{self.address}] set_anti_splash: not implemented for {self._proto_version}")
            return False
        return await self.send_command(build_v2pro_command(0x0210, [0x01 if enabled else 0x00]))

    async def set_power_ramp_up(self, enabled: bool) -> bool:
        """CMD_TB_FADEIN_ONOFF=0x211 (setFadeIn), '3s Power Ramp-Up'. Readback: p24."""
        if self._proto_version != "v2pro":
            _LOGGER.debug(f"[{self.address}] set_power_ramp_up: not implemented for {self._proto_version}")
            return False
        return await self.send_command(build_v2pro_command(0x0211, [0x01 if enabled else 0x00]))

    async def set_bristle_protection(self, enabled: bool) -> bool:
        """CMD_TB_SLEEP_PROTEC_ONOFF=0x213 (setSleepProtect), 'Bristle Protection'. Readback: p30."""
        if self._proto_version != "v2pro":
            _LOGGER.debug(f"[{self.address}] set_bristle_protection: not implemented for {self._proto_version}")
            return False
        return await self.send_command(build_v2pro_command(0x0213, [0x01 if enabled else 0x00]))

    async def set_lift_to_wake(self, enabled: bool) -> bool:
        """CMD_TB_WAKEUP_MODE=0x208 (setWakeup), 'Lift to Wake' reminder. Readback: p32.

        Note: decompiled setWakeup(Z,I) takes a boolean + an int (possibly
        a delay/timeout). Starting with a simple LEN=1 toggle matching the
        confirmed numeric pattern; if this doesn't fully work, a 2-byte
        payload may be needed.
        """
        if self._proto_version != "v2pro":
            _LOGGER.debug(f"[{self.address}] set_lift_to_wake: not implemented for {self._proto_version}")
            return False
        return await self.send_command(build_v2pro_command(0x0208, [0x01 if enabled else 0x00]))

    async def set_brushing_duration(self, value: int, mode: int | None = None) -> bool:
        """
        CMD_TB_BRUSHING_TIME=0x200 (setBrushingTime).

        Confirmed via HCI snoop capture of the Laifen app. The command format is:

            AA 02 00 00 00 03 [mode] [dur_hi] [dur_lo] [xor_cs]

        - LEN = 3
        - payload[0] = mode index (0-3, one of the four brushing modes)
        - payload[1:3] = duration in seconds, BIG-ENDIAN 16-bit
          (note: the status packet reports duration little-endian, but the
           command uses big-endian — they differ)

        The app sends four separate commands (one per mode 0-3) to set the
        same duration across all modes. We replicate that by default so the
        duration applies regardless of which mode is active. If `mode` is
        given, only that mode is set.

        Range 60-300 seconds (1-5 min) in 30-second steps, matching the app.
        """
        if self._proto_version != "v2pro":
            _LOGGER.debug(f"[{self.address}] set_brushing_duration: not implemented for {self._proto_version}")
            return False

        seconds = max(60, min(300, int(round(value / 30) * 30)))
        hi = (seconds >> 8) & 0xFF
        lo = seconds & 0xFF

        modes = [mode] if mode is not None else [0, 1, 2, 3]
        ok = True
        for m in modes:
            cmd = build_v2pro_command(0x0200, [m & 0xFF, hi, lo])
            result = await self.send_command(cmd)
            ok = ok and result
            # Small gap between the per-mode writes, mirroring the app
            await asyncio.sleep(0.15)
        return ok

    async def set_over_pressure_level(self, level: str) -> bool:
        """
        CMD_TB_PRESS_REMINDER=0x20B (setPressReminder), LEN=3.

        UNCONFIRMED: decompiled setPressReminder(boolean enabled, int a, int b)
        takes 3 params, matching the 3 confirmed readback bytes p26/p27/p28.
        Selecting a level implies enabling Over Pressure (p26=1), with
        (p27,p28) set to the byte pair confirmed for that level:
          Light=(0x00,0xDC)  Medium=(0x01,0x4A)  Hard=(0x01,0xB8)
        i.e. AA 02 0B 00 00 03 [enabled] [p27] [p28] [cs]
        """
        if self._proto_version != "v2pro":
            _LOGGER.debug(f"[{self.address}] set_over_pressure_level: not implemented for {self._proto_version}")
            return False

        levels = {
            "Light":  (0x00, 0xDC),
            "Medium": (0x01, 0x4A),
            "Hard":   (0x01, 0xB8),
        }
        if level not in levels:
            _LOGGER.warning(f"[{self.address}] set_over_pressure_level: unknown level {level!r}")
            return False

        p27, p28 = levels[level]
        return await self.send_command(build_v2pro_command(0x020B, [0x01, p27, p28]))

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────

    def _empty_result(self, data):
        return {
            "raw_data":           data.hex() if data else "",
            "status":             "Unknown",
            "mode":               "1",
            "mode_index":         0,
            "battery_level":      0,
            "brushing_time":      0,
            "vibration_strength": 0,
            "oscillation_range":  0,
            "oscillation_speed":  0,
            "m1_strength": 0, "m1_range": 0, "m1_speed": 0,
            "m2_strength": 0, "m2_range": 0, "m2_speed": 0,
            "m3_strength": 0, "m3_range": 0, "m3_speed": 0,
            "m4_strength": 0, "m4_range": 0, "m4_speed": 0,
            "airplane_mode":  False,
            "high_frequency": False,
            "reminder_30s":   False,
        }
