import json
import getpass
import sys
import sqlite3
import ipaddress
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jwt
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.openapi.utils import get_openapi
from pwdlib import PasswordHash
from pydantic import BaseModel, Field


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "scada.db"

SCADA_TIMEOUT_SECONDS = 5

# ============================================================
# SECURITY CONFIGURATION
# ============================================================

API_VERSION = "6.1.0"

JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY") or secrets.token_urlsafe(64)
ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# Comma-separated IPs/CIDRs. Example:
# 127.0.0.1,::1,192.168.1.0/24
ALLOWED_IPS_RAW = os.getenv(
    "ALLOWED_IPS",
    "127.0.0.1,::1",
)
ALLOWED_IP_NETWORKS = []
for _item in ALLOWED_IPS_RAW.split(","):
    _item = _item.strip()
    if not _item:
        continue
    try:
        ALLOWED_IP_NETWORKS.append(
            ipaddress.ip_network(_item, strict=False)
        )
    except ValueError as exc:
        raise RuntimeError(
            f"Invalid ALLOWED_IPS entry: {_item}"
        ) from exc

# Internal SCADA/slave service token.
# Keep this outside source control.
SCADA_SERVICE_TOKEN = os.getenv("SCADA_SERVICE_TOKEN")

# CORS is disabled by default. Example:
# CORS_ORIGINS=http://localhost:3000,http://192.168.1.20:3000
CORS_ORIGINS = [
    item.strip()
    for item in os.getenv("CORS_ORIGINS", "").split(",")
    if item.strip()
]

# Hosts accepted by the API.
ALLOWED_HOSTS = [
    item.strip()
    for item in os.getenv(
        "ALLOWED_HOSTS",
        "127.0.0.1,localhost",
    ).split(",")
    if item.strip()
]

pwd_hasher = PasswordHash.recommended()

# Simple in-process login throttling.
# For multi-worker/production deployments, enforce rate limiting
# at the reverse proxy/API gateway as well.
LOGIN_WINDOW_SECONDS = 60
LOGIN_MAX_ATTEMPTS = 5
_login_attempts: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _ip_allowed(client_ip: str) -> bool:
    if client_ip == "unknown":
        return False

    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return False

    return any(address in network for network in ALLOWED_IP_NETWORKS)


def _login_rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    attempts = _login_attempts.setdefault(client_ip, [])
    attempts[:] = [
        timestamp
        for timestamp in attempts
        if now - timestamp < LOGIN_WINDOW_SECONDS
    ]

    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        return True

    attempts.append(now)
    return False


