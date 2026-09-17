from __future__ import annotations

import logging
import sqlite3
import struct
import time
from dataclasses import dataclass
from enum import IntEnum

from pymodbus.client import ModbusTcpClient


# ============================================================
# CONFIG
# ============================================================

@dataclass(frozen=True)
class AppConfig:
    # slave.py çalışan bilgisayar
    ip: str = "127.0.0.1"
    port: int = 502
    unit_id: int = 1

    # FLOAT32 word order
    word_order: str = "big"

    # SCADA PLC'den saniyede 1 kez okur
    polling_interval_s: float = 1.0

    heartbeat_timeout_s: float = 5.0
    connection_timeout_s: float = 3.0

    # SQLite database
    database_path: str = "scada.db"


CONFIG = AppConfig()


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)-8s | "
        "%(name)-18s | "
        "%(message)s"
    ),
)

LOGGER = logging.getLogger("SCADA")


# ============================================================
# REGISTER MAP
# ============================================================

class HR(IntEnum):
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

    HEARTBEAT = 14

    REGISTER_COUNT = 15


# ============================================================
# DATABASE
# ============================================================

def initialize_database() -> None:
    """
    SQLite database ve readings tablosunu oluşturur.
    """

    connection = sqlite3.connect(
        CONFIG.database_path
    )

    try:
        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS plc_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,

                motor_command INTEGER NOT NULL,
                mode INTEGER NOT NULL,

                speed_setpoint REAL NOT NULL,
                actual_rpm REAL NOT NULL,
                current REAL NOT NULL,

                pressure_setpoint REAL NOT NULL,
                actual_pressure REAL NOT NULL,

                temperature REAL NOT NULL,
                heartbeat INTEGER NOT NULL,

                raw_40001 INTEGER NOT NULL,
                raw_40002 INTEGER NOT NULL,
                raw_40003 INTEGER NOT NULL,
                raw_40004 INTEGER NOT NULL,
                raw_40005 INTEGER NOT NULL,
                raw_40006 INTEGER NOT NULL,
                raw_40007 INTEGER NOT NULL,
                raw_40008 INTEGER NOT NULL,
                raw_40009 INTEGER NOT NULL,
                raw_40010 INTEGER NOT NULL,
                raw_40011 INTEGER NOT NULL,
                raw_40012 INTEGER NOT NULL,
                raw_40013 INTEGER NOT NULL,
                raw_40014 INTEGER NOT NULL,
                raw_40015 INTEGER NOT NULL
            )
            """
        )

        connection.commit()

    finally:
        connection.close()


def save_to_database(
    registers: list[int],
    values: dict,
) -> bool:
    """
    SCADA tarafından okunan verileri SQLite database'e kaydeder.

    decoded değerler:
        Gerçek mühendislik değerleri.

    raw_40001 ... raw_40015:
        Modbus'tan gelen ham 16-bit register değerleri.
        Hiçbir scaling / bölme / çarpma uygulanmaz.
    """

    connection = None

    try:
        raw = list(registers)

        # Tam olarak 15 register bekliyoruz.
        if len(raw) != HR.REGISTER_COUNT:
            LOGGER.error(
                "Database kayıt hatası: "
                "Beklenen %s register, gelen %s.",
                HR.REGISTER_COUNT,
                len(raw),
            )
            return False

        connection = sqlite3.connect(
            CONFIG.database_path
        )

        connection.execute(
            """
            INSERT INTO plc_readings (
                motor_command,
                mode,
                speed_setpoint,
                actual_rpm,
                current,
                pressure_setpoint,
                actual_pressure,
                temperature,
                heartbeat,

                raw_40001,
                raw_40002,
                raw_40003,
                raw_40004,
                raw_40005,
                raw_40006,
                raw_40007,
                raw_40008,
                raw_40009,
                raw_40010,
                raw_40011,
                raw_40012,
                raw_40013,
                raw_40014,
                raw_40015
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                # ------------------------------------------------
                # DECODED / ENGINEERING VALUES
                # ------------------------------------------------

                values["MotorCommand"],
                values["Mode"],
                values["SpeedSetpoint"],
                values["ActualRPM"],
                values["Current"],
                values["PressureSetpoint"],
                values["ActualPressure"],
                values["Temperature"],
                values["Heartbeat"],

                # ------------------------------------------------
                # RAW MODBUS REGISTERS
                # ------------------------------------------------

                raw[0],
                raw[1],
                raw[2],
                raw[3],
                raw[4],
                raw[5],
                raw[6],
                raw[7],
                raw[8],
                raw[9],
                raw[10],
                raw[11],
                raw[12],
                raw[13],
                raw[14],
            ),
        )

        connection.commit()

        return True

    except Exception as exc:

        if connection is not None:
            connection.rollback()

        LOGGER.error(
            "SQL database kayıt hatası: %s",
            exc,
        )

        return False

    finally:

        if connection is not None:
            connection.close()


