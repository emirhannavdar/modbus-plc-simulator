import json
import logging
import struct
import threading
import time

from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pymodbus.client import ModbusTcpClient


# ============================================================
# CONFIGURATION
# ============================================================

@dataclass
class AppConfig:
    api_url: str = "http://127.0.0.1:8000"
    api_refresh_interval: float = 5.0
    connection_timeout: float = 3.0
    default_poll_interval: float = 1.0
    reconnect_interval: float = 2.0


CONFIG = AppConfig()


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("SCADA")


# ============================================================
# HTTP CLIENT
# ============================================================

class APIClient:

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, endpoint: str, payload=None):
        url = f"{self.base_url}{endpoint}"

        data = None

        headers = {
            "Accept": "application/json",
        }

        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )

        try:
            with urlopen(
                request,
                timeout=CONFIG.connection_timeout,
            ) as response:

                body = response.read().decode("utf-8")

                if not body:
                    return {}

                return json.loads(body)

        except HTTPError as exc:

            error_body = ""

            try:
                error_body = exc.read().decode("utf-8")
            except Exception:
                pass

            if error_body:
                raise RuntimeError(
                    f"HTTP {exc.code}: {error_body}"
                ) from exc

            raise

    def get(self, endpoint: str):
        return self.request("GET", endpoint)

    def post(self, endpoint: str, payload):
        return self.request(
            "POST",
            endpoint,
            payload,
        )

    # --------------------------------------------------------
    # DEVICES
    # --------------------------------------------------------

    def get_devices(self):
        return self.get(
            "/api/v1/devices"
        )

    def get_device_registers(self, device_id: int):
        return self.get(
            f"/api/v1/devices/{device_id}/registers"
        )

    # --------------------------------------------------------
    # COMMANDS
    # --------------------------------------------------------

    def get_pending_commands(self):
        return self.get(
            "/api/v1/commands/pending"
        )

    def complete_command(
        self,
        command_id,
        success,
        error=None,
    ):
        return self.post(
            f"/api/v1/commands/{command_id}/result",
            {
                "status": (
                    "completed"
                    if success
                    else "failed"
                ),
                "error": error,
            },
        )

    # --------------------------------------------------------
    # SNAPSHOT
    # --------------------------------------------------------

    def send_snapshot(
        self,
        device_id,
        values,
    ):
        return self.post(
            "/api/v1/scada/snapshot",
            {
                "device_id": device_id,
                "values": values,
            },
        )

    # --------------------------------------------------------
    # HEARTBEAT
    # --------------------------------------------------------

    def heartbeat(
        self,
        device_id,
        status="online",
        register_count=0,
        error=None,
    ):
        return self.post(
            "/api/v1/scada/heartbeat",
            {
                "device_id": device_id,
                "status": status,
                "register_count": register_count,
                "error": error,
            },
        )


# ============================================================
# REGISTER HELPERS
# ============================================================

def register_length(data_type: str) -> int:

    lengths = {
        "BOOL": 1,
        "UINT16": 1,
        "INT16": 1,
        "UINT32": 2,
        "INT32": 2,
        "FLOAT32": 2,
    }

    if data_type not in lengths:
        raise ValueError(
            f"Unsupported data type: {data_type}"
        )

    return lengths[data_type]


def decode_registers(
    registers: list[int],
    data_type: str,
):

    if data_type == "BOOL":
        return bool(registers[0])

    if data_type == "UINT16":
        return registers[0]

    if data_type == "INT16":

        raw = struct.pack(
            ">H",
            registers[0],
        )

        return struct.unpack(
            ">h",
            raw,
        )[0]

    if data_type == "UINT32":

        raw = struct.pack(
            ">HH",
            registers[0],
            registers[1],
        )

        return struct.unpack(
            ">I",
            raw,
        )[0]

    if data_type == "INT32":

        raw = struct.pack(
            ">HH",
            registers[0],
            registers[1],
        )

        return struct.unpack(
            ">i",
            raw,
        )[0]

    if data_type == "FLOAT32":

        raw = struct.pack(
            ">HH",
            registers[0],
            registers[1],
        )

        return struct.unpack(
            ">f",
            raw,
        )[0]

    raise ValueError(
        f"Unsupported data type: {data_type}"
    )