def _create_access_token(username: str, role: str) -> str:
    now = int(time.time())
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "type": "access",
    }
    return jwt.encode(
        payload,
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def _decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if payload.get("type") != "access" or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


def _extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    scheme, _, credentials = authorization.partition(" ")

    if scheme.lower() != "bearer" or not credentials:
        return None

    return credentials.strip()


def _role_allowed(role: str, method: str, path: str) -> bool:
    # User administration is administrator-only, including GET.
    if path.startswith("/api/v1/auth/users"):
        return role == "admin"

    # Users can change their own password.
    if path == "/api/v1/auth/change-password":
        return role in {"viewer", "operator", "admin"}

    # Register/device configuration is administrator-only.
    if (
        path.startswith("/api/v1/devices")
        or path.startswith("/api/v1/registers")
    ):
        return role == "admin"

    # Command creation is allowed to operators and administrators.
    if path == "/api/v1/commands" and method == "POST":
        return role in {"operator", "admin"}

    # Read-only endpoints.
    if method == "GET":
        return role in {"viewer", "operator", "admin"}

    return role in {"operator", "admin"}


class SecurityMiddleware:
    """
    Authentication/authorization middleware.

    Public:
      - /api/v1/auth/login
      - /api/v1/health

    Internal SCADA service-token endpoints:
      - /api/v1/scada/*
      - /api/v1/commands/pending
      - /api/v1/commands/{id}/result

    All other /api/v1 endpoints require a JWT.
    """

    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.application(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        path = request.url.path
        method = request.method.upper()

        # API documentation is disabled for non-local clients.
        if path in {"/docs", "/redoc", "/openapi.json"}:
            if not _ip_allowed(_client_ip(request)):
                response = JSONResponse(
                    status_code=404,
                    content={"detail": "Not found."},
                )
                await response(scope, receive, send)
                return
            await self.application(scope, receive, send)
            return

        # IP allowlist applies to API traffic.
        if path.startswith("/api/v1/") and not _ip_allowed(
            _client_ip(request)
        ):
            response = JSONResponse(
                status_code=403,
                content={"detail": "Client IP is not allowed."},
            )
            await response(scope, receive, send)
            return

        # Public endpoints.
        if path in {
            "/api/v1/auth/login",
            "/api/v1/health",
        }:
            await self.application(scope, receive, send)
            return

        # Internal SCADA/slave communication uses a separate
        # long-lived service token, not a human JWT.
        service_endpoint = (
            path.startswith("/api/v1/scada/")
            or path == "/api/v1/commands/pending"
            or (
                path.startswith("/api/v1/commands/")
                and path.endswith("/result")
            )
        )

        if service_endpoint:
            token = _extract_bearer_token(request)

            if (
                not SCADA_SERVICE_TOKEN
                or not token
                or not secrets.compare_digest(
                    token,
                    SCADA_SERVICE_TOKEN,
                )
            ):
                response = JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid SCADA service token."},
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return

            await self.application(scope, receive, send)
            return

        token = _extract_bearer_token(request)

        if not token:
            response = JSONResponse(
                status_code=401,
                content={"detail": "Authentication required."},
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        payload = _decode_access_token(token)

        # Verify the user still exists and is active.
        connection = get_connection()
        try:
            user = connection.execute(
                """
                SELECT username, role, enabled
                FROM users
                WHERE username = ?
                """,
                (payload["sub"],),
            ).fetchone()
        finally:
            connection.close()

        if user is None or not user["enabled"]:
            response = JSONResponse(
                status_code=401,
                content={"detail": "User is disabled or does not exist."},
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        if not _role_allowed(user["role"], method, path):
            response = JSONResponse(
                status_code=403,
                content={"detail": "Insufficient permissions."},
            )
            await response(scope, receive, send)
            return

        # Expose authenticated user to endpoints through request.state.
        scope.setdefault("state", {})
        scope["state"]["user"] = {
            "username": user["username"],
            "role": user["role"],
        }

        await self.application(scope, receive, send)


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
        "ScadaWatt REST API.\n\n"
        "### Kullanıcı girişi\n"
        "1. `POST /api/v1/auth/login` ile kullanıcı adı ve şifrenizle giriş yapın.\n"
        "2. Dönen `access_token` değerini Swagger üzerindeki **Authorize** butonuna girin.\n"
        "3. Yetkinize göre cihaz, register ve komut işlemlerini kullanın.\n\n"
        "### Yetkilendirme\n"
        "Normal API kullanımı JWT tabanlı kullanıcı oturumu ile korunur. "
        "SCADA servislerine ait dahili kimlik doğrulama bilgileri Swagger arayüzünde gösterilmez."
    ),
    version=API_VERSION,
    swagger_ui_parameters={
        "persistAuthorization": True,
        "displayRequestDuration": True,
        "docExpansion": "list",
        "filter": True,
    },
)


# HTTP security middleware.
if ALLOWED_HOSTS:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=ALLOWED_HOSTS,
    )

if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

app.add_middleware(SecurityMiddleware)


# OpenAPI security definitions for Swagger UI.
# The runtime authentication is enforced by SecurityMiddleware above;
# these definitions make the same rules visible and usable in /docs.
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schema["components"]["securitySchemes"]["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": (
            "Human user JWT. Obtain it from POST /api/v1/auth/login "
            "and enter only the token value here."
        ),
    }
    # Internal SCADA/slave service endpoints are intentionally kept out of
    # the public Swagger document. They are still available at runtime and
    # are protected by SCADA_SERVICE_TOKEN in SecurityMiddleware.
    internal_paths = {
        path
        for path in list(schema.get("paths", {}))
        if (
            path.startswith("/api/v1/scada/")
            or path == "/api/v1/commands/pending"
            or (
                path.startswith("/api/v1/commands/")
                and path.endswith("/result")
            )
        )
    }
    for path in internal_paths:
        schema["paths"].pop(path, None)

    public_paths = {
        "/api/v1/auth/login",
        "/api/v1/health",
    }

    # Keep the Swagger UI focused on the human-user authentication flow.
    for path, path_item in schema.get("paths", {}).items():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue

            if path in public_paths:
                operation.pop("security", None)
            elif path.startswith("/api/v1/"):
                operation["security"] = [{"BearerAuth": []}]

        if path.startswith("/api/v1/auth/"):
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation.setdefault("tags", ["🔐 Kimlik Doğrulama"])
        elif path.startswith("/api/v1/devices"):
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation.setdefault("tags", ["Cihazlar"])
        elif path.startswith("/api/v1/registers"):
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation.setdefault("tags", ["Registerlar"])
        elif path.startswith("/api/v1/commands"):
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation.setdefault("tags", ["Komutlar"])
        elif path.startswith("/api/v1/health"):
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation.setdefault("tags", ["Sistem"])

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


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
        # Security tables / migrations
        # ----------------------------------------------------

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'viewer',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK(role IN ('viewer', 'operator', 'admin'))
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                action TEXT NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                client_ip TEXT,
                detail TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

        if not column_exists(
            connection,
            "commands",
            "created_by",
        ):
            connection.execute(
                """
                ALTER TABLE commands
                ADD COLUMN created_by TEXT
                """
            )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at
            ON audit_logs(created_at)
            """
        )

        # Create the initial administrator only once.
        admin_exists = connection.execute(
            """
            SELECT id
            FROM users
            WHERE username = ?
            """,
            (ADMIN_USERNAME,),
        ).fetchone()

        if admin_exists is None:
            initial_password = (
                ADMIN_PASSWORD
                or secrets.token_urlsafe(18)
            )

            connection.execute(
                """
                INSERT INTO users (
                    username,
                    password_hash,
                    role,
                    enabled,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, 'admin', 1, ?, ?)
                """,
                (
                    ADMIN_USERNAME,
                    pwd_hasher.hash(initial_password),
                    utc_now(),
                    utc_now(),
                ),
            )

            if ADMIN_PASSWORD:
                print(
                    f"Initial admin user created: {ADMIN_USERNAME}"
                )
            else:
                print(
                    "WARNING: ADMIN_PASSWORD was not set."
                )
                print(
                    "Initial admin password (save it and change it): "
                    f"{initial_password}"
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


# ============================================================
# COMMAND-LINE ADMIN PASSWORD RESET
# ============================================================

def reset_admin_password_cli() -> int:
    """Reset the configured admin user's password without deleting data."""
    username = ADMIN_USERNAME.strip()

    if not username:
        print("ERROR: ADMIN_USERNAME is empty.")
        return 1

    print()
    print("=== ScadaWatt Admin Password Reset ===")
    print(f"Admin username: {username}")
    print("This changes only the user's password.")
    print("SCADA devices, registers, values and history are not deleted.")
    print()

    while True:
        new_password = getpass.getpass("New password (minimum 8 characters): ")
        confirm_password = getpass.getpass("Confirm new password: ")

        if len(new_password) < 8:
            print("ERROR: Password must be at least 8 characters.")
            print()
            continue

        if new_password != confirm_password:
            print("ERROR: Passwords do not match.")
            print()
            continue

        break

    connection = get_connection()
    try:
        user = connection.execute(
            """
            SELECT id, username, role
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()

        if user is None:
            print(f"ERROR: Admin user '{username}' was not found.")
            print("Check ADMIN_USERNAME or create the admin user first.")
            return 1

        if user["role"] != "admin":
            print(
                f"ERROR: User '{username}' exists but its role is "
                f"'{user['role']}', not 'admin'."
            )
            return 1

        connection.execute(
            """
            UPDATE users
            SET password_hash = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                pwd_hasher.hash(new_password),
                utc_now(),
                user["id"],
            ),
        )
        connection.commit()
    finally:
        connection.close()

    print()
    print(f"SUCCESS: Password changed for admin user '{username}'.")
    print("You can now log in from Swagger:")
    print("http://127.0.0.1:8000/docs")
    print()
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--reset-admin-password":
        raise SystemExit(reset_admin_password_cli())


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



