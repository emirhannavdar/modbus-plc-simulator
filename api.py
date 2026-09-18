import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "scada.db"

SCADA_TIMEOUT_SECONDS = 5

SUPPORTED_DATA_TYPES = {
    "BOOL": 1,
    "UINT16": 1,
    "INT16": 1,
    "UINT32": 2,
    "INT32": 2,
    "FLOAT32": 2,
}

SUPPORTED_ACCESS = {
    "read",
    "write",
    "read_write",
}


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="ScadaWatt REST API",
    description=(
        "Dynamic REST API for multi-device Modbus/SCADA "
        "register configuration, live values, snapshots, "
        "history and command management."
    ),
    version="6.0.1",
)


# ============================================================
# UTILITY
# ============================================================

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=10,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def table_exists(
    connection: sqlite3.Connection,
    table_name: str,
) -> bool:
    row = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        AND name = ?
        """,
        (table_name,),
    ).fetchone()

    return row is not None


def column_exists(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
) -> bool:
    rows = connection.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return any(
        row["name"] == column_name
        for row in rows
    )


def validate_register_definition(
    name: str,
    address: int,
    data_type: str,
    access: str,
) -> None:
    if not name.strip():
        raise HTTPException(
            status_code=400,
            detail="Register name cannot be empty.",
        )

    if address < 0:
        raise HTTPException(
            status_code=400,
            detail="Register address cannot be negative.",
        )

    if data_type not in SUPPORTED_DATA_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported data_type '{data_type}'. "
                f"Supported types: "
                f"{sorted(SUPPORTED_DATA_TYPES.keys())}"
            ),
        )

    if access not in SUPPORTED_ACCESS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported access '{access}'. "
                f"Supported values: "
                f"{sorted(SUPPORTED_ACCESS)}"
            ),
        )


def serialize_register(row: sqlite3.Row) -> dict:
    result = dict(row)

    if "enabled" in result:
        result["enabled"] = bool(result["enabled"])

    result["word_count"] = SUPPORTED_DATA_TYPES.get(
        result["data_type"],
        1,
    )

    return result


def serialize_device(row: sqlite3.Row) -> dict:
    result = dict(row)

    if "enabled" in result:
        result["enabled"] = bool(result["enabled"])

    return result


def load_json_value(value_json: str) -> Any:
    try:
        return json.loads(value_json)
    except json.JSONDecodeError:
        return value_json


# ============================================================
# DATABASE MIGRATION
# ============================================================

def migrate_old_database(
    connection: sqlite3.Connection,
) -> None:
    """
    Migrates the old single-device schema to the new
    multi-device schema while preserving historical data.
    """

    if not table_exists(connection, "registers"):
        return

    # Already migrated.
    if column_exists(
        connection,
        "registers",
        "device_id",
    ):
        return

    print("Old database schema detected.")
    print("Migrating database to multi-device architecture...")

    connection.execute("PRAGMA foreign_keys = OFF")

    try:
        # ----------------------------------------------------
        # Rename old tables
        # ----------------------------------------------------

        old_tables = [
            "registers",
            "register_values",
            "scada_snapshots",
            "commands",
            "scada_status",
        ]

        for table in old_tables:
            if table_exists(connection, table):
                connection.execute(
                    f"ALTER TABLE {table} "
                    f"RENAME TO {table}_legacy"
                )

        # ----------------------------------------------------
        # Devices
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                host TEXT NOT NULL DEFAULT '127.0.0.1',
                port INTEGER NOT NULL DEFAULT 502,
                unit_id INTEGER NOT NULL DEFAULT 1,
                description TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                poll_interval INTEGER NOT NULL DEFAULT 1000,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        # ----------------------------------------------------
        # New registers
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE registers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                address INTEGER NOT NULL,
                data_type TEXT NOT NULL,
                access TEXT NOT NULL DEFAULT 'read',
                unit TEXT,
                description TEXT,
                multiplier REAL NOT NULL DEFAULT 1.0,
                offset REAL NOT NULL DEFAULT 0.0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                FOREIGN KEY (device_id)
                    REFERENCES devices(id)
                    ON DELETE CASCADE,

                UNIQUE(device_id, name)
            )
            """
        )

        # ----------------------------------------------------
        # Register values
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE register_values (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                register_id INTEGER NOT NULL,
                value_json TEXT NOT NULL,
                timestamp TEXT NOT NULL,

                FOREIGN KEY (register_id)
                    REFERENCES registers(id)
                    ON DELETE CASCADE
            )
            """
        )

        # ----------------------------------------------------
        # Snapshots
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE scada_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                register_count INTEGER NOT NULL DEFAULT 0,
                values_json TEXT NOT NULL
            )
            """
        )

        # ----------------------------------------------------
        # Commands
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                register_id INTEGER NOT NULL,
                value_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT,

                FOREIGN KEY (register_id)
                    REFERENCES registers(id)
                    ON DELETE CASCADE
            )
            """
        )

        # ----------------------------------------------------
        # SCADA status
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE scada_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                status TEXT NOT NULL DEFAULT 'offline',
                last_seen TEXT,
                last_success TEXT,
                last_error TEXT,
                register_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )

        # ----------------------------------------------------
        # Indexes
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_registers_device_id
            ON registers(device_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_register_values_register_id
            ON register_values(register_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_register_values_timestamp
            ON register_values(timestamp)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_commands_status
            ON commands(status)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_commands_created_at
            ON commands(created_at)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
            ON scada_snapshots(timestamp)
            """
        )

        # ----------------------------------------------------
        # Create Device 1
        # ----------------------------------------------------

        timestamp = utc_now()

        connection.execute(
            """
            INSERT INTO devices (
                id,
                name,
                host,
                port,
                unit_id,
                description,
                enabled,
                poll_interval,
                created_at,
                updated_at
            )
            VALUES (
                1,
                'PLC-01',
                '127.0.0.1',
                502,
                1,
                'Migrated from legacy SCADA configuration.',
                1,
                1000,
                ?,
                ?
            )
            """,
            (timestamp, timestamp),
        )

        # ----------------------------------------------------
        # Copy old registers
        # ----------------------------------------------------

        if table_exists(connection, "registers_legacy"):
            old_registers = connection.execute(
                """
                SELECT *
                FROM registers_legacy
                ORDER BY id ASC
                """
            ).fetchall()

            for register in old_registers:
                connection.execute(
                    """
                    INSERT INTO registers (
                        id,
                        device_id,
                        name,
                        address,
                        data_type,
                        access,
                        unit,
                        description,
                        multiplier,
                        offset,
                        enabled,
                        created_at,
                        updated_at
                    )
                    VALUES (?, 1, ?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?, ?)
                    """,
                    (
                        register["id"],
                        register["name"],
                        register["address"],
                        register["data_type"],
                        register["access"],
                        register["unit"],
                        register["description"],
                        register["enabled"],
                        register["created_at"],
                        register["updated_at"],
                    ),
                )

        # ----------------------------------------------------
        # Copy historical values
        # ----------------------------------------------------

        if table_exists(
            connection,
            "register_values_legacy",
        ):
            connection.execute(
                """
                INSERT INTO register_values (
                    id,
                    register_id,
                    value_json,
                    timestamp
                )
                SELECT
                    id,
                    register_id,
                    value_json,
                    timestamp
                FROM register_values_legacy
                """
            )

        # ----------------------------------------------------
        # Copy snapshots
        # ----------------------------------------------------

        if table_exists(
            connection,
            "scada_snapshots_legacy",
        ):
            connection.execute(
                """
                INSERT INTO scada_snapshots (
                    id,
                    timestamp,
                    register_count,
                    values_json
                )
                SELECT
                    id,
                    timestamp,
                    register_count,
                    values_json
                FROM scada_snapshots_legacy
                """
            )

        # ----------------------------------------------------
        # Copy commands
        # ----------------------------------------------------

        if table_exists(
            connection,
            "commands_legacy",
        ):
            connection.execute(
                """
                INSERT INTO commands (
                    id,
                    register_id,
                    value_json,
                    status,
                    error,
                    created_at,
                    processed_at
                )
                SELECT
                    id,
                    register_id,
                    value_json,
                    status,
                    error,
                    created_at,
                    processed_at
                FROM commands_legacy
                """
            )

        # ----------------------------------------------------
        # Copy SCADA status
        # ----------------------------------------------------

        if table_exists(
            connection,
            "scada_status_legacy",
        ):
            connection.execute(
                """
                INSERT INTO scada_status (
                    id,
                    status,
                    last_seen,
                    last_success,
                    last_error,
                    register_count,
                    updated_at
                )
                SELECT
                    id,
                    status,
                    last_seen,
                    last_success,
                    last_error,
                    register_count,
                    updated_at
                FROM scada_status_legacy
                WHERE id = 1
                """
            )

        # ----------------------------------------------------
        # Guarantee SCADA status
        # ----------------------------------------------------

        connection.execute(
            """
            INSERT OR IGNORE INTO scada_status (
                id,
                status,
                updated_at
            )
            VALUES (
                1,
                'offline',
                ?
            )
            """,
            (utc_now(),),
        )

        # ----------------------------------------------------
        # Remove legacy tables
        # ----------------------------------------------------

        for table in old_tables:
            legacy_table = f"{table}_legacy"

            if table_exists(
                connection,
                legacy_table,
            ):
                connection.execute(
                    f"DROP TABLE {legacy_table}"
                )

        connection.execute(
            "PRAGMA foreign_keys = ON"
        )

        connection.commit()

        print(
            "Database migration completed successfully."
        )

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.execute(
            "PRAGMA foreign_keys = ON"
        )


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def initialize_database() -> None:
    connection = get_connection()

    try:
        migrate_old_database(connection)

        # ----------------------------------------------------
        # Devices
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                host TEXT NOT NULL DEFAULT '127.0.0.1',
                port INTEGER NOT NULL DEFAULT 502,
                unit_id INTEGER NOT NULL DEFAULT 1,
                description TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                poll_interval INTEGER NOT NULL DEFAULT 1000,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        # ----------------------------------------------------
        # Registers
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS registers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                address INTEGER NOT NULL,
                data_type TEXT NOT NULL,
                access TEXT NOT NULL DEFAULT 'read',
                unit TEXT,
                description TEXT,
                multiplier REAL NOT NULL DEFAULT 1.0,
                offset REAL NOT NULL DEFAULT 0.0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                FOREIGN KEY (device_id)
                    REFERENCES devices(id)
                    ON DELETE CASCADE,

                UNIQUE(device_id, name)
            )
            """
        )

        # ----------------------------------------------------
        # Register values
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS register_values (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                register_id INTEGER NOT NULL,
                value_json TEXT NOT NULL,
                timestamp TEXT NOT NULL,

                FOREIGN KEY (register_id)
                    REFERENCES registers(id)
                    ON DELETE CASCADE
            )
            """
        )

        # ----------------------------------------------------
        # Snapshots
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scada_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                register_count INTEGER NOT NULL DEFAULT 0,
                values_json TEXT NOT NULL
            )
            """
        )

        # ----------------------------------------------------
        # Commands
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                register_id INTEGER NOT NULL,
                value_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT,

                FOREIGN KEY (register_id)
                    REFERENCES registers(id)
                    ON DELETE CASCADE
            )
            """
        )

        # ----------------------------------------------------
        # SCADA status
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scada_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                status TEXT NOT NULL DEFAULT 'offline',
                last_seen TEXT,
                last_success TEXT,
                last_error TEXT,
                register_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )

        # ----------------------------------------------------
        # Indexes
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_registers_device_id
            ON registers(device_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_register_values_register_id
            ON register_values(register_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_register_values_timestamp
            ON register_values(timestamp)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_commands_status
            ON commands(status)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_commands_created_at
            ON commands(created_at)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_scada_snapshots_timestamp
            ON scada_snapshots(timestamp)
            """
        )

        # ----------------------------------------------------
        # Default device
        # ----------------------------------------------------

        device_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM devices
            """
        ).fetchone()[0]

        if device_count == 0:
            seed_device(connection)

        # ----------------------------------------------------
        # Default registers
        # ----------------------------------------------------

        register_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM registers
            """
        ).fetchone()[0]

        if register_count == 0:
            device = connection.execute(
                """
                SELECT id
                FROM devices
                ORDER BY id ASC
                LIMIT 1
                """
            ).fetchone()

            if device:
                seed_registers(
                    connection,
                    device["id"],
                )

        # ----------------------------------------------------
        # SCADA status
        # ----------------------------------------------------

        connection.execute(
            """
            INSERT OR IGNORE INTO scada_status (
                id,
                status,
                updated_at
            )
            VALUES (
                1,
                'offline',
                ?
            )
            """,
            (utc_now(),),
        )

        connection.commit()

    finally:
        connection.close()


# ============================================================
# DEVICE SEED
# ============================================================

def seed_device(
    connection: sqlite3.Connection,
) -> None:
    timestamp = utc_now()

    connection.execute(
        """
        INSERT INTO devices (
            name,
            host,
            port,
            unit_id,
            description,
            enabled,
            poll_interval,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "PLC-01",
            "127.0.0.1",
            502,
            1,
            "Default Modbus TCP PLC simulator.",
            1,
            1000,
            timestamp,
            timestamp,
        ),
    )