def encode_value(value, data_type):
    """
    Convert an engineering value into Modbus holding-register words.

    FLOAT32 uses IEEE-754 big-endian representation:
        float -> 4 bytes -> 2 x 16-bit Modbus registers
    """

    data_type = str(data_type).upper()

    if data_type == "UINT16":
        value = int(round(float(value)))

        if not 0 <= value <= 65535:
            raise ValueError(
                f"UINT16 value out of range: {value}"
            )

        return [value]

    if data_type == "INT16":
        value = int(round(float(value)))

        if not -32768 <= value <= 32767:
            raise ValueError(
                f"INT16 value out of range: {value}"
            )

        if value < 0:
            value += 65536

        return [value]

    if data_type == "FLOAT32":
        raw = struct.pack(">f", float(value))

        high_word, low_word = struct.unpack(">HH", raw)

        return [high_word, low_word]

    if data_type == "UINT32":
        value = int(round(float(value)))

        if not 0 <= value <= 4294967295:
            raise ValueError(
                f"UINT32 value out of range: {value}"
            )

        return [
            (value >> 16) & 0xFFFF,
            value & 0xFFFF,
        ]

    if data_type == "INT32":
        value = int(round(float(value)))

        if not -2147483648 <= value <= 2147483647:
            raise ValueError(
                f"INT32 value out of range: {value}"
            )

        if value < 0:
            value += 4294967296

        return [
            (value >> 16) & 0xFFFF,
            value & 0xFFFF,
        ]

    raise ValueError(
        f"Unsupported data type for write: {data_type}"
    )


# ============================================================
# SCALING
# ============================================================

def apply_scaling(
    raw_value,
    register,
):
    multiplier = float(
        register.get(
            "multiplier",
            1.0,
        )
    )

    offset = float(
        register.get(
            "offset",
            0.0,
        )
    )

    return (
        raw_value * multiplier
        + offset
    )


def remove_scaling(
    engineering_value,
    register,
):
    multiplier = float(
        register.get(
            "multiplier",
            1.0,
        )
    )

    offset = float(
        register.get(
            "offset",
            0.0,
        )
    )

    if multiplier == 0:
        raise ValueError(
            f"Multiplier cannot be zero for "
            f"register {register.get('name')}"
        )

    return (
        float(engineering_value) - offset
    ) / multiplier


# ============================================================
# DEVICE STATE
# ============================================================

@dataclass
class DeviceState:

    device: dict

    registers: list = field(
        default_factory=list
    )

    client: object = None

    connected: bool = False

    last_connection_attempt: float = 0.0

    last_successful_read: float = 0.0

    last_heartbeat: object = None

    last_register_refresh: float = 0.0

    lock: threading.Lock = field(
        default_factory=threading.Lock
    )

    def device_id(self):
        return self.device["id"]

    def device_name(self):
        return self.device["name"]

    def host(self):
        return self.device["host"]

    def port(self):
        return self.device["port"]

    def unit_id(self):
        return self.device["unit_id"]

    def poll_interval(self):

        value = self.device.get(
            "poll_interval",
            1000,
        )

        try:
            value = float(value) / 1000.0
        except (
            TypeError,
            ValueError,
        ):
            value = CONFIG.default_poll_interval

        return max(
            value,
            0.1,
        )


# ============================================================
# SCADA ENGINE
# ============================================================