# ============================================================
# AUTHENTICATION / USER MANAGEMENT
# ============================================================

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=12, max_length=200)
    role: str = Field(default="viewer")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


def _get_authenticated_user_from_request(
    request: Request,
) -> dict:
    user = getattr(request.state, "user", None)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
        )

    return user


def _audit(
    username: str | None,
    action: str,
    method: str,
    path: str,
    client_ip: str | None,
    detail: str | None = None,
) -> None:
    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO audit_logs (
                username,
                action,
                method,
                path,
                client_ip,
                detail,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                action,
                method,
                path,
                client_ip,
                detail,
                utc_now(),
            ),
        )
        connection.commit()
    finally:
        connection.close()


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest, request: Request):
    client_ip = _client_ip(request)

    if _login_rate_limited(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Try again later.",
        )

    connection = get_connection()
    try:
        user = connection.execute(
            """
            SELECT username, password_hash, role, enabled
            FROM users
            WHERE username = ?
            """,
            (payload.username.strip(),),
        ).fetchone()
    finally:
        connection.close()

    if (
        user is None
        or not user["enabled"]
        or not pwd_hasher.verify(
            payload.password,
            user["password_hash"],
        )
    ):
        _audit(
            payload.username.strip(),
            "login_failed",
            "POST",
            "/api/v1/auth/login",
            client_ip,
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = _create_access_token(
        user["username"],
        user["role"],
    )

    _audit(
        user["username"],
        "login_success",
        "POST",
        "/api/v1/auth/login",
        client_ip,
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": {
            "username": user["username"],
            "role": user["role"],
        },
    }


