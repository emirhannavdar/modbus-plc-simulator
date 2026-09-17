import asyncio
import logging
import random
from dataclasses import dataclass

from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-18s | %(message)s",
)

logger = logging.getLogger("PLC-DEVICE")


# ============================================================
# CONFIGURATION
# ============================================================

@dataclass
class AppConfig:
    host: str = "0.0.0.0"
    port: int = 502
    unit_id: int = 1

    simulation_interval: float = 1.0
    log_interval: float = 1.0

    speed_setpoint: float = 1500.0
    pressure_setpoint: float = 50.0

    motor_command: int = 1
    mode: int = 1


CONFIG = AppConfig()


# ============================================================
# REGISTER MAP
# ============================================================

REGISTER_COUNT = 15


class HR:
    MOTOR_COMMAND = 0
    MODE = 1

    SPEED_SETPOINT = 2
    ACTUAL_RPM = 4
    CURRENT = 6
    PRESSURE_SETPOINT = 8
    ACTUAL_PRESSURE = 10
    TEMPERATURE = 12

    HEARTBEAT = 14


# ============================================================
# PLC STATE
# ============================================================

@dataclass
class PLCState:
    motor_command: int = 1
    mode: int = 1

    speed_setpoint: float = 1500.0
    actual_rpm: float = 0.0

    current: float = 0.0

    pressure_setpoint: float = 50.0
    actual_pressure: float = 0.0

    temperature: float = 25.0

    heartbeat: int = 0


STATE = PLCState(
    motor_command=CONFIG.motor_command,
    mode=CONFIG.mode,
    speed_setpoint=CONFIG.speed_setpoint,
    pressure_setpoint=CONFIG.pressure_setpoint,
)


# ============================================================
# REGISTER DATA
# ============================================================

def create_simdata():
    """
    Create the actual Modbus register map.

    PyModbus handles FLOAT32 -> 2 registers automatically.
    """

    return [
        # 40001
        SimData(
            address=0,
            values=STATE.motor_command,
            datatype=DataType.UINT16,
        ),

        # 40002
        SimData(
            address=1,
            values=STATE.mode,
            datatype=DataType.UINT16,
        ),

        # 40003-40004
        SimData(
            address=2,
            values=STATE.speed_setpoint,
            datatype=DataType.FLOAT32,
        ),

        # 40005-40006
        SimData(
            address=4,
            values=STATE.actual_rpm,
            datatype=DataType.FLOAT32,
        ),

        # 40007-40008
        SimData(
            address=6,
            values=STATE.current,
            datatype=DataType.FLOAT32,
        ),

        # 40009-40010
        SimData(
            address=8,
            values=STATE.pressure_setpoint,
            datatype=DataType.FLOAT32,
        ),

        # 40011-40012
        SimData(
            address=10,
            values=STATE.actual_pressure,
            datatype=DataType.FLOAT32,
        ),

        # 40013-40014
        SimData(
            address=12,
            values=STATE.temperature,
            datatype=DataType.FLOAT32,
        ),

        # 40015
        SimData(
            address=14,
            values=STATE.heartbeat,
            datatype=DataType.UINT16,
        ),
    ]


# ============================================================
# REGISTER ENCODING
# ============================================================

def float_to_words(value: float) -> tuple[int, int]:
    """
    Convert FLOAT32 to two unsigned 16-bit Modbus words.
    """

    import struct

    raw = struct.pack(">f", float(value))

    return struct.unpack(">HH", raw)


def build_registers() -> list[int]:
    """
    Build the current physical register image.

    15 Modbus registers total.
    """

    import struct

    registers = [0] * REGISTER_COUNT

    # --------------------------------------------------------
    # UINT16
    # --------------------------------------------------------

    registers[HR.MOTOR_COMMAND] = (
        STATE.motor_command & 0xFFFF
    )

    registers[HR.MODE] = (
        STATE.mode & 0xFFFF
    )

    # --------------------------------------------------------
    # FLOAT32
    # --------------------------------------------------------

    values = [
        (HR.SPEED_SETPOINT, STATE.speed_setpoint),
        (HR.ACTUAL_RPM, STATE.actual_rpm),
        (HR.CURRENT, STATE.current),
        (HR.PRESSURE_SETPOINT, STATE.pressure_setpoint),
        (HR.ACTUAL_PRESSURE, STATE.actual_pressure),
        (HR.TEMPERATURE, STATE.temperature),
    ]

    for address, value in values:

        raw = struct.pack(">f", float(value))

        high, low = struct.unpack(">HH", raw)

        registers[address] = high
        registers[address + 1] = low

    # --------------------------------------------------------
    # HEARTBEAT
    # --------------------------------------------------------

    registers[HR.HEARTBEAT] = (
        STATE.heartbeat & 0xFFFF
    )

    return registers


# ============================================================
# MODBUS ACTION
# ============================================================

async def device_action(
    function_code,
    start_address,
    address,
    count,
    current_registers,
    set_values,
):
    """
    Expose the continuously simulated PLC state.

    The simulation runs independently in simulation_loop().
    This action only synchronizes the current state with the
    Modbus read response.
    """

    # Function code 03 = Read Holding Registers
    if function_code == 3:

        registers = build_registers()

        offset = address - start_address

        if offset < 0:
            offset = 0

        selected = registers[
            address:address + count
        ]

        for index, value in enumerate(selected):

            target_index = offset + index

            if target_index < len(current_registers):
                current_registers[target_index] = value

    return None


# ============================================================
# PLC SIMULATION
# ============================================================

