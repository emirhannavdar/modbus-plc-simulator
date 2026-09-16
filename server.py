from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Optional

from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice


# ============================================================
# APPLICATION CONFIGURATION
# ============================================================

@dataclass(frozen=True)
class AppConfig:
    """Central PLC simulator configuration."""

    ip: str = "127.0.0.1"
    port: int = 5020
    unit_id: int = 1

    # PLC scan cycle
    scan_time_s: float = 0.5

    # Motor
    max_rpm: int = 3000
    acceleration_rpm_per_scan: int = 100
    deceleration_rpm_per_scan: int = 150

    # Process limits
    max_pressure_bar: float = 10.0
    max_temperature_c: float = 100.0
    max_current_a: float = 30.0

    # Alarm thresholds
    warning_pressure_bar: float = 8.5
    warning_temperature_c: float = 70.0
    warning_current_a: float = 24.0

    # Scaling
    pressure_scale: int = 10
    current_scale: int = 10
    temperature_scale: int = 10

    # Process dynamics
    ambient_temperature_c: float = 25.0
    temperature_rise_per_rpm: float = 0.012
    temperature_cooling_factor: float = 0.08

    pressure_response_factor: float = 0.20

    # Logging
    state_log_interval_s: float = 10.0


CONFIG = AppConfig()


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)-8s | "
        "%(name)-16s | "
        "%(message)s"
    ),
)

LOGGER = logging.getLogger("PLC")


# ============================================================
# REGISTER MAP
# ============================================================

class HR(IntEnum):
    """
    Holding Register map.

    Zero-based Modbus addresses.

    Example:

        HR.MOTOR_COMMAND = 0

    corresponds to:

        Holding Register 40001
    """

    MOTOR_COMMAND = 0
    MODE = 1

    SPEED_SETPOINT_HI = 2
    SPEED_SETPOINT_LO = 3

    ACTUAL_RPM_HI = 4
    ACTUAL_RPM_LO = 5

    CURRENT_HI = 6
    CURRENT_LO = 7

    PRESSURE_SETPOINT_HI = 8
    PRESSURE_SETPOINT_LO = 9

    ACTUAL_PRESSURE_HI = 10
    ACTUAL_PRESSURE_LO = 11

    TEMPERATURE_HI = 12
    TEMPERATURE_LO = 13

    ALARM = 14
    EMERGENCY_STOP = 15

    HEARTBEAT = 16
    STATUS_WORD = 17
    FAULT_CODE = 18

    RUNTIME_SECONDS_HI = 19
    RUNTIME_SECONDS_LO = 20


REGISTER_COUNT = 21


# ============================================================
# ENUMS
# ============================================================

class MotorCommand(IntEnum):
    STOP = 0
    START = 1


class OperationMode(IntEnum):
    MANUAL = 0
    AUTO = 1


class MotorState(IntEnum):
    STOPPED = 0
    STARTING = 1
    RUNNING = 2
    STOPPING = 3
    FAULT = 4


class FaultCode(IntEnum):
    NONE = 0
    EMERGENCY_STOP = 1
    OVER_TEMPERATURE = 2
    OVER_SPEED = 3
    OVER_CURRENT = 4
    OVER_PRESSURE = 5
    WATCHDOG_TIMEOUT = 6
    INVALID_SETPOINT = 7


class StatusBits(IntFlag):
    STOPPED = 1 << 0
    RUNNING = 1 << 1

    AUTO_MODE = 1 << 2
    MANUAL_MODE = 1 << 3

    ALARM_ACTIVE = 1 << 4
    EMERGENCY_STOP = 1 << 5
    FAULT_ACTIVE = 1 << 6
    READY = 1 << 7


# ============================================================
# INITIAL REGISTER VALUES
# ============================================================

INITIAL_VALUES = [0] * REGISTER_COUNT

# Motor
INITIAL_VALUES[HR.MOTOR_COMMAND] = MotorCommand.STOP

# Mode
INITIAL_VALUES[HR.MODE] = OperationMode.MANUAL

# Speed setpoint
INITIAL_VALUES[HR.SPEED_SETPOINT_HI] = 0
INITIAL_VALUES[HR.SPEED_SETPOINT_LO] = 0

