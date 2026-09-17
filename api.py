from __future__ import annotations

import sqlite3
import struct
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pymodbus.client import ModbusTcpClient


# ============================================================
# CONFIGURATION
# ============================================================

MODBUS_HOST = "127.0.0.1"
MODBUS_PORT = 502
MODBUS_UNIT_ID = 1

REGISTER_COUNT = 15
MODBUS_TIMEOUT = 3

DATABASE_PATH = Path("scada.db")


# ============================================================
# REGISTER MAP
# ============================================================

class HR:
    MOTOR_COMMAND = 0          # 40001
    MODE = 1                    # 40002

    SPEED_SETPOINT = 2         # 40003-40004
    ACTUAL_RPM = 4              # 40005-40006
    CURRENT = 6                 # 40007-40008
    PRESSURE_SETPOINT = 8      # 40009-40010
    ACTUAL_PRESSURE = 10       # 40011-40012
    TEMPERATURE = 12            # 40013-40014

    HEARTBEAT = 14              # 40015


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="ScadaWatt API",
    description="REST API gateway for ScadaWatt Modbus TCP SCADA system",
    version="3.0.0",
)


# ============================================================
# MODBUS CLIENT
# ============================================================

def get_modbus_client() -> ModbusTcpClient:
    client = ModbusTcpClient(
        host=MODBUS_HOST,
        port=MODBUS_PORT,
        timeout=MODBUS_TIMEOUT,
    )

    if not client.connect():
        raise HTTPException(
            status_code=503,
            detail=(
                f"Modbus cihazına bağlanılamadı: "
                f"{MODBUS_HOST}:{MODBUS_PORT}"
            ),
        )

    return client


# ============================================================
# READ MODBUS REGISTERS
# ============================================================

def read_registers() -> list[int]:
    client = get_modbus_client()

    try:
        response = client.read_holding_registers(
            address=0,
            count=REGISTER_COUNT,
            device_id=MODBUS_UNIT_ID,
        )

        if response.isError():
            raise HTTPException(
                status_code=502,
                detail=f"Modbus okuma hatası: {response}",
            )

        if not response.registers:
            raise HTTPException(
                status_code=502,
                detail="Modbus cihazından register verisi alınamadı.",
            )

        return [
            int(value) & 0xFFFF
            for value in response.registers
        ]

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Modbus bağlantı/okuma hatası: {exc}",
        )

    finally:
        client.close()


# ============================================================
# FLOAT32 DECODER
# ============================================================

def decode_float32(
    high_word: int,
    low_word: int,
) -> float:

    high_word &= 0xFFFF
    low_word &= 0xFFFF

    raw = struct.pack(
        ">HH",
        high_word,
        low_word,
    )

    return struct.unpack(
        ">f",
        raw,
    )[0]


# ============================================================
# DATA CONVERSION
# ============================================================

def convert_registers(
    registers: list[int],
) -> dict[str, Any]:

    if len(registers) < REGISTER_COUNT:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Beklenen {REGISTER_COUNT} register, "
                f"gelen {len(registers)}."
            ),
        )

    return {
        "MotorCommand": registers[HR.MOTOR_COMMAND],
        "Mode": registers[HR.MODE],

        "SpeedSetpoint": decode_float32(
            registers[HR.SPEED_SETPOINT],
            registers[HR.SPEED_SETPOINT + 1],
        ),

        "ActualRPM": decode_float32(
            registers[HR.ACTUAL_RPM],
            registers[HR.ACTUAL_RPM + 1],
        ),

        "Current": decode_float32(
            registers[HR.CURRENT],
            registers[HR.CURRENT + 1],
        ),

        "PressureSetpoint": decode_float32(
            registers[HR.PRESSURE_SETPOINT],
            registers[HR.PRESSURE_SETPOINT + 1],
        ),

        "ActualPressure": decode_float32(
            registers[HR.ACTUAL_PRESSURE],
            registers[HR.ACTUAL_PRESSURE + 1],
        ),

        "Temperature": decode_float32(
            registers[HR.TEMPERATURE],
            registers[HR.TEMPERATURE + 1],
        ),

        "Heartbeat": registers[HR.HEARTBEAT],
    }


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_database_connection() -> sqlite3.Connection:

    if not DATABASE_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail=f"SCADA database bulunamadı: {DATABASE_PATH}",
        )

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=5,
    )

    connection.row_factory = sqlite3.Row

    return connection


# ============================================================
# DATABASE - LATEST RECORD
# ============================================================

def get_latest_database_record() -> dict[str, Any] | None:

    connection = get_database_connection()

    try:
        row = connection.execute(
            """
            SELECT *
            FROM plc_readings
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

        if row is None:
            return None

        return dict(row)

    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=500,
            detail=f"SQLite okuma hatası: {exc}",
        )

    finally:
        connection.close()


# ============================================================
# DATABASE - RECORD COUNT
# ============================================================

def get_database_record_count() -> int:

    connection = get_database_connection()

    try:
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM plc_readings
            """
        ).fetchone()

        return int(row[0])

    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=500,
            detail=f"SQLite okuma hatası: {exc}",
        )

    finally:
        connection.close()


# ============================================================
# DATABASE - HISTORY
# ============================================================