# ============================================================
# REGISTER SEED
# ============================================================

def seed_registers(
    connection: sqlite3.Connection,
    device_id: int,
) -> None:
    registers = [
        {
            "name": "MotorCommand",
            "address": 0,
            "data_type": "UINT16",
            "access": "read_write",
            "unit": "",
            "description": "Motor start/stop command.",
        },
        {
            "name": "Mode",
            "address": 1,
            "data_type": "UINT16",
            "access": "read_write",
            "unit": "",
            "description": "PLC operating mode.",
        },
        {
            "name": "SpeedSetpoint",
            "address": 2,
            "data_type": "FLOAT32",
            "access": "read_write",
            "unit": "RPM",
            "description": "Motor speed setpoint.",
        },
        {
            "name": "ActualRPM",
            "address": 4,
            "data_type": "FLOAT32",
            "access": "read",
            "unit": "RPM",
            "description": "Actual motor speed.",
        },
        {
            "name": "Current",
            "address": 6,
            "data_type": "FLOAT32",
            "access": "read",
            "unit": "A",
            "description": "Motor current.",
        },
        {
            "name": "PressureSetpoint",
            "address": 8,
            "data_type": "FLOAT32",
            "access": "read_write",
            "unit": "bar",
            "description": "Pressure setpoint.",
        },
        {
            "name": "ActualPressure",
            "address": 10,
            "data_type": "FLOAT32",
            "access": "read",
            "unit": "bar",
            "description": "Actual pressure.",
        },
        {
            "name": "Temperature",
            "address": 12,
            "data_type": "FLOAT32",
            "access": "read",
            "unit": "°C",
            "description": "PLC process temperature.",
        },
        {
            "name": "Heartbeat",
            "address": 14,
            "data_type": "UINT16",
            "access": "read",
            "unit": "",
            "description": "PLC heartbeat counter.",
        },
    ]

    timestamp = utc_now()

    for register in registers:
        connection.execute(
            """
            INSERT INTO registers (
                device_id,
                name,
                address,
                data_type,
                access,
                unit,
                description,
                multiplier,
                offset,
                enabled,
                created_at,
                updated_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, 1.0, 0.0, 1, ?, ?
            )
            """,
            (
                device_id,
                register["name"],
                register["address"],
                register["data_type"],
                register["access"],
                register["unit"],
                register["description"],
                timestamp,
                timestamp,
            ),
        )


