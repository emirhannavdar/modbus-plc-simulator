from __future__ import annotations

import asyncio
import logging
import random
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

    # --------------------------------------------------------
    # REALISTIC PROCESS RANDOMIZATION
    # --------------------------------------------------------

    # Manual mode
    manual_rpm_variation: float = 0.035
    manual_pressure_variation: float = 0.035

    # Auto environment
    auto_min_irradiance: float = 0.35
    auto_max_irradiance: float = 1.00
    irradiance_change_per_scan: float = 0.025

    # Process noise
    rpm_noise: float = 0.015
    pressure_noise: float = 0.020
    current_noise: float = 0.030
    temperature_noise: float = 0.050

    # Solar influence
    auto_pressure_factor: float = 0.90

    # --------------------------------------------------------
    # AUTO MODE DEFAULTS
    # --------------------------------------------------------

    auto_default_min_rpm: int = 1200
    auto_default_max_rpm: int = 2500

    auto_default_min_pressure: float = 3.0
    auto_default_max_pressure: float = 8.0

    auto_default_target_interval_s: int = 10

    # Maximum percentage by which a new target may move
    # from the previous AUTO target.
    auto_default_target_change_percent: int = 15

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

    Existing registers 0-20 are preserved.

    Example:
        HR.MOTOR_COMMAND = 0

    corresponds to:
        Holding Register 40001
    """

    # --------------------------------------------------------
    # EXISTING PROCESS REGISTERS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # AUTO MODE SETTINGS
    # --------------------------------------------------------

    # UINT32 RPM
    AUTO_MIN_RPM_HI = 21
    AUTO_MIN_RPM_LO = 22

    # UINT32 RPM
    AUTO_MAX_RPM_HI = 23
    AUTO_MAX_RPM_LO = 24

    # UINT32 pressure x10
    AUTO_MIN_PRESSURE_HI = 25
    AUTO_MIN_PRESSURE_LO = 26

    # UINT32 pressure x10
    AUTO_MAX_PRESSURE_HI = 27
    AUTO_MAX_PRESSURE_LO = 28

    # Seconds
    AUTO_TARGET_INTERVAL = 29

    # Percentage
    AUTO_TARGET_CHANGE_PERCENT = 30

    # UINT32 RPM - PLC generated target
    AUTO_TARGET_RPM_HI = 31
    AUTO_TARGET_RPM_LO = 32

    # UINT32 pressure x10 - PLC generated target
    AUTO_TARGET_PRESSURE_HI = 33
    AUTO_TARGET_PRESSURE_LO = 34

    # Irradiance x100
    SOLAR_IRRADIANCE = 35

    # Motor state enum
    MOTOR_STATE = 36

    # Load factor x100
    LOAD_FACTOR = 37


REGISTER_COUNT = 38


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

# ------------------------------------------------------------
# Existing process values
# ------------------------------------------------------------

INITIAL_VALUES[HR.MOTOR_COMMAND] = MotorCommand.STOP
INITIAL_VALUES[HR.MODE] = OperationMode.MANUAL

# Speed setpoint
INITIAL_VALUES[HR.SPEED_SETPOINT_HI] = 0
INITIAL_VALUES[HR.SPEED_SETPOINT_LO] = 0

# Pressure setpoint = 5.0 bar
INITIAL_VALUES[HR.PRESSURE_SETPOINT_HI] = 0
INITIAL_VALUES[HR.PRESSURE_SETPOINT_LO] = 50

# Emergency stop released
INITIAL_VALUES[HR.EMERGENCY_STOP] = 0

# Temperature = 25.0 C
INITIAL_VALUES[HR.TEMPERATURE_HI] = 0
INITIAL_VALUES[HR.TEMPERATURE_LO] = 250

# ------------------------------------------------------------
# AUTO MODE DEFAULTS
# ------------------------------------------------------------

# Min RPM = 1200
INITIAL_VALUES[HR.AUTO_MIN_RPM_HI] = 0
INITIAL_VALUES[HR.AUTO_MIN_RPM_LO] = CONFIG.auto_default_min_rpm

# Max RPM = 2500
INITIAL_VALUES[HR.AUTO_MAX_RPM_HI] = 0
INITIAL_VALUES[HR.AUTO_MAX_RPM_LO] = CONFIG.auto_default_max_rpm

# Min pressure = 3.0 bar
INITIAL_VALUES[HR.AUTO_MIN_PRESSURE_HI] = 0
INITIAL_VALUES[HR.AUTO_MIN_PRESSURE_LO] = int(
    CONFIG.auto_default_min_pressure
    * CONFIG.pressure_scale
)

# Max pressure = 8.0 bar
INITIAL_VALUES[HR.AUTO_MAX_PRESSURE_HI] = 0
INITIAL_VALUES[HR.AUTO_MAX_PRESSURE_LO] = int(
    CONFIG.auto_default_max_pressure
    * CONFIG.pressure_scale
)

# Target interval = 10 seconds
INITIAL_VALUES[HR.AUTO_TARGET_INTERVAL] = (
    CONFIG.auto_default_target_interval_s
)

# Target change = 15%
INITIAL_VALUES[HR.AUTO_TARGET_CHANGE_PERCENT] = (
    CONFIG.auto_default_target_change_percent
)

# Initial AUTO targets
initial_auto_rpm = (
    CONFIG.auto_default_min_rpm
    + CONFIG.auto_default_max_rpm
) // 2

initial_auto_pressure = (
    CONFIG.auto_default_min_pressure
    + CONFIG.auto_default_max_pressure
) / 2

INITIAL_VALUES[HR.AUTO_TARGET_RPM_HI] = (
    initial_auto_rpm >> 16
) & 0xFFFF
INITIAL_VALUES[HR.AUTO_TARGET_RPM_LO] = (
    initial_auto_rpm & 0xFFFF
)

initial_pressure_raw = int(
    initial_auto_pressure
    * CONFIG.pressure_scale
)

INITIAL_VALUES[HR.AUTO_TARGET_PRESSURE_HI] = (
    initial_pressure_raw >> 16
) & 0xFFFF
INITIAL_VALUES[HR.AUTO_TARGET_PRESSURE_LO] = (
    initial_pressure_raw & 0xFFFF
)

# Solar irradiance = 100%
INITIAL_VALUES[HR.SOLAR_IRRADIANCE] = 100

# Motor state = STOPPED
INITIAL_VALUES[HR.MOTOR_STATE] = MotorState.STOPPED

# Load factor = 0%
INITIAL_VALUES[HR.LOAD_FACTOR] = 0


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
    Abstraction over the PyModbus datastore.

    The Modbus datastore remains the single source of truth.
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

    # --------------------------------------------------------
    # Simulated solar environment
    # --------------------------------------------------------

    solar_irradiance: float = 1.0

    # --------------------------------------------------------
    # Effective targets
    # --------------------------------------------------------

    effective_speed_target: float = 0.0
    effective_pressure_target: float = 0.0

    # --------------------------------------------------------
    # AUTO generated targets
    # --------------------------------------------------------

    auto_target_rpm: float = 1850.0
    auto_target_pressure: float = 5.5

    auto_target_timer_s: float = 0.0

    # --------------------------------------------------------
    # System load
    # --------------------------------------------------------

    load_factor: float = 0.0


# ============================================================
# PLC SIMULATOR
# ============================================================

class PLCSimulator:
    """
    Industrial-style PLC process simulator.

    MANUAL:
        Operator setpoints remain the main reference.
        Actual process values contain realistic variation.

    AUTO:
        PLC automatically selects operating targets inside
        an operator-defined operating range.

        The operator controls:
            - Minimum RPM
            - Maximum RPM
            - Minimum pressure
            - Maximum pressure
            - Target change interval
            - Maximum target change percentage

        The PLC smoothly moves between generated targets.
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

        self.random = random.Random()

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

        # ----------------------------------------------------
        # AUTO SETTINGS
        # ----------------------------------------------------

        auto_min_rpm = await self.registers.read_uint32(
            HR.AUTO_MIN_RPM_HI
        )

        auto_max_rpm = await self.registers.read_uint32(
            HR.AUTO_MAX_RPM_HI
        )

        auto_min_pressure_raw = (
            await self.registers.read_uint32(
                HR.AUTO_MIN_PRESSURE_HI
            )
        )

        auto_max_pressure_raw = (
            await self.registers.read_uint32(
                HR.AUTO_MAX_PRESSURE_HI
            )
        )

        auto_min_pressure = (
            auto_min_pressure_raw
            / self.config.pressure_scale
        )

        auto_max_pressure = (
            auto_max_pressure_raw
            / self.config.pressure_scale
        )

        auto_target_interval = await self.registers.read_one(
            HR.AUTO_TARGET_INTERVAL
        )

        auto_target_change_percent = (
            await self.registers.read_one(
                HR.AUTO_TARGET_CHANGE_PERCENT
            )
        )

        return {
            "motor_command": motor_command,
            "mode": mode,
            "emergency_stop": emergency_stop,

            "speed_setpoint": speed_setpoint,
            "pressure_setpoint": pressure_setpoint,

            "auto_min_rpm": auto_min_rpm,
            "auto_max_rpm": auto_max_rpm,

            "auto_min_pressure": auto_min_pressure,
            "auto_max_pressure": auto_max_pressure,

            "auto_target_interval": auto_target_interval,
            "auto_target_change_percent": (
                auto_target_change_percent
            ),
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

        auto_min_rpm = commands["auto_min_rpm"]
        auto_max_rpm = commands["auto_max_rpm"]

        auto_min_pressure = commands[
            "auto_min_pressure"
        ]

        auto_max_pressure = commands[
            "auto_max_pressure"
        ]

        auto_target_interval = commands[
            "auto_target_interval"
        ]

        auto_target_change_percent = commands[
            "auto_target_change_percent"
        ]

        # ----------------------------------------------------
        # Basic command validation
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Manual setpoint validation
        # ----------------------------------------------------

        if speed > self.config.max_rpm:
            return FaultCode.OVER_SPEED

        if pressure > self.config.max_pressure_bar:
            return FaultCode.OVER_PRESSURE

        # ----------------------------------------------------
        # AUTO range validation
        # ----------------------------------------------------

        if auto_min_rpm > self.config.max_rpm:
            return FaultCode.INVALID_SETPOINT

        if auto_max_rpm > self.config.max_rpm:
            return FaultCode.INVALID_SETPOINT

        if auto_min_rpm > auto_max_rpm:
            return FaultCode.INVALID_SETPOINT

        if auto_min_pressure < 0:
            return FaultCode.INVALID_SETPOINT

        if auto_max_pressure < 0:
            return FaultCode.INVALID_SETPOINT

        if auto_min_pressure > self.config.max_pressure_bar:
            return FaultCode.OVER_PRESSURE

        if auto_max_pressure > self.config.max_pressure_bar:
            return FaultCode.OVER_PRESSURE

        if auto_min_pressure > auto_max_pressure:
            return FaultCode.INVALID_SETPOINT

        # ----------------------------------------------------
        # AUTO timing validation
        # ----------------------------------------------------

        if auto_target_interval < 1:
            return FaultCode.INVALID_SETPOINT

        if auto_target_interval > 3600:
            return FaultCode.INVALID_SETPOINT

        if auto_target_change_percent < 1:
            return FaultCode.INVALID_SETPOINT

        if auto_target_change_percent > 100:
            return FaultCode.INVALID_SETPOINT

        return FaultCode.NONE

    # ========================================================
    # SOLAR / ENVIRONMENT MODEL
    # ========================================================

    def update_solar_environment(
        self,
        commands: dict,
    ) -> None:

        change = self.random.uniform(
            -self.config.irradiance_change_per_scan,
            self.config.irradiance_change_per_scan,
        )

        self.state.solar_irradiance += change

        self.state.solar_irradiance = max(
            self.config.auto_min_irradiance,
            min(
                self.config.auto_max_irradiance,
                self.state.solar_irradiance,
            ),
        )

    # ========================================================
    # AUTO TARGET GENERATION
    # ========================================================

    def _clamp_auto_target(
        self,
        value: float,
        minimum: float,
        maximum: float,
    ) -> float:

        return max(
            minimum,
            min(
                maximum,
                value,
            ),
        )

    def generate_auto_target(
        self,
        commands: dict,
    ) -> None:

        min_rpm = float(
            commands["auto_min_rpm"]
        )

        max_rpm = float(
            commands["auto_max_rpm"]
        )

        min_pressure = float(
            commands["auto_min_pressure"]
        )

        max_pressure = float(
            commands["auto_max_pressure"]
        )

        change_percent = float(
            commands["auto_target_change_percent"]
        )

        # ----------------------------------------------------
        # First AUTO target
        # ----------------------------------------------------

        if self.state.auto_target_rpm <= 0:
            self.state.auto_target_rpm = (
                min_rpm + max_rpm
            ) / 2.0

        if self.state.auto_target_pressure <= 0:
            self.state.auto_target_pressure = (
                min_pressure + max_pressure
            ) / 2.0

        # ----------------------------------------------------
        # Calculate allowed movement from previous target
        # ----------------------------------------------------

        rpm_range = max_rpm - min_rpm
        pressure_range = max_pressure - min_pressure

        max_rpm_step = max(
            50.0,
            rpm_range * (
                change_percent / 100.0
            ),
        )

        max_pressure_step = max(
            0.1,
            pressure_range * (
                change_percent / 100.0
            ),
        )

        # ----------------------------------------------------
        # Generate new RPM target
        # ----------------------------------------------------

        rpm_delta = self.random.uniform(
            -max_rpm_step,
            max_rpm_step,
        )

        new_rpm = (
            self.state.auto_target_rpm
            + rpm_delta
        )

        # Solar conditions slightly influence the target.
        solar_factor = (
            0.85
            + (
                0.15
                * self.state.solar_irradiance
            )
        )

        new_rpm *= solar_factor

        new_rpm = self._clamp_auto_target(
            new_rpm,
            min_rpm,
            max_rpm,
        )

        # ----------------------------------------------------
        # Generate new pressure target
        # ----------------------------------------------------

        pressure_delta = self.random.uniform(
            -max_pressure_step,
            max_pressure_step,
        )

        new_pressure = (
            self.state.auto_target_pressure
            + pressure_delta
        )

        # Solar conditions have a smaller influence
        # on pressure than on the RPM target.
        new_pressure *= (
            0.95
            + (
                0.05
                * self.state.solar_irradiance
            )
        )

        new_pressure = self._clamp_auto_target(
            new_pressure,
            min_pressure,
            max_pressure,
        )

        self.state.auto_target_rpm = new_rpm
        self.state.auto_target_pressure = new_pressure

        self.state.auto_target_timer_s = 0.0

        self.logger.info(
            "AUTO TARGET CHANGE | "
            "RPM=%.0f | "
            "PRESSURE=%.1f bar | "
            "SOLAR=%.0f%%",
            new_rpm,
            new_pressure,
            self.state.solar_irradiance * 100,
        )

    # ========================================================
    # AUTO TARGET UPDATE
    # ========================================================

    def update_auto_target(
        self,
        commands: dict,
    ) -> None:

        if commands["mode"] != OperationMode.AUTO:
            self.state.auto_target_timer_s = 0.0
            return

        if commands["motor_command"] != MotorCommand.START:
            self.state.auto_target_timer_s = 0.0
            return

        interval = max(
            1,
            commands["auto_target_interval"],
        )

        self.state.auto_target_timer_s += (
            self.config.scan_time_s
        )

        # If the current target is outside the newly
        # configured range, immediately correct it.
        if (
            self.state.auto_target_rpm
            < commands["auto_min_rpm"]
            or self.state.auto_target_rpm
            > commands["auto_max_rpm"]
        ):
            self.state.auto_target_rpm = (
                commands["auto_min_rpm"]
                + commands["auto_max_rpm"]
            ) / 2.0

        if (
            self.state.auto_target_pressure
            < commands["auto_min_pressure"]
            or self.state.auto_target_pressure
            > commands["auto_max_pressure"]
        ):
            self.state.auto_target_pressure = (
                commands["auto_min_pressure"]
                + commands["auto_max_pressure"]
            ) / 2.0

        if (
            self.state.auto_target_timer_s
            >= interval
        ):
            self.generate_auto_target(
                commands
            )

    # ========================================================
    # EFFECTIVE TARGET CALCULATION
    # ========================================================

    def calculate_effective_targets(
        self,
        commands: dict,
    ) -> None:

        requested_rpm = float(
            commands["speed_setpoint"]
        )

        requested_pressure = float(
            commands["pressure_setpoint"]
        )

        mode = commands["mode"]

        if commands["motor_command"] != MotorCommand.START:
            self.state.effective_speed_target = 0.0
            self.state.effective_pressure_target = 0.0
            return

        # ----------------------------------------------------
        # AUTO MODE
        # ----------------------------------------------------

        if mode == OperationMode.AUTO:

            effective_rpm = (
                self.state.auto_target_rpm
            )

            effective_pressure = (
                self.state.auto_target_pressure
            )

            # Very small realistic process variation.
            effective_rpm *= self.random.uniform(
                1.0 - self.config.rpm_noise,
                1.0 + self.config.rpm_noise,
            )

            effective_pressure *= self.random.uniform(
                1.0 - self.config.pressure_noise,
                1.0 + self.config.pressure_noise,
            )

        # ----------------------------------------------------
        # MANUAL MODE
        # ----------------------------------------------------

        else:

            rpm_variation = self.random.uniform(
                1.0 - self.config.manual_rpm_variation,
                1.0 + self.config.manual_rpm_variation,
            )

            pressure_variation = self.random.uniform(
                1.0 - self.config.manual_pressure_variation,
                1.0 + self.config.manual_pressure_variation,
            )

            effective_rpm = (
                requested_rpm
                * rpm_variation
            )

            effective_pressure = (
                requested_pressure
                * pressure_variation
            )

        # ----------------------------------------------------
        # LIMITS
        # ----------------------------------------------------

        self.state.effective_speed_target = max(
            0.0,
            min(
                effective_rpm,
                self.config.max_rpm,
            ),
        )

        self.state.effective_pressure_target = max(
            0.0,
            min(
                effective_pressure,
                self.config.max_pressure_bar,
            ),
        )

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
        emergency_stop = commands["emergency_stop"]

        fault = self.state.fault_code

        target_rpm = (
            self.state.effective_speed_target
        )

        # ----------------------------------------------------
        # EMERGENCY STOP
        # ----------------------------------------------------

        if emergency_stop == 1:

            self.state.motor_state = (
                MotorState.FAULT
            )

            self.state.actual_rpm = 0

            return

        # ----------------------------------------------------
        # ACTIVE FAULT
        # ----------------------------------------------------

        if fault != FaultCode.NONE:

            self.state.motor_state = (
                MotorState.FAULT
            )

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

            if target_rpm <= 0:

                self.state.motor_state = (
                    MotorState.STOPPED
                )

                self.state.actual_rpm = 0

                return

            acceleration = int(
                self.config.acceleration_rpm_per_scan
                * self.random.uniform(
                    0.90,
                    1.10,
                )
            )

            deceleration = int(
                self.config.deceleration_rpm_per_scan
                * self.random.uniform(
                    0.90,
                    1.10,
                )
            )

            if self.state.actual_rpm < target_rpm:

                self.state.motor_state = (
                    MotorState.STARTING
                )

                self.state.actual_rpm = min(
                    int(target_rpm),
                    self.state.actual_rpm
                    + acceleration,
                )

            elif self.state.actual_rpm > target_rpm:

                self.state.motor_state = (
                    MotorState.STOPPING
                )

                self.state.actual_rpm = max(
                    int(target_rpm),
                    self.state.actual_rpm
                    - deceleration,
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

        requested_rpm = float(
            commands["speed_setpoint"]
        )

        effective_pressure_target = (
            self.state.effective_pressure_target
        )

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

            if (
                self.state.motor_state
                == MotorState.STARTING
            ):
                current += 2.0

            current *= self.random.uniform(
                1.0 - self.config.current_noise,
                1.0 + self.config.current_noise,
            )

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
            and (
                requested_rpm > 0
                or commands["mode"] == OperationMode.AUTO
            )
        ):

            if commands["mode"] == OperationMode.AUTO:

                target_rpm = max(
                    1.0,
                    self.state.auto_target_rpm,
                )

            else:

                target_rpm = max(
                    1.0,
                    requested_rpm,
                )

            rpm_ratio = (
                rpm / target_rpm
            )

            # Prevent unrealistic pressure increase
            # when RPM temporarily exceeds the target.
            rpm_ratio = max(
                0.0,
                min(
                    1.10,
                    rpm_ratio,
                ),
            )

            target_pressure = (
                rpm_ratio
                * effective_pressure_target
            )

            target_pressure *= self.random.uniform(
                1.0 - self.config.pressure_noise,
                1.0 + self.config.pressure_noise,
            )

            target_pressure = max(
                0.0,
                min(
                    target_pressure,
                    self.config.max_pressure_bar,
                ),
            )

        else:

            target_pressure = 0.0

        # Smooth pressure response
        self.state.pressure_bar += (
            target_pressure
            - self.state.pressure_bar
        ) * self.config.pressure_response_factor

        self.state.pressure_bar = max(
            0.0,
            self.state.pressure_bar,
        )

        # ----------------------------------------------------
        # TEMPERATURE
        # ----------------------------------------------------

        target_temperature = (
            self.config.ambient_temperature_c
            + rpm
            * self.config.temperature_rise_per_rpm
        )

        target_temperature += (
            self.state.current_a
            * 0.20
        )

        if (
            target_temperature
            > self.state.temperature_c
        ):

            self.state.temperature_c += (
                target_temperature
                - self.state.temperature_c
            ) * 0.15

        else:

            self.state.temperature_c += (
                target_temperature
                - self.state.temperature_c
            ) * self.config.temperature_cooling_factor

        self.state.temperature_c += (
            self.random.uniform(
                -self.config.temperature_noise,
                self.config.temperature_noise,
            )
        )

        self.state.temperature_c = max(
            self.config.ambient_temperature_c,
            self.state.temperature_c,
        )

        # ----------------------------------------------------
        # SYSTEM LOAD
        # ----------------------------------------------------

        self.state.load_factor = max(
            0.0,
            min(
                1.0,
                self.state.current_a
                / self.config.max_current_a,
            ),
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

        # ----------------------------------------------------
        # AUTO GENERATED TARGET RPM
        # ----------------------------------------------------

        await self.registers.write_uint32(
            HR.AUTO_TARGET_RPM_HI,
            int(self.state.auto_target_rpm),
        )

        # ----------------------------------------------------
        # AUTO GENERATED TARGET PRESSURE
        # ----------------------------------------------------

        auto_pressure_raw = int(
            self.state.auto_target_pressure
            * self.config.pressure_scale
        )

        await self.registers.write_uint32(
            HR.AUTO_TARGET_PRESSURE_HI,
            auto_pressure_raw,
        )

        # ----------------------------------------------------
        # SOLAR IRRADIANCE x100
        # ----------------------------------------------------

        solar_raw = int(
            self.state.solar_irradiance
            * 100
        )

        await self.registers.write_one(
            HR.SOLAR_IRRADIANCE,
            solar_raw,
        )

        # ----------------------------------------------------
        # MOTOR STATE
        # ----------------------------------------------------

        await self.registers.write_one(
            HR.MOTOR_STATE,
            int(self.state.motor_state),
        )

        # ----------------------------------------------------
        # LOAD FACTOR x100
        # ----------------------------------------------------

        load_raw = int(
            self.state.load_factor
            * 100
        )

        await self.registers.write_one(
            HR.LOAD_FACTOR,
            load_raw,
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
                "AUTO_RANGE=%d-%d RPM | "
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

                commands["auto_min_rpm"],
                commands["auto_max_rpm"],

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

            or commands["auto_min_rpm"]
            != self._last_commands["auto_min_rpm"]

            or commands["auto_max_rpm"]
            != self._last_commands["auto_max_rpm"]

            or commands["auto_min_pressure"]
            != self._last_commands["auto_min_pressure"]

            or commands["auto_max_pressure"]
            != self._last_commands["auto_max_pressure"]

            or commands["auto_target_interval"]
            != self._last_commands["auto_target_interval"]

            or commands["auto_target_change_percent"]
            != self._last_commands[
                "auto_target_change_percent"
            ]
        )

        if changed:

            self.logger.info(
                "MODBUS COMMAND CHANGE | "
                "MOTOR=%s | "
                "MODE=%s | "
                "SET_RPM=%d | "
                "SET_PRESSURE=%.1f bar | "
                "AUTO_RANGE=%d-%d RPM | "
                "AUTO_PRESSURE=%.1f-%.1f bar | "
                "INTERVAL=%ds | "
                "CHANGE=%d%% | "
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

                commands["auto_min_rpm"],
                commands["auto_max_rpm"],

                commands["auto_min_pressure"],
                commands["auto_max_pressure"],

                commands["auto_target_interval"],
                commands["auto_target_change_percent"],

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
            "RPM=%d | "
            "TARGET=%d | "
            "CURRENT=%.1f A | "
            "PRESSURE=%.1f bar | "
            "TEMP=%.1f C | "
            "MODE=%s | "
            "AUTO_TARGET=%.0f RPM / %.1f bar | "
            "SOLAR=%.0f%% | "
            "LOAD=%.0f%% | "
            "ALARM=%s | "
            "FAULT=%s | "
            "RUNTIME=%ds",

            self.state.motor_state.name,

            self.state.actual_rpm,

            int(
                self.state.effective_speed_target
            ),

            self.state.current_a,

            self.state.pressure_bar,

            self.state.temperature_c,

            (
                "AUTO"
                if commands["mode"]
                else "MANUAL"
            ),

            self.state.auto_target_rpm,

            self.state.auto_target_pressure,

            self.state.solar_irradiance * 100,

            self.state.load_factor * 100,

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
                # 3. SOLAR ENVIRONMENT
                # ------------------------------------------------

                self.update_solar_environment(
                    commands
                )

                # ------------------------------------------------
                # 4. AUTO TARGET GENERATION
                # ------------------------------------------------

                self.update_auto_target(
                    commands
                )

                # ------------------------------------------------
                # 5. CALCULATE EFFECTIVE TARGETS
                # ------------------------------------------------

                self.calculate_effective_targets(
                    commands
                )

                # ------------------------------------------------
                # 6. INITIAL PROTECTION
                # ------------------------------------------------

                preliminary_fault = (
                    self.determine_fault(
                        commands,
                        validation_fault,
                    )
                )

                self.state.fault_code = (
                    preliminary_fault
                )

                # ------------------------------------------------
                # 7. MOTOR STATE MACHINE
                # ------------------------------------------------

                self.update_motor(
                    commands
                )

                # ------------------------------------------------
                # 8. PROCESS SIMULATION
                # ------------------------------------------------

                self.update_process(
                    commands
                )

                # ------------------------------------------------
                # 9. FINAL PROTECTION
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
                # 10. ALARM
                # ------------------------------------------------

                self.state.alarm = (
                    self.determine_alarm()
                )

                # ------------------------------------------------
                # 11. PUBLISH TO MODBUS
                # ------------------------------------------------

                await self.publish(
                    commands
                )

                # ------------------------------------------------
                # 12. MONITORING
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
        "AUTO RPM : %d - %d",
        CONFIG.auto_default_min_rpm,
        CONFIG.auto_default_max_rpm,
    )

    LOGGER.info(
        "AUTO BAR : %.1f - %.1f",
        CONFIG.auto_default_min_pressure,
        CONFIG.auto_default_max_pressure,
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