class SCADAEngine:

    def __init__(self):

        self.api = APIClient(
            CONFIG.api_url
        )

        self.devices = {}

        self.devices_lock = threading.Lock()

        self.running = True

        self.last_device_refresh = 0.0

    # ========================================================
    # DEVICE CONFIGURATION
    # ========================================================

    def refresh_devices(self):

        try:

            response = self.api.get_devices()

            devices = response.get(
                "devices",
                [],
            )

            active_devices = {
                device["id"]: device
                for device in devices
                if device.get(
                    "enabled",
                    True,
                )
            }

            with self.devices_lock:

                existing_ids = set(
                    self.devices.keys()
                )

                active_ids = set(
                    active_devices.keys()
                )

                new_ids = (
                    active_ids
                    - existing_ids
                )

                removed_ids = (
                    existing_ids
                    - active_ids
                )

                # --------------------------------------------
                # Update existing devices
                # --------------------------------------------

                for device_id, device in (
                    active_devices.items()
                ):

                    if device_id in self.devices:

                        self.devices[
                            device_id
                        ].device = device

                # --------------------------------------------
                # Add new devices
                # --------------------------------------------

                for device_id in new_ids:

                    state = DeviceState(
                        device=active_devices[
                            device_id
                        ]
                    )

                    self.devices[
                        device_id
                    ] = state

                    logger.info(
                        "New device discovered: "
                        "%s (%s:%s)",
                        state.device_name(),
                        state.host(),
                        state.port(),
                    )

                # --------------------------------------------
                # Remove disabled/deleted devices
                # --------------------------------------------

                for device_id in removed_ids:

                    state = self.devices[
                        device_id
                    ]

                    logger.info(
                        "Device removed or disabled: %s",
                        state.device_name(),
                    )

                    self.close_device(
                        state
                    )

                    del self.devices[
                        device_id
                    ]

            # ------------------------------------------------
            # Refresh register configuration
            # ------------------------------------------------

            for device_id in active_devices:

                self.refresh_device_registers(
                    device_id
                )

            self.last_device_refresh = (
                time.time()
            )

            logger.info(
                "Device configuration loaded: %d devices",
                len(active_devices),
            )

        except Exception as exc:

            logger.error(
                "Could not load device configuration: %s",
                exc,
            )

    # ========================================================
    # REGISTER CONFIGURATION
    # ========================================================

    def refresh_device_registers(
        self,
        device_id,
    ):

        try:

            response = (
                self.api.get_device_registers(
                    device_id
                )
            )

            registers = response.get(
                "registers",
                [],
            )

            registers = [
                register
                for register in registers
                if register.get(
                    "enabled",
                    True,
                )
            ]

            registers.sort(
                key=lambda item: item[
                    "address"
                ]
            )

            with self.devices_lock:

                state = self.devices.get(
                    device_id
                )

            if state is None:
                return

            with state.lock:

                state.registers = registers

                state.last_register_refresh = (
                    time.time()
                )

            logger.info(
                "%s: Register configuration loaded: %d registers",
                state.device_name(),
                len(registers),
            )

        except Exception as exc:

            logger.error(
                "Device %s register configuration error: %s",
                device_id,
                exc,
            )

    # ========================================================
    # MODBUS CONNECTION
    # ========================================================

    def connect_device(
        self,
        state: DeviceState,
    ):

        now = time.time()

        if (
            now
            - state.last_connection_attempt
            < CONFIG.reconnect_interval
        ):
            return False

        state.last_connection_attempt = now

        self.close_device(
            state
        )

        try:

            client = ModbusTcpClient(
                host=state.host(),
                port=state.port(),
                timeout=CONFIG.connection_timeout,
            )

            if client.connect():

                state.client = client

                if not state.connected:

                    logger.info(
                        "%s connected to PLC %s:%s",
                        state.device_name(),
                        state.host(),
                        state.port(),
                    )

                state.connected = True

                try:

                    self.api.heartbeat(
                        device_id=state.device_id(),
                        status="online",
                        register_count=len(
                            state.registers
                        ),
                    )

                except Exception as exc:

                    logger.warning(
                        "%s heartbeat failed: %s",
                        state.device_name(),
                        exc,
                    )

                return True

            client.close()

        except Exception as exc:

            logger.error(
                "%s connection error: %s",
                state.device_name(),
                exc,
            )

        state.connected = False

        return False

    # ========================================================
    # CLOSE DEVICE
    # ========================================================

    def close_device(
        self,
        state: DeviceState,
    ):

        if state.client:

            try:
                state.client.close()
            except Exception:
                pass

        state.client = None
        state.connected = False

    # ========================================================
    # MODBUS READ
    # ========================================================

    def read_device_registers(
        self,
        state: DeviceState,
    ):

        with state.lock:

            registers = list(
                state.registers
            )

        if not registers:
            return []

        if state.client is None:
            raise RuntimeError(
                "PLC is not connected"
            )

        start_address = min(
            item["address"]
            for item in registers
        )

        end_address = max(
            item["address"]
            + register_length(
                item["data_type"]
            )
            - 1
            for item in registers
        )

        count = (
            end_address
            - start_address
            + 1
        )

        try:

            response = (
                state.client.read_holding_registers(
                    address=start_address,
                    count=count,
                    device_id=state.unit_id(),
                )
            )

            if response.isError():

                raise RuntimeError(
                    f"Modbus read error: {response}"
                )

            raw = list(
                response.registers
            )

            snapshot = []

            for register in registers:

                offset = (
                    register["address"]
                    - start_address
                )

                length = register_length(
                    register["data_type"]
                )

                words = raw[
                    offset:
                    offset + length
                ]

                if len(words) != length:
                    continue

                raw_value = decode_registers(
                    words,
                    register["data_type"],
                )

                value = apply_scaling(
                    raw_value,
                    register,
                )

                snapshot.append(
                    {
                        "register_id": register[
                            "id"
                        ],
                        "value": value,
                    }
                )

                if (
                    register["name"]
                    == "Heartbeat"
                ):

                    state.last_heartbeat = value

            state.last_successful_read = (
                time.time()
            )

            return snapshot

        except Exception:

            self.close_device(
                state
            )

            try:

                self.api.heartbeat(
                    device_id=state.device_id(),
                    status="offline",
                    register_count=len(
                        registers
                    ),
                    error="Modbus read failed",
                )

            except Exception:
                pass

            raise

    # ========================================================
    # MODBUS WRITE
    # ========================================================

    def write_register(self, state, register, value):
        register_id = register["id"]
        name = register["name"]
        address = int(register["address"])
        data_type = str(register["data_type"]).upper()

        try:
            engineering_value = float(value)

            # Remove engineering scaling before writing raw PLC value.
            raw_value = remove_scaling(
                engineering_value,
                register,
            )

            words = encode_value(
                raw_value,
                data_type,
            )

            if not words:
                raise ValueError(
                    f"No Modbus words generated for {name}"
                )

            logging.info(
                "PLC WRITE REQUEST | %s | address=%s | type=%s | value=%s | words=%s",
                state.device["name"],
                address,
                data_type,
                engineering_value,
                words,
            )

            if len(words) == 1:
                result = state.client.write_register(
                    address=address,
                    value=words[0],
                    device_id=state.unit_id(),
                )
            else:
                result = state.client.write_registers(
                    address=address,
                    values=words,
                    device_id=state.unit_id(),
                )

            if result.isError():
                raise RuntimeError(
                    f"Modbus write failed: {result}"
                )

            logging.info(
                "PLC WRITE SUCCESS | %s | %s = %s | words=%s",
                state.device["name"],
                name,
                engineering_value,
                words,
            )

            return True

        except Exception as exc:
            logging.error(
                "PLC WRITE FAILED | %s | %s = %s | %s",
                state.device["name"],
                name,
                value,
                exc,
            )

            return False

    # ========================================================
    # FIND REGISTER
    # ========================================================

    def find_register(
        self,
        state: DeviceState,
        register_id,
    ):

        with state.lock:

            for register in state.registers:

                if register["id"] == register_id:
                    return register

        return None

    # ========================================================
    # COMMAND PROCESSING
    # ========================================================

    def process_commands(self):

        try:

            response = (
                self.api.get_pending_commands()
            )

            commands = response.get(
                "commands",
                [],
            )

            for command in commands:

                command_id = command["id"]

                device_id = command.get(
                    "device_id"
                )

                if device_id is None:

                    logger.error(
                        "Command %s has no device_id",
                        command_id,
                    )

                    try:

                        self.api.complete_command(
                            command_id,
                            False,
                            "Command has no device_id",
                        )

                    except Exception:
                        pass

                    continue

                with self.devices_lock:

                    state = self.devices.get(
                        device_id
                    )

                if state is None:

                    logger.warning(
                        "Command %s belongs to unknown device %s",
                        command_id,
                        device_id,
                    )

                    try:

                        self.api.complete_command(
                            command_id,
                            False,
                            f"Device {device_id} is not available",
                        )

                    except Exception:
                        pass

                    continue

                try:

                    if not state.connected:

                        if not self.connect_device(
                            state
                        ):

                            raise RuntimeError(
                                f"{state.device_name()} "
                                f"is not connected"
                            )

                    register = self.find_register(
                        state,
                        command["register_id"],
                    )

                    if register is None:

                        raise RuntimeError(
                            f"Register "
                            f"{command['register_id']} "
                            f"does not belong to "
                            f"device {device_id}"
                        )

                    if register["access"] not in (
                        "write",
                        "read_write",
                    ):

                        raise RuntimeError(
                            f"Register "
                            f"{register['name']} "
                            f"is not writable"
                        )

                    self.write_register(
                        state=state,
                        register=register,
                        value=command["value"],
                    )

                    self.api.complete_command(
                        command_id,
                        True,
                    )

                    logger.info(
                        "Command executed | "
                        "%s | %s = %s",
                        state.device_name(),
                        register["name"],
                        command["value"],
                    )

                except Exception as exc:

                    logger.error(
                        "Command %s failed on %s: %s",
                        command_id,
                        state.device_name(),
                        exc,
                    )

                    try:

                        self.api.complete_command(
                            command_id,
                            False,
                            str(exc),
                        )

                    except Exception:
                        pass

        except Exception as exc:

            logger.error(
                "Command polling error: %s",
                exc,
            )

    # ========================================================
    # SNAPSHOT
    # ========================================================

    def publish_snapshot(
        self,
        state: DeviceState,
        snapshot,
    ):

        if not snapshot:
            return

        try:

            self.api.send_snapshot(
                device_id=state.device_id(),
                values=snapshot,
            )

        except Exception as exc:

            logger.error(
                "%s snapshot API error: %s",
                state.device_name(),
                exc,
            )

    # ========================================================
    # DEVICE POLLING
    # ========================================================

    def poll_device(
        self,
        state: DeviceState,
    ):

        if (
            time.time()
            - state.last_register_refresh
            >= CONFIG.api_refresh_interval
        ):

            self.refresh_device_registers(
                state.device_id()
            )

        if state.client is None:

            if not self.connect_device(
                state
            ):
                return

        snapshot = (
            self.read_device_registers(
                state
            )
        )

        if snapshot:

            self.publish_snapshot(
                state,
                snapshot,
            )

    # ========================================================
    # MAIN LOOP
    # ========================================================

    def run(self):

        logger.info("=" * 70)

        logger.info(
            "SCADAWATT MULTI-DEVICE SCADA ENGINE"
        )

        logger.info("=" * 70)

        self.refresh_devices()

        while self.running:

            cycle_start = time.time()

            try:

                if (
                    time.time()
                    - self.last_device_refresh
                    >= CONFIG.api_refresh_interval
                ):

                    self.refresh_devices()

                with self.devices_lock:

                    device_states = list(
                        self.devices.values()
                    )

                for state in device_states:

                    if not self.running:
                        break

                    try:

                        self.poll_device(
                            state
                        )

                    except Exception as exc:

                        logger.error(
                            "%s polling error: %s",
                            state.device_name(),
                            exc,
                        )

                        self.close_device(
                            state
                        )

                        state.connected = False

                self.process_commands()

            except (
                ConnectionError,
                URLError,
                HTTPError,
            ) as exc:

                logger.error(
                    "Communication error: %s",
                    exc,
                )

            except Exception as exc:

                logger.error(
                    "SCADA cycle error: %s",
                    exc,
                )

            elapsed = (
                time.time()
                - cycle_start
            )

            with self.devices_lock:

                if self.devices:

                    poll_intervals = [
                        state.poll_interval()
                        for state in
                        self.devices.values()
                    ]

                    sleep_time = min(
                        poll_intervals
                    )

                else:

                    sleep_time = (
                        CONFIG.default_poll_interval
                    )

            sleep_time = max(
                0.05,
                sleep_time - elapsed,
            )

            time.sleep(
                sleep_time
            )

        with self.devices_lock:

            for state in self.devices.values():

                self.close_device(
                    state
                )

    # ========================================================
    # STOP
    # ========================================================

    def stop(self):

        self.running = False

        with self.devices_lock:

            for state in self.devices.values():

                self.close_device(
                    state
                )


# ============================================================
# APPLICATION
# ============================================================

def main():

    engine = SCADAEngine()

    try:

        engine.run()

    except KeyboardInterrupt:

        logger.info(
            "SCADA shutting down..."
        )

    finally:

        engine.stop()


if __name__ == "__main__":
    main()