initialize_database()


class DeviceCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=100,
    )
    host: str = "127.0.0.1"
    port: int = Field(
        default=502,
        ge=1,
        le=65535,
    )
    unit_id: int = Field(
        default=1,
        ge=0,
        le=255,
    )
    description: str | None = None
    enabled: bool = True
    poll_interval: int = Field(
        default=1000,
        ge=100,
    )


class DeviceUpdate(BaseModel):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    host: str | None = None
    port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
    )
    unit_id: int | None = Field(
        default=None,
        ge=0,
        le=255,
    )
    description: str | None = None
    enabled: bool | None = None
    poll_interval: int | None = Field(
        default=None,
        ge=100,
    )


class RegisterCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=100,
    )
    address: int = Field(
        ge=0,
    )
    data_type: str
    access: str = "read"
    unit: str | None = None
    description: str | None = None
    multiplier: float = 1.0
    offset: float = 0.0
    enabled: bool = True


class RegisterUpdate(BaseModel):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    address: int | None = Field(
        default=None,
        ge=0,
    )
    data_type: str | None = None
    access: str | None = None
    unit: str | None = None
    description: str | None = None
    multiplier: float | None = None
    offset: float | None = None
    enabled: bool | None = None


class RegisterValue(BaseModel):
    register_id: int
    value: Any


class BulkValuesRequest(BaseModel):
    values: list[RegisterValue]


class SnapshotRegister(BaseModel):
    register_id: int
    value: Any


class SnapshotRequest(BaseModel):
    values: list[SnapshotRegister]


class CommandCreate(BaseModel):
    register_id: int
    value: Any


class CommandResult(BaseModel):
    status: str
    error: str | None = None


class ScadaHeartbeat(BaseModel):
    status: str = "online"
    register_count: int = 0
    error: str | None = None


@app.get("/Emirhan")
def root():
    return {
        "application": "ScadaWatt REST API",
        "version": "6.0.1",
        "status": "online",
        "architecture": "multi-device",
        "database": str(DATABASE_PATH),
        "docs": "/docs",
    }


@app.get("/api/v1/devices")
def get_devices(enabled_only: bool = Query(default=False, description="Return only enabled devices.",),):
    connection = get_connection()                                   

    try:
        if enabled_only:
            rows = connection.execute(
                """
                SELECT *
                FROM devices
                WHERE enabled = 1
                ORDER BY id ASC
                """
            ).fetchall()                                            
        else:
            rows = connection.execute(
                """
                SELECT *
                FROM devices
                ORDER BY id ASC
                """
            ).fetchall()

        devices = []

        for row in rows:
            device = serialize_device(row)

            register_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM registers
                WHERE device_id = ?
                AND enabled = 1
                """,
                (row["id"],),
            ).fetchone()[0]

            device["register_count"] = register_count

            devices.append(device)

        return {
            "count": len(devices),
            "devices": devices,
        }

    finally:
        connection.close()


@app.get("/api/v1/devices/{device_id}")
def get_device(device_id: int):
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT *
            FROM devices
            WHERE id = ?
            """,
            (device_id,),
        ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Cihaz bulunamadı emirhan kurcalama.",
            )

        device = serialize_device(row)

        device["register_count"] = connection.execute(
            """
            SELECT COUNT(*)
            FROM registers
            WHERE device_id = ?
            AND enabled = 1
            """,
            (device_id,),
        ).fetchone()[0]

        return device

    finally:
        connection.close()