# ============================================================
# FLOAT32 DECODER
# ============================================================

def decode_float32(
    first: int,
    second: int,
) -> float:

    first = int(first) & 0xFFFF
    second = int(second) & 0xFFFF

    if CONFIG.word_order == "big":

        raw = struct.pack(
            ">HH",
            first,
            second,
        )

    elif CONFIG.word_order == "little":

        raw = struct.pack(
            ">HH",
            second,
            first,
        )

    else:

        raise ValueError(
            f"Geçersiz word_order: "
            f"{CONFIG.word_order}"
        )

    return struct.unpack(
        ">f",
        raw,
    )[0]


# ============================================================
# MODBUS CLIENT
# ============================================================

def create_client() -> ModbusTcpClient:

    return ModbusTcpClient(
        host=CONFIG.ip,
        port=CONFIG.port,
        timeout=CONFIG.connection_timeout_s,
    )


# ============================================================
# READ REGISTERS
# ============================================================

def read_registers(
    client: ModbusTcpClient,
) -> list[int]:

    response = client.read_holding_registers(
        address=0,
        count=HR.REGISTER_COUNT,
        device_id=CONFIG.unit_id,
    )

    if response.isError():

        raise RuntimeError(
            f"Modbus okuma hatası: {response}"
        )

    if not response.registers:

        raise RuntimeError(
            "PLC'den register verisi alınamadı."
        )

    return [
        int(value) & 0xFFFF
        for value in response.registers
    ]


# ============================================================
# CONVERT REGISTERS
# ============================================================

def convert_registers(
    registers: list[int],
) -> dict:

    if len(registers) < HR.REGISTER_COUNT:

        raise RuntimeError(
            f"Beklenen "
            f"{HR.REGISTER_COUNT} register, "
            f"gelen {len(registers)}."
        )

    return {

        "MotorCommand":
            registers[HR.MOTOR_COMMAND],

        "Mode":
            registers[HR.MODE],

        "SpeedSetpoint":
            decode_float32(
                registers[HR.SPEED_SETPOINT_HI],
                registers[HR.SPEED_SETPOINT_LO],
            ),

        "ActualRPM":
            decode_float32(
                registers[HR.ACTUAL_RPM_HI],
                registers[HR.ACTUAL_RPM_LO],
            ),

        "Current":
            decode_float32(
                registers[HR.CURRENT_HI],
                registers[HR.CURRENT_LO],
            ),

        "PressureSetpoint":
            decode_float32(
                registers[HR.PRESSURE_SETPOINT_HI],
                registers[HR.PRESSURE_SETPOINT_LO],
            ),

        "ActualPressure":
            decode_float32(
                registers[HR.ACTUAL_PRESSURE_HI],
                registers[HR.ACTUAL_PRESSURE_LO],
            ),

        "Temperature":
            decode_float32(
                registers[HR.TEMPERATURE_HI],
                registers[HR.TEMPERATURE_LO],
            ),

        "Heartbeat":
            registers[HR.HEARTBEAT],
    }


# ============================================================
# PRINT SCADA DATA
# ============================================================

def print_values(
    values: dict,
) -> None:

    LOGGER.info(
        "SCADA DATA | "
        "Motor=%s | "
        "Mode=%s | "
        "Speed=%.2f | "
        "RPM=%.2f | "
        "Current=%.2f A | "
        "PressureSP=%.2f | "
        "Pressure=%.2f | "
        "Temperature=%.2f C | "
        "Heartbeat=%s",

        values["MotorCommand"],
        values["Mode"],
        values["SpeedSetpoint"],
        values["ActualRPM"],
        values["Current"],
        values["PressureSetpoint"],
        values["ActualPressure"],
        values["Temperature"],
        values["Heartbeat"],
    )


# ============================================================
# VALUE CHANGE CHECK
# ============================================================

def values_changed(
    old: dict,
    new: dict,
) -> bool:

    for key in new:

        old_value = old[key]
        new_value = new[key]

        if isinstance(new_value, float):

            if abs(
                new_value - old_value
            ) > 0.000001:

                return True

        else:

            if new_value != old_value:

                return True

    return False


# ============================================================
# SCADA MONITOR
# ============================================================