# Pressure setpoint = 5.0 bar
INITIAL_VALUES[HR.PRESSURE_SETPOINT_HI] = 0
INITIAL_VALUES[HR.PRESSURE_SETPOINT_LO] = 50

# Emergency stop released
INITIAL_VALUES[HR.EMERGENCY_STOP] = 0

# Temperature starts at ambient = 25.0 C
INITIAL_VALUES[HR.TEMPERATURE_HI] = 0
INITIAL_VALUES[HR.TEMPERATURE_LO] = 250


# ============================================================
# PYMODBUS SIMULATOR DATASTORE
# ============================================================

simdata = SimData(
    address=0,
    count=REGISTER_COUNT,
    values=INITIAL_VALUES,
    datatype=DataType.REGISTERS,
    readonly=False,
)

device = SimDevice(
    id=CONFIG.unit_id,
    simdata=[simdata],
)


# ============================================================
# REGISTER DATABASE
# ============================================================

class RegisterDatabase:
    """
    Thread-safe abstraction over the real PyModbus datastore.

    The Modbus datastore remains the single source of truth.

    Modbus Poll
          |
          v
    PyModbus datastore
          |
          v
    RegisterDatabase
          |
          v
    PLC logic
    """

    HOLDING_REGISTER_FUNCTION = 3

    def __init__(
        self,
        server: ModbusTcpServer,
        device_id: int,
    ) -> None:
        self.server = server
        self.device_id = device_id

    async def read(
        self,
        address: int,
        count: int = 1,
    ) -> list[int]:

        if address < 0:
            raise ValueError("address must be >= 0")

        if count <= 0:
            raise ValueError("count must be > 0")

        if address + count > REGISTER_COUNT:
            raise ValueError(
                f"register range exceeds datastore: "
                f"address={address}, count={count}"
            )

        values = await self.server.async_getValues(
            self.device_id,
            self.HOLDING_REGISTER_FUNCTION,
            address,
            count=count,
        )

        return [
            int(value) & 0xFFFF
            for value in values
        ]

    async def write(
        self,
        address: int,
        values: list[int],
    ) -> None:

        if address < 0:
            raise ValueError("address must be >= 0")

        if not values:
            raise ValueError("values cannot be empty")

        if address + len(values) > REGISTER_COUNT:
            raise ValueError(
                f"register range exceeds datastore: "
                f"address={address}, "
                f"count={len(values)}"
            )

        sanitized_values = [
            int(value) & 0xFFFF
            for value in values
        ]

        await self.server.async_setValues(
            self.device_id,
            self.HOLDING_REGISTER_FUNCTION,
            address,
            sanitized_values,
        )

    async def read_one(
        self,
        address: int,
    ) -> int:

        values = await self.read(
            address,
            1,
        )

        return values[0]

    async def write_one(
        self,
        address: int,
        value: int,
    ) -> None:

        await self.write(
            address,
            [value],
        )

    async def read_uint32(
        self,
        high_address: int,
    ) -> int:

        values = await self.read(
            high_address,
            2,
        )

        return (
            (values[0] << 16)
            | values[1]
        )

    async def write_uint32(
        self,
        high_address: int,
        value: int,
    ) -> None:

        value = max(
            0,
            min(
                int(value),
                0xFFFFFFFF,
            ),
        )

        await self.write(
            high_address,
            [
                (value >> 16) & 0xFFFF,
                value & 0xFFFF,
            ],
        )


# ============================================================
# PLC PROCESS STATE
# ============================================================

@dataclass
class ProcessState:
    actual_rpm: int = 0

    current_a: float = 0.0

    pressure_bar: float = 0.0

    temperature_c: float = 25.0

    alarm: bool = False

    fault_code: FaultCode = FaultCode.NONE

    motor_state: MotorState = MotorState.STOPPED

    runtime_seconds: float = 0.0

    heartbeat: int = 0

    scan_cycles: int = 0

    last_state_log_time: float = 0.0


# ============================================================
# PLC SIMULATOR
# ============================================================