@app.post("/api/v1/devices", status_code=201,)
def create_device(device: DeviceCreate):
    connection = get_connection()

    try:
        existing = connection.execute(
            """
            SELECT id
            FROM devices
            WHERE LOWER(name) = LOWER(?)
            """,
            (device.name.strip(),),
        ).fetchone()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="A device with this name already exists.",
            )

        timestamp = utc_now()

        cursor = connection.execute(
            """
            INSERT INTO devices (
                name,
                host,
                port,
                unit_id,
                description,
                enabled,
                poll_interval,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device.name.strip(),
                device.host.strip(),
                device.port,
                device.unit_id,
                device.description,
                int(device.enabled),
                device.poll_interval,
                timestamp,
                timestamp,
            ),
        )

        connection.commit()

        row = connection.execute(
            """
            SELECT *
            FROM devices
            WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()

        return serialize_device(row)

    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Database constraint error: {exc}",
        )

    finally:
        connection.close()


@app.put("/api/v1/devices/{device_id}")
def update_device(device_id: int, device: DeviceUpdate,):
    connection = get_connection()

    try:
        existing = connection.execute(
            """
            SELECT *
            FROM devices
            WHERE id = ?
            """,
            (device_id,),
        ).fetchone()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Device not found.",
            )

        current = dict(existing)

        name = (
            device.name.strip()
            if device.name is not None
            else current["name"]
        )

        host = (
            device.host.strip()
            if device.host is not None
            else current["host"]
        )

        port = (
            device.port
            if device.port is not None
            else current["port"]
        )

        unit_id = (
            device.unit_id
            if device.unit_id is not None
            else current["unit_id"]
        )

        description = (
            device.description
            if device.description is not None
            else current["description"]
        )

        enabled = (
            device.enabled
            if device.enabled is not None
            else bool(current["enabled"])
        )

        poll_interval = (
            device.poll_interval
            if device.poll_interval is not None
            else current["poll_interval"]
        )

        connection.execute(
            """
            UPDATE devices
            SET
                name = ?,
                host = ?,
                port = ?,
                unit_id = ?,
                description = ?,
                enabled = ?,
                poll_interval = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                name,
                host,
                port,
                unit_id,
                description,
                int(enabled),
                poll_interval,
                utc_now(),
                device_id,
            ),
        )

        connection.commit()

        row = connection.execute(
            """
            SELECT *
            FROM devices
            WHERE id = ?
            """,
            (device_id,),
        ).fetchone()

        return serialize_device(row)

    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Database constraint error: {exc}",
        )

    finally:
        connection.close()


@app.get("/api/v1/Emirhan/navdar/{device_id}")
def showDevices(device_id: int):
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT *
            FROM devices
            WHERE id = ?
            """,
            (device_id,),
        ).fetchone()

        if row is None:
            raise HTTPException(
                status_code = 404,
                detail="Cihaz bulunamadı emirhan kurcalama.",
            )
        device = serialize_device(row)

        device["register_count"] = connection.execute(
            """
            SELECT COUNT(*)
            FROM registers
            WHERE device_id = ?
            AND enabled = 1
            """,
            (device_id,),
        ).fetchone()[0]

        return device
    finally:
        connection.close()


@app.delete("/api/v1/devices/{device_id}")
def delete_device(device_id: int):
    connection = get_connection()

    try:
        existing = connection.execute(
            """
            SELECT id, name
            FROM devices
            WHERE id = ?
            """,
            (device_id,),
        ).fetchone()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Device not found.",
            )

        connection.execute(
            """
            DELETE FROM devices
            WHERE id = ?
            """,
            (device_id,),
        )

        connection.commit()

        return {
            "success": True,
            "deleted_device_id": device_id,
            "deleted_device_name": existing["name"],
        }

    finally:
        connection.close()


@app.get("/api/v1/registers")
def get_registers(
    enabled_only: bool = Query(
        default=False,
        description="Return only enabled registers.",
    ),
    device_id: int | None = Query(
        default=None,
        description="Filter registers by device.",
    ),
    emirhan_düsündügün_gibi_mi_görceeess: str = Query(
        default=True,
        description="doğru düsündün la",
    )
):
    connection = get_connection()

    try:
        query = """
            SELECT
                r.*,
                d.name AS device_name,
                d.host AS device_host,
                d.port AS device_port,
                d.unit_id AS device_unit_id
            FROM registers r
            INNER JOIN devices d
                ON d.id = r.device_id
            WHERE 1 = 1
        """

        params = []

        if enabled_only:
            query += " AND r.enabled = 1"

        if device_id is not None:
            query += " AND r.device_id = ?"
            params.append(device_id)

        query += """
            ORDER BY
                r.device_id ASC,
                r.address ASC,
                r.id ASC
        """

        rows = connection.execute(
            query,
            params,
        ).fetchall()

        result = []

        for row in rows:
            result.append(
                serialize_register(row)
            )

        return {
            "count": len(result),
            "registers": result,
        }

    finally:
        connection.close()


@app.get("/api/v1/devices/{device_id}/registers")
def get_device_registers(
    device_id: int,
    enabled_only: bool = Query(
        default=False,
    ),
):
    connection = get_connection()

    try:
        device = connection.execute(
            """
            SELECT *
            FROM devices
            WHERE id = ?
            """,
            (device_id,),
        ).fetchone()

        if device is None:
            raise HTTPException(
                status_code=404,
                detail="Device not found.",
            )

        if enabled_only:
            rows = connection.execute(
                """
                SELECT
                    r.*,
                    d.name AS device_name,
                    d.host AS device_host,
                    d.port AS device_port,
                    d.unit_id AS device_unit_id
                FROM registers r
                INNER JOIN devices d
                    ON d.id = r.device_id
                WHERE r.device_id = ?
                AND r.enabled = 1
                ORDER BY r.address ASC, r.id ASC
                """,
                (device_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT
                    r.*,
                    d.name AS device_name,
                    d.host AS device_host,
                    d.port AS device_port,
                    d.unit_id AS device_unit_id
                FROM registers r
                INNER JOIN devices d
                    ON d.id = r.device_id
                WHERE r.device_id = ?
                ORDER BY r.address ASC, r.id ASC
                """,
                (device_id,),
            ).fetchall()

        registers = [
            serialize_register(row)
            for row in rows
        ]

        return {
            "device": serialize_device(device),
            "count": len(registers),
            "registers": registers,
        }

    finally:
        connection.close()


@app.get("/api/v1/registers/{register_id}")
def get_register(register_id: int):
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT
                r.*,
                d.name AS device_name,
                d.host AS device_host,
                d.port AS device_port,
                d.unit_id AS device_unit_id
            FROM registers r
            INNER JOIN devices d
                ON d.id = r.device_id
            WHERE r.id = ?
            """,
            (register_id,),
        ).fetchone()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="Register not found.",
            )

        return serialize_register(row)

    finally:
        connection.close()


