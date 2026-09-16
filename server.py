"""
Architecture:

    Modbus TCP Server
          |
          v
    SimDevice / SimData
          |
          v
    Register Database
          |
          v
    PLC Control Logic
          |
          v
    Process Simulation
"""

from __future__ import annotations

import asyncio
import logging
import signal

from dataclasses import dataclass
from enum import IntEnum, IntFlag
from threading import Lock

from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice


# ============================================================
# APPLICATION CONFIGURATION
# ============================================================

@dataclass(frozen=True)
class AppConfig:
    """Application configuration."""

    ip: str = "127.0.0.1"
    port: int = 5020
    unit_id: int = 1

    scan_time_s: float = 0.5

    max_rpm: int = 3000
    acceleration_rpm_per_scan: int = 100
    deceleration_rpm_per_scan: int = 150

    max_pressure_bar: float = 10.0
    max_temperature_c: float = 100.0
    max_current_a: float = 30.0

    pressure_scale: int = 10
    current_scale: int = 10
    temperature_scale: int = 10

    watchdog_timeout_s: float = 5.0


CONFIG = AppConfig()


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)-8s | "
        "%(name)-12s | "
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

    These are ZERO-BASED Modbus addresses.

    Example:
        HR.MOTOR_COMMAND = 0
        This corresponds to Holding Register 40001
        in the usual 4xxxx notation.
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

# Motor command = STOP
INITIAL_VALUES[HR.MOTOR_COMMAND] = MotorCommand.STOP

# Mode = MANUAL
INITIAL_VALUES[HR.MODE] = OperationMode.MANUAL

# Speed setpoint = 0
INITIAL_VALUES[HR.SPEED_SETPOINT_HI] = 0
INITIAL_VALUES[HR.SPEED_SETPOINT_LO] = 0

# Pressure setpoint = 5.0 bar
# 5.0 * 10 = 50
INITIAL_VALUES[HR.PRESSURE_SETPOINT_HI] = 0
INITIAL_VALUES[HR.PRESSURE_SETPOINT_LO] = 50

# Emergency stop released
INITIAL_VALUES[HR.EMERGENCY_STOP] = 0


# ============================================================
# PYMODBUS 3.13.1 SIMULATOR DATASTORE
# ============================================================