class PLCSimulator:
    """
    Industrial-style PLC process simulator.

    Execution pipeline:

        READ INPUTS
             ↓
        VALIDATE
             ↓
        PROTECTION
             ↓
        MOTOR STATE MACHINE
             ↓
        PROCESS MODEL
             ↓
        ALARM / FAULT
             ↓
        REGISTER PUBLISH
             ↓
        LOGGING
    """

    def __init__(
        self,
        registers: RegisterDatabase,
        config: AppConfig,
    ) -> None:

        self.registers = registers
        self.config = config

        self.state = ProcessState()

        self.stop_event = asyncio.Event()

        self.logger = logging.getLogger(
            "PLC.Simulator"
        )

        self._last_commands: Optional[dict] = None
        self._last_fault = FaultCode.NONE
        self._last_motor_state = MotorState.STOPPED

    # ========================================================
    # COMMAND READ
    # ========================================================

    async def read_commands(self) -> dict:

        motor_command = await self.registers.read_one(
            HR.MOTOR_COMMAND
        )

        mode = await self.registers.read_one(
            HR.MODE
        )

        emergency_stop = await self.registers.read_one(
            HR.EMERGENCY_STOP
        )

        speed_setpoint = await self.registers.read_uint32(
            HR.SPEED_SETPOINT_HI
        )

        pressure_setpoint_raw = (
            await self.registers.read_uint32(
                HR.PRESSURE_SETPOINT_HI
            )
        )

        pressure_setpoint = (
            pressure_setpoint_raw
            / self.config.pressure_scale
        )

        return {
            "motor_command": motor_command,
            "mode": mode,
            "emergency_stop": emergency_stop,
            "speed_setpoint": speed_setpoint,
            "pressure_setpoint": pressure_setpoint,
        }

    # ========================================================
    # INPUT VALIDATION
    # ========================================================

    def validate_commands(
        self,
        commands: dict,
    ) -> FaultCode:

        speed = commands["speed_setpoint"]

        pressure = commands["pressure_setpoint"]

        motor_command = commands["motor_command"]

        mode = commands["mode"]

        emergency_stop = commands["emergency_stop"]

        if motor_command not in (
            MotorCommand.STOP,
            MotorCommand.START,
        ):
            return FaultCode.INVALID_SETPOINT

        if mode not in (
            OperationMode.MANUAL,
            OperationMode.AUTO,
        ):
            return FaultCode.INVALID_SETPOINT

        if emergency_stop not in (0, 1):
            return FaultCode.INVALID_SETPOINT

        if speed > self.config.max_rpm:
            return FaultCode.OVER_SPEED

        if pressure > self.config.max_pressure_bar:
            return FaultCode.OVER_PRESSURE

        return FaultCode.NONE

    # ========================================================
    # PROTECTION
    # ========================================================

    def determine_fault(
        self,
        commands: dict,
        validation_fault: FaultCode,
    ) -> FaultCode:

        if commands["emergency_stop"] == 1:
            return FaultCode.EMERGENCY_STOP

        if validation_fault != FaultCode.NONE:
            return validation_fault

        if (
            self.state.temperature_c
            >= self.config.max_temperature_c
        ):
            return FaultCode.OVER_TEMPERATURE

        if (
            self.state.actual_rpm
            > self.config.max_rpm
        ):
            return FaultCode.OVER_SPEED

        if (
            self.state.current_a
            > self.config.max_current_a
        ):
            return FaultCode.OVER_CURRENT

        if (
            self.state.pressure_bar
            > self.config.max_pressure_bar
        ):
            return FaultCode.OVER_PRESSURE

        return FaultCode.NONE

    # ========================================================
    # ALARM
    # ========================================================

    def determine_alarm(self) -> bool:

        if self.state.fault_code != FaultCode.NONE:
            return True

        if (
            self.state.temperature_c
            >= self.config.warning_temperature_c
        ):
            return True

        if (
            self.state.current_a
            >= self.config.warning_current_a
        ):
            return True

        if (
            self.state.pressure_bar
            >= self.config.warning_pressure_bar
        ):
            return True

        return False

    # ========================================================
    # MOTOR STATE MACHINE
    # ========================================================

    def update_motor(
        self,
        commands: dict,
    ) -> None:

        motor_command = commands["motor_command"]

        speed_setpoint = commands["speed_setpoint"]

        emergency_stop = commands["emergency_stop"]

        fault = self.state.fault_code

        # ----------------------------------------------------
        # EMERGENCY STOP
        # ----------------------------------------------------

        if emergency_stop == 1:
            self.state.motor_state = MotorState.FAULT
            self.state.actual_rpm = 0
            return

        # ----------------------------------------------------
        # ACTIVE FAULT
        # ----------------------------------------------------

        if fault != FaultCode.NONE:
            self.state.motor_state = MotorState.FAULT

            self.state.actual_rpm = max(
                0,
                self.state.actual_rpm
                - self.config.deceleration_rpm_per_scan,
            )

            return

        # ----------------------------------------------------
        # STOP COMMAND
        # ----------------------------------------------------

        if motor_command == MotorCommand.STOP:

            if self.state.actual_rpm > 0:

                self.state.motor_state = (
                    MotorState.STOPPING
                )

                self.state.actual_rpm = max(
                    0,
                    self.state.actual_rpm
                    - self.config.deceleration_rpm_per_scan,
                )

            else:

                self.state.motor_state = (
                    MotorState.STOPPED
                )

            return

        # ----------------------------------------------------
        # START COMMAND
        # ----------------------------------------------------

        if motor_command == MotorCommand.START:

            if speed_setpoint <= 0:

                self.state.motor_state = (
                    MotorState.STOPPED
                )

                self.state.actual_rpm = 0

                return

            if (
                self.state.actual_rpm
                < speed_setpoint
            ):

                self.state.motor_state = (
                    MotorState.STARTING
                )

                self.state.actual_rpm = min(
                    speed_setpoint,
                    self.state.actual_rpm
                    + self.config.acceleration_rpm_per_scan,
                )

            elif (
                self.state.actual_rpm
                > speed_setpoint
            ):

                self.state.motor_state = (
                    MotorState.STOPPING
                )

                self.state.actual_rpm = max(
                    speed_setpoint,
                    self.state.actual_rpm
                    - self.config.deceleration_rpm_per_scan,
                )

            else:

                self.state.motor_state = (
                    MotorState.RUNNING
                )

    # ========================================================
    # PROCESS MODEL
    # ========================================================

    def update_process(
        self,
        commands: dict,
    ) -> None:

        rpm = self.state.actual_rpm

        speed_setpoint = commands[
            "speed_setpoint"
        ]

        pressure_setpoint = commands[
            "pressure_setpoint"
        ]

        # ----------------------------------------------------
        # CURRENT
        # ----------------------------------------------------

        if rpm > 0:

            load_ratio = (
                rpm / self.config.max_rpm
            )

            current = (
                2.0
                + 15.0 * load_ratio
            )

            # Small additional load while accelerating.
            if self.state.motor_state == MotorState.STARTING:
                current += 2.0

        else:

            current = 0.0

        self.state.current_a = max(
            0.0,
            min(
                current,
                self.config.max_current_a,
            ),
        )

        # ----------------------------------------------------
        # PRESSURE
        # ----------------------------------------------------

        if (
            rpm > 0
            and speed_setpoint > 0
        ):

            target_pressure = (
                rpm / speed_setpoint
            ) * pressure_setpoint

            target_pressure = max(
                0.0,
                min(
                    target_pressure,
                    self.config.max_pressure_bar,
                ),
            )

        else:

            target_pressure = 0.0

        # Smooth pressure response.
        self.state.pressure_bar += (
            target_pressure
            - self.state.pressure_bar
        ) * self.config.pressure_response_factor

        # ----------------------------------------------------
        # TEMPERATURE
        # ----------------------------------------------------

        target_temperature = (
            self.config.ambient_temperature_c
            + rpm
            * self.config.temperature_rise_per_rpm
        )

        # Heating
        if target_temperature > self.state.temperature_c:

            self.state.temperature_c += (
                target_temperature
                - self.state.temperature_c
            ) * 0.15

        # Cooling
        else:

            self.state.temperature_c += (
                target_temperature
                - self.state.temperature_c
            ) * self.config.temperature_cooling_factor

        self.state.temperature_c = max(
            self.config.ambient_temperature_c,
            self.state.temperature_c,
        )

    # ========================================================
    # STATUS WORD
    # ========================================================

    def build_status_word(
        self,
        commands: dict,
    ) -> int:

        status = StatusBits(0)

        # Motor state
        if self.state.actual_rpm == 0:
            status |= StatusBits.STOPPED
        else:
            status |= StatusBits.RUNNING

        # Mode
        if commands["mode"] == OperationMode.AUTO:
            status |= StatusBits.AUTO_MODE
        else:
            status |= StatusBits.MANUAL_MODE

        # Alarm
        if self.state.alarm:
            status |= StatusBits.ALARM_ACTIVE

        # E-stop
        if commands["emergency_stop"] == 1:
            status |= StatusBits.EMERGENCY_STOP

        # Fault
        if self.state.fault_code != FaultCode.NONE:
            status |= StatusBits.FAULT_ACTIVE

        # Ready
        if (
            self.state.fault_code == FaultCode.NONE
            and commands["emergency_stop"] == 0
        ):
            status |= StatusBits.READY

        return int(status)

    # ========================================================
    # REGISTER PUBLISH
    # ========================================================

    async def publish(
        self,
        commands: dict,
    ) -> None:

        # ----------------------------------------------------
        # ACTUAL RPM
        # ----------------------------------------------------

        await self.registers.write_uint32(
            HR.ACTUAL_RPM_HI,
            self.state.actual_rpm,
        )

        # ----------------------------------------------------
        # CURRENT x10
        # ----------------------------------------------------

        current_raw = int(
            self.state.current_a
            * self.config.current_scale
        )

        await self.registers.write_uint32(
            HR.CURRENT_HI,
            current_raw,
        )

        # ----------------------------------------------------
        # ACTUAL PRESSURE x10
        # ----------------------------------------------------

        pressure_raw = int(
            self.state.pressure_bar
            * self.config.pressure_scale
        )

        await self.registers.write_uint32(
            HR.ACTUAL_PRESSURE_HI,
            pressure_raw,
        )

        # ----------------------------------------------------
        # TEMPERATURE x10
        # ----------------------------------------------------

        temperature_raw = int(
            self.state.temperature_c
            * self.config.temperature_scale
        )

        await self.registers.write_uint32(
            HR.TEMPERATURE_HI,
            temperature_raw,
        )

        # ----------------------------------------------------
        # ALARM
        # ----------------------------------------------------

        await self.registers.write_one(
            HR.ALARM,
            1 if self.state.alarm else 0,
        )

        # ----------------------------------------------------
        # HEARTBEAT
        # ----------------------------------------------------

        self.state.heartbeat = (
            self.state.heartbeat + 1
        ) & 0xFFFF

        await self.registers.write_one(
            HR.HEARTBEAT,
            self.state.heartbeat,
        )

        # ----------------------------------------------------
        # STATUS WORD
        # ----------------------------------------------------

        status_word = self.build_status_word(
            commands
        )

        await self.registers.write_one(
            HR.STATUS_WORD,
            status_word,
        )

        # ----------------------------------------------------
        # FAULT CODE
        # ----------------------------------------------------

        await self.registers.write_one(
            HR.FAULT_CODE,
            int(self.state.fault_code),
        )

        # ----------------------------------------------------
        # RUNTIME
        # ----------------------------------------------------

        if self.state.actual_rpm > 0:

            self.state.runtime_seconds += (
                self.config.scan_time_s
            )

        await self.registers.write_uint32(
            HR.RUNTIME_SECONDS_HI,
            int(self.state.runtime_seconds),
        )

    # ========================================================
    # COMMAND CHANGE MONITOR
    # ========================================================

    def log_command_changes(
        self,
        commands: dict,
    ) -> None:

        if self._last_commands is None:

            self._last_commands = dict(commands)

            self.logger.info(
                "INITIAL COMMANDS | "
                "MOTOR=%s | "
                "MODE=%s | "
                "SET_RPM=%d | "
                "SET_PRESSURE=%.1f bar | "
                "E_STOP=%d",

                (
                    "START"
                    if commands["motor_command"]
                    else "STOP"
                ),

                (
                    "AUTO"
                    if commands["mode"]
                    else "MANUAL"
                ),

                commands["speed_setpoint"],
                commands["pressure_setpoint"],
                commands["emergency_stop"],
            )

            return

        changed = (
            commands["motor_command"]
            != self._last_commands["motor_command"]

            or commands["mode"]
            != self._last_commands["mode"]

            or commands["speed_setpoint"]
            != self._last_commands["speed_setpoint"]

            or commands["pressure_setpoint"]
            != self._last_commands["pressure_setpoint"]

            or commands["emergency_stop"]
            != self._last_commands["emergency_stop"]
        )

        if changed:

            self.logger.info(
                "MODBUS COMMAND CHANGE | "
                "MOTOR=%s | "
                "MODE=%s | "
                "SET_RPM=%d | "
                "SET_PRESSURE=%.1f bar | "
                "E_STOP=%d",

                (
                    "START"
                    if commands["motor_command"]
                    else "STOP"
                ),

                (
                    "AUTO"
                    if commands["mode"]
                    else "MANUAL"
                ),

                commands["speed_setpoint"],
                commands["pressure_setpoint"],
                commands["emergency_stop"],
            )

        self._last_commands = dict(commands)

    # ========================================================
    # FAULT / STATE MONITOR
    # ========================================================

    def log_state_changes(self) -> None:

        # Motor state transition
        if (
            self.state.motor_state
            != self._last_motor_state
        ):

            self.logger.info(
                "MOTOR STATE | %s -> %s | RPM=%d",
                self._last_motor_state.name,
                self.state.motor_state.name,
                self.state.actual_rpm,
            )

            self._last_motor_state = (
                self.state.motor_state
            )

        # Fault transition
        if (
            self.state.fault_code
            != self._last_fault
        ):

            if self.state.fault_code == FaultCode.NONE:

                self.logger.info(
                    "FAULT CLEARED"
                )

            else:

                self.logger.error(
                    "FAULT ACTIVE | CODE=%d | NAME=%s",
                    int(self.state.fault_code),
                    self.state.fault_code.name,
                )

            self._last_fault = (
                self.state.fault_code
            )

    # ========================================================
    # PERIODIC STATE MONITOR
    # ========================================================

    def log_periodic_state(
        self,
        commands: dict,
        current_time: float,
    ) -> None:

        if (
            current_time
            - self.state.last_state_log_time
            < self.config.state_log_interval_s
        ):
            return

        self.state.last_state_log_time = current_time

        self.logger.info(
            "PLC STATUS | "
            "STATE=%s | "
            "RPM=%d/%d | "
            "CURRENT=%.1f A | "
            "PRESSURE=%.1f bar | "
            "TEMP=%.1f C | "
            "MODE=%s | "
            "ALARM=%s | "
            "FAULT=%s | "
            "RUNTIME=%ds",

            self.state.motor_state.name,

            self.state.actual_rpm,
            commands["speed_setpoint"],

            self.state.current_a,

            self.state.pressure_bar,

            self.state.temperature_c,

            (
                "AUTO"
                if commands["mode"]
                else "MANUAL"
            ),

            (
                "YES"
                if self.state.alarm
                else "NO"
            ),

            self.state.fault_code.name,

            int(self.state.runtime_seconds),
        )

    # ========================================================
    # PLC MAIN LOOP
    # ========================================================

    async def run(self) -> None:

        self.logger.info(
            "PLC simulation started."
        )

        self.logger.info(
            "Scan cycle: %.3f seconds",
            self.config.scan_time_s,
        )

        while not self.stop_event.is_set():

            cycle_start = (
                asyncio.get_running_loop().time()
            )

            try:

                # ------------------------------------------------
                # 1. READ MODBUS INPUTS
                # ------------------------------------------------

                commands = await self.read_commands()

                self.log_command_changes(
                    commands
                )

                # ------------------------------------------------
                # 2. VALIDATE COMMANDS
                # ------------------------------------------------

                validation_fault = (
                    self.validate_commands(
                        commands
                    )
                )

                # ------------------------------------------------
                # 3. INITIAL PROTECTION CHECK
                # ------------------------------------------------

                preliminary_fault = (
                    self.determine_fault(
                        commands,
                        validation_fault,
                    )
                )

                # Invalid command or active E-stop
                # must immediately prevent normal operation.
                self.state.fault_code = (
                    preliminary_fault
                )

                # ------------------------------------------------
                # 4. MOTOR STATE MACHINE
                # ------------------------------------------------

                self.update_motor(
                    commands
                )

                # ------------------------------------------------
                # 5. PROCESS SIMULATION
                # ------------------------------------------------

                self.update_process(
                    commands
                )

                # ------------------------------------------------
                # 6. FINAL PROTECTION CHECK
                # ------------------------------------------------

                final_fault = (
                    self.determine_fault(
                        commands,
                        validation_fault,
                    )
                )

                self.state.fault_code = (
                    final_fault
                )

                # ------------------------------------------------
                # 7. ALARM
                # ------------------------------------------------

                self.state.alarm = (
                    self.determine_alarm()
                )

                # ------------------------------------------------
                # 8. PUBLISH TO MODBUS
                # ------------------------------------------------

                await self.publish(
                    commands
                )

                # ------------------------------------------------
                # 9. MONITORING
                # ------------------------------------------------

                self.log_state_changes()

                current_time = (
                    asyncio.get_running_loop().time()
                )

                self.log_periodic_state(
                    commands,
                    current_time,
                )

                self.state.scan_cycles += 1

            except Exception:

                self.logger.exception(
                    "PLC scan cycle failed."
                )

            # ----------------------------------------------------
            # MAINTAIN FIXED SCAN PERIOD
            # ----------------------------------------------------

            elapsed = (
                asyncio.get_running_loop().time()
                - cycle_start
            )

            sleep_time = max(
                0.0,
                self.config.scan_time_s
                - elapsed,
            )

            try:

                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=sleep_time,
                )

            except asyncio.TimeoutError:
                pass

        self.logger.info(
            "PLC simulation stopped."
        )

    def stop(self) -> None:

        self.stop_event.set()