@app.post(
    "/api/v1/devices/{device_id}/registers",
    status_code=201,
)
def create_device_register(
    device_id: int,
    register: RegisterCreate,
):
    validate_register_definition(
        register.name,
        register.address,
        register.data_type,
        register.access,
    )

    connection = get_connection()

    try:
        device = connection.execute(
            """
            SELECT id
            FROM devices
            WHERE id = ?
            """,
            (device_id,),
        ).fetchone()

        if device is None:
            raise HTTPException(
                status_code=404,
                detail="Device not found.",
            )

        existing = connection.execute(
            """
            SELECT id
            FROM registers
            WHERE device_id = ?
            AND LOWER(name) = LOWER(?)
            """,
            (
                device_id,
                register.name.strip(),
            ),
        ).fetchone()

        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A register with this name "
                    "already exists for this device."
                ),
            )

        timestamp = utc_now()

        cursor = connection.execute(
            """
            INSERT INTO registers (
                device_id,
                name,
                address,
                data_type,
                access,
                unit,
                description,
                multiplier,
                offset,
                enabled,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                register.name.strip(),
                register.address,
                register.data_type,
                register.access,
                register.unit,
                register.description,
                register.multiplier,
                register.offset,
                int(register.enabled),
                timestamp,
                timestamp,
            ),
        )

        connection.commit()

        row = connection.execute(
            """
            SELECT
                r.*,
                d.name AS device_name,
                d.host AS device_host,
                d.port AS device_port,
                d.unit_id AS device_unit_id
            FROM registers r
            INNER JOIN devices d
                ON d.id = r.device_id
            WHERE r.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()

        return serialize_register(row)

    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Database constraint error: {exc}",
        )

    finally:
        connection.close()


@app.post(
    "/api/v1/registers",
    status_code=201,
)
def create_register(register: RegisterCreate):
    """
    Compatibility endpoint.

    Creates the register on the first enabled device.
    Prefer /devices/{device_id}/registers for multi-device use.
    """

    connection = get_connection()

    try:
        device = connection.execute(
            """
            SELECT id
            FROM devices
            WHERE enabled = 1
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()

        if device is None:
            raise HTTPException(
                status_code=400,
                detail="No enabled device exists.",
            )

        device_id = device["id"]

    finally:
        connection.close()

    return create_device_register(
        device_id,
        register,
    )


@app.put("/api/v1/registers/{register_id}")
def update_register(
    register_id: int,
    register: RegisterUpdate,
):
    connection = get_connection()

    try:
        existing = connection.execute(
            """
            SELECT *
            FROM registers
            WHERE id = ?
            """,
            (register_id,),
        ).fetchone()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Register not found.",
            )

        current = dict(existing)

        name = (
            register.name.strip()
            if register.name is not None
            else current["name"]
        )

        address = (
            register.address
            if register.address is not None
            else current["address"]
        )

        data_type = (
            register.data_type
            if register.data_type is not None
            else current["data_type"]
        )

        access = (
            register.access
            if register.access is not None
            else current["access"]
        )

        validate_register_definition(
            name,
            address,
            data_type,
            access,
        )

        unit = (
            register.unit
            if register.unit is not None
            else current["unit"]
        )

        description = (
            register.description
            if register.description is not None
            else current["description"]
        )

        multiplier = (
            register.multiplier
            if register.multiplier is not None
            else current["multiplier"]
        )

        offset = (
            register.offset
            if register.offset is not None
            else current["offset"]
        )

        enabled = (
            register.enabled
            if register.enabled is not None
            else bool(current["enabled"])
        )

        connection.execute(
            """
            UPDATE registers
            SET
                name = ?,
                address = ?,
                data_type = ?,
                access = ?,
                unit = ?,
                description = ?,
                multiplier = ?,
                offset = ?,
                enabled = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                name,
                address,
                data_type,
                access,
                unit,
                description,
                multiplier,
                offset,
                int(enabled),
                utc_now(),
                register_id,
            ),
        )

        connection.commit()

        row = connection.execute(
            """
            SELECT
                r.*,
                d.name AS device_name,
                d.host AS device_host,
                d.port AS device_port,
                d.unit_id AS device_unit_id
            FROM registers r
            INNER JOIN devices d
                ON d.id = r.device_id
            WHERE r.id = ?
            """,
            (register_id,),
        ).fetchone()

        return serialize_register(row)

    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Database constraint error: {exc}",
        )

    finally:
        connection.close()


@app.delete("/api/v1/registers/{register_id}")
def delete_register(register_id: int):
    connection = get_connection()

    try:
        existing = connection.execute(
            """
            SELECT id, name, device_id
            FROM registers
            WHERE id = ?
            """,
            (register_id,),
        ).fetchone()

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Register not found.",
            )

        connection.execute(
            """
            DELETE FROM registers
            WHERE id = ?
            """,
            (register_id,),
        )

        connection.commit()

        return {
            "success": True,
            "deleted_register_id": register_id,
            "deleted_register_name": existing["name"],
            "device_id": existing["device_id"],
        }

    finally:
        connection.close()


# ============================================================
# SNAPSHOT
# ============================================================