# IMPORTANT:
#
# SimData defines the actual Modbus register range.
#
# address=0
# count=21
#
# means:
#
#   Modbus address 0  -> 40001
#   Modbus address 1  -> 40002
#   ...
#   Modbus address 20 -> 40021
#
# We intentionally use ONE shared register block.
#
# Do NOT create:
#
#   []
#   []
#   [holding registers]
#   []
#
# because SimDevice validates those blocks and an empty block
# causes the "IndexError: list index out of range" error.

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
    Thread-safe interface to the REAL PyModbus server datastore.

    IMPORTANT:
        We do NOT keep a second register array here.

    The source of truth is the ModbusTcpServer datastore itself.

    This means:

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

    Therefore a value written by Modbus Poll is immediately visible
    to the PLC scan cycle.
    """

    HOLDING_REGISTER_FUNCTION = 3

    def __init__(
        self,
        server: ModbusTcpServer,
        device_id: int,
    ) -> None:

        self.server = server
        self.device_id = device_id

        self._lock = Lock()

    async def read(
        self,
        address: int,
        count: int = 1,
    ) -> list[int]:

        if address < 0:
            raise ValueError("address must be >= 0")

        if count <= 0:
            raise ValueError("count must be > 0")

        values = await self.server.async_getValues(
            self.device_id,
            self.HOLDING_REGISTER_FUNCTION,
            address,
            count=count,
        )

        return [int(value) & 0xFFFF for value in values]

    async def write(
        self,
        address: int,
        values: list[int],
    ) -> None:

        if address < 0:
            raise ValueError("address must be >= 0")

        values = [
            int(value) & 0xFFFF
            for value in values
        ]

        await self.server.async_setValues(
            self.device_id,
            self.HOLDING_REGISTER_FUNCTION,
            address,
            values,
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

    runtime_seconds: float = 0.0

    heartbeat: int = 0


# ============================================================
# PLC SIMULATOR
# ============================================================

class PLCSimulator:
    """
    Industrial-style PLC process simulator.

    Separates:

        - command reading
        - validation
        - motor simulation
        - process simulation
        - protection
        - register publishing
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

        self._last_commands: dict | None = None

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

        commands = {
            "motor_command": motor_command,
            "mode": mode,
            "emergency_stop": emergency_stop,
            "speed_setpoint": speed_setpoint,
            "pressure_setpoint": pressure_setpoint,
        }

        return commands

    # ========================================================
    # INPUT VALIDATION
    # ========================================================

    def validate_commands(
        self,
        commands: dict,
    ) -> FaultCode:

        speed = commands["speed_setpoint"]
        pressure = commands["pressure_setpoint"]

        if speed > self.config.max_rpm:
            return FaultCode.OVER_SPEED

        if pressure > self.config.max_pressure_bar:
            return FaultCode.OVER_PRESSURE

        if commands["motor_command"] not in (
            MotorCommand.STOP,
            MotorCommand.START,
        ):
            return FaultCode.INVALID_SETPOINT

        if commands["mode"] not in (
            OperationMode.MANUAL,
            OperationMode.AUTO,
        ):
            return FaultCode.INVALID_SETPOINT

        return FaultCode.NONE

    # ========================================================
    # MOTOR MODEL
    # ========================================================

    def update_motor(
        self,
        commands: dict,
    ) -> None:

        emergency_stop = commands["emergency_stop"]
        motor_command = commands["motor_command"]
        speed_setpoint = commands["speed_setpoint"]

        # Emergency stop
        if emergency_stop == 1:

            self.state.actual_rpm = 0

            return

        # STOP
        if motor_command == MotorCommand.STOP:

            self.state.actual_rpm = max(
                0,
                self.state.actual_rpm
                - self.config.deceleration_rpm_per_scan,
            )

            return

        # START
        if motor_command == MotorCommand.START:

            if self.state.actual_rpm < speed_setpoint:

                self.state.actual_rpm = min(
                    speed_setpoint,
                    self.state.actual_rpm
                    + self.config.acceleration_rpm_per_scan,
                )

            elif self.state.actual_rpm > speed_setpoint:

                self.state.actual_rpm = max(
                    speed_setpoint,
                    self.state.actual_rpm
                    - self.config.deceleration_rpm_per_scan,
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

            current = (
                2.0
                + rpm * 0.005
            )

        else:

            current = 0.0

        self.state.current_a = min(
            current,
            self.config.max_current_a,
        )

        # ----------------------------------------------------
        # PRESSURE
        # ----------------------------------------------------

        if (
            rpm > 0
            and speed_setpoint > 0
        ):

            ratio = (
                rpm
                / speed_setpoint
            )

            ratio = max(
                0.0,
                min(ratio, 1.0),
            )

            self.state.pressure_bar = (
                ratio
                * pressure_setpoint
            )

        else:

            self.state.pressure_bar = 0.0

        # ----------------------------------------------------
        # TEMPERATURE
        # ----------------------------------------------------

        self.state.temperature_c = (
            25.0
            + rpm * 0.01
        )

    # ========================================================
    # PROTECTION
    # ========================================================

    def update_protection(
        self,
        commands: dict,
        validation_fault: FaultCode,
    ) -> None:

        fault = FaultCode.NONE

        emergency_stop = commands[
            "emergency_stop"
        ]

        if emergency_stop == 1:

            fault = FaultCode.EMERGENCY_STOP

        elif validation_fault != FaultCode.NONE:

            fault = validation_fault

        elif (
            self.state.temperature_c
            >= self.config.max_temperature_c
        ):

            fault = FaultCode.OVER_TEMPERATURE

        elif (
            self.state.actual_rpm
            > self.config.max_rpm
        ):

            fault = FaultCode.OVER_SPEED

        elif (
            self.state.current_a
            > self.config.max_current_a
        ):

            fault = FaultCode.OVER_CURRENT

        elif (
            self.state.pressure_bar
            > self.config.max_pressure_bar
        ):

            fault = FaultCode.OVER_PRESSURE

        self.state.fault_code = fault

        self.state.alarm = (
            fault != FaultCode.NONE
        )

    # ========================================================
    # STATUS WORD
    # ========================================================

    def build_status_word(
        self,
        commands: dict,
    ) -> int:

        status = StatusBits(0)

        if self.state.actual_rpm == 0:

            status |= StatusBits.STOPPED

        else:

            status |= StatusBits.RUNNING

        if commands["mode"] == OperationMode.AUTO:

            status |= StatusBits.AUTO_MODE

        else:

            status |= StatusBits.MANUAL_MODE

        if self.state.alarm:

            status |= StatusBits.ALARM_ACTIVE

        if commands["emergency_stop"] == 1:

            status |= StatusBits.EMERGENCY_STOP

        if self.state.fault_code != FaultCode.NONE:

            status |= StatusBits.FAULT_ACTIVE

        if (
            not self.state.alarm
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

            self._last_commands = dict(
                commands
            )

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

        self._last_commands = dict(
            commands
        )

    # ========================================================
    # MONITORING
    # ========================================================

    def log_state(
        self,
        commands: dict,
    ) -> None:

        self.logger.info(
            "MOTOR=%s | "
            "MODE=%s | "
            "SET_RPM=%d | "
            "RPM=%d | "
            "CURRENT=%.1f A | "
            "SET_PRESSURE=%.1f bar | "
            "PRESSURE=%.1f bar | "
            "TEMP=%.1f C | "
            "ALARM=%s | "
            "FAULT=%s | "
            "HB=%d",
            (
                "ON"
                if commands["motor_command"]
                else "OFF"
            ),
            (
                "AUTO"
                if commands["mode"]
                else "MANUAL"
            ),
            commands["speed_setpoint"],
            self.state.actual_rpm,
            self.state.current_a,
            commands["pressure_setpoint"],
            self.state.pressure_bar,
            self.state.temperature_c,
            self.state.alarm,
            self.state.fault_code.name,
            self.state.heartbeat,
        )

    # ========================================================
    # MAIN PLC LOOP
    # ========================================================

    async def run(self) -> None:

        self.logger.info(
            "PLC simulation started."
        )

        while not self.stop_event.is_set():

            cycle_start = asyncio.get_running_loop().time()

            try:

                # Read values written by Modbus Poll.
                commands = await self.read_commands()

                # Show immediately when Modbus Poll changes something.
                self.log_command_changes(
                    commands
                )

                validation_fault = (
                    self.validate_commands(
                        commands
                    )
                )

                self.update_motor(
                    commands
                )

                self.update_process(
                    commands
                )

                self.update_protection(
                    commands,
                    validation_fault,
                )

                await self.publish(
                    commands
                )

                self.log_state(
                    commands
                )

            except Exception:

                self.logger.exception(
                    "PLC scan cycle failed."
                )

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
        "        PROFESSIONAL MODBUS TCP PLC"
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
        "=================================================="
    )

    # --------------------------------------------------------
    # CREATE REAL MODBUS TCP SERVER
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
    #
    # IMPORTANT:
    # It uses the SAME server datastore that Modbus Poll uses.
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
    # START SERVER
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

        # Wait until one of the tasks exits.
        done, pending = await asyncio.wait(
            [server_task, plc_task],
            return_when=asyncio.FIRST_EXCEPTION,
        )

        for task in done:

            exception = task.exception()

            if exception is not None:

                LOGGER.exception(
                    "Application task failed.",
                    exc_info=exception,
                )

                raise exception

    except asyncio.CancelledError:

        pass

    finally:

        LOGGER.info(
            "Shutdown signal received."
        )

        plc.stop()

        # Stop Modbus server.
        try:

            await server.shutdown()

        except Exception:

            LOGGER.exception(
                "Error while shutting down Modbus server."
            )

        # Cancel tasks.
        for task in (
            server_task,
            plc_task,
        ):

            if not task.done():

                task.cancel()

        # Wait for tasks.
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