@app.get("/api/v1/auth/me")
def me(request: Request):
    return _get_authenticated_user_from_request(request)


@app.post("/api/v1/auth/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
):
    current_user = _get_authenticated_user_from_request(request)

    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=400,
            detail="New password must be different.",
        )

    connection = get_connection()
    try:
        user = connection.execute(
            """
            SELECT id, password_hash
            FROM users
            WHERE username = ?
            """,
            (current_user["username"],),
        ).fetchone()

        if user is None:
            raise HTTPException(
                status_code=404,
                detail="User not found.",
            )

        if not pwd_hasher.verify(
            payload.current_password,
            user["password_hash"],
        ):
            raise HTTPException(
                status_code=401,
                detail="Current password is incorrect.",
            )

        connection.execute(
            """
            UPDATE users
            SET password_hash = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                pwd_hasher.hash(payload.new_password),
                utc_now(),
                user["id"],
            ),
        )
        connection.commit()
    finally:
        connection.close()

    _audit(
        current_user["username"],
        "password_changed",
        "POST",
        "/api/v1/auth/change-password",
        _client_ip(request),
    )

    return {"success": True}


@app.get("/api/v1/auth/users")
def list_users(request: Request):
    _get_authenticated_user_from_request(request)

    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT id, username, role, enabled, created_at, updated_at
            FROM users
            ORDER BY id ASC
            """
        ).fetchall()
        return {
            "count": len(rows),
            "users": [dict(row) for row in rows],
        }
    finally:
        connection.close()


@app.post("/api/v1/auth/users", status_code=201)
def create_user(
    payload: CreateUserRequest,
    request: Request,
):
    current_user = _get_authenticated_user_from_request(request)

    if payload.role not in {"viewer", "operator", "admin"}:
        raise HTTPException(
            status_code=400,
            detail="Role must be viewer, operator or admin.",
        )

    username = payload.username.strip()

    if not username:
        raise HTTPException(
            status_code=400,
            detail="Username cannot be empty.",
        )

    connection = get_connection()
    try:
        try:
            connection.execute(
                """
                INSERT INTO users (
                    username,
                    password_hash,
                    role,
                    enabled,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (
                    username,
                    pwd_hasher.hash(payload.password),
                    payload.role,
                    utc_now(),
                    utc_now(),
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="Username already exists.",
            ) from exc
    finally:
        connection.close()

    _audit(
        current_user["username"],
        "user_created",
        "POST",
        "/api/v1/auth/users",
        _client_ip(request),
        f"created={username}, role={payload.role}",
    )

    return {
        "success": True,
        "username": username,
        "role": payload.role,
    }


@app.get("/Emirhan")
def root():
    return {
        "application": "ScadaWatt REST API",
        "version": API_VERSION,
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
def create_command(command: CommandCreate, request: Request):
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
        authenticated_user = getattr(
            request.state,
            "user",
            None,
        )
        created_by = (
            authenticated_user["username"]
            if authenticated_user
            else None
        )

        cursor = connection.execute(
            """
            INSERT INTO commands (
                register_id,
                value_json,
                status,
                created_at,
                created_by
            )
            VALUES (?, ?, 'pending', ?, ?)
            """,
            (
                command.register_id,
                json.dumps(
                    command.value,
                    ensure_ascii=False,
                ),
                timestamp,
                created_by,
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
                c.processed_at,
                c.created_by
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
                c.processed_at,
                c.created_by
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
                    "created_by": row["created_by"],
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
                c.processed_at,
                c.created_by
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
                    "created_by": row["created_by"],
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
                "version": API_VERSION,
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
        "api_version": API_VERSION,
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
            "login": "/api/v1/auth/login",
            "me": "/api/v1/auth/me",
            "change_password": "/api/v1/auth/change-password",
            "users": "/api/v1/auth/users",
        },
        "security": {
            "authentication": "JWT Bearer",
            "roles": ["viewer", "operator", "admin"],
            "scada_authentication": "Bearer service token",
            "ip_allowlist": True,
            "https_required_in_production": True,
        },
    }