@app.post("/api/v1/scada/snapshot")
def save_snapshot(payload: SnapshotRequest):
    connection = get_connection()

    try:
        timestamp = utc_now()
        snapshot_values = []

        for item in payload.values:
            register = connection.execute(
                """
                SELECT
                    id,
                    device_id,
                    name,
                    address,
                    data_type,
                    unit,
                    multiplier,
                    offset
                FROM registers
                WHERE id = ?
                AND enabled = 1
                """,
                (item.register_id,),
            ).fetchone()

            if register is None:
                continue

            value_json = json.dumps(
                item.value,
                ensure_ascii=False,
            )

            connection.execute(
                """
                INSERT INTO register_values (
                    register_id,
                    value_json,
                    timestamp
                )
                VALUES (?, ?, ?)
                """,
                (
                    item.register_id,
                    value_json,
                    timestamp,
                ),
            )

            snapshot_values.append(
                {
                    "register_id": item.register_id,
                    "device_id": register["device_id"],
                    "name": register["name"],
                    "address": register["address"],
                    "data_type": register["data_type"],
                    "unit": register["unit"],
                    "multiplier": register["multiplier"],
                    "offset": register["offset"],
                    "value": item.value,
                }
            )

        connection.execute(
            """
            INSERT INTO scada_snapshots (
                timestamp,
                register_count,
                values_json
            )
            VALUES (?, ?, ?)
            """,
            (
                timestamp,
                len(snapshot_values),
                json.dumps(
                    snapshot_values,
                    ensure_ascii=False,
                ),
            ),
        )

        connection.execute(
            """
            UPDATE scada_status
            SET
                status = 'online',
                last_seen = ?,
                last_success = ?,
                last_error = NULL,
                register_count = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (
                timestamp,
                timestamp,
                len(snapshot_values),
                timestamp,
            ),
        )

        connection.commit()

        return {
            "success": True,
            "timestamp": timestamp,
            "register_count": len(snapshot_values),
        }

    except Exception as exc:
        connection.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Could not save SCADA snapshot: {exc}",
        )

    finally:
        connection.close()


# ============================================================
# LATEST VALUES
# ============================================================

@app.get("/api/v1/latest")
def get_latest(
    device_id: int | None = Query(
        default=None,
    ),
):
    connection = get_connection()

    try:
        query = """
            SELECT
                r.*,
                d.name AS device_name,
                d.host AS device_host,
                d.port AS device_port,
                d.unit_id AS device_unit_id
            FROM registers r
            INNER JOIN devices d
                ON d.id = r.device_id
            WHERE r.enabled = 1
        """

        params = []

        if device_id is not None:
            query += " AND r.device_id = ?"
            params.append(device_id)

        query += """
            ORDER BY
                r.device_id ASC,
                r.address ASC,
                r.id ASC
        """

        registers = connection.execute(
            query,
            params,
        ).fetchall()

        values = []

        for register in registers:
            value_row = connection.execute(
                """
                SELECT
                    value_json,
                    timestamp
                FROM register_values
                WHERE register_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (register["id"],),
            ).fetchone()

            value = None
            timestamp = None

            if value_row:
                value = load_json_value(
                    value_row["value_json"]
                )
                timestamp = value_row["timestamp"]

            values.append(
                {
                    "register_id": register["id"],
                    "device_id": register["device_id"],
                    "device_name": register["device_name"],
                    "name": register["name"],
                    "address": register["address"],
                    "data_type": register["data_type"],
                    "access": register["access"],
                    "unit": register["unit"],
                    "multiplier": register["multiplier"],
                    "offset": register["offset"],
                    "value": value,
                    "timestamp": timestamp,
                }
            )

        return {
            "count": len(values),
            "values": values,
        }

    finally:
        connection.close()


# ============================================================
# HISTORY
# ============================================================

@app.get("/api/v1/history")
def get_history(
    limit: int = Query(
        default=100,
        ge=1,
        le=5000,
    ),
    register_id: int | None = Query(
        default=None,
    ),
    device_id: int | None = Query(
        default=None,
    ),
):
    connection = get_connection()

    try:
        query = """
            SELECT
                rv.id,
                rv.register_id,
                r.device_id,
                d.name AS device_name,
                r.name,
                r.address,
                r.data_type,
                r.unit,
                r.multiplier,
                r.offset,
                rv.value_json,
                rv.timestamp
            FROM register_values rv
            INNER JOIN registers r
                ON r.id = rv.register_id
            INNER JOIN devices d
                ON d.id = r.device_id
            WHERE 1 = 1
        """

        params = []

        if register_id is not None:
            query += " AND rv.register_id = ?"
            params.append(register_id)

        if device_id is not None:
            query += " AND r.device_id = ?"
            params.append(device_id)

        query += """
            ORDER BY rv.id DESC
            LIMIT ?
        """

        params.append(limit)

        rows = connection.execute(
            query,
            params,
        ).fetchall()

        history = []

        for row in rows:
            history.append(
                {
                    "id": row["id"],
                    "register_id": row["register_id"],
                    "device_id": row["device_id"],
                    "device_name": row["device_name"],
                    "name": row["name"],
                    "address": row["address"],
                    "data_type": row["data_type"],
                    "unit": row["unit"],
                    "multiplier": row["multiplier"],
                    "offset": row["offset"],
                    "value": load_json_value(
                        row["value_json"]
                    ),
                    "timestamp": row["timestamp"],
                }
            )

        return {
            "count": len(history),
            "history": history,
        }

    finally:
        connection.close()


# ============================================================
# SCADA SNAPSHOT HISTORY
# ============================================================

@app.get("/api/v1/scada/snapshots")
def get_snapshots(
    limit: int = Query(
        default=50,
        ge=1,
        le=1000,
    ),
):
    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT
                id,
                timestamp,
                register_count,
                values_json
            FROM scada_snapshots
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        snapshots = []

        for row in rows:
            snapshots.append(
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "register_count": row["register_count"],
                    "values": load_json_value(
                        row["values_json"]
                    ),
                }
            )

        return {
            "count": len(snapshots),
            "snapshots": snapshots,
        }

    finally:
        connection.close()


# ============================================================
# COMMAND QUEUE
# ============================================================