async def simulation_loop():

    while True:

        # ----------------------------------------------------
        # TARGET RPM
        # ----------------------------------------------------

        if STATE.motor_command == 1:

            target_rpm = STATE.speed_setpoint

        else:

            target_rpm = 0.0

        # ----------------------------------------------------
        # RPM RAMP
        # ----------------------------------------------------

        rpm_error = (
            target_rpm
            - STATE.actual_rpm
        )

        acceleration = 120.0

        if abs(rpm_error) <= acceleration:

            STATE.actual_rpm = target_rpm

        elif rpm_error > 0:

            STATE.actual_rpm += acceleration

        else:

            STATE.actual_rpm -= acceleration

        # Small measurement noise
        if STATE.actual_rpm > 0:

            STATE.actual_rpm += random.uniform(
                -3.0,
                3.0,
            )

        STATE.actual_rpm = max(
            0.0,
            min(
                STATE.actual_rpm,
                STATE.speed_setpoint + 10.0,
            ),
        )

        # ----------------------------------------------------
        # CURRENT
        # ----------------------------------------------------

        if STATE.speed_setpoint > 0:

            rpm_ratio = (
                STATE.actual_rpm
                / STATE.speed_setpoint
            )

        else:

            rpm_ratio = 0.0

        if STATE.motor_command == 1:

            base_current = (
                2.0
                + 8.0 * rpm_ratio
            )

            STATE.current = (
                base_current
                + random.uniform(
                    -0.15,
                    0.15,
                )
            )

        else:

            STATE.current = max(
                0.0,
                STATE.current - 0.5,
            )

        # ----------------------------------------------------
        # PRESSURE
        # ----------------------------------------------------

        pressure_ratio = min(
            max(rpm_ratio, 0.0),
            1.0,
        )

        target_pressure = (
            STATE.pressure_setpoint
            * pressure_ratio
        )

        STATE.actual_pressure += (
            target_pressure
            - STATE.actual_pressure
        ) * 0.08

        STATE.actual_pressure += random.uniform(
            -0.15,
            0.15,
        )

        STATE.actual_pressure = max(
            0.0,
            STATE.actual_pressure,
        )

        # ----------------------------------------------------
        # TEMPERATURE
        # ----------------------------------------------------

        if STATE.motor_command == 1:

            target_temperature = (
                25.0
                + (
                    STATE.actual_rpm
                    / 1500.0
                ) * 35.0
            )

            STATE.temperature += (
                target_temperature
                - STATE.temperature
            ) * 0.015

        else:

            STATE.temperature += (
                25.0
                - STATE.temperature
            ) * 0.02

        STATE.temperature += random.uniform(
            -0.03,
            0.03,
        )

        STATE.temperature = max(
            20.0,
            min(
                STATE.temperature,
                80.0,
            ),
        )

        # ----------------------------------------------------
        # HEARTBEAT
        # ----------------------------------------------------

        STATE.heartbeat = (
            STATE.heartbeat + 1
        ) % 65536

        await asyncio.sleep(
            CONFIG.simulation_interval
        )


# ============================================================
# DEVICE LOGGER
# ============================================================

async def device_logger():

    while True:

        logger.info(
            "DEVICE | "
            "Motor=%d | "
            "Mode=%d | "
            "Speed=%.2f RPM | "
            "ActualRPM=%.2f | "
            "Current=%.2f A | "
            "Pressure=%.2f | "
            "Temperature=%.2f C | "
            "Heartbeat=%d",
            STATE.motor_command,
            STATE.mode,
            STATE.speed_setpoint,
            STATE.actual_rpm,
            STATE.current,
            STATE.actual_pressure,
            STATE.temperature,
            STATE.heartbeat,
        )

        await asyncio.sleep(
            CONFIG.log_interval
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info("=" * 70)
    logger.info(
        "INDUSTRIAL MODBUS TCP DEVICE SIMULATOR"
    )
    logger.info("=" * 70)

    logger.info(
        "Host       : %s",
        CONFIG.host,
    )

    logger.info(
        "Port       : %d",
        CONFIG.port,
    )

    logger.info(
        "Unit ID    : %d",
        CONFIG.unit_id,
    )

    logger.info(
        "Registers  : %d",
        REGISTER_COUNT,
    )

    logger.info("-" * 70)

    logger.info(
        "40001  MotorCommand      UINT16"
    )

    logger.info(
        "40002  Mode              UINT16"
    )

    logger.info(
        "40003-40004  SpeedSetpoint    FLOAT32"
    )

    logger.info(
        "40005-40006  ActualRPM         FLOAT32"
    )

    logger.info(
        "40007-40008  Current           FLOAT32"
    )

    logger.info(
        "40009-40010  PressureSetpoint  FLOAT32"
    )

    logger.info(
        "40011-40012  ActualPressure    FLOAT32"
    )

    logger.info(
        "40013-40014  Temperature       FLOAT32"
    )

    logger.info(
        "40015  Heartbeat         UINT16"
    )

    logger.info("-" * 70)

    # --------------------------------------------------------
    # SimDevice
    # --------------------------------------------------------

    simdata = create_simdata()

    device = SimDevice(
        id=CONFIG.unit_id,
        simdata=simdata,
        action=device_action,
    )

    # --------------------------------------------------------
    # Modbus TCP server
    # --------------------------------------------------------

    server = ModbusTcpServer(
        device,
        address=(
            CONFIG.host,
            CONFIG.port,
        ),
    )

    logger.info(
        "Modbus TCP server başlatılıyor..."
    )

    logger.info(
        "PLC sürekli veri üretmeye başladı."
    )

    logger.info("=" * 70)

    await asyncio.gather(
        server.serve_forever(),
        simulation_loop(),
        device_logger(),
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        logger.info(
            "PLC simulator stopped."
        )