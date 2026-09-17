import asyncio
import logging
import random
import struct
from dataclasses import dataclass

from pymodbus.server import ModbusTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice


# ============================================================
# CONFIGURATION
# ============================================================

HOST = "0.0.0.0"
PORT = 502
UNIT_ID = 1

SIMULATION_INTERVAL = 1.0
LOG_INTERVAL = 1.0

INITIAL_SPEED_SETPOINT = 1500.0
INITIAL_PRESSURE_SETPOINT = 50.0


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("PLC")


# ============================================================
# PLC STATE
# ============================================================

@dataclass
class PLCState:
    motor_command: int = 1
    mode: int = 1

    speed_setpoint: float = INITIAL_SPEED_SETPOINT
    actual_rpm: float = 0.0
    current: float = 0.0

    pressure_setpoint: float = INITIAL_PRESSURE_SETPOINT
    actual_pressure: float = 0.0

    temperature: float = 25.0

    heartbeat: int = 0
    fault: int = 0


state = PLCState()
state_lock = asyncio.Lock()


# ============================================================
# REGISTER MAP
# ============================================================

REGISTER_ADDRESS = {
    "MotorCommand": 0,
    "Mode": 1,
    "SpeedSetpoint": 2,
    "ActualRPM": 4,
    "Current": 6,
    "PressureSetpoint": 8,
    "ActualPressure": 10,
    "Temperature": 12,
    "Heartbeat": 14,
}


# ============================================================
# FLOAT32 HELPERS
# ============================================================

def float_to_words(value: float) -> list[int]:
    raw = struct.pack(">f", float(value))

    high_word, low_word = struct.unpack(">HH", raw)

    return [high_word, low_word]


def words_to_float(words: list[int]) -> float:
    if len(words) < 2:
        raise ValueError(
            f"FLOAT32 requires 2 registers, received {len(words)}"
        )

    raw = struct.pack(
        ">HH",
        int(words[0]) & 0xFFFF,
        int(words[1]) & 0xFFFF,
    )

    return struct.unpack(">f", raw)[0]


# ============================================================
# BUILD REGISTER IMAGE
# ============================================================

def build_registers() -> list[int]:
    registers = [0] * 15

    # --------------------------------------------------------
    # UINT16
    # --------------------------------------------------------

    registers[0] = state.motor_command & 0xFFFF
    registers[1] = state.mode & 0xFFFF

    # --------------------------------------------------------
    # FLOAT32
    # --------------------------------------------------------

    registers[2:4] = float_to_words(
        state.speed_setpoint
    )

    registers[4:6] = float_to_words(
        state.actual_rpm
    )

    registers[6:8] = float_to_words(
        state.current
    )

    registers[8:10] = float_to_words(
        state.pressure_setpoint
    )

    registers[10:12] = float_to_words(
        state.actual_pressure
    )

    registers[12:14] = float_to_words(
        state.temperature
    )

    # --------------------------------------------------------
    # HEARTBEAT
    # --------------------------------------------------------

    registers[14] = state.heartbeat & 0xFFFF

    return registers


# ============================================================
# APPLY MODBUS WRITE
# ============================================================

def apply_write(
    start_address: int,
    values: list[int],
):
    if not values:
        return

    values = [
        int(value) & 0xFFFF
        for value in values
    ]

    logger.info(
        "MODBUS WRITE RECEIVED | address=%s | values=%s",
        start_address,
        values,
    )

    # --------------------------------------------------------
    # MotorCommand
    # --------------------------------------------------------

    if start_address == 0 and len(values) >= 1:

        state.motor_command = (
            1 if values[0] else 0
        )

        logger.info(
            "PLC WRITE | MotorCommand = %s",
            state.motor_command,
        )

        return

    # --------------------------------------------------------
    # Mode
    # --------------------------------------------------------

    if start_address == 1 and len(values) >= 1:

        state.mode = (
            1 if values[0] else 0
        )

        logger.info(
            "PLC WRITE | Mode = %s",
            state.mode,
        )

        return

    # --------------------------------------------------------
    # SpeedSetpoint FLOAT32
    # --------------------------------------------------------

    if start_address == 2 and len(values) >= 2:

        new_value = words_to_float(
            values[:2]
        )

        state.speed_setpoint = new_value

        logger.info(
            "PLC WRITE | SpeedSetpoint = %.2f",
            state.speed_setpoint,
        )

        return

    # --------------------------------------------------------
    # PressureSetpoint FLOAT32
    # --------------------------------------------------------

    if start_address == 8 and len(values) >= 2:

        new_value = words_to_float(
            values[:2]
        )

        state.pressure_setpoint = new_value

        logger.info(
            "PLC WRITE | PressureSetpoint = %.2f",
            state.pressure_setpoint,
        )

        return

    logger.warning(
        "PLC WRITE | Unsupported address=%s values=%s",
        start_address,
        values,
    )