def monitor() -> None:

    LOGGER.info("=" * 70)

    LOGGER.info(
        "INDUSTRIAL SCADA CLIENT"
    )

    LOGGER.info("=" * 70)

    LOGGER.info(
        "PLC IP       : %s",
        CONFIG.ip,
    )

    LOGGER.info(
        "PLC PORT     : %s",
        CONFIG.port,
    )

    LOGGER.info(
        "UNIT ID      : %s",
        CONFIG.unit_id,
    )

    LOGGER.info(
        "POLLING      : %.0f ms",
        CONFIG.polling_interval_s * 1000,
    )

    LOGGER.info(
        "DATABASE     : %s",
        CONFIG.database_path,
    )

    LOGGER.info(
        "HEARTBEAT    : %.1f sec",
        CONFIG.heartbeat_timeout_s,
    )

    LOGGER.info("=" * 70)

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    initialize_database()

    LOGGER.info(
        "SQL database hazır."
    )

    # --------------------------------------------------------
    # MODBUS
    # --------------------------------------------------------

    client = create_client()

    last_values = None
    last_heartbeat = None
    last_heartbeat_time = time.monotonic()
    heartbeat_warning = False
    connected = False

    try:

        while True:

            # ------------------------------------------------
            # CONNECTION
            # ------------------------------------------------

            if not connected:

                LOGGER.info(
                    "PLC'ye bağlanılıyor: %s:%s",
                    CONFIG.ip,
                    CONFIG.port,
                )

                try:

                    connected = client.connect()

                except Exception as exc:

                    LOGGER.error(
                        "PLC bağlantı hatası: %s",
                        exc,
                    )

                    connected = False

                if not connected:

                    LOGGER.warning(
                        "PLC'ye bağlanılamadı."
                    )

                    time.sleep(2)

                    continue

                LOGGER.info(
                    "PLC bağlantısı başarılı."
                )

            # ------------------------------------------------
            # READ
            # ------------------------------------------------

            try:

                registers = read_registers(
                    client
                )

                values = convert_registers(
                    registers
                )

            except Exception as exc:

                LOGGER.error(
                    "Modbus okuma hatası: %s",
                    exc,
                )

                connected = False

                try:
                    client.close()
                except Exception:
                    pass

                time.sleep(1)

                continue

            # ------------------------------------------------
            # DATABASE
            # ------------------------------------------------

            database_saved = save_to_database(
                registers,
                values,
            )

            if database_saved:

                LOGGER.debug(
                    "PLC verisi SQL database'e kaydedildi."
                )

            # ------------------------------------------------
            # HEARTBEAT
            # ------------------------------------------------

            heartbeat = values[
                "Heartbeat"
            ]

            if last_heartbeat is None:

                last_heartbeat = heartbeat

                last_heartbeat_time = (
                    time.monotonic()
                )

                LOGGER.info(
                    "Heartbeat başlatıldı: %s",
                    heartbeat,
                )

            elif heartbeat != last_heartbeat:

                LOGGER.info(
                    "HEARTBEAT | %s -> %s",
                    last_heartbeat,
                    heartbeat,
                )

                last_heartbeat = heartbeat

                last_heartbeat_time = (
                    time.monotonic()
                )

                heartbeat_warning = False

            else:

                elapsed = (
                    time.monotonic()
                    - last_heartbeat_time
                )

                if (
                    elapsed
                    > CONFIG.heartbeat_timeout_s
                ):

                    if not heartbeat_warning:

                        LOGGER.warning(
                            "HEARTBEAT TIMEOUT | "
                            "PLC %.2f saniyedir "
                            "heartbeat değiştirmiyor.",
                            elapsed,
                        )

                        heartbeat_warning = True

            # ------------------------------------------------
            # DATA CHANGE
            # ------------------------------------------------

            if last_values is None:

                LOGGER.info(
                    "İlk PLC verisi alındı."
                )

                print_values(
                    values
                )

                last_values = values

            elif values_changed(
                last_values,
                values,
            ):

                print_values(
                    values
                )

                last_values = values

            # ------------------------------------------------
            # POLLING
            # ------------------------------------------------

            time.sleep(
                CONFIG.polling_interval_s
            )

    except KeyboardInterrupt:

        LOGGER.info(
            "SCADA client kullanıcı tarafından durduruldu."
        )

    finally:

        client.close()

        LOGGER.info(
            "PLC bağlantısı kapatıldı."
        )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    try:

        monitor()

    except Exception as exc:

        LOGGER.exception(
            "SCADA client beklenmeyen hata nedeniyle kapandı: %s",
            exc,
        )