@app.post(
    "/api/v1/commands",
    status_code=201,
)
def create_command(command: CommandCreate):
    connection = get_connection()

    try:
        register = connection.execute(
            """
            SELECT
                r.*,
                d.name AS device_name
            FROM registers r
            INNER JOIN devices d
                ON d.id = r.device_id
            WHERE r.id = ?
            AND r.enabled = 1
            """,
            (command.register_id,),
        ).fetchone()

        if register is None:
            raise HTTPException(
                status_code=404,
                detail="Register not found.",
            )

        if register["access"] not in {
            "write",
            "read_write",
        }:
            raise HTTPException(
                status_code=403,
                detail=(
                    "This register does not "
                    "allow write operations."
                ),
            )

        timestamp = utc_now()

        cursor = connection.execute(
            """
            INSERT INTO commands (
                register_id,
                value_json,
                status,
                created_at
            )
            VALUES (?, ?, 'pending', ?)
            """,
            (
                command.register_id,
                json.dumps(
                    command.value,
                    ensure_ascii=False,
                ),
                timestamp,
            ),
        )

        connection.commit()

        command_id = cursor.lastrowid

        row = connection.execute(
            """
            SELECT
                c.id,
                c.register_id,
                r.device_id,
                d.name AS device_name,
                r.name,
                r.address,
                r.data_type,
                r.unit,
                r.multiplier,
                r.offset,
                c.value_json,
                c.status,
                c.error,
                c.created_at,
                c.processed_at
            FROM commands c
            INNER JOIN registers r
                ON r.id = c.register_id
            INNER JOIN devices d
                ON d.id = r.device_id
            WHERE c.id = ?
            """,
            (command_id,),
        ).fetchone()

        return {
            "id": row["id"],
            "register_id": row["register_id"],
            "device_id": row["device_id"],
            "device_name": row["device_name"],
            "name": row["name"],
            "address": row["address"],
            "data_type": row["data_type"],
            "unit": row["unit"],
            "multiplier": row["multiplier"],
            "offset": row["offset"],
            "value": load_json_value(
                row["value_json"]
            ),
            "status": row["status"],
            "error": row["error"],
            "created_at": row["created_at"],
            "processed_at": row["processed_at"],
        }

    finally:
        connection.close()


@app.get("/api/v1/commands/pending")
def get_pending_commands():
    connection = get_connection()

    try:
        rows = connection.execute(
            """
            SELECT
                c.id,
                c.register_id,
                r.device_id,
                d.name AS device_name,
                r.name,
                r.address,
                r.data_type,
                r.unit,
                r.multiplier,
                r.offset,
                c.value_json,
                c.status,
                c.error,
                c.created_at,
                c.processed_at
            FROM commands c
            INNER JOIN registers r
                ON r.id = c.register_id
            INNER JOIN devices d
                ON d.id = r.device_id
            WHERE c.status = 'pending'
            ORDER BY c.id ASC
            """
        ).fetchall()

        commands = []

        for row in rows:
            commands.append(
                {
                    "id": row["id"],
                    "register_id": row["register_id"],
                    "device_id": row["device_id"],
                    "device_name": row["device_name"],
                    "name": row["name"],
                    "address": row["address"],
                    "data_type": row["data_type"],
                    "unit": row["unit"],
                    "multiplier": row["multiplier"],
                    "offset": row["offset"],
                    "value": load_json_value(
                        row["value_json"]
                    ),
                    "status": row["status"],
                    "error": row["error"],
                    "created_at": row["created_at"],
                    "processed_at": row["processed_at"],
                }
            )

        return {
            "count": len(commands),
            "commands": commands,
        }

    finally:
        connection.close()


@app.post("/api/v1/commands/{command_id}/result")
def update_command_result(
    command_id: int,
    result: CommandResult,
):
    if result.status not in {
        "completed",
        "failed",
    }:
        raise HTTPException(
            status_code=400,
            detail=(
                "Command status must be "
                "'completed' or 'failed'."
            ),
        )

    connection = get_connection()

    try:
        command = connection.execute(
            """
            SELECT id
            FROM commands
            WHERE id = ?
            """,
            (command_id,),
        ).fetchone()

        if command is None:
            raise HTTPException(
                status_code=404,
                detail="Command not found.",
            )

        connection.execute(
            """
            UPDATE commands
            SET
                status = ?,
                error = ?,
                processed_at = ?
            WHERE id = ?
            """,
            (
                result.status,
                result.error,
                utc_now(),
                command_id,
            ),
        )

        connection.commit()

        return {
            "success": True,
            "command_id": command_id,
            "status": result.status,
        }

    finally:
        connection.close()


@app.get("/api/v1/commands")
def get_commands(
    limit: int = Query(
        default=100,
        ge=1,
        le=5000,
    ),
    status: str | None = Query(
        default=None,
    ),
    device_id: int | None = Query(
        default=None,
    ),
):
    connection = get_connection()

    try:
        query = """
            SELECT
                c.id,
                c.register_id,
                r.device_id,
                d.name AS device_name,
                r.name,
                r.address,
                r.data_type,
                r.unit,
                r.multiplier,
                r.offset,
                c.value_json,
                c.status,
                c.error,
                c.created_at,
                c.processed_at
            FROM commands c
            INNER JOIN registers r
                ON r.id = c.register_id
            INNER JOIN devices d
                ON d.id = r.device_id
            WHERE 1 = 1
        """

        params = []

        if status:
            query += " AND c.status = ?"
            params.append(status)

        if device_id is not None:
            query += " AND r.device_id = ?"
            params.append(device_id)

        query += """
            ORDER BY c.id DESC
            LIMIT ?
        """

        params.append(limit)

        rows = connection.execute(
            query,
            params,
        ).fetchall()

        commands = []

        for row in rows:
            commands.append(
                {
                    "id": row["id"],
                    "register_id": row["register_id"],
                    "device_id": row["device_id"],
                    "device_name": row["device_name"],
                    "name": row["name"],
                    "address": row["address"],
                    "data_type": row["data_type"],
                    "unit": row["unit"],
                    "multiplier": row["multiplier"],
                    "offset": row["offset"],
                    "value": load_json_value(
                        row["value_json"]
                    ),
                    "status": row["status"],
                    "error": row["error"],
                    "created_at": row["created_at"],
                    "processed_at": row["processed_at"],
                }
            )

        return {
            "count": len(commands),
            "commands": commands,
        }

    finally:
        connection.close()


# ============================================================
# SCADA STATUS
# ============================================================