# ============================================================
# MAIN APPLICATION
# ============================================================

async def main() -> None:

    LOGGER.info(
        "=================================================="
    )

    LOGGER.info(
        "       PROFESSIONAL MODBUS TCP PLC"
    )

    LOGGER.info(
        "=================================================="
    )

    LOGGER.info(
        "IP       : %s",
        CONFIG.ip,
    )

    LOGGER.info(
        "PORT     : %d",
        CONFIG.port,
    )

    LOGGER.info(
        "UNIT ID  : %d",
        CONFIG.unit_id,
    )

    LOGGER.info(
        "SCAN     : %.3f s",
        CONFIG.scan_time_s,
    )

    LOGGER.info(
        "REGISTERS: %d",
        REGISTER_COUNT,
    )

    LOGGER.info(
        "=================================================="
    )

    # --------------------------------------------------------
    # CREATE MODBUS TCP SERVER
    # --------------------------------------------------------

    server = ModbusTcpServer(
        device,
        address=(
            CONFIG.ip,
            CONFIG.port,
        ),
    )

    # --------------------------------------------------------
    # REGISTER DATABASE
    # --------------------------------------------------------

    register_db = RegisterDatabase(
        server=server,
        device_id=CONFIG.unit_id,
    )

    # --------------------------------------------------------
    # PLC
    # --------------------------------------------------------

    plc = PLCSimulator(
        registers=register_db,
        config=CONFIG,
    )

    # --------------------------------------------------------
    # START MODBUS SERVER
    # --------------------------------------------------------

    LOGGER.info(
        "Starting Modbus TCP server..."
    )

    server_task = asyncio.create_task(
        server.serve_forever()
    )

    # --------------------------------------------------------
    # START PLC
    # --------------------------------------------------------

    plc_task = asyncio.create_task(
        plc.run()
    )

    try:

        done, pending = await asyncio.wait(
            [
                server_task,
                plc_task,
            ],
            return_when=asyncio.FIRST_EXCEPTION,
        )

        for task in done:

            exception = task.exception()

            if exception is not None:

                LOGGER.error(
                    "Application task failed.",
                    exc_info=(
                        type(exception),
                        exception,
                        exception.__traceback__,
                    ),
                )

                raise exception

    except asyncio.CancelledError:

        pass

    finally:

        LOGGER.info(
            "Shutdown signal received."
        )

        # ----------------------------------------------------
        # STOP PLC
        # ----------------------------------------------------

        plc.stop()

        # ----------------------------------------------------
        # STOP MODBUS SERVER
        # ----------------------------------------------------

        try:

            await server.shutdown()

        except Exception:

            LOGGER.exception(
                "Error while shutting down Modbus server."
            )

        # ----------------------------------------------------
        # CANCEL TASKS
        # ----------------------------------------------------

        for task in (
            server_task,
            plc_task,
        ):

            if not task.done():
                task.cancel()

        # ----------------------------------------------------
        # WAIT FOR TASKS
        # ----------------------------------------------------

        await asyncio.gather(
            server_task,
            plc_task,
            return_exceptions=True,
        )

        LOGGER.info(
            "Application stopped."
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        LOGGER.info(
            "Keyboard interrupt."
        )