def get_database_history(
    limit: int,
) -> list[dict[str, Any]]:

    connection = get_database_connection()

    try:
        rows = connection.execute(
            """
            SELECT
                id,
                timestamp,
                motor_command,
                mode,
                speed_setpoint,
                actual_rpm,
                current,
                pressure_setpoint,
                actual_pressure,
                temperature,
                heartbeat
            FROM plc_readings
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        return [dict(row) for row in rows]

    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=500,
            detail=f"SQLite history okuma hatası: {exc}",
        )

    finally:
        connection.close()


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return {
        "name": "ScadaWatt API",
        "version": "3.0.0",
        "status": "online",

        "architecture": {
            "plc": "Modbus TCP",
            "scada": "Python SCADA Client",
            "database": "SQLite",
            "api": "FastAPI",
        },

        "modbus": {
            "host": MODBUS_HOST,
            "port": MODBUS_PORT,
            "unit_id": MODBUS_UNIT_ID,
        },

        "database": {
            "path": str(DATABASE_PATH),
            "table": "plc_readings",
        },

        "register_count": REGISTER_COUNT,
    }


# ============================================================
# LIVE STATUS
# ============================================================

@app.get("/api/v1/status")
def get_status():

    registers = read_registers()
    data = convert_registers(registers)

    record_count = get_database_record_count()

    return {
        "status": "online",

        "modbus": {
            "status": "connected",
            "host": MODBUS_HOST,
            "port": MODBUS_PORT,
            "unit_id": MODBUS_UNIT_ID,
        },

        "database": {
            "status": "connected",
            "record_count": record_count,
        },

        "data": data,
    }


# ============================================================
# RAW REGISTERS
# ============================================================

@app.get("/api/v1/registers")
def get_registers():

    registers = read_registers()

    return {
        "status": "success",

        "registers": registers,

        "count": len(registers),

        "address_range": "40001-40015",

        "map": {
            "40001": "MotorCommand",
            "40002": "Mode",
            "40003-40004": "SpeedSetpoint",
            "40005-40006": "ActualRPM",
            "40007-40008": "Current",
            "40009-40010": "PressureSetpoint",
            "40011-40012": "ActualPressure",
            "40013-40014": "Temperature",
            "40015": "Heartbeat",
        },
    }


# ============================================================
# LATEST SCADA DATABASE RECORD
# ============================================================

@app.get("/api/v1/latest")
def get_latest():

    record = get_latest_database_record()

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="SCADA database içinde henüz kayıt bulunmuyor.",
        )

    return {
        "status": "success",
        "source": "scada.db",
        "record": record,
    }


# ============================================================
# SCADA HISTORY
# ============================================================

@app.get("/api/v1/history")
def get_history(
    limit: int = Query(
        default=20,
        ge=1,
        le=500,
        description="Döndürülecek maksimum kayıt sayısı",
    )
):

    records = get_database_history(limit)

    return {
        "status": "success",
        "count": len(records),
        "limit": limit,
        "records": records,
    }


# ============================================================
# DATABASE STATUS
# ============================================================

@app.get("/api/v1/database")
def database_status():

    record_count = get_database_record_count()
    latest = get_latest_database_record()

    return {
        "status": "connected",
        "database": str(DATABASE_PATH),
        "table": "plc_readings",
        "record_count": record_count,
        "latest_record": latest,
    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/api/v1/health")
def health():

    modbus_status = "disconnected"
    database_status_value = "disconnected"

    heartbeat = None
    register_count = 0
    database_records = 0

    # --------------------------------------------------------
    # MODBUS HEALTH
    # --------------------------------------------------------

    try:
        registers = read_registers()

        modbus_status = "connected"
        register_count = len(registers)

        if len(registers) > HR.HEARTBEAT:
            heartbeat = registers[HR.HEARTBEAT]

    except HTTPException:
        pass

    except Exception:
        pass

    # --------------------------------------------------------
    # DATABASE HEALTH
    # --------------------------------------------------------

    try:
        database_records = get_database_record_count()
        database_status_value = "connected"

    except HTTPException:
        pass

    except Exception:
        pass

    # --------------------------------------------------------
    # OVERALL STATUS
    # --------------------------------------------------------

    if (
        modbus_status == "connected"
        and database_status_value == "connected"
    ):
        overall_status = "healthy"

    elif (
        modbus_status == "connected"
        or database_status_value == "connected"
    ):
        overall_status = "degraded"

    else:
        overall_status = "unhealthy"

    return {
        "status": overall_status,

        "services": {
            "modbus": {
                "status": modbus_status,
                "host": MODBUS_HOST,
                "port": MODBUS_PORT,
                "unit_id": MODBUS_UNIT_ID,
                "register_count": register_count,
                "heartbeat": heartbeat,
            },

            "database": {
                "status": database_status_value,
                "path": str(DATABASE_PATH),
                "record_count": database_records,
            },
        },
    }


# ============================================================
# APPLICATION INFO
# ============================================================

@app.get("/api/v1/info")
def application_info():

    return {
        "application": "ScadaWatt",
        "api_version": "3.0.0",

        "components": {
            "plc_simulator": "slave.py",
            "scada_client": "scada.py",
            "database": "SQLite",
            "api": "FastAPI",
        },

        "protocol": {
            "name": "Modbus TCP",
            "host": MODBUS_HOST,
            "port": MODBUS_PORT,
            "unit_id": MODBUS_UNIT_ID,
            "function_code": 3,
        },

        "register_map": {
            "40001": "MotorCommand",
            "40002": "Mode",
            "40003-40004": "SpeedSetpoint FLOAT32",
            "40005-40006": "ActualRPM FLOAT32",
            "40007-40008": "Current FLOAT32",
            "40009-40010": "PressureSetpoint FLOAT32",
            "40011-40012": "ActualPressure FLOAT32",
            "40013-40014": "Temperature FLOAT32",
            "40015": "Heartbeat UINT16",
        },

        "database": {
            "engine": "SQLite",
            "file": str(DATABASE_PATH),
            "table": "plc_readings",
        },
    }