# ============================================================
# SIMULATOR ACTION
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
    PyModbus simulator callback.

    function_code:
        Modbus function code.

    start_address:
        Address represented by current_registers[0].

    address:
        Requested Modbus address.

    count:
        Requested register count.

    current_registers:
        Current register values.

    set_values:
        Values supplied by a write request.
    """

    # --------------------------------------------------------
    # WRITE REQUEST
    # --------------------------------------------------------

    if set_values is not None:

        incoming_values = list(set_values)

        logger.info(
            "SIMULATOR ACTION WRITE | FC=%s | address=%s | "
            "count=%s | values=%s",
            function_code,
            address,
            count,
            incoming_values,
        )

        apply_write(
            address,
            incoming_values,
        )

    # --------------------------------------------------------
    # ALWAYS BUILD CURRENT PLC IMAGE
    # --------------------------------------------------------

    image = build_registers()

    # --------------------------------------------------------
    # COPY REQUESTED PART OF IMAGE
    # --------------------------------------------------------

    if current_registers is not None:

        for index in range(
            min(
                len(current_registers),
                len(image) - start_address,
            )
        ):
            current_registers[index] = (
                image[start_address + index]
            )

    return None


# ============================================================
# SIMULATOR DATA MODEL
# ============================================================

def create_simdata():
    """
    Use one raw UINT16 register block.

    We intentionally do NOT define FLOAT32 SimData blocks here.

    FLOAT32 conversion is handled by our own PLC state and
    device_action() so Modbus writes and reads use exactly
    the same register map as the SCADA system.
    """

    return [
        SimData(
            address=0,
            count=15,
            values=[0] * 15,
            datatype=DataType.REGISTERS,
        )
    ]


# ============================================================
# SIMULATION
# ============================================================

async def simulation_loop():

    while True:

        async with state_lock:

            # ------------------------------------------------
            # RPM
            # ------------------------------------------------

            if state.motor_command == 1:
                target_rpm = state.speed_setpoint
            else:
                target_rpm = 0.0

            rpm_difference = (
                target_rpm
                - state.actual_rpm
            )

            state.actual_rpm += (
                rpm_difference * 0.15
            )

            # ------------------------------------------------
            # MOTOR RUNNING
            # ------------------------------------------------

            if state.motor_command == 1:

                state.current = (
                    2.0
                    + (
                        state.actual_rpm
                        / max(
                            state.speed_setpoint,
                            1.0,
                        )
                    )
                    * 8.0
                )

                target_pressure = (
                    state.pressure_setpoint
                    * (
                        state.actual_rpm
                        / max(
                            state.speed_setpoint,
                            1.0,
                        )
                    )
                )

                state.actual_pressure += (
                    target_pressure
                    - state.actual_pressure
                ) * 0.15

                state.temperature += (
                    state.current * 0.015
                )

            # ------------------------------------------------
            # MOTOR STOPPED
            # ------------------------------------------------

            else:

                state.current *= 0.85

                state.actual_pressure *= 0.90

                state.temperature += (
                    25.0
                    - state.temperature
                ) * 0.03

            # ------------------------------------------------
            # TEMPERATURE NOISE
            # ------------------------------------------------

            state.temperature += random.uniform(
                -0.15,
                0.15,
            )

            # ------------------------------------------------
            # HEARTBEAT
            # ------------------------------------------------

            state.heartbeat = (
                state.heartbeat + 1
            ) % 65536

        await asyncio.sleep(
            SIMULATION_INTERVAL
        )


# ============================================================
# LOGGER
# ============================================================

async def device_logger():

    while True:

        async with state_lock:

            logger.info(
                "PLC | MOTOR=%s | MODE=%s | "
                "SETPOINT=%.1f | RPM=%.1f | "
                "CURRENT=%.2f | PRESSURE=%.2f | "
                "TEMP=%.2f | HEARTBEAT=%s",

                "RUN"
                if state.motor_command
                else "STOP",

                "AUTO"
                if state.mode
                else "MANUAL",

                state.speed_setpoint,
                state.actual_rpm,
                state.current,
                state.actual_pressure,
                state.temperature,
                state.heartbeat,
            )

        await asyncio.sleep(
            LOG_INTERVAL
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    logger.info("=" * 70)
    logger.info("MODBUS TCP PLC SIMULATOR")
    logger.info("=" * 70)

    sim_device = SimDevice(
        id=UNIT_ID,
        simdata=create_simdata(),
        action=device_action,
    )

    server = ModbusTcpServer(
        sim_device,
        address=(HOST, PORT),
    )

    logger.info(
        "PLC listening on %s:%s",
        HOST,
        PORT,
    )

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