@app.post("/api/v1/scada/heartbeat")
def scada_heartbeat(payload: ScadaHeartbeat):
    connection = get_connection()

    try:
        timestamp = utc_now()

        status = (
            "online"
            if payload.status == "online"
            else "error"
        )

        connection.execute(
            """
            UPDATE scada_status
            SET
                status = ?,
                last_seen = ?,
                last_success = CASE
                    WHEN ? = 'online'
                    THEN ?
                    ELSE last_success
                END,
                last_error = ?,
                register_count = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (
                status,
                timestamp,
                status,
                timestamp,
                payload.error,
                payload.register_count,
                timestamp,
            ),
        )

        connection.commit()

        return {
            "success": True,
            "status": status,
            "timestamp": timestamp,
        }

    finally:
        connection.close()


@app.get("/api/v1/scada/status")
def get_scada_status():
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT *
            FROM scada_status
            WHERE id = 1
            """
        ).fetchone()

        if row is None:
            return {
                "status": "offline",
                "online": False,
                "last_seen": None,
                "last_success": None,
                "last_error": None,
                "register_count": 0,
            }

        result = dict(row)

        last_seen = result["last_seen"]
        online = False

        if last_seen:
            try:
                last_seen_dt = datetime.fromisoformat(
                    last_seen
                )

                elapsed = (
                    datetime.now(timezone.utc)
                    - last_seen_dt
                ).total_seconds()

                online = (
                    elapsed <= SCADA_TIMEOUT_SECONDS
                )

            except ValueError:
                online = False

        result["online"] = online

        if not online:
            result["status"] = "offline"

        return result

    finally:
        connection.close()


# ============================================================
# SYSTEM STATUS
# ============================================================

@app.get("/api/v1/status")
def get_status():
    connection = get_connection()

    try:
        device_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM devices
            WHERE enabled = 1
            """
        ).fetchone()[0]

        register_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM registers
            WHERE enabled = 1
            """
        ).fetchone()[0]

        value_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM register_values
            """
        ).fetchone()[0]

        snapshot_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM scada_snapshots
            """
        ).fetchone()[0]

        pending_commands = connection.execute(
            """
            SELECT COUNT(*)
            FROM commands
            WHERE status = 'pending'
            """
        ).fetchone()[0]

        scada = connection.execute(
            """
            SELECT *
            FROM scada_status
            WHERE id = 1
            """
        ).fetchone()

        scada_online = False

        if scada and scada["last_seen"]:
            try:
                last_seen = datetime.fromisoformat(
                    scada["last_seen"]
                )

                elapsed = (
                    datetime.now(timezone.utc)
                    - last_seen
                ).total_seconds()

                scada_online = (
                    elapsed <= SCADA_TIMEOUT_SECONDS
                )

            except ValueError:
                scada_online = False

        return {
            "api": {
                "status": "online",
                "version": "6.0.0",
            },
            "database": {
                "status": "connected",
                "device_count": device_count,
                "register_count": register_count,
                "value_count": value_count,
                "snapshot_count": snapshot_count,
                "pending_commands": pending_commands,
            },
            "scada": {
                "status": (
                    "online"
                    if scada_online
                    else "offline"
                ),
                "last_seen": (
                    scada["last_seen"]
                    if scada
                    else None
                ),
                "last_success": (
                    scada["last_success"]
                    if scada
                    else None
                ),
                "last_error": (
                    scada["last_error"]
                    if scada
                    else None
                ),
            },
        }

    finally:
        connection.close()


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/v1/health")
def health():
    try:
        connection = get_connection()

        connection.execute(
            "SELECT 1"
        ).fetchone()

        connection.close()

        scada = get_scada_status()

        return {
            "status": (
                "healthy"
                if scada["online"]
                else "degraded"
            ),
            "services": {
                "api": "online",
                "database": "online",
                "scada": (
                    "online"
                    if scada["online"]
                    else "offline"
                ),
            },
            "timestamp": utc_now(),
        }

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unhealthy",
                "error": str(exc),
            },
        )


# ============================================================
# DATABASE INFO
# ============================================================

@app.get("/api/v1/database")
def database_info():
    connection = get_connection()

    try:
        device_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM devices
            """
        ).fetchone()[0]

        enabled_device_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM devices
            WHERE enabled = 1
            """
        ).fetchone()[0]

        register_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM registers
            """
        ).fetchone()[0]

        enabled_register_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM registers
            WHERE enabled = 1
            """
        ).fetchone()[0]

        value_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM register_values
            """
        ).fetchone()[0]

        snapshot_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM scada_snapshots
            """
        ).fetchone()[0]

        command_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM commands
            """
        ).fetchone()[0]

        return {
            "database": DATABASE_PATH.name,
            "path": str(DATABASE_PATH),
            "devices": device_count,
            "enabled_devices": enabled_device_count,
            "registers": register_count,
            "enabled_registers": enabled_register_count,
            "stored_values": value_count,
            "snapshots": snapshot_count,
            "commands": command_count,
        }

    finally:
        connection.close()


# ============================================================
# API INFORMATION
# ============================================================

@app.get("/api/v1/info")
def api_info():
    return {
        "application": "ScadaWatt",
        "api_version": "6.0.0",
        "architecture": {
            "api": "FastAPI",
            "database": "SQLite",
            "communication": "REST / HTTP",
            "plc_protocol": "Modbus TCP",
            "architecture_type": "multi-device",
        },
        "supported_data_types": SUPPORTED_DATA_TYPES,
        "supported_access_modes": sorted(
            SUPPORTED_ACCESS
        ),
        "register_scaling": {
            "formula": (
                "engineering_value = "
                "raw_value * multiplier + offset"
            ),
            "multiplier_default": 1.0,
            "offset_default": 0.0,
        },
        "endpoints": {
            "devices": "/api/v1/devices",
            "device": "/api/v1/devices/{device_id}",
            "device_registers": (
                "/api/v1/devices/{device_id}/registers"
            ),
            "registers": "/api/v1/registers",
            "snapshot": "/api/v1/scada/snapshot",
            "latest": "/api/v1/latest",
            "history": "/api/v1/history",
            "commands": "/api/v1/commands",
            "pending_commands": (
                "/api/v1/commands/pending"
            ),
            "command_result": (
                "/api/v1/commands/{command_id}/result"
            ),
            "scada_status": "/api/v1/scada/status",
            "status": "/api/v1/status",
            "health": "/api/v1/health",
            "database": "/api/v1/database",
        },
    }