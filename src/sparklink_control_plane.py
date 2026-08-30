#!/usr/bin/env python3
"""Small, auditable SparkLink MVP control plane.

The module intentionally uses only Python's standard library. Runtime secrets,
subscription URIs and the SQLite database belong outside Git.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit
from wsgiref.simple_server import WSGIRequestHandler, make_server
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from src.sparklink_subscription_naming import alias_from_uri, replace_uri_alias
except ModuleNotFoundError:  # direct `python src/sparklink_control_plane.py` execution
    from sparklink_subscription_naming import alias_from_uri, replace_uri_alias


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "web"
PLAN_ORDER = {"Free": 0, "Basic": 1, "Plus": 2}
POOL_NAMES = ("STANDARD", "ADVANCED", "PREMIUM")
PLAN_POOL_ENTITLEMENTS = {
    "Free": frozenset(),
    "Basic": frozenset({"STANDARD", "ADVANCED"}),
    "Plus": frozenset({"STANDARD", "ADVANCED", "PREMIUM"}),
}
ACCESS_DECISIONS = {"allow", "deny"}
ALLOCATION_ROLES = {"default", "primary", "available", "backup", "reserved", "deny"}
MIGRATION_STATES = {
    "issued", "delivered", "fetched", "managed_traffic_observed",
    "confirmed", "legacy_retirement_ready", "retired",
}
DEFAULT_COVERAGE_MAX_AGE_SECONDS = 900
CUSTOMER_CYCLE_TIMEZONE = "Asia/Shanghai"
CUSTOMER_CYCLE_POLICY_ID = "customer-monthly-15th-asia-shanghai-v1"
try:
    CUSTOMER_CYCLE_ZONE = ZoneInfo(CUSTOMER_CYCLE_TIMEZONE)
except ZoneInfoNotFoundError:
    # Asia/Shanghai has no DST transition; keep the canonical name in metadata.
    CUSTOMER_CYCLE_ZONE = timezone(timedelta(hours=8), name=CUSTOMER_CYCLE_TIMEZONE)
CUSTOMER_CYCLE_BASELINE = datetime(2026, 9, 15, tzinfo=CUSTOMER_CYCLE_ZONE)


class ClosingConnection(sqlite3.Connection):
    """Make ``with connect()`` close as well as commit/rollback."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS resource_pools (
    pool_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL,
    qualification TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS infrastructure_resources (
    resource_id TEXT PRIMARY KEY,
    provider_name TEXT NOT NULL,
    provider_instance_id TEXT NOT NULL UNIQUE,
    node_id TEXT REFERENCES nodes(node_id),
    location TEXT NOT NULL,
    network_label TEXT NOT NULL,
    asn TEXT,
    public_ipv4 TEXT,
    cpu_cores INTEGER,
    memory_gib REAL,
    disk_gib REAL,
    bandwidth_limit TEXT,
    transfer_limit TEXT,
    local_timezone TEXT NOT NULL,
    timezone_source TEXT NOT NULL,
    contract_cycle TEXT,
    contract_amount TEXT,
    contract_currency TEXT,
    next_due_local TEXT,
    next_due_timezone TEXT,
    next_due_source TEXT,
    resource_cycle_status TEXT NOT NULL,
    resource_cycle_source TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_resource_cycles (
    provider_cycle_id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL REFERENCES infrastructure_resources(resource_id),
    cycle_key TEXT NOT NULL,
    starts_at TEXT,
    ends_at TEXT,
    timezone TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    traffic_reset_authoritative INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(resource_id, cycle_key)
);

CREATE TABLE IF NOT EXISTS provider_resource_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL REFERENCES infrastructure_resources(resource_id),
    capacity_bytes INTEGER,
    used_bytes INTEGER,
    remaining_bytes INTEGER,
    resource_cycle_start TEXT,
    resource_cycle_end TEXT,
    next_reset_at TEXT,
    financial_cycle TEXT,
    next_due_at TEXT,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('available', 'stale', 'unknown', 'unavailable')),
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS node_pool_memberships (
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    pool_id TEXT NOT NULL REFERENCES resource_pools(pool_id),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    status TEXT NOT NULL,
    PRIMARY KEY (node_id, pool_id, effective_from)
);

CREATE TABLE IF NOT EXISTS node_capabilities (
    node_id TEXT PRIMARY KEY REFERENCES nodes(node_id),
    access_status TEXT NOT NULL CHECK(access_status IN ('allowed', 'staged', 'unavailable', 'unknown')),
    subscription_status TEXT NOT NULL CHECK(subscription_status IN ('allowed', 'staged', 'unavailable', 'unknown')),
    metering_status TEXT NOT NULL CHECK(metering_status IN ('available', 'unknown', 'unavailable')),
    quota_status TEXT NOT NULL CHECK(quota_status IN ('policy_only', 'unavailable')),
    supported_protocols TEXT NOT NULL,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    plan TEXT NOT NULL CHECK(plan IN ('Free', 'Basic', 'Plus')),
    role TEXT NOT NULL DEFAULT 'CUSTOMER',
    status TEXT NOT NULL,
    portal_token_hash TEXT NOT NULL UNIQUE,
    subscription_token_hash TEXT NOT NULL UNIQUE,
    subscription_token_legacy_hash TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_access_overrides (
    access_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    decision TEXT NOT NULL CHECK(decision IN ('allow', 'deny')),
    allocation_role TEXT NOT NULL CHECK(allocation_role IN ('default', 'primary', 'available', 'backup', 'reserved', 'deny')),
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    status TEXT NOT NULL CHECK(status IN ('active', 'superseded', 'retired')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operational_budgets (
    budget_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    node_id TEXT REFERENCES nodes(node_id),
    pool_id TEXT REFERENCES resource_pools(pool_id),
    provider_cycle_id TEXT REFERENCES provider_resource_cycles(provider_cycle_id),
    allowance_bytes INTEGER NOT NULL CHECK(allowance_bytes >= 0),
    budget_kind TEXT NOT NULL CHECK(budget_kind IN ('policy_only', 'enforceable')),
    status TEXT NOT NULL CHECK(status IN ('active', 'superseded', 'retired')),
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    created_at TEXT NOT NULL,
    CHECK(node_id IS NOT NULL OR pool_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS billing_cycles (
    cycle_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    cycle_key TEXT NOT NULL,
    starts_at TEXT,
    ends_at TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    cycle_kind TEXT NOT NULL DEFAULT 'legacy',
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    policy_id TEXT NOT NULL DEFAULT 'legacy',
    commercial_applies INTEGER NOT NULL DEFAULT 0,
    baseline_at TEXT,
    UNIQUE(user_id, cycle_key)
);

CREATE TABLE IF NOT EXISTS entitlements (
    entitlement_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    pool_id TEXT NOT NULL REFERENCES resource_pools(pool_id),
    plan TEXT NOT NULL CHECK(plan IN ('Free', 'Basic', 'Plus')),
    allowance_bytes INTEGER,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS credentials (
    credential_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    user_id TEXT REFERENCES users(user_id),
    runtime_ref_hash TEXT NOT NULL,
    runtime_family TEXT NOT NULL,
    protocol TEXT NOT NULL,
    credential_kind TEXT NOT NULL DEFAULT 'legacy',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(node_id, runtime_ref_hash)
);

CREATE TABLE IF NOT EXISTS credential_migration_events (
    event_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    subject_kind TEXT NOT NULL,
    subject_ref TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('issued', 'delivered', 'fetched', 'managed_traffic_observed', 'confirmed', 'legacy_retirement_ready', 'retired')),
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coverage_events (
    coverage_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    source TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('available', 'gap', 'stale', 'unknown')),
    observed_at TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collector_heartbeats (
    heartbeat_id TEXT PRIMARY KEY,
    collector_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'degraded', 'failed')),
    observed_at TEXT NOT NULL,
    attempted_nodes INTEGER NOT NULL CHECK(attempted_nodes >= 0),
    ingested_nodes INTEGER NOT NULL CHECK(ingested_nodes >= 0),
    failed_nodes INTEGER NOT NULL CHECK(failed_nodes >= 0),
    source TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_observations (
    observation_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    runtime_ref_hash TEXT NOT NULL,
    counter_epoch TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    uplink_bytes INTEGER NOT NULL,
    downlink_bytes INTEGER NOT NULL,
    source TEXT NOT NULL,
    attribution_status TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(node_id, runtime_ref_hash, counter_epoch, observed_at, source)
);

CREATE TABLE IF NOT EXISTS usage_ledger (
    ledger_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL UNIQUE REFERENCES usage_observations(observation_id),
    user_id TEXT REFERENCES users(user_id),
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    pool_id TEXT REFERENCES resource_pools(pool_id),
    cycle_id TEXT REFERENCES billing_cycles(cycle_id),
    provider_cycle_id TEXT REFERENCES provider_resource_cycles(provider_cycle_id),
    observed_at TEXT NOT NULL,
    delta_uplink_bytes INTEGER NOT NULL,
    delta_downlink_bytes INTEGER NOT NULL,
    attribution_status TEXT NOT NULL,
    detail TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS upgrade_requests (
    request_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    from_plan TEXT NOT NULL,
    to_plan TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    reviewed_at TEXT,
    note TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscription_entries (
    entry_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    node_id TEXT REFERENCES nodes(node_id),
    pool_id TEXT REFERENCES resource_pools(pool_id),
    credential_id TEXT REFERENCES credentials(credential_id),
    protocol TEXT NOT NULL,
    uri TEXT NOT NULL,
    minimum_plan TEXT NOT NULL CHECK(minimum_plan IN ('Free', 'Basic', 'Plus')),
    projection_status TEXT NOT NULL DEFAULT 'current',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_observation_lookup
    ON usage_observations(node_id, runtime_ref_hash, counter_epoch, observed_at);
CREATE INDEX IF NOT EXISTS idx_ledger_user_cycle
    ON usage_ledger(user_id, cycle_id, pool_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_ledger_node_pool
    ON usage_ledger(node_id, pool_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_access_override_lookup
    ON user_access_overrides(user_id, node_id, effective_from, effective_to, status);
CREATE INDEX IF NOT EXISTS idx_operational_budget_lookup
    ON operational_budgets(user_id, node_id, pool_id, effective_from, effective_to, status);
CREATE INDEX IF NOT EXISTS idx_migration_event_lookup
    ON credential_migration_events(user_id, subject_kind, subject_ref, created_at);
CREATE INDEX IF NOT EXISTS idx_provider_snapshot_lookup
    ON provider_resource_snapshots(resource_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_collector_heartbeat_lookup
    ON collector_heartbeats(collector_id, observed_at);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def normalize_time(value: str | None) -> str | None:
    parsed = parse_time(value)
    if parsed is None:
        return None
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + offset
    return index // 12, index % 12 + 1


def customer_cycle_window(value: str | datetime) -> tuple[str, str, str, str] | None:
    parsed = value if isinstance(value, datetime) else parse_time(value)
    if parsed is None or parsed.tzinfo is None:
        return None
    local = parsed.astimezone(CUSTOMER_CYCLE_ZONE)
    if local < CUSTOMER_CYCLE_BASELINE:
        return None
    year, month = local.year, local.month
    if local.day < 15:
        year, month = _add_months(year, month, -1)
    end_year, end_month = _add_months(year, month, 1)
    start_local = datetime(year, month, 15, tzinfo=CUSTOMER_CYCLE_ZONE)
    end_local = datetime(end_year, end_month, 15, tzinfo=CUSTOMER_CYCLE_ZONE)
    return (
        start_local.date().isoformat(),
        normalize_time(start_local.isoformat()),
        normalize_time(end_local.isoformat()),
        CUSTOMER_CYCLE_POLICY_ID,
    )


def customer_cycle_baseline_utc() -> str:
    return normalize_time(CUSTOMER_CYCLE_BASELINE.isoformat()) or "2026-09-14T16:00:00Z"


def ref_hash(runtime_ref: str) -> str:
    return hashlib.sha256(runtime_ref.strip().encode("utf-8")).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class ControlPlaneError(Exception):
    status = 400


class NotFound(ControlPlaneError):
    status = 404


class Unauthorized(ControlPlaneError):
    status = 401


class Conflict(ControlPlaneError):
    status = 409


class ServiceUnavailable(ControlPlaneError):
    status = 503


class ControlPlane:
    def __init__(self, db_path: str | Path, admin_token: str | None = None,
                 subscription_base_url: str | None = None,
                 coverage_max_age_seconds: int | None = None):
        self.db_path = Path(db_path)
        self.admin_token = admin_token or os.environ.get("SPARKLINK_ADMIN_TOKEN", "")
        self.subscription_base_url = (subscription_base_url or os.environ.get(
            "SPARKLINK_SUBSCRIPTION_BASE_URL", "https://sub.enrpiglink.top"
        )).rstrip("/")
        configured_age = coverage_max_age_seconds
        if configured_age is None:
            configured_age = int(os.environ.get(
                "SPARKLINK_COVERAGE_MAX_AGE_SECONDS", str(DEFAULT_COVERAGE_MAX_AGE_SECONDS)
            ))
        if configured_age <= 0:
            raise ValueError("coverage_max_age_seconds must be positive")
        self.coverage_max_age_seconds = configured_age

    @staticmethod
    def _hash_only_users_schema(table_name: str) -> str:
        if table_name != "users_hash_only":
            raise ValueError("unexpected users migration table")
        return """
            CREATE TABLE users_hash_only (
                user_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                plan TEXT NOT NULL CHECK(plan IN ('Free', 'Basic', 'Plus')),
                role TEXT NOT NULL DEFAULT 'CUSTOMER',
                status TEXT NOT NULL,
                portal_token_hash TEXT NOT NULL UNIQUE,
                subscription_token_hash TEXT NOT NULL UNIQUE,
                subscription_token_legacy_hash TEXT UNIQUE,
                created_at TEXT NOT NULL
            )
        """

    @staticmethod
    def _migrate_schema(db: sqlite3.Connection) -> None:
        additions = {
            "users": {
                "role": "TEXT NOT NULL DEFAULT 'CUSTOMER'",
                "subscription_token_hash": "TEXT",
                "subscription_token_legacy_hash": "TEXT",
            },
            "billing_cycles": {
                "cycle_kind": "TEXT NOT NULL DEFAULT 'legacy'",
                "timezone": "TEXT NOT NULL DEFAULT 'Asia/Shanghai'",
                "policy_id": "TEXT NOT NULL DEFAULT 'legacy'",
                "commercial_applies": "INTEGER NOT NULL DEFAULT 0",
                "baseline_at": "TEXT",
            },
            "credentials": {"credential_kind": "TEXT NOT NULL DEFAULT 'legacy'"},
            "usage_ledger": {"provider_cycle_id": "TEXT"},
            "subscription_entries": {
                "credential_id": "TEXT",
                "projection_status": "TEXT NOT NULL DEFAULT 'current'",
            },
        }
        for table, columns in additions.items():
            existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in existing:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        user_columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        if "subscription_token_hash" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN subscription_token_hash TEXT")
            user_columns.add("subscription_token_hash")

        # A pre-hardening database may still contain the legacy plaintext column.
        # Hash any value that is present, generate an unreachable hash for a
        # missing value, then rebuild the table so the plaintext column and its
        # UNIQUE constraint cannot survive the migration.
        legacy_column_present = "subscription_token" in user_columns
        select_columns = "user_id,subscription_token_hash,subscription_token_legacy_hash"
        if legacy_column_present:
            select_columns += ",subscription_token"
        rows = db.execute(f"SELECT {select_columns} FROM users").fetchall()
        for row in rows:
            subscription_hash = row["subscription_token_hash"]
            if not subscription_hash:
                legacy_token = row["subscription_token"] if legacy_column_present else None
                subscription_hash = token_hash(legacy_token) if legacy_token else token_hash(new_token())
                db.execute(
                    "UPDATE users SET subscription_token_hash=? WHERE user_id=?",
                    (subscription_hash, row["user_id"]),
                )

        if legacy_column_present:
            db.commit()
            db.execute("PRAGMA foreign_keys = OFF")
            try:
                db.execute("BEGIN")
                db.execute(ControlPlane._hash_only_users_schema("users_hash_only"))
                db.execute(
                    """INSERT INTO users_hash_only(
                           user_id,display_name,plan,role,status,portal_token_hash,
                           subscription_token_hash,subscription_token_legacy_hash,created_at
                       )
                       SELECT user_id,display_name,plan,role,status,portal_token_hash,
                              subscription_token_hash,subscription_token_legacy_hash,created_at
                       FROM users"""
                )
                db.execute("DROP TABLE users")
                db.execute("ALTER TABLE users_hash_only RENAME TO users")
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.execute("PRAGMA foreign_keys = ON")
            if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ControlPlaneError("users hash-only migration broke foreign keys")
            # Rebuilding the table removes the column from the schema. VACUUM
            # also removes old plaintext pages from the SQLite freelist.
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("VACUUM")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_subscription_token_hash "
            "ON users(subscription_token_hash)"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_subscription_token_legacy_hash "
            "ON users(subscription_token_legacy_hash)"
        )

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 5000")
        return db

    def init_db(self) -> None:
        with self.connect() as db:
            db.executescript(SCHEMA_SQL)
            self._migrate_schema(db)
            now = utc_now()
            for pool in POOL_NAMES:
                db.execute(
                    "INSERT OR IGNORE INTO resource_pools(pool_id, display_name, created_at) VALUES (?, ?, ?)",
                    (pool, pool.title(), now),
                )

    def seed_nodes(self) -> None:
        self.init_db()
        now = utc_now()
        nodes = [
            ("racknerd", "Standard serving Node", "active", "verified"),
            ("vmiss", "Existing Premium serving Node", "active", "verified"),
            ("hypro02", "Conditional Premium serving Node", "active", "conditional"),
            ("dedirock", "Legacy reference Node", "reference-only", "unqualified"),
        ]
        with self.connect() as db:
            for node in nodes:
                db.execute(
                    "INSERT OR IGNORE INTO nodes(node_id, display_name, status, qualification, created_at) VALUES (?, ?, ?, ?, ?)",
                    (*node, now),
                )
            memberships = [("racknerd", "STANDARD"), ("vmiss", "PREMIUM"), ("hypro02", "PREMIUM")]
            for node_id, pool_id in memberships:
                db.execute(
                    """INSERT OR IGNORE INTO node_pool_memberships
                       (node_id, pool_id, effective_from, effective_to, status)
                       VALUES (?, ?, ?, NULL, ?)""",
                    (node_id, pool_id, "2026-08-24T00:00:00Z", "active"),
                )

    def upsert_infrastructure_resource(self, resource: dict) -> str:
        required = {
            "resource_id", "provider_name", "provider_instance_id", "location", "network_label",
            "local_timezone", "timezone_source", "resource_cycle_status", "resource_cycle_source",
        }
        if not required.issubset(resource):
            raise ControlPlaneError("infrastructure resource fields are incomplete")
        resource_id = str(resource["resource_id"])
        now = utc_now()
        values = (
            resource_id, str(resource["provider_name"]), str(resource["provider_instance_id"]),
            resource.get("node_id"), str(resource["location"]), str(resource["network_label"]),
            resource.get("asn"), resource.get("public_ipv4"), resource.get("cpu_cores"),
            resource.get("memory_gib"), resource.get("disk_gib"), resource.get("bandwidth_limit"),
            resource.get("transfer_limit"), str(resource["local_timezone"]),
            str(resource["timezone_source"]), resource.get("contract_cycle"),
            resource.get("contract_amount"), resource.get("contract_currency"),
            resource.get("next_due_local"), resource.get("next_due_timezone"),
            resource.get("next_due_source"), str(resource["resource_cycle_status"]),
            str(resource["resource_cycle_source"]), now,
        )
        with self.connect() as db:
            if resource.get("node_id") and db.execute(
                "SELECT 1 FROM nodes WHERE node_id=?", (resource["node_id"],)
            ).fetchone() is None:
                raise NotFound("node not found")
            exists = db.execute(
                "SELECT 1 FROM infrastructure_resources WHERE resource_id=?", (resource_id,)
            ).fetchone()
            if exists:
                db.execute(
                    """UPDATE infrastructure_resources SET
                           provider_name=?,provider_instance_id=?,node_id=?,location=?,network_label=?,
                           asn=?,public_ipv4=?,cpu_cores=?,memory_gib=?,disk_gib=?,bandwidth_limit=?,
                           transfer_limit=?,local_timezone=?,timezone_source=?,contract_cycle=?,
                           contract_amount=?,contract_currency=?,next_due_local=?,next_due_timezone=?,
                           next_due_source=?,resource_cycle_status=?,resource_cycle_source=?
                       WHERE resource_id=?""",
                    (*values[1:-1], resource_id),
                )
            else:
                db.execute(
                    """INSERT INTO infrastructure_resources(
                           resource_id,provider_name,provider_instance_id,node_id,location,network_label,
                           asn,public_ipv4,cpu_cores,memory_gib,disk_gib,bandwidth_limit,transfer_limit,
                           local_timezone,timezone_source,contract_cycle,contract_amount,contract_currency,
                           next_due_local,next_due_timezone,next_due_source,resource_cycle_status,
                           resource_cycle_source,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
        return resource_id

    def record_provider_resource_cycle(self, cycle: dict) -> str:
        required = {"provider_cycle_id", "resource_id", "cycle_key", "timezone", "status", "source"}
        if not required.issubset(cycle):
            raise ControlPlaneError("provider resource cycle fields are incomplete")
        starts_at = normalize_time(cycle.get("starts_at")) if cycle.get("starts_at") else None
        ends_at = normalize_time(cycle.get("ends_at")) if cycle.get("ends_at") else None
        if ((cycle.get("starts_at") and starts_at is None)
                or (cycle.get("ends_at") and ends_at is None)):
            raise ControlPlaneError("provider cycle timestamps must be ISO-8601")
        provider_cycle_id = str(cycle["provider_cycle_id"])
        with self.connect() as db:
            if db.execute(
                "SELECT 1 FROM infrastructure_resources WHERE resource_id=?", (cycle["resource_id"],)
            ).fetchone() is None:
                raise NotFound("infrastructure resource not found")
            db.execute(
                """INSERT INTO provider_resource_cycles(
                       provider_cycle_id,resource_id,cycle_key,starts_at,ends_at,timezone,status,source,
                       traffic_reset_authoritative,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(resource_id,cycle_key) DO UPDATE SET
                       starts_at=excluded.starts_at,ends_at=excluded.ends_at,timezone=excluded.timezone,
                       status=excluded.status,source=excluded.source,
                       traffic_reset_authoritative=excluded.traffic_reset_authoritative""",
                (provider_cycle_id, str(cycle["resource_id"]), str(cycle["cycle_key"]), starts_at, ends_at,
                 str(cycle["timezone"]), str(cycle["status"]), str(cycle["source"]),
                 int(bool(cycle.get("traffic_reset_authoritative", False))), utc_now()),
            )
        return provider_cycle_id

    @staticmethod
    def _validate_capability_values(access_status: str, subscription_status: str,
                                    metering_status: str, quota_status: str,
                                    supported_protocols: object) -> list[str]:
        if access_status not in {"allowed", "staged", "unavailable", "unknown"}:
            raise ControlPlaneError("invalid node access status")
        if subscription_status not in {"allowed", "staged", "unavailable", "unknown"}:
            raise ControlPlaneError("invalid node subscription status")
        if metering_status not in {"available", "unknown", "unavailable"}:
            raise ControlPlaneError("invalid node metering status")
        if quota_status not in {"policy_only", "unavailable"}:
            raise Conflict("hard quota enforcement is not authorized")
        if not isinstance(supported_protocols, (list, tuple, set)):
            raise ControlPlaneError("node protocols must be a list")
        protocols = sorted({str(value).strip().lower() for value in supported_protocols if str(value).strip()})
        if not protocols or any(value != "vless" for value in protocols):
            raise Conflict("only Xray VLESS is currently supported")
        return protocols

    def set_node_capability(self, node_id: str, access_status: str = "allowed",
                            subscription_status: str = "allowed",
                            metering_status: str = "unknown",
                            quota_status: str = "unavailable",
                            supported_protocols: object = ("vless",),
                            source: str = "operator", detail: str = "",
                            observed_at: str | None = None) -> dict:
        protocols = self._validate_capability_values(
            access_status, subscription_status, metering_status, quota_status, supported_protocols
        )
        if not str(source).strip():
            raise ControlPlaneError("node capability source must not be empty")
        sample_time = normalize_time(observed_at) if observed_at else utc_now()
        if sample_time is None:
            raise ControlPlaneError("observed_at must be ISO-8601")
        with self.connect() as db:
            if db.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone() is None:
                raise NotFound("node not found")
            db.execute(
                """INSERT INTO node_capabilities(
                       node_id,access_status,subscription_status,metering_status,quota_status,
                       supported_protocols,source,observed_at,detail
                   ) VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(node_id) DO UPDATE SET
                       access_status=excluded.access_status,
                       subscription_status=excluded.subscription_status,
                       metering_status=excluded.metering_status,
                       quota_status=excluded.quota_status,
                       supported_protocols=excluded.supported_protocols,
                       source=excluded.source,observed_at=excluded.observed_at,
                       detail=excluded.detail""",
                (node_id, access_status, subscription_status, metering_status, quota_status,
                 json.dumps(protocols, separators=(",", ":")), str(source).strip(), sample_time,
                 str(detail)[:500]),
            )
        return {
            "node_id": node_id,
            "access_status": access_status,
            "subscription_status": subscription_status,
            "metering_status": metering_status,
            "quota_status": quota_status,
            "supported_protocols": protocols,
            "source": str(source).strip(),
            "observed_at": sample_time,
        }

    def admit_node(self, node_id: str, pool_id: str, qualification: str = "conditional",
                   source: str = "operator", effective_from: str | None = None,
                   metering_status: str = "unknown",
                   supported_protocols: object = ("vless",),
                   detail: str = "") -> dict:
        """Admit a node to the management-plane model without changing its runtime.

        Runtime configuration changes remain a separate, explicitly staged
        operation. Admission records that access/subscription are allowed and
        that metering/quota capabilities are independent dimensions.
        """
        if pool_id not in POOL_NAMES:
            raise ControlPlaneError("invalid pool")
        when = normalize_time(effective_from) if effective_from else utc_now()
        if when is None:
            raise ControlPlaneError("effective_from must be ISO-8601")
        protocols = self._validate_capability_values(
            "allowed", "allowed", metering_status, "unavailable", supported_protocols
        )
        if not str(source).strip():
            raise ControlPlaneError("node admission source must not be empty")
        with self.connect() as db:
            node = db.execute("SELECT node_id FROM nodes WHERE node_id=?", (node_id,)).fetchone()
            if node is None:
                raise NotFound("node not found")
            db.execute(
                "UPDATE nodes SET status='active',qualification=? WHERE node_id=?",
                (qualification, node_id),
            )
            db.execute(
                """UPDATE node_pool_memberships
                   SET effective_to=?,status='superseded'
                   WHERE node_id=? AND status='active' AND effective_to IS NULL AND pool_id<>?""",
                (when, node_id, pool_id),
            )
            db.execute(
                """INSERT INTO node_pool_memberships(node_id,pool_id,effective_from,effective_to,status)
                   VALUES (?,?,?,NULL,'active')
                   ON CONFLICT(node_id,pool_id,effective_from) DO UPDATE SET
                       effective_to=NULL,status='active'""",
                (node_id, pool_id, when),
            )
            db.execute(
                """INSERT INTO node_capabilities(
                       node_id,access_status,subscription_status,metering_status,quota_status,
                       supported_protocols,source,observed_at,detail
                   ) VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(node_id) DO UPDATE SET
                       access_status='allowed',subscription_status='allowed',
                       metering_status=excluded.metering_status,quota_status='unavailable',
                       supported_protocols=excluded.supported_protocols,source=excluded.source,
                       observed_at=excluded.observed_at,detail=excluded.detail""",
                (node_id, "allowed", "allowed", metering_status, "unavailable",
                 json.dumps(protocols, separators=(",", ":")), str(source).strip(), when,
                 str(detail)[:500]),
            )
        return {
            "node_id": node_id, "pool_id": pool_id, "status": "active",
            "qualification": qualification, "access_status": "allowed",
            "subscription_status": "allowed", "metering_status": metering_status,
            "quota_status": "unavailable", "supported_protocols": protocols,
            "effective_from": when, "source": str(source).strip(),
        }

    def admit_runtime_entries(self, node_id: str, pool_id: str, entries: list[dict],
                              qualification: str = "verified",
                              display_name: str | None = None,
                              source: str = "runtime-admission",
                              effective_from: str | None = None,
                              metering_status: str = "unknown",
                              supported_protocols: object = ("vless",),
                              detail: str = "") -> dict:
        """Atomically admit managed runtime identities and current projections.

        Runtime mutation is deliberately performed by a separate, root-only
        Node operator.  This method records only the already-verified,
        non-secret mapping after runtime acceptance.  It is idempotent for the
        same node/runtime reference hash and subscription entry.
        """
        if pool_id not in POOL_NAMES:
            raise ControlPlaneError("invalid pool")
        if not isinstance(entries, list) or not entries:
            raise ControlPlaneError("runtime admission entries are empty")
        when = normalize_time(effective_from) if effective_from else utc_now()
        if when is None:
            raise ControlPlaneError("effective_from must be ISO-8601")
        protocols = self._validate_capability_values(
            "allowed", "allowed", metering_status, "unavailable", supported_protocols
        )
        if not str(source).strip():
            raise ControlPlaneError("runtime admission source must not be empty")
        if display_name is not None and (
            not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 120
        ):
            raise ControlPlaneError("runtime admission display name is invalid")

        normalized: list[dict] = []
        seen_users: set[str] = set()
        seen_hashes: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ControlPlaneError("runtime admission entry is invalid")
            user_id = str(entry.get("user_id") or "").strip()
            runtime_ref_hash = str(entry.get("runtime_ref_hash") or "").strip().lower()
            runtime_family = str(entry.get("runtime_family") or "").strip().lower()
            protocol = str(entry.get("protocol") or "").strip().lower()
            credential_kind = str(entry.get("credential_kind", "managed")).strip().lower()
            uri = str(entry.get("uri") or "").strip()
            minimum_plan = str(entry.get("minimum_plan") or "").strip()
            if not user_id or user_id in seen_users:
                raise Conflict("runtime admission user mapping is duplicate or empty")
            if (len(runtime_ref_hash) != 64
                    or any(c not in "0123456789abcdef" for c in runtime_ref_hash)
                    or runtime_ref_hash in seen_hashes):
                raise Conflict("runtime admission reference hash is duplicate or invalid")
            if not runtime_family or len(runtime_family) > 80:
                raise ControlPlaneError("runtime family is invalid")
            if protocol != "vless" or protocol not in protocols:
                raise Conflict("runtime admission supports only VLESS")
            if credential_kind != "managed":
                raise Conflict("runtime admission requires managed credentials")
            parsed = urlsplit(uri)
            if (parsed.scheme.lower() != "vless" or not parsed.netloc
                    or any(char.isspace() for char in uri) or len(uri) > 4096):
                raise ControlPlaneError("runtime admission URI is invalid")
            if minimum_plan not in PLAN_ORDER or pool_id not in PLAN_POOL_ENTITLEMENTS[minimum_plan]:
                raise ControlPlaneError("runtime admission minimum plan is invalid")
            seen_users.add(user_id)
            seen_hashes.add(runtime_ref_hash)
            normalized.append({
                "user_id": user_id,
                "runtime_ref_hash": runtime_ref_hash,
                "runtime_family": runtime_family,
                "protocol": protocol,
                "credential_kind": credential_kind,
                "uri": uri,
                "minimum_plan": minimum_plan,
            })

        credential_created = 0
        credential_reused = 0
        subscription_created = 0
        subscription_reused = 0
        migration_events = 0
        with self.connect() as db:
            if db.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone() is None:
                raise NotFound("node not found")

            # Keep the same effective membership/capability semantics as the
            # ordinary admission endpoint, but in this transaction so a
            # projection can never be committed without its Node admission.
            db.execute(
                """UPDATE nodes SET status='active',qualification=?,
                          display_name=COALESCE(?,display_name) WHERE node_id=?""",
                (qualification, display_name.strip() if display_name is not None else None, node_id),
            )
            same_membership = db.execute(
                """SELECT 1 FROM node_pool_memberships
                   WHERE node_id=? AND pool_id=? AND status='active' AND effective_to IS NULL
                   ORDER BY effective_from DESC LIMIT 1""",
                (node_id, pool_id),
            ).fetchone()
            if same_membership is None:
                db.execute(
                    """UPDATE node_pool_memberships
                       SET effective_to=?,status='superseded'
                       WHERE node_id=? AND status='active' AND effective_to IS NULL AND pool_id<>?""",
                    (when, node_id, pool_id),
                )
                db.execute(
                    """INSERT INTO node_pool_memberships(node_id,pool_id,effective_from,effective_to,status)
                       VALUES (?,?,?,NULL,'active')
                       ON CONFLICT(node_id,pool_id,effective_from) DO UPDATE SET
                           effective_to=NULL,status='active'""",
                    (node_id, pool_id, when),
                )
            db.execute(
                """INSERT INTO node_capabilities(
                       node_id,access_status,subscription_status,metering_status,quota_status,
                       supported_protocols,source,observed_at,detail
                   ) VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(node_id) DO UPDATE SET
                       access_status='allowed',subscription_status='allowed',
                       metering_status=excluded.metering_status,quota_status='unavailable',
                       supported_protocols=excluded.supported_protocols,source=excluded.source,
                       observed_at=excluded.observed_at,detail=excluded.detail""",
                (node_id, "allowed", "allowed", metering_status, "unavailable",
                 json.dumps(protocols, separators=(",", ":")), str(source).strip(), when,
                 str(detail)[:500]),
            )

            for item in normalized:
                user = db.execute(
                    "SELECT user_id,plan FROM users WHERE user_id=?", (item["user_id"],)
                ).fetchone()
                if user is None:
                    raise NotFound("user not found")
                if pool_id not in PLAN_POOL_ENTITLEMENTS.get(user["plan"], frozenset()):
                    raise Conflict("runtime admission user is not entitled to pool")
                if PLAN_ORDER[user["plan"]] < PLAN_ORDER[item["minimum_plan"]]:
                    raise Conflict("runtime admission minimum plan exceeds user plan")
                access = self._access_at(db, user, node_id, when)
                if access["decision"] != "allow":
                    raise Conflict("runtime admission user access is denied")

                credential = db.execute(
                    """SELECT credential_id,user_id,runtime_family,protocol,credential_kind,status
                       FROM credentials WHERE node_id=? AND runtime_ref_hash=?""",
                    (node_id, item["runtime_ref_hash"]),
                ).fetchone()
                if credential is None:
                    credential_id = f"cred_{uuid.uuid4().hex}"
                    db.execute(
                        """INSERT INTO credentials
                           (credential_id,node_id,user_id,runtime_ref_hash,runtime_family,protocol,
                            credential_kind,status,created_at)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (credential_id, node_id, item["user_id"], item["runtime_ref_hash"],
                         item["runtime_family"], item["protocol"], item["credential_kind"],
                         "active", utc_now()),
                    )
                    db.execute(
                        """INSERT INTO credential_migration_events(
                               event_id,user_id,subject_kind,subject_ref,state,observed_at,source,detail,created_at
                           ) VALUES (?,?,?,?,?,?,?,?,?)""",
                        (f"mig_{uuid.uuid4().hex}", item["user_id"], "runtime_credential", credential_id,
                         "issued", when, str(source)[:200],
                         "managed runtime identity admitted after isolated acceptance", utc_now()),
                    )
                    credential_created += 1
                    migration_events += 1
                else:
                    if (credential["user_id"] != item["user_id"]
                            or credential["runtime_family"] != item["runtime_family"]
                            or credential["protocol"] != item["protocol"]
                            or credential["credential_kind"] != item["credential_kind"]
                            or credential["status"] != "active"):
                        raise Conflict("runtime admission credential mapping conflicts")
                    credential_id = credential["credential_id"]
                    credential_reused += 1

                existing = db.execute(
                    """SELECT entry_id,uri,minimum_plan,projection_status,enabled
                       FROM subscription_entries
                       WHERE user_id=? AND node_id=? AND pool_id=? AND credential_id=? AND protocol=?
                       ORDER BY created_at DESC,entry_id DESC LIMIT 1""",
                    (item["user_id"], node_id, pool_id, credential_id, item["protocol"]),
                ).fetchone()
                if existing is not None:
                    if (existing["uri"] != item["uri"]
                            or existing["minimum_plan"] != item["minimum_plan"]):
                        db.execute(
                            "UPDATE subscription_entries SET projection_status='retired',enabled=0 WHERE entry_id=?",
                            (existing["entry_id"],),
                        )
                        existing = None
                    elif existing["projection_status"] != "current" or not existing["enabled"]:
                        db.execute(
                            "UPDATE subscription_entries SET projection_status='current',enabled=1 WHERE entry_id=?",
                            (existing["entry_id"],),
                        )
                    if existing is not None:
                        subscription_reused += 1
                        continue

                # A single Node may intentionally expose more than one
                # managed credential for distinct egress variants (for
                # example Origin/native and HyTru). The credential identity
                # is the disambiguator; only an unbound current projection
                # remains a conflict because it cannot be safely associated
                # with this managed runtime identity.
                conflict = db.execute(
                    """SELECT entry_id FROM subscription_entries
                       WHERE user_id=? AND node_id=? AND pool_id=? AND protocol=?
                         AND projection_status='current' AND enabled=1
                         AND credential_id IS NULL""",
                    (item["user_id"], node_id, pool_id, item["protocol"]),
                ).fetchone()
                if conflict is not None:
                    raise Conflict("runtime admission has a conflicting current projection")
                db.execute(
                    """INSERT INTO subscription_entries
                       (entry_id,user_id,node_id,pool_id,credential_id,protocol,uri,minimum_plan,
                        projection_status,enabled,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,1,?)""",
                    (f"sub_{uuid.uuid4().hex}", item["user_id"], node_id, pool_id, credential_id,
                     item["protocol"], item["uri"], item["minimum_plan"], "current", utc_now()),
                )
                subscription_created += 1

        return {
            "ok": True,
            "node_id": node_id,
            "pool_id": pool_id,
            "display_name": display_name.strip() if display_name is not None else None,
            "qualification": qualification,
            "metering_status": metering_status,
            "quota_status": "unavailable",
            "managed_users": len(normalized),
            "credentials_created": credential_created,
            "credentials_reused": credential_reused,
            "subscriptions_created": subscription_created,
            "subscriptions_reused": subscription_reused,
            "migration_events": migration_events,
            "effective_from": when,
            "source": str(source).strip(),
        }

    def set_access_override(self, user_id: str, node_id: str, decision: str,
                            allocation_role: str, reason: str, source: str,
                            effective_from: str | None = None,
                            effective_to: str | None = None) -> dict:
        if decision not in ACCESS_DECISIONS:
            raise ControlPlaneError("invalid access decision")
        if allocation_role not in ALLOCATION_ROLES:
            raise ControlPlaneError("invalid allocation role")
        if (decision == "deny") != (allocation_role == "deny"):
            raise ControlPlaneError("deny access must use deny allocation role")
        if decision == "allow" and allocation_role == "deny":
            raise ControlPlaneError("allow access cannot use deny allocation role")
        if not str(reason).strip() or not str(source).strip():
            raise ControlPlaneError("access override reason and source are required")
        starts = normalize_time(effective_from) if effective_from else utc_now()
        ends = normalize_time(effective_to) if effective_to else None
        if starts is None or (effective_to and ends is None):
            raise ControlPlaneError("access override timestamps must be ISO-8601")
        if ends and parse_time(ends) <= parse_time(starts):
            raise ControlPlaneError("access override effective_to must be after effective_from")
        access_id = f"uxa_{uuid.uuid4().hex}"
        with self.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None:
                raise NotFound("user not found")
            if db.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone() is None:
                raise NotFound("node not found")
            db.execute(
                """UPDATE user_access_overrides SET effective_to=?,status='superseded'
                   WHERE user_id=? AND node_id=? AND status='active' AND effective_to IS NULL
                     AND effective_from < ?""",
                (starts, user_id, node_id, starts),
            )
            db.execute(
                """INSERT INTO user_access_overrides(
                       access_id,user_id,node_id,decision,allocation_role,reason,source,
                       effective_from,effective_to,status,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,'active',?)""",
                (access_id, user_id, node_id, decision, allocation_role, str(reason)[:500],
                 str(source)[:200], starts, ends, utc_now()),
            )
        return {
            "access_id": access_id, "user_id": user_id, "node_id": node_id,
            "decision": decision, "allocation_role": allocation_role,
            "reason": str(reason)[:500], "source": str(source)[:200],
            "effective_from": starts, "effective_to": ends, "status": "active",
        }

    def set_operational_budget(self, user_id: str, allowance_bytes: int,
                               node_id: str | None = None, pool_id: str | None = None,
                               provider_cycle_id: str | None = None,
                               budget_kind: str = "policy_only",
                               reason: str = "", source: str = "operator",
                               effective_from: str | None = None,
                               effective_to: str | None = None) -> dict:
        if isinstance(allowance_bytes, bool) or not isinstance(allowance_bytes, int) or allowance_bytes < 0:
            raise ControlPlaneError("allowance_bytes must be a non-negative integer")
        if budget_kind != "policy_only":
            raise Conflict("hard quota enforcement is not authorized")
        if not node_id and not pool_id:
            raise ControlPlaneError("operational budget needs a node or pool scope")
        if pool_id and pool_id not in POOL_NAMES:
            raise ControlPlaneError("invalid pool")
        if not str(reason).strip() or not str(source).strip():
            raise ControlPlaneError("budget reason and source are required")
        starts = normalize_time(effective_from) if effective_from else utc_now()
        ends = normalize_time(effective_to) if effective_to else None
        if starts is None or (effective_to and ends is None):
            raise ControlPlaneError("budget timestamps must be ISO-8601")
        if ends and parse_time(ends) <= parse_time(starts):
            raise ControlPlaneError("budget effective_to must be after effective_from")
        budget_id = f"bud_{uuid.uuid4().hex}"
        with self.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None:
                raise NotFound("user not found")
            if node_id and db.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone() is None:
                raise NotFound("node not found")
            if provider_cycle_id and db.execute(
                "SELECT 1 FROM provider_resource_cycles WHERE provider_cycle_id=?", (provider_cycle_id,)
            ).fetchone() is None:
                raise NotFound("provider resource cycle not found")
            db.execute(
                """UPDATE operational_budgets SET effective_to=?,status='superseded'
                   WHERE user_id=? AND COALESCE(node_id,'')=COALESCE(?, '')
                     AND COALESCE(pool_id,'')=COALESCE(?, '') AND status='active'
                     AND effective_to IS NULL AND effective_from < ?""",
                (starts, user_id, node_id, pool_id, starts),
            )
            db.execute(
                """INSERT INTO operational_budgets(
                       budget_id,user_id,node_id,pool_id,provider_cycle_id,allowance_bytes,
                       budget_kind,status,reason,source,effective_from,effective_to,created_at
                   ) VALUES (?,?,?,?,?,?,?,'active',?,?,?,?,?)""",
                (budget_id, user_id, node_id, pool_id, provider_cycle_id, allowance_bytes,
                 budget_kind, str(reason)[:500], str(source)[:200], starts, ends, utc_now()),
            )
        return {
            "budget_id": budget_id, "user_id": user_id, "node_id": node_id,
            "pool_id": pool_id, "provider_cycle_id": provider_cycle_id,
            "allowance_bytes": allowance_bytes, "budget_kind": budget_kind,
            "status": "active", "reason": str(reason)[:500],
            "source": str(source)[:200], "effective_from": starts, "effective_to": ends,
        }

    def record_migration_event(self, user_id: str, subject_kind: str,
                               subject_ref: str, state: str, source: str,
                               detail: str = "", observed_at: str | None = None) -> dict:
        if state not in MIGRATION_STATES:
            raise ControlPlaneError("invalid migration state")
        if not str(subject_kind).strip() or not str(subject_ref).strip() or not str(source).strip():
            raise ControlPlaneError("migration event fields are required")
        sample_time = normalize_time(observed_at) if observed_at else utc_now()
        if sample_time is None:
            raise ControlPlaneError("observed_at must be ISO-8601")
        event_id = f"mig_{uuid.uuid4().hex}"
        with self.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None:
                raise NotFound("user not found")
            db.execute(
                """INSERT INTO credential_migration_events(
                       event_id,user_id,subject_kind,subject_ref,state,observed_at,source,detail,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (event_id, user_id, str(subject_kind)[:80], str(subject_ref)[:200], state,
                 sample_time, str(source)[:200], str(detail)[:500], utc_now()),
            )
        return {"event_id": event_id, "user_id": user_id, "subject_kind": str(subject_kind)[:80],
                "subject_ref": str(subject_ref)[:200], "state": state,
                "observed_at": sample_time, "source": str(source)[:200]}

    def record_collector_heartbeat(self, collector_id: str, status: str,
                                   attempted_nodes: int, ingested_nodes: int,
                                   failed_nodes: int, source: str = "collector",
                                   detail: str = "", observed_at: str | None = None) -> dict:
        if status not in {"running", "completed", "degraded", "failed"}:
            raise ControlPlaneError("invalid collector heartbeat status")
        values = (attempted_nodes, ingested_nodes, failed_nodes)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ControlPlaneError("collector heartbeat counts are invalid")
        if ingested_nodes > attempted_nodes or failed_nodes > attempted_nodes:
            raise ControlPlaneError("collector heartbeat counts are inconsistent")
        if not str(collector_id).strip() or not str(source).strip():
            raise ControlPlaneError("collector heartbeat source is required")
        sample_time = normalize_time(observed_at) if observed_at else utc_now()
        if sample_time is None:
            raise ControlPlaneError("observed_at must be ISO-8601")
        heartbeat_id = f"hb_{uuid.uuid4().hex}"
        with self.connect() as db:
            db.execute(
                """INSERT INTO collector_heartbeats(
                       heartbeat_id,collector_id,status,observed_at,attempted_nodes,
                       ingested_nodes,failed_nodes,source,detail
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (heartbeat_id, str(collector_id)[:120], status, sample_time,
                 attempted_nodes, ingested_nodes, failed_nodes, str(source)[:200], str(detail)[:500]),
            )
        return {"heartbeat_id": heartbeat_id, "collector_id": str(collector_id)[:120],
                "status": status, "observed_at": sample_time,
                "attempted_nodes": attempted_nodes, "ingested_nodes": ingested_nodes,
                "failed_nodes": failed_nodes}

    def record_provider_resource_snapshot(self, snapshot: dict) -> str:
        required = {"resource_id", "observed_at", "source", "status"}
        if not required.issubset(snapshot):
            raise ControlPlaneError("provider snapshot fields are incomplete")
        allowed = required | {
            "snapshot_id", "capacity_bytes", "used_bytes", "remaining_bytes",
            "resource_cycle_start", "resource_cycle_end", "next_reset_at",
            "financial_cycle", "next_due_at", "detail",
        }
        if set(snapshot) - allowed:
            raise ControlPlaneError("provider snapshot fields are not allowed")
        if (not isinstance(snapshot["resource_id"], str) or not snapshot["resource_id"].strip()
                or not isinstance(snapshot["source"], str) or not snapshot["source"].strip()):
            raise ControlPlaneError("provider snapshot source/resource is invalid")
        status = str(snapshot["status"])
        if status not in {"available", "stale", "unknown", "unavailable"}:
            raise ControlPlaneError("invalid provider snapshot status")
        observed_at = normalize_time(str(snapshot["observed_at"]))
        if observed_at is None:
            raise ControlPlaneError("provider snapshot source/time is invalid")
        detail = snapshot.get("detail", "")
        if detail is None:
            detail = ""
        if not isinstance(detail, str) or re.search(
                r"(?i)(?:vless|anytls)://|-----BEGIN [^-]*PRIVATE KEY-----|"
                r"\b(?:bearer|access[_-]?token|subscription[_-]?token|password|secret|private[_-]?key)\b\s*[:=]",
                detail):
            raise ControlPlaneError("provider snapshot detail must not contain credentials")
        numeric_fields = ("capacity_bytes", "used_bytes", "remaining_bytes")
        numeric: dict[str, int | None] = {}
        for field in numeric_fields:
            value = snapshot.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ControlPlaneError("provider snapshot byte fields are invalid")
            numeric[field] = value
        times: dict[str, str | None] = {}
        for field in ("resource_cycle_start", "resource_cycle_end", "next_reset_at", "next_due_at"):
            value = snapshot.get(field)
            normalized = normalize_time(str(value)) if value else None
            if value and normalized is None:
                raise ControlPlaneError("provider snapshot timestamp is invalid")
            times[field] = normalized
        byte_values = tuple(numeric[field] for field in numeric_fields)
        if status == "available" and any(value is None for value in byte_values):
            raise ControlPlaneError("available provider snapshot requires complete byte fields")
        if status in {"unknown", "unavailable"}:
            if any(value is not None for value in byte_values):
                raise ControlPlaneError("unknown provider snapshot must not contain byte values")
            if any(times[field] is not None for field in times) or snapshot.get("financial_cycle") is not None:
                raise ControlPlaneError("unknown provider snapshot must not contain cycle values")
        if any(value is not None for value in byte_values) and any(value is None for value in byte_values):
            raise ControlPlaneError("provider snapshot byte fields are incomplete")
        if all(value is not None for value in byte_values):
            capacity, used, remaining = (int(value) for value in byte_values)
            if used > capacity or remaining != capacity - used:
                raise ControlPlaneError("provider snapshot byte fields are inconsistent")
        snapshot_id = str(snapshot.get("snapshot_id") or f"ps_{uuid.uuid4().hex}")
        with self.connect() as db:
            if db.execute(
                "SELECT 1 FROM infrastructure_resources WHERE resource_id=?", (snapshot["resource_id"],)
            ).fetchone() is None:
                raise NotFound("infrastructure resource not found")
            db.execute(
                """INSERT INTO provider_resource_snapshots(
                       snapshot_id,resource_id,capacity_bytes,used_bytes,remaining_bytes,
                       resource_cycle_start,resource_cycle_end,next_reset_at,financial_cycle,
                       next_due_at,observed_at,source,status,detail,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (snapshot_id, str(snapshot["resource_id"]), numeric["capacity_bytes"], numeric["used_bytes"],
                 numeric["remaining_bytes"], times["resource_cycle_start"], times["resource_cycle_end"],
                 times["next_reset_at"], snapshot.get("financial_cycle"), times["next_due_at"],
                  observed_at, snapshot["source"][:200], status, detail[:500],
                 utc_now()),
            )
        return snapshot_id

    def _require_admin(self, supplied: str | None) -> None:
        if not self.admin_token or not supplied or not secrets.compare_digest(supplied, self.admin_token):
            raise Unauthorized("admin authentication required")

    def _user_by_token(self, token: str | None) -> sqlite3.Row:
        if not token:
            raise Unauthorized("user authentication required")
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM users WHERE portal_token_hash = ? AND status = 'active'",
                (token_hash(token),),
            ).fetchone()
        if row is None:
            raise Unauthorized("invalid or revoked user token")
        return row

    def _user_by_subscription_token(self, token: str | None) -> sqlite3.Row:
        if not token:
            raise Unauthorized("subscription token required")
        with self.connect() as db:
            row = db.execute(
                """SELECT * FROM users
                   WHERE status='active'
                     AND (subscription_token_hash=? OR subscription_token_legacy_hash=?)""",
                (token_hash(token), token_hash(token)),
            ).fetchone()
        if row is None:
            raise Unauthorized("invalid or revoked subscription token")
        return row

    def create_user(self, display_name: str, plan: str = "Free", user_id: str | None = None,
                    role: str = "CUSTOMER") -> dict:
        if plan not in PLAN_ORDER:
            raise ControlPlaneError("invalid plan")
        if role not in {"CUSTOMER", "OWNER"}:
            raise ControlPlaneError("invalid user role")
        uid = user_id or f"usr_{uuid.uuid4().hex}"
        token = new_token()
        sub_token = new_token()
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO users(
                       user_id,display_name,plan,role,status,portal_token_hash,
                       subscription_token_hash,subscription_token_legacy_hash,created_at
                   ) VALUES (?,?,?,?,'active',?,?,NULL,?)""",
                (uid, display_name, plan, role, token_hash(token), token_hash(sub_token), now),
            )
        return {"user_id": uid, "portal_token": token, "subscription_token": sub_token}

    def reconcile_user(self, user_id: str, display_name: str, plan: str,
                       role: str = "CUSTOMER") -> dict:
        """Create or reconcile current identity metadata without rewriting usage."""
        if plan not in PLAN_ORDER:
            raise ControlPlaneError("invalid plan")
        if role not in {"CUSTOMER", "OWNER"}:
            raise ControlPlaneError("invalid user role")
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                token = new_token()
                sub_token = new_token()
                db.execute(
                    """INSERT INTO users(
                           user_id,display_name,plan,role,status,portal_token_hash,
                           subscription_token_hash,subscription_token_legacy_hash,created_at
                       ) VALUES (?,?,?,?,'active',?,?,NULL,?)""",
                    (user_id, display_name, plan, role, token_hash(token), token_hash(sub_token), utc_now()),
                )
                return {"user_id": user_id, "portal_token": token,
                        "subscription_token": sub_token, "created": True}
            db.execute(
                """UPDATE users SET display_name=?,plan=?,role=?,status='active',
                          portal_token_hash=?,subscription_token_hash=? WHERE user_id=?""",
                (display_name, plan, role, row["portal_token_hash"], row["subscription_token_hash"], user_id),
            )
            # Existing plaintext is intentionally not recoverable through this
            # reconciliation path. Use issue_tokens() to rotate and deliver it.
            return {"user_id": user_id, "portal_token": None,
                    "subscription_token": None, "created": False}

    def issue_tokens(self, user_id: str, token_kind: str = "portal",
                     revoke_old: bool = True) -> dict:
        """Issue one or both user tokens and return plaintext exactly once.

        The returned values are for the caller's protected delivery path only.
        The database receives hashes. Portal issuance always replaces and
        immediately revokes the previous Portal token. Subscription issuance
        may explicitly retain one previous Subscription hash during a staged
        migration; that retained hash is never returned to the caller.
        """
        if token_kind not in {"portal", "subscription", "both"}:
            raise ControlPlaneError("invalid token kind")
        if not isinstance(revoke_old, bool):
            raise ControlPlaneError("revoke_old must be boolean")
        if revoke_old is not True and token_kind == "portal":
            raise Conflict("portal token issuance must revoke the previous token")
        kinds = ("portal", "subscription") if token_kind == "both" else (token_kind,)
        issued = {kind: new_token() for kind in kinds}
        with self.connect() as db:
            user = db.execute(
                """SELECT user_id,display_name,plan,role,status,
                          subscription_token_hash,subscription_token_legacy_hash
                   FROM users WHERE user_id=?""",
                (user_id,),
            ).fetchone()
            if user is None:
                raise NotFound("user not found")
            if user["status"] != "active":
                raise Conflict("user is not active")
            retaining_subscription = not revoke_old and "subscription" in kinds
            if retaining_subscription and user["subscription_token_legacy_hash"]:
                raise Conflict("a legacy Subscription hash is already retained")
            assignments = []
            parameters: list[object] = []
            if "portal" in issued:
                assignments.append("portal_token_hash=?")
                parameters.append(token_hash(issued["portal"]))
            if "subscription" in issued:
                if retaining_subscription:
                    assignments.extend([
                        "subscription_token_legacy_hash=?",
                        "subscription_token_hash=?",
                    ])
                    parameters.extend([
                        user["subscription_token_hash"],
                        token_hash(issued["subscription"]),
                    ])
                else:
                    assignments.extend([
                        "subscription_token_hash=?",
                        "subscription_token_legacy_hash=NULL",
                    ])
                    parameters.append(token_hash(issued["subscription"]))
            db.execute(
                f"UPDATE users SET {', '.join(assignments)} WHERE user_id=?",
                (*parameters, user_id),
            )
            for kind in issued:
                db.execute(
                    """INSERT INTO credential_migration_events(
                           event_id,user_id,subject_kind,subject_ref,state,observed_at,source,detail,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (f"mig_{uuid.uuid4().hex}", user_id, f"{kind}_token", "current", "issued",
                     utc_now(), "admin-token-issuance", "hash-only Control Plane issuance", utc_now()),
                )
        retained_kinds = ["subscription"] if retaining_subscription else []
        revoked_kinds = [kind for kind in kinds if kind not in retained_kinds]
        return {
            "ok": True,
            "user_id": user["user_id"],
            "display_name": user["display_name"],
            "plan": user["plan"],
            "role": user["role"],
            "token_kind": token_kind,
            "issued_at": utc_now(),
            "revoked_previous": not retained_kinds,
            "revoked_previous_kinds": revoked_kinds,
            "retained_previous_kinds": retained_kinds,
            "tokens": issued,
        }

    def revoke_legacy_subscription(self, user_id: str, confirmation: str | None = None) -> dict:
        """Explicitly revoke the one retained legacy Subscription hash."""
        if confirmation != f"REVOKE LEGACY {user_id}":
            raise Conflict("legacy revocation confirmation required")
        with self.connect() as db:
            user = db.execute(
                "SELECT user_id,subscription_token_legacy_hash FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if user is None:
                raise NotFound("user not found")
            if not user["subscription_token_legacy_hash"]:
                raise Conflict("no retained legacy Subscription hash")
            latest_migration = db.execute(
                """SELECT state FROM credential_migration_events
                   WHERE user_id=? AND subject_kind='subscription_token'
                     AND subject_ref='current'
                   ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
            if latest_migration is None or latest_migration["state"] != "confirmed":
                raise Conflict("legacy revocation requires confirmed migration")
            db.execute(
                "UPDATE users SET subscription_token_legacy_hash=NULL WHERE user_id=?",
                (user_id,),
            )
            db.execute(
                """INSERT INTO credential_migration_events(
                       event_id,user_id,subject_kind,subject_ref,state,observed_at,source,detail,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (f"mig_{uuid.uuid4().hex}", user_id, "legacy_subscription", "current", "retired",
                 utc_now(), "admin-legacy-revoke", "retained legacy hash revoked", utc_now()),
            )
        return {"ok": True, "user_id": user_id, "revoked": "subscription_legacy"}

    def admin_user_detail(self, user_id: str) -> dict:
        """Return a non-secret OWNER view for one User."""
        now = utc_now()
        with self.connect() as db:
            user = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if user is None:
                raise NotFound("user not found")
            access_rows = [
                self._access_at(db, user, row[0], now)
                for row in db.execute("SELECT node_id FROM nodes ORDER BY node_id").fetchall()
            ]
            cycle = self._current_cycle(db, user_id)
            cycle_view = {
                "cycle_id": cycle["cycle_id"], "cycle_key": cycle["cycle_key"],
                "starts_at": cycle["starts_at"], "ends_at": cycle["ends_at"],
                "cycle_kind": cycle["cycle_kind"], "timezone": cycle["timezone"],
                "policy_id": cycle["policy_id"], "commercial_applies": bool(cycle["commercial_applies"]),
                "baseline_at": cycle["baseline_at"],
            } if cycle else None
            entitlements = [dict(row) for row in db.execute(
                """SELECT entitlement_id,pool_id,plan,allowance_bytes,effective_from,effective_to,status
                   FROM entitlements WHERE user_id=? ORDER BY effective_from,pool_id""",
                (user_id,),
            )]
            credential_rows = db.execute(
                """SELECT credential_id,node_id,runtime_family,protocol,credential_kind,status,created_at
                   FROM credentials WHERE user_id=? ORDER BY node_id,credential_id""",
                (user_id,),
            ).fetchall()
            credentials = [dict(row) for row in credential_rows]
            access_by_node = {item["node_id"]: item for item in access_rows}
            usage_by_node = []
            usage_by_pool: dict[str, int | None] = {}
            for credential_node in sorted({row["node_id"] for row in credential_rows}):
                access = access_by_node.get(credential_node)
                pool_id = access["pool_id"] if access else self._pool_at(db, credential_node, now)
                coverage = self._latest_coverage_record(db, credential_node)
                coverage_status = self._coverage_status(coverage) or "unknown"
                hashes = [row[0] for row in db.execute(
                    """SELECT runtime_ref_hash FROM credentials
                       WHERE user_id=? AND node_id=? AND status='active'""",
                    (user_id, credential_node),
                )]
                missing = 0
                unresolved = 0
                if cycle and hashes:
                    placeholders = ",".join("?" for _ in hashes)
                    where = ["o.node_id=?", f"o.runtime_ref_hash IN ({placeholders})"]
                    params: list[object] = [credential_node, *hashes]
                    if cycle["starts_at"] is not None:
                        where.append("o.observed_at >= ?")
                        params.append(cycle["starts_at"])
                    if cycle["ends_at"] is not None:
                        where.append("o.observed_at < ?")
                        params.append(cycle["ends_at"])
                    observed = db.execute(
                        f"SELECT COUNT(DISTINCT o.runtime_ref_hash) FROM usage_observations o WHERE {' AND '.join(where)}",
                        params,
                    ).fetchone()[0]
                    missing = max(0, len(hashes) - int(observed))
                    unresolved = int(db.execute(
                        f"""SELECT COUNT(*) FROM usage_ledger l JOIN usage_observations o
                            ON o.observation_id=l.observation_id
                            WHERE l.user_id=? AND l.node_id=? AND l.cycle_id=?
                              AND l.attribution_status!='attributed'
                              AND o.runtime_ref_hash IN ({placeholders})""",
                        (user_id, credential_node, cycle["cycle_id"], *hashes),
                    ).fetchone()[0])
                known = bool(cycle and coverage_status == "available" and missing == 0 and unresolved == 0)
                used = None
                if known:
                    used = int(db.execute(
                        """SELECT COALESCE(SUM(delta_uplink_bytes+delta_downlink_bytes),0)
                           FROM usage_ledger WHERE user_id=? AND node_id=? AND cycle_id=?
                             AND attribution_status='attributed'""",
                        (user_id, credential_node, cycle["cycle_id"]),
                    ).fetchone()[0])
                usage_by_node.append({
                    "node_id": credential_node,
                    "node_name": access["node_name"] if access else credential_node,
                    "pool_id": pool_id,
                    "allocation_role": access["allocation_role"] if access else "deny",
                    "access_decision": access["decision"] if access else "deny",
                    "coverage_status": coverage_status,
                    "coverage_observed_at": coverage["observed_at"] if coverage else None,
                    "coverage_age_seconds": self._coverage_age_seconds(coverage),
                    "used_bytes": used,
                    "unresolved_observations": unresolved,
                    "missing_counters": missing,
                })
                if pool_id:
                    current = usage_by_pool.get(pool_id)
                    usage_by_pool[pool_id] = used if current is None and pool_id not in usage_by_pool else (
                        None if current is None or used is None else current + used
                    )
            entries = []
            for row in db.execute(
                """SELECT entry_id,node_id,pool_id,credential_id,protocol,minimum_plan,
                          projection_status,enabled,created_at,uri
                   FROM subscription_entries WHERE user_id=? ORDER BY entry_id""",
                (user_id,),
            ):
                entry = dict(row)
                entry["display_alias"] = alias_from_uri(entry.pop("uri"))
                entries.append(entry)
            projected_entries = [
                row for row in entries
                if row["enabled"] and row["projection_status"] == "current"
                and row["protocol"].lower() != "anytls"
                and (row["node_id"] is None or self._subscription_access_allowed(
                    access_by_node.get(row["node_id"]), row["protocol"]
                ))
            ]
            budgets = [dict(row) for row in db.execute(
                """SELECT budget_id,node_id,pool_id,provider_cycle_id,allowance_bytes,budget_kind,
                          status,reason,source,effective_from,effective_to
                   FROM operational_budgets WHERE user_id=? ORDER BY effective_from,budget_id""",
                (user_id,),
            )]
            migration = [dict(row) for row in db.execute(
                """SELECT subject_kind,subject_ref,state,observed_at,source,detail
                   FROM credential_migration_events WHERE user_id=?
                   ORDER BY created_at DESC,rowid DESC""",
                (user_id,),
            )]
            latest_migration = {}
            for event in migration:
                latest_migration.setdefault(
                    (event["subject_kind"], event["subject_ref"]), event
                )
        return {
            "user_id": user["user_id"], "display_name": user["display_name"],
            "plan": user["plan"], "role": user["role"], "status": user["status"],
            "token_status": {"portal": "hash_only", "subscription": "hash_only"},
            "customer_billing_cycle": cycle_view,
            "default_entitled_pools": sorted(PLAN_POOL_ENTITLEMENTS.get(user["plan"], frozenset())),
            "entitlements": entitlements,
            "effective_access": access_rows,
            "credentials": credentials,
            "usage_by_node": usage_by_node,
            "usage_by_pool_bytes": usage_by_pool,
            "usage_bytes": sum(value for value in usage_by_pool.values() if value is not None)
                if all(value is not None for value in usage_by_pool.values()) else None,
            "subscription_status": "available" if projected_entries else "not_configured",
            "subscription_entry_count": len(projected_entries),
            "subscription_pool_ids": sorted({row["pool_id"] for row in projected_entries if row["pool_id"]}),
            "subscription_protocols": sorted({row["protocol"].lower() for row in projected_entries}),
            "subscription_anytls_count": sum(1 for row in entries if row["protocol"].lower() == "anytls"),
            "subscription_legacy_retained": bool(user["subscription_token_legacy_hash"]),
            "subscription_entries": entries,
            "operational_budgets": budgets,
            "migration_events": migration,
            "migration_latest": list(latest_migration.values()),
        }

    def admin_users(self) -> list[dict]:
        """Return non-secret user metadata for the Admin operator."""
        with self.connect() as db:
            user_ids = [row[0] for row in db.execute(
                "SELECT user_id FROM users ORDER BY user_id"
            ).fetchall()]
        details = [self.admin_user_detail(user_id) for user_id in user_ids]
        return [
            {
                "user_id": detail["user_id"],
                "display_name": detail["display_name"],
                "plan": detail["plan"],
                "role": detail["role"],
                "status": detail["status"],
                "subscription_status": detail["subscription_status"],
                "subscription_entry_count": detail["subscription_entry_count"],
                "subscription_pool_ids": detail["subscription_pool_ids"],
                "subscription_protocols": detail["subscription_protocols"],
                "subscription_anytls_count": detail["subscription_anytls_count"],
                "subscription_legacy_retained": detail["subscription_legacy_retained"],
                "migration_latest": detail["migration_latest"],
            }
            for detail in details
        ]

    def create_cycle(self, user_id: str, cycle_key: str, starts_at: str | None, ends_at: str | None,
                     cycle_kind: str = "manual", cycle_timezone: str = CUSTOMER_CYCLE_TIMEZONE,
                     policy_id: str = "manual", commercial_applies: bool = False,
                     baseline_at: str | None = None, status: str = "active") -> str:
        cycle_id = f"cyc_{uuid.uuid4().hex}"
        now = utc_now()
        normalized_start = normalize_time(starts_at) if starts_at else None
        normalized_end = normalize_time(ends_at) if ends_at else None
        if ((starts_at and normalized_start is None)
                or (ends_at and normalized_end is None)):
            raise ControlPlaneError("cycle timestamps must be ISO-8601")
        with self.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is None:
                raise NotFound("user not found")
            if status == "active":
                db.execute("UPDATE billing_cycles SET status='closed' WHERE user_id=? AND status='active'", (user_id,))
            db.execute(
                """INSERT INTO billing_cycles(
                       cycle_id,user_id,cycle_key,starts_at,ends_at,status,created_at,
                       cycle_kind,timezone,policy_id,commercial_applies,baseline_at
                   ) VALUES (?,?,?,?,?,?,?, ?,?,?,?,?)""",
                (cycle_id, user_id, cycle_key, normalized_start, normalized_end, status, now,
                 cycle_kind, cycle_timezone, policy_id, int(commercial_applies),
                 normalize_time(baseline_at) if baseline_at else None),
            )
        return cycle_id

    @staticmethod
    def _ensure_customer_cycle(db: sqlite3.Connection, user_id: str, observed_at: str) -> str | None:
        window = customer_cycle_window(observed_at)
        if window is None:
            return None
        cycle_key, starts_at, ends_at, policy_id = window
        row = db.execute(
            "SELECT cycle_id FROM billing_cycles WHERE user_id=? AND cycle_key=?",
            (user_id, cycle_key),
        ).fetchone()
        if row:
            start = parse_time(starts_at)
            end = parse_time(ends_at)
            now = datetime.now(timezone.utc)
            status = "active" if start and end and start <= now < end else ("scheduled" if start and now < start else "closed")
            if status == "active":
                db.execute(
                    "UPDATE billing_cycles SET status='closed' WHERE user_id=? AND status='active' AND cycle_key<>?",
                    (user_id, cycle_key),
                )
            db.execute(
                """UPDATE billing_cycles
                   SET cycle_kind='customer',timezone=?,policy_id=?,commercial_applies=1,baseline_at=?,
                       starts_at=?,ends_at=?,status=?
                   WHERE cycle_id=?""",
                (CUSTOMER_CYCLE_TIMEZONE, policy_id, customer_cycle_baseline_utc(), starts_at, ends_at,
                 status, row[0]),
            )
            return row[0]
        cycle_id = f"cyc_{uuid.uuid4().hex}"
        start = parse_time(starts_at)
        end = parse_time(ends_at)
        now = datetime.now(timezone.utc)
        status = "active" if start and end and start <= now < end else ("scheduled" if start and now < start else "closed")
        if status == "active":
            db.execute(
                "UPDATE billing_cycles SET status='closed' WHERE user_id=? AND status='active'",
                (user_id,),
            )
        db.execute(
            """INSERT INTO billing_cycles(
                   cycle_id,user_id,cycle_key,starts_at,ends_at,status,created_at,
                   cycle_kind,timezone,policy_id,commercial_applies,baseline_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cycle_id, user_id, cycle_key, starts_at, ends_at, status, utc_now(),
             "customer", CUSTOMER_CYCLE_TIMEZONE, policy_id, 1, customer_cycle_baseline_utc()),
        )
        return cycle_id

    def reconcile_customer_cycles(self, user_ids: list[str]) -> dict:
        baseline = customer_cycle_baseline_utc()
        first_window = customer_cycle_window(CUSTOMER_CYCLE_BASELINE)
        if first_window is None:
            raise ControlPlaneError("customer cycle baseline is invalid")
        first_key, first_start, first_end, _ = first_window
        created = 0
        legacy = 0
        with self.connect() as db:
            for user_id in user_ids:
                if db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None:
                    raise NotFound("user not found")
                active = db.execute(
                    """SELECT cycle_id FROM billing_cycles
                       WHERE user_id=? AND status='active' ORDER BY created_at DESC LIMIT 1""",
                    (user_id,),
                ).fetchone()
                if active:
                    db.execute(
                        """UPDATE billing_cycles
                           SET cycle_key='legacy-pre-baseline', cycle_kind='legacy_pre_baseline',
                               timezone=?, policy_id='legacy', commercial_applies=0, baseline_at=?,
                               ends_at=?
                           WHERE cycle_id=?""",
                        (CUSTOMER_CYCLE_TIMEZONE, baseline, baseline, active[0]),
                    )
                    legacy += 1
                else:
                    legacy_id = f"cyc_{uuid.uuid4().hex}"
                    db.execute(
                        """INSERT INTO billing_cycles(
                               cycle_id,user_id,cycle_key,starts_at,ends_at,status,created_at,
                               cycle_kind,timezone,policy_id,commercial_applies,baseline_at
                           ) VALUES (?,?,?,?,?,'active',?,?,?,?,?,?)""",
                        (legacy_id, user_id, "legacy-pre-baseline", None, baseline, utc_now(),
                         "legacy_pre_baseline", CUSTOMER_CYCLE_TIMEZONE, "legacy", 0, baseline),
                    )
                    legacy += 1
                row = db.execute(
                    "SELECT cycle_id FROM billing_cycles WHERE user_id=? AND cycle_key=?",
                    (user_id, first_key),
                ).fetchone()
                if row is None:
                    cycle_id = f"cyc_{uuid.uuid4().hex}"
                    db.execute(
                        """INSERT INTO billing_cycles(
                               cycle_id,user_id,cycle_key,starts_at,ends_at,status,created_at,
                               cycle_kind,timezone,policy_id,commercial_applies,baseline_at
                           ) VALUES (?,?,?,?,?,'scheduled',?,?,?,?,?,?)""",
                        (cycle_id, user_id, first_key, first_start, first_end, utc_now(),
                         "customer", CUSTOMER_CYCLE_TIMEZONE, CUSTOMER_CYCLE_POLICY_ID, 1, baseline),
                    )
                    created += 1
        return {"users": len(user_ids), "legacy_marked": legacy, "scheduled_created": created}

    @staticmethod
    def _entitlement_at(db: sqlite3.Connection, user_id: str, pool_id: str,
                        observed_at: str) -> sqlite3.Row | None:
        return db.execute(
            """SELECT entitlement_id,plan,allowance_bytes,effective_from,effective_to,status
               FROM entitlements
               WHERE user_id=? AND pool_id=? AND status IN ('active','superseded')
                 AND effective_from <= ? AND (effective_to IS NULL OR effective_to > ?)
               ORDER BY effective_from DESC,entitlement_id DESC LIMIT 1""",
            (user_id, pool_id, observed_at, observed_at),
        ).fetchone()

    def set_entitlement(self, user_id: str, pool_id: str, plan: str,
                        allowance_bytes: int | None, effective_from: str | None = None) -> str:
        if pool_id not in POOL_NAMES or plan not in PLAN_ORDER:
            raise ControlPlaneError("invalid pool or plan")
        if (allowance_bytes is not None
                and (isinstance(allowance_bytes, bool)
                     or not isinstance(allowance_bytes, int)
                     or allowance_bytes < 0)):
            raise ControlPlaneError("allowance_bytes must be null or a non-negative integer")
        starts = normalize_time(effective_from) if effective_from else utc_now()
        if starts is None:
            raise ControlPlaneError("effective_from must be ISO-8601")
        entitlement_id = f"ent_{uuid.uuid4().hex}"
        with self.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None:
                raise NotFound("user not found")
            if db.execute("SELECT 1 FROM resource_pools WHERE pool_id=?", (pool_id,)).fetchone() is None:
                raise NotFound("pool not found")
            db.execute(
                """UPDATE entitlements SET effective_to=?, status='superseded'
                   WHERE user_id=? AND pool_id=? AND status='active'
                     AND effective_from < ? AND (effective_to IS NULL OR effective_to > ?)""",
                (starts, user_id, pool_id, starts, starts),
            )
            db.execute(
                """INSERT INTO entitlements
                   (entitlement_id,user_id,pool_id,plan,allowance_bytes,effective_from,effective_to,status)
                   VALUES (?,?,?,?,?,?,NULL,'active')""",
                (entitlement_id, user_id, pool_id, plan, allowance_bytes, starts),
            )
        return entitlement_id

    def add_credential(self, node_id: str, runtime_ref_hash: str, runtime_family: str,
                       protocol: str, user_id: str | None = None,
                       credential_kind: str = "managed") -> str:
        if len(runtime_ref_hash) != 64 or any(c not in "0123456789abcdef" for c in runtime_ref_hash.lower()):
            raise ControlPlaneError("runtime_ref_hash must be a SHA-256 hex string")
        if credential_kind not in {"legacy", "managed", "standby"}:
            raise ControlPlaneError("invalid credential kind")
        credential_id = f"cred_{uuid.uuid4().hex}"
        with self.connect() as db:
            if db.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone() is None:
                raise NotFound("node not found")
            if user_id and db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None:
                raise NotFound("user not found")
            db.execute(
                """INSERT INTO credentials
                   (credential_id,node_id,user_id,runtime_ref_hash,runtime_family,protocol,credential_kind,status,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (credential_id, node_id, user_id, runtime_ref_hash.lower(), runtime_family, protocol,
                 credential_kind, "active", utc_now()),
            )
            if user_id:
                db.execute(
                    """INSERT INTO credential_migration_events(
                           event_id,user_id,subject_kind,subject_ref,state,observed_at,source,detail,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (f"mig_{uuid.uuid4().hex}", user_id, "runtime_credential", credential_id, "issued",
                     utc_now(), "credential-provisioning", f"{credential_kind} credential registered", utc_now()),
                )
        return credential_id

    def map_credential(self, credential_id: str, user_id: str) -> None:
        with self.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None:
                raise NotFound("user not found")
            cur = db.execute("UPDATE credentials SET user_id=? WHERE credential_id=?", (user_id, credential_id))
            if cur.rowcount != 1:
                raise NotFound("credential not found")

    def set_coverage(self, node_id: str, source: str, status: str, detail: str, observed_at: str | None = None) -> str:
        if status not in {"available", "gap", "stale", "unknown"}:
            raise ControlPlaneError("invalid coverage status")
        sample_time = normalize_time(observed_at) if observed_at else utc_now()
        if sample_time is None:
            raise ControlPlaneError("observed_at must be ISO-8601")
        coverage_id = f"cov_{uuid.uuid4().hex}"
        with self.connect() as db:
            if db.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone() is None:
                raise NotFound("node not found")
            db.execute(
                "INSERT INTO coverage_events(coverage_id,node_id,source,status,observed_at,detail) VALUES (?,?,?,?,?,?)",
                (coverage_id, node_id, source, status, sample_time, detail[:500]),
            )
        return coverage_id

    def _pool_at(self, db: sqlite3.Connection, node_id: str, observed_at: str) -> str | None:
        row = db.execute(
            """SELECT pool_id FROM node_pool_memberships
               WHERE node_id=? AND effective_from <= ? AND (effective_to IS NULL OR effective_to > ?)
                 AND status IN ('active','superseded') ORDER BY effective_from DESC LIMIT 1""",
            (node_id, observed_at, observed_at),
        ).fetchone()
        return row[0] if row else None

    def _access_at(self, db: sqlite3.Connection, user: sqlite3.Row,
                   node_id: str, observed_at: str) -> dict:
        node = db.execute(
            "SELECT node_id,display_name,status,qualification FROM nodes WHERE node_id=?",
            (node_id,),
        ).fetchone()
        if node is None:
            raise NotFound("node not found")
        pool_id = self._pool_at(db, node_id, observed_at)
        capability = db.execute(
            """SELECT access_status,subscription_status,metering_status,quota_status,
                      supported_protocols,source,observed_at,detail
               FROM node_capabilities WHERE node_id=?""",
            (node_id,),
        ).fetchone()
        override = db.execute(
            """SELECT decision,allocation_role,reason,source,effective_from,effective_to
               FROM user_access_overrides
               WHERE user_id=? AND node_id=? AND status IN ('active','superseded')
                 AND effective_from <= ? AND (effective_to IS NULL OR effective_to > ?)
               ORDER BY effective_from DESC LIMIT 1""",
            (user["user_id"], node_id, observed_at, observed_at),
        ).fetchone()
        plan_allowed = pool_id in PLAN_POOL_ENTITLEMENTS.get(user["plan"], frozenset())
        node_available = node["status"] == "active"
        capability_access = capability is None or capability["access_status"] == "allowed"
        if override is not None:
            decision = override["decision"]
            allocation_role = override["allocation_role"]
            reason = override["reason"]
            source = override["source"]
            decision_source = "user_override"
            # A User override can grant/restrict product access, but cannot
            # make an inactive or explicitly unavailable Node operational.
            if decision == "allow" and (not node_available or not capability_access):
                decision = "deny"
                allocation_role = "deny"
                reason = "node_not_operational"
        else:
            decision = "allow" if plan_allowed and node_available and capability_access else "deny"
            allocation_role = "default" if decision == "allow" else "deny"
            if not node_available:
                reason = "node_not_active"
            elif not capability_access:
                reason = "node_access_not_allowed"
            elif not plan_allowed:
                reason = "plan_default_not_entitled"
            else:
                reason = "plan_default"
            source = "plan_default"
            decision_source = "plan_default"
        protocols = []
        if capability is not None:
            try:
                parsed_protocols = json.loads(capability["supported_protocols"])
                if isinstance(parsed_protocols, list):
                    protocols = [str(value) for value in parsed_protocols]
            except (TypeError, json.JSONDecodeError):
                protocols = []
        return {
            "node_id": node["node_id"],
            "node_name": node["display_name"],
            "node_status": node["status"],
            "qualification": node["qualification"],
            "pool_id": pool_id,
            "decision": decision,
            "allocation_role": allocation_role,
            "reason": reason,
            "source": source,
            "decision_source": decision_source,
            "plan_default_allowed": plan_allowed,
            "capability": {
                "access_status": capability["access_status"] if capability else "allowed",
                "subscription_status": capability["subscription_status"] if capability else "allowed",
                "metering_status": capability["metering_status"] if capability else "unknown",
                "quota_status": capability["quota_status"] if capability else "unavailable",
                "supported_protocols": protocols or ["vless"],
                "source": capability["source"] if capability else "node_seed",
                "observed_at": capability["observed_at"] if capability else None,
                "detail": capability["detail"] if capability else "",
            },
        }

    def effective_access(self, user_id: str, observed_at: str | None = None) -> list[dict]:
        sample_time = normalize_time(observed_at) if observed_at else utc_now()
        if sample_time is None:
            raise ControlPlaneError("observed_at must be ISO-8601")
        with self.connect() as db:
            user = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if user is None:
                raise NotFound("user not found")
            return [self._access_at(db, user, row[0], sample_time)
                    for row in db.execute("SELECT node_id FROM nodes ORDER BY node_id").fetchall()]

    def _cycle_for(self, db: sqlite3.Connection, user_id: str, observed_at: str) -> str | None:
        normalized = normalize_time(observed_at)
        if normalized is None:
            return None
        customer_window = customer_cycle_window(normalized)
        if customer_window is not None:
            return self._ensure_customer_cycle(db, user_id, normalized)
        row = db.execute(
            """SELECT cycle_id FROM billing_cycles
               WHERE user_id=? AND cycle_kind IN ('legacy_pre_baseline','manual')
                 AND (starts_at IS NULL OR starts_at <= ?)
                 AND (ends_at IS NULL OR ends_at > ?)
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, normalized, normalized),
        ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _provider_cycle_for(db: sqlite3.Connection, node_id: str, observed_at: str) -> str | None:
        row = db.execute(
            """SELECT c.provider_cycle_id
               FROM provider_resource_cycles c
               JOIN infrastructure_resources r ON r.resource_id=c.resource_id
               WHERE r.node_id=?
                 AND c.starts_at IS NOT NULL AND c.starts_at <= ?
                 AND (c.ends_at IS NULL OR c.ends_at > ?)
               ORDER BY c.starts_at DESC LIMIT 1""",
            (node_id, observed_at, observed_at),
        ).fetchone()
        return row[0] if row else None

    def _current_cycle(self, db: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
        now = utc_now()
        window = customer_cycle_window(now)
        if window is not None:
            cycle_id = self._ensure_customer_cycle(db, user_id, now)
            return db.execute(
                "SELECT * FROM billing_cycles WHERE cycle_id=?", (cycle_id,)
            ).fetchone()
        return db.execute(
            """SELECT * FROM billing_cycles
               WHERE user_id=? AND cycle_kind IN ('legacy_pre_baseline','manual') AND status='active'
               ORDER BY created_at DESC LIMIT 1""",
            (user_id,),
        ).fetchone()

    def ingest_observations(self, node_id: str, source: str, counter_epoch: str,
                            observations: list[dict], observed_at: str | None = None) -> dict:
        if not observations:
            raise ControlPlaneError("observations must not be empty")
        sample_time = normalize_time(observed_at) if observed_at else utc_now()
        if not source.strip():
            raise ControlPlaneError("source must not be empty")
        if not counter_epoch.strip():
            raise ControlPlaneError("counter_epoch must not be empty")
        if sample_time is None:
            raise ControlPlaneError("observed_at must be ISO-8601")
        inserted = 0
        duplicates = 0
        unresolved = 0
        with self.connect() as db:
            if db.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone() is None:
                raise NotFound("node not found")
            for item in observations:
                if not isinstance(item, dict):
                    raise ControlPlaneError("observation is invalid")
                if "uplink_bytes" not in item or "downlink_bytes" not in item:
                    raise ControlPlaneError("observation counters are incomplete")
                runtime_hash = str(item.get("runtime_ref_hash", "")).lower()
                if len(runtime_hash) != 64 or any(c not in "0123456789abcdef" for c in runtime_hash):
                    raise ControlPlaneError("observation runtime_ref_hash is invalid")
                up = item["uplink_bytes"]
                down = item["downlink_bytes"]
                if (isinstance(up, bool) or not isinstance(up, int)
                        or isinstance(down, bool) or not isinstance(down, int)):
                    raise ControlPlaneError("observation counters are invalid")
                if up < 0 or down < 0:
                    raise ControlPlaneError("counters cannot be negative")
                item_time = normalize_time(str(item.get("observed_at"))) if item.get("observed_at") else sample_time
                if item_time is None:
                    raise ControlPlaneError("observation observed_at must be ISO-8601")
                oid = str(item.get("observation_id") or hashlib.sha256(
                    f"{node_id}|{runtime_hash}|{counter_epoch}|{item_time}|{source}".encode()
                ).hexdigest())
                existing = db.execute(
                    """SELECT node_id,runtime_ref_hash,counter_epoch,observed_at,
                              uplink_bytes,downlink_bytes,source
                       FROM usage_observations WHERE observation_id=?""",
                    (oid,),
                ).fetchone()
                if existing:
                    same = (
                        existing[0] == node_id and existing[1] == runtime_hash
                        and existing[2] == counter_epoch and existing[3] == item_time
                        and existing[4] == up and existing[5] == down and existing[6] == source
                    )
                    if same:
                        duplicates += 1
                        continue
                    raise Conflict("observation_id conflicts with existing observation")
                natural = db.execute(
                    """SELECT observation_id,uplink_bytes,downlink_bytes
                       FROM usage_observations
                       WHERE node_id=? AND runtime_ref_hash=? AND counter_epoch=?
                         AND observed_at=? AND source=?""",
                    (node_id, runtime_hash, counter_epoch, item_time, source),
                ).fetchone()
                if natural:
                    if natural[1] == up and natural[2] == down:
                        duplicates += 1
                        continue
                    raise Conflict("observation natural key conflicts with existing observation")
                try:
                    db.execute(
                        """INSERT INTO usage_observations
                           (observation_id,node_id,runtime_ref_hash,counter_epoch,observed_at,
                            uplink_bytes,downlink_bytes,source,attribution_status,detail,created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (oid, node_id, runtime_hash, counter_epoch, item_time, up, down, source,
                         "pending", "", utc_now()),
                    )
                except sqlite3.IntegrityError as exc:
                    if "UNIQUE" not in str(exc):
                        raise
                    raise Conflict("observation conflicts with existing observation") from exc
                inserted += 1
                prev = db.execute(
                    """SELECT uplink_bytes,downlink_bytes FROM usage_observations
                       WHERE node_id=? AND runtime_ref_hash=? AND counter_epoch=?
                         AND source=? AND observed_at < ? ORDER BY observed_at DESC LIMIT 1""",
                    (node_id, runtime_hash, counter_epoch, source, item_time),
                ).fetchone()
                following = db.execute(
                    """SELECT observation_id FROM usage_observations
                       WHERE node_id=? AND runtime_ref_hash=? AND counter_epoch=?
                         AND source=? AND observed_at > ? ORDER BY observed_at ASC LIMIT 1""",
                    (node_id, runtime_hash, counter_epoch, source, item_time),
                ).fetchone()
                detail = "baseline"
                delta_up = delta_down = 0
                if following:
                    detail = "out_of_order_observation"
                elif prev:
                    if up < prev[0] or down < prev[1]:
                        detail = "counter_reset_or_non_monotonic"
                    else:
                        detail = "delta"
                        delta_up = up - prev[0]
                        delta_down = down - prev[1]
                cred = db.execute(
                    "SELECT credential_id,user_id FROM credentials WHERE node_id=? AND runtime_ref_hash=? AND status='active'",
                    (node_id, runtime_hash),
                ).fetchone()
                user_id = cred[1] if cred else None
                pool_id = self._pool_at(db, node_id, item_time)
                cycle_id = self._cycle_for(db, user_id, item_time) if user_id else None
                provider_cycle_id = self._provider_cycle_for(db, node_id, item_time)
                status = "attributed" if user_id and pool_id and cycle_id else "unresolved"
                if status == "unresolved":
                    unresolved += 1
                db.execute(
                    "UPDATE usage_observations SET attribution_status=?, detail=? WHERE observation_id=?",
                    (status, detail, oid),
                )
                db.execute(
                    """INSERT INTO usage_ledger
                       (ledger_id,observation_id,user_id,node_id,pool_id,cycle_id,observed_at,
                        provider_cycle_id,delta_uplink_bytes,delta_downlink_bytes,attribution_status,detail)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"led_{uuid.uuid4().hex}", oid, user_id, node_id, pool_id, cycle_id,
                     item_time, provider_cycle_id, delta_up, delta_down, status, detail),
                )
            db.execute(
                "INSERT INTO coverage_events(coverage_id,node_id,source,status,observed_at,detail) VALUES (?,?,?,?,?,?)",
                (f"cov_{uuid.uuid4().hex}", node_id, source, "available", sample_time,
                 f"ingested={inserted};duplicates={duplicates};unresolved={unresolved}"),
            )
        return {"inserted": inserted, "duplicates": duplicates, "unresolved": unresolved}

    def _latest_coverage_record(self, db: sqlite3.Connection, node_id: str) -> sqlite3.Row | None:
        row = db.execute(
            """SELECT status,observed_at,source,detail FROM coverage_events
               WHERE node_id=? ORDER BY observed_at DESC LIMIT 1""", (node_id,)
        ).fetchone()
        return row

    def _coverage_status(self, row: sqlite3.Row | None) -> str | None:
        if row is None:
            return None
        status = row["status"]
        if status != "available":
            return status
        observed = parse_time(row["observed_at"])
        if observed is None:
            return "unknown"
        age = (datetime.now(timezone.utc) - observed).total_seconds()
        return "stale" if age > self.coverage_max_age_seconds else "available"

    def _latest_coverage(self, db: sqlite3.Connection, node_id: str) -> str | None:
        return self._coverage_status(self._latest_coverage_record(db, node_id))

    @staticmethod
    def _coverage_age_seconds(row: sqlite3.Row | None) -> int | None:
        if row is None:
            return None
        observed = parse_time(row["observed_at"])
        if observed is None:
            return None
        return max(0, int((datetime.now(timezone.utc) - observed).total_seconds()))

    @staticmethod
    def _subscription_access_allowed(access: dict | None, protocol: str) -> bool:
        if access is None or access.get("decision") != "allow":
            return False
        capability = access.get("capability") or {}
        return (
            capability.get("subscription_status") == "allowed"
            and protocol.lower() in {str(value).lower() for value in capability.get("supported_protocols", [])}
        )

    def _active_pool_nodes(self, db: sqlite3.Connection, pool_id: str,
                           observed_at: str | None = None) -> list[str]:
        when = observed_at or utc_now()
        rows = db.execute(
            """SELECT node_id FROM node_pool_memberships
               WHERE pool_id=? AND status IN ('active','superseded') AND effective_from <= ?
                 AND (effective_to IS NULL OR effective_to > ?)""", (pool_id, when, when)
        ).fetchall()
        return [r[0] for r in rows]

    @staticmethod
    def _display_bytes(value: int | None) -> int | None:
        return None if value is None else int(value)

    def user_view(self, token: str) -> dict:
        user = self._user_by_token(token)
        with self.connect() as db:
            now = utc_now()
            cycle = self._current_cycle(db, user["user_id"])
            cycle_id = cycle["cycle_id"] if cycle else None
            cycle_view = {
                "cycle_id": cycle["cycle_id"], "cycle_key": cycle["cycle_key"],
                "starts_at": cycle["starts_at"], "ends_at": cycle["ends_at"],
                "cycle_kind": cycle["cycle_kind"], "timezone": cycle["timezone"],
                "policy_id": cycle["policy_id"],
                "commercial_applies": bool(cycle["commercial_applies"]),
                "baseline_at": cycle["baseline_at"],
            } if cycle else None
            pools = []
            unknown_pool = False
            for pool_id in POOL_NAMES:
                nodes = self._active_pool_nodes(db, pool_id, now)
                access_by_node = {
                    node_id: self._access_at(db, user, node_id, now)
                    for node_id in nodes
                }
                allowed_nodes = [
                    node_id for node_id in nodes
                    if access_by_node[node_id]["decision"] == "allow"
                ]
                allowance_row = self._entitlement_at(db, user["user_id"], pool_id, now)
                has_entitlement = allowance_row is not None
                if allowed_nodes:
                    placeholders = ",".join("?" for _ in nodes)
                    relevant_rows = db.execute(
                        f"SELECT DISTINCT node_id FROM credentials WHERE user_id=? AND status='active' AND node_id IN ({','.join('?' for _ in allowed_nodes)})",
                        (user["user_id"], *allowed_nodes),
                    ).fetchall()
                    relevant_nodes = [r[0] for r in relevant_rows]
                else:
                    relevant_nodes = []
                # A plan entitlement is an eligibility/default, not evidence
                # that a per-user runtime credential is configured. Until a
                # credential exists this pool has no customer observation and
                # is therefore not applicable to the current projection. An
                # unmapped runtime identity is different: it is a coverage
                # gap, not an actual zero.
                unmapped_runtime = False
                if nodes:
                    unmapped_runtime = db.execute(
                        f"""SELECT 1 FROM credentials
                            WHERE status='active' AND user_id IS NULL
                              AND node_id IN ({','.join('?' for _ in nodes)}) LIMIT 1""",
                        nodes,
                    ).fetchone() is not None
                not_applicable = not relevant_nodes and not has_entitlement and not unmapped_runtime
                unresolved = 0
                missing_counters = 0
                usage_by_node = []
                for node_id in relevant_nodes:
                    coverage_row = self._latest_coverage_record(db, node_id)
                    coverage_status = self._coverage_status(coverage_row) or "unknown"
                    credential_hashes = db.execute(
                        """SELECT runtime_ref_hash FROM credentials
                           WHERE user_id=? AND node_id=? AND status='active'""",
                        (user["user_id"], node_id),
                    ).fetchall()
                    hashes = [row[0] for row in credential_hashes]
                    node_missing = 0
                    node_unresolved = 0
                    if cycle_id and hashes:
                        hash_placeholders = ",".join("?" for _ in hashes)
                        observation_where = [
                            "o.node_id=?",
                            f"o.runtime_ref_hash IN ({hash_placeholders})",
                        ]
                        observation_params: list[object] = [node_id, *hashes]
                        if cycle["starts_at"] is not None:
                            observation_where.append("o.observed_at >= ?")
                            observation_params.append(cycle["starts_at"])
                        if cycle["ends_at"] is not None:
                            observation_where.append("o.observed_at < ?")
                            observation_params.append(cycle["ends_at"])
                        observed_hashes = db.execute(
                            f"SELECT COUNT(DISTINCT o.runtime_ref_hash) FROM usage_observations o WHERE {' AND '.join(observation_where)}",
                            observation_params,
                        ).fetchone()[0]
                        node_missing = max(0, len(hashes) - int(observed_hashes))
                        node_unresolved = db.execute(
                            f"""SELECT COUNT(*)
                                FROM usage_ledger l JOIN usage_observations o ON o.observation_id=l.observation_id
                                WHERE l.user_id=? AND l.node_id=? AND l.cycle_id=?
                                  AND l.attribution_status!='attributed'
                                  AND o.runtime_ref_hash IN ({hash_placeholders})""",
                            (user["user_id"], node_id, cycle_id, *hashes),
                        ).fetchone()[0]
                    node_known = bool(cycle_id and coverage_status == "available"
                                      and node_missing == 0 and node_unresolved == 0)
                    node_used = None
                    if node_known:
                        node_used = int(db.execute(
                            """SELECT COALESCE(SUM(delta_uplink_bytes+delta_downlink_bytes),0)
                               FROM usage_ledger WHERE user_id=? AND node_id=? AND pool_id=? AND cycle_id=?
                                 AND attribution_status='attributed'""",
                            (user["user_id"], node_id, pool_id, cycle_id),
                        ).fetchone()[0])
                    unresolved += int(node_unresolved)
                    missing_counters += int(node_missing)
                    access = access_by_node[node_id]
                    usage_by_node.append({
                        "node_id": node_id,
                        "node_name": access["node_name"],
                        "allocation_role": access["allocation_role"],
                        "coverage_status": coverage_status,
                        "coverage_observed_at": coverage_row["observed_at"] if coverage_row else None,
                        "coverage_age_seconds": self._coverage_age_seconds(coverage_row),
                        "used_bytes": node_used,
                        "unresolved_observations": int(node_unresolved),
                        "missing_counters": int(node_missing),
                    })
                coverage_known = not_applicable or (
                    bool(relevant_nodes) and all(item["used_bytes"] is not None for item in usage_by_node)
                )
                used = None if not relevant_nodes else (
                    sum(item["used_bytes"] or 0 for item in usage_by_node) if coverage_known else None
                )
                allowance = (
                    int(allowance_row[0])
                    if cycle and bool(cycle["commercial_applies"])
                    and allowance_row and allowance_row[0] is not None
                    else None
                )
                remaining = allowance - used if allowance is not None and used is not None else None
                if not_applicable:
                    used = 0
                    remaining = allowance
                coverage_status = "not_applicable" if not_applicable else ("available" if coverage_known and cycle_id else "unknown")
                if coverage_status == "unknown":
                    unknown_pool = True
                pools.append({
                    "pool_id": pool_id,
                    "used_bytes": self._display_bytes(used),
                    "allowance_bytes": allowance,
                    "remaining_bytes": remaining,
                    "coverage_status": coverage_status,
                    "unresolved_observations": unresolved,
                    "missing_counters": missing_counters,
                    "nodes": relevant_nodes,
                    "usage_by_node": usage_by_node,
                })
            total = None if unknown_pool else sum(p["used_bytes"] or 0 for p in pools)
            upgrade = db.execute(
                """SELECT request_id,to_plan,status,requested_at FROM upgrade_requests
                   WHERE user_id=? ORDER BY requested_at DESC LIMIT 1""", (user["user_id"],)
            ).fetchone()
            subscription_entries = db.execute(
                """SELECT node_id,protocol FROM subscription_entries
                   WHERE user_id=? AND enabled=1 AND projection_status='current'
                     AND lower(protocol)!='anytls'""",
                (user["user_id"],),
            ).fetchall()
            subscription_count = sum(
                1 for item in subscription_entries
                if item["node_id"] is None
                or self._subscription_access_allowed(
                    self._access_at(db, user, item["node_id"], now), item["protocol"]
                )
            )
        return {
            "user_id": user["user_id"],
            "display_name": user["display_name"],
            "role": user["role"],
            "plan": user["plan"],
            "subscription_status": "available" if subscription_count else "not_configured",
            # A hash-only Control Plane cannot reconstruct the current
            # Subscription token. The URL is delivered out-of-band by the
            # Admin operator and is intentionally absent from this response.
            "subscription_url": None,
            "customer_billing_cycle": cycle_view,
            "pools": pools,
            "total_usage_bytes": total,
            "latest_upgrade_request": dict(upgrade) if upgrade else None,
        }

    def subscription(self, token: str, token_kind: str = "subscription") -> str:
        if token_kind != "subscription":
            raise Unauthorized("subscription token required")
        user = self._user_by_subscription_token(token)
        with self.connect() as db:
            rows = db.execute(
                """SELECT node_id,protocol,uri FROM subscription_entries
                   WHERE user_id=? AND enabled=1 AND projection_status='current' AND lower(protocol)!='anytls'
                   AND ? >= CASE minimum_plan WHEN 'Free' THEN 0 WHEN 'Basic' THEN 1 ELSE 2 END
                   ORDER BY entry_id""",
                (user["user_id"], PLAN_ORDER[user["plan"]]),
            ).fetchall()
            access_by_node = {
                item["node_id"]: item
                for item in self.effective_access(user["user_id"])
            }
        projected_rows = [
            row for row in rows
            if row["node_id"] is None
            or self._subscription_access_allowed(access_by_node.get(row["node_id"]), row["protocol"])
        ]
        if not projected_rows:
            raise ServiceUnavailable("subscription is not configured")
        return base64.b64encode(("\n".join(r["uri"] for r in projected_rows) + "\n").encode()).decode() + "\n"

    def request_upgrade(self, token: str, target_plan: str) -> dict:
        user = self._user_by_token(token)
        if target_plan not in PLAN_ORDER:
            raise ControlPlaneError("invalid plan")
        if PLAN_ORDER[target_plan] <= PLAN_ORDER[user["plan"]]:
            raise Conflict("downgrade or duplicate plan request is not allowed")
        request_id = f"upg_{uuid.uuid4().hex}"
        with self.connect() as db:
            db.execute(
                "INSERT INTO upgrade_requests(request_id,user_id,from_plan,to_plan,status,requested_at,note) VALUES (?,?,?,?,?,?,?)",
                (request_id, user["user_id"], user["plan"], target_plan, "pending_manual_review", utc_now(),
                 "proration/effective-time/allowance semantics require manual admin review"),
            )
        return {"request_id": request_id, "status": "pending_manual_review", "to_plan": target_plan}

    def approve_upgrade(self, request_id: str, note: str = "manual admin approval") -> None:
        with self.connect() as db:
            request = db.execute(
                "SELECT user_id,to_plan,status FROM upgrade_requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if request is None:
                raise NotFound("upgrade request not found")
            if request["status"] != "pending_manual_review":
                raise Conflict("upgrade request is not pending")
            db.execute("UPDATE users SET plan=? WHERE user_id=?", (request["to_plan"], request["user_id"]))
            db.execute(
                "UPDATE upgrade_requests SET status='approved',reviewed_at=?,note=? WHERE request_id=?",
                (utc_now(), note[:500], request_id),
            )

    def add_subscription_entry(self, user_id: str, node_id: str | None, pool_id: str | None,
                               protocol: str, uri: str, minimum_plan: str = "Free",
                               credential_id: str | None = None,
                               projection_status: str = "current") -> str:
        if protocol.lower() == "anytls" or uri.lower().startswith("anytls://"):
            raise Conflict("AnyTLS is deferred until reliable per-user accounting")
        if minimum_plan not in PLAN_ORDER:
            raise ControlPlaneError("invalid minimum plan")
        if projection_status not in {"current", "staged", "legacy", "retired"}:
            raise ControlPlaneError("invalid subscription projection status")
        entry_id = f"sub_{uuid.uuid4().hex}"
        with self.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None:
                raise NotFound("user not found")
            if node_id and db.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone() is None:
                raise NotFound("node not found")
            if pool_id and pool_id not in POOL_NAMES:
                raise ControlPlaneError("invalid pool")
            if credential_id:
                credential = db.execute(
                    "SELECT user_id,node_id,protocol FROM credentials WHERE credential_id=?",
                    (credential_id,),
                ).fetchone()
                if (credential is None or credential[0] != user_id
                        or credential[1] != node_id or credential[2] != protocol):
                    raise ControlPlaneError("subscription credential does not match entry")
            if projection_status == "current" and node_id:
                user = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
                access = self._access_at(db, user, node_id, utc_now())
                if access["decision"] != "allow":
                    raise Conflict("subscription node is not currently allowed for user")
                if access["capability"]["subscription_status"] != "allowed":
                    raise Conflict("subscription capability is not currently available")
                if protocol.lower() not in access["capability"]["supported_protocols"]:
                    raise Conflict("subscription protocol is not supported by node")
            db.execute(
                """INSERT INTO subscription_entries
                   (entry_id,user_id,node_id,pool_id,credential_id,protocol,uri,minimum_plan,
                    projection_status,enabled,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,1,?)""",
                (entry_id, user_id, node_id, pool_id, credential_id, protocol, uri.strip(), minimum_plan,
                 projection_status, utc_now()),
            )
        return entry_id

    def rename_subscription_entries(self, entries: list[dict], source: str = "operator") -> dict:
        """Change only current VLESS display fragments in one transaction.

        The endpoint is intentionally entry-id based so an operator cannot
        accidentally rename a different User's projection. URI core fields
        are validated before any update, and duplicate aliases within one
        User are rejected to keep client-side node selection unambiguous.
        """

        if not isinstance(entries, list) or not entries or len(entries) > 1000:
            raise ControlPlaneError("subscription_alias_entries_invalid")
        normalized: list[tuple[str, str]] = []
        seen_entry_ids: set[str] = set()
        for item in entries:
            if not isinstance(item, dict):
                raise ControlPlaneError("subscription_alias_entry_invalid")
            entry_id = str(item.get("entry_id") or "").strip()
            alias = str(item.get("alias") or "").strip()
            if not entry_id or entry_id in seen_entry_ids:
                raise Conflict("subscription_alias_entry_duplicate")
            if len(entry_id) > 160 or len(alias) > 128:
                raise ControlPlaneError("subscription_alias_entry_invalid")
            seen_entry_ids.add(entry_id)
            normalized.append((entry_id, alias))
        source_value = str(source or "operator").strip()
        if not source_value or len(source_value) > 160 or any(char.isspace() for char in source_value):
            raise ControlPlaneError("subscription_alias_source_invalid")

        with self.connect() as db:
            staged: list[dict] = []
            user_ids: set[str] = set()
            for entry_id, alias in normalized:
                row = db.execute(
                    """SELECT entry_id,user_id,node_id,protocol,uri,projection_status,enabled
                       FROM subscription_entries WHERE entry_id=?""",
                    (entry_id,),
                ).fetchone()
                if row is None:
                    raise NotFound("subscription entry not found")
                if (row["projection_status"] != "current" or not row["enabled"]
                        or row["protocol"].lower() != "vless"):
                    raise Conflict("only current enabled VLESS entries can be renamed")
                try:
                    updated_uri = replace_uri_alias(row["uri"], alias)
                except ValueError as exc:
                    raise ControlPlaneError(str(exc)) from exc
                staged.append({
                    "entry_id": row["entry_id"], "user_id": row["user_id"],
                    "node_id": row["node_id"], "old_uri": row["uri"],
                    "new_uri": updated_uri, "alias": alias,
                })
                user_ids.add(row["user_id"])

            # The request may contain only some of a User's entries. Include
            # the untouched current entries in the collision check as well.
            aliases_by_user: dict[str, dict[str, str]] = {}
            for user_id in user_ids:
                aliases_by_user[user_id] = {}
                for row in db.execute(
                    """SELECT entry_id,uri FROM subscription_entries
                       WHERE user_id=? AND enabled=1 AND projection_status='current'
                         AND lower(protocol)='vless'""",
                    (user_id,),
                ):
                    aliases_by_user[user_id][row["entry_id"]] = alias_from_uri(row["uri"])
            for item in staged:
                aliases_by_user[item["user_id"]][item["entry_id"]] = alias_from_uri(item["new_uri"])
            for aliases in aliases_by_user.values():
                if len(aliases.values()) != len(set(aliases.values())):
                    raise Conflict("subscription_alias_collision")

            changed = 0
            result_entries = []
            for item in staged:
                changed_entry = item["old_uri"] != item["new_uri"]
                if changed_entry:
                    db.execute(
                        "UPDATE subscription_entries SET uri=? WHERE entry_id=?",
                        (item["new_uri"], item["entry_id"]),
                    )
                    changed += 1
                result_entries.append({
                    "entry_id": item["entry_id"], "user_id": item["user_id"],
                    "node_id": item["node_id"], "alias": item["alias"],
                    "changed": changed_entry,
                })
        return {
            "ok": True,
            "requested": len(normalized),
            "changed": changed,
            "unchanged": len(normalized) - changed,
            "source": source_value,
            "entries": result_entries,
        }

    def admin_overview(self) -> dict:
        with self.connect() as db:
            now = utc_now()
            nodes = []
            for row in db.execute("SELECT * FROM nodes ORDER BY node_id"):
                pool_id = self._pool_at(db, row["node_id"], now)
                pool = db.execute(
                    """SELECT pool_id,status FROM node_pool_memberships
                       WHERE node_id=? AND pool_id=? AND status='active'
                         AND effective_from <= ? AND (effective_to IS NULL OR effective_to > ?)
                       ORDER BY effective_from DESC LIMIT 1""",
                    (row["node_id"], pool_id, now, now),
                ).fetchone() if pool_id else None
                coverage = self._latest_coverage_record(db, row["node_id"])
                capability = db.execute(
                    """SELECT access_status,subscription_status,metering_status,quota_status,
                              supported_protocols,source,observed_at,detail
                       FROM node_capabilities WHERE node_id=?""", (row["node_id"],)
                ).fetchone()
                usage = db.execute(
                    """SELECT COALESCE(SUM(delta_uplink_bytes+delta_downlink_bytes),0)
                       FROM usage_ledger WHERE node_id=?""", (row["node_id"],)
                ).fetchone()[0]
                nodes.append({
                    "node_id": row["node_id"], "display_name": row["display_name"],
                    "status": row["status"], "qualification": row["qualification"],
                    "pool_id": pool[0] if pool else None, "pool_membership_status": pool[1] if pool else None,
                    "infrastructure_usage_bytes": int(usage),
                    "coverage_status": self._coverage_status(coverage) or "unknown",
                    "coverage_observed_at": coverage["observed_at"] if coverage else None,
                    "coverage_age_seconds": self._coverage_age_seconds(coverage),
                    "capability": {
                        "access_status": capability["access_status"] if capability else "allowed",
                        "subscription_status": capability["subscription_status"] if capability else "allowed",
                        "metering_status": capability["metering_status"] if capability else "unknown",
                        "quota_status": capability["quota_status"] if capability else "unavailable",
                        "supported_protocols": json.loads(capability["supported_protocols"])
                            if capability else ["vless"],
                        "source": capability["source"] if capability else "node_seed",
                        "observed_at": capability["observed_at"] if capability else None,
                        "detail": capability["detail"] if capability else "",
                    },
                })
            users = []
            for row in db.execute("SELECT user_id,display_name,plan,role,status FROM users ORDER BY user_id"):
                by_pool = {
                    p["pool_id"]: int(p["bytes"])
                    for p in db.execute(
                        """SELECT pool_id,COALESCE(SUM(delta_uplink_bytes+delta_downlink_bytes),0) AS bytes
                           FROM usage_ledger WHERE user_id=? AND attribution_status='attributed'
                           GROUP BY pool_id""", (row["user_id"],)
                    )
                }
                users.append({**dict(row), "usage_by_pool_bytes": by_pool,
                              "usage_bytes": sum(by_pool.values())})
            resources = [dict(row) for row in db.execute(
                """SELECT resource_id,provider_name,provider_instance_id,node_id,location,network_label,
                          asn,public_ipv4,cpu_cores,memory_gib,disk_gib,bandwidth_limit,transfer_limit,
                          local_timezone,timezone_source,contract_cycle,contract_amount,contract_currency,
                          next_due_local,next_due_timezone,next_due_source,resource_cycle_status,
                          resource_cycle_source
                   FROM infrastructure_resources ORDER BY resource_id"""
            )]
            provider_cycles = [dict(row) for row in db.execute(
                """SELECT provider_cycle_id,resource_id,cycle_key,starts_at,ends_at,timezone,status,
                          source,traffic_reset_authoritative
                   FROM provider_resource_cycles ORDER BY resource_id,cycle_key"""
            )]
            snapshots = [dict(row) for row in db.execute(
                """SELECT snapshot_id,resource_id,capacity_bytes,used_bytes,remaining_bytes,
                          resource_cycle_start,resource_cycle_end,next_reset_at,financial_cycle,
                          next_due_at,observed_at,source,status,detail
                   FROM provider_resource_snapshots ORDER BY resource_id,observed_at DESC"""
            )]
            latest_snapshots = {}
            for snapshot in snapshots:
                latest_snapshots.setdefault(snapshot["resource_id"], snapshot)
            for snapshot in latest_snapshots.values():
                observed = parse_time(snapshot["observed_at"])
                if snapshot["status"] == "available" and observed:
                    age = max(0, int((datetime.now(timezone.utc) - observed).total_seconds()))
                    snapshot["freshness_status"] = (
                        "stale" if age > self.coverage_max_age_seconds else "available"
                    )
                    snapshot["age_seconds"] = age
                else:
                    snapshot["freshness_status"] = snapshot["status"]
                    snapshot["age_seconds"] = None
            heartbeats = [dict(row) for row in db.execute(
                """SELECT heartbeat_id,collector_id,status,observed_at,attempted_nodes,
                          ingested_nodes,failed_nodes,source,detail
                   FROM collector_heartbeats ORDER BY collector_id,observed_at DESC"""
            )]
            latest_heartbeats = {}
            for heartbeat in heartbeats:
                latest_heartbeats.setdefault(heartbeat["collector_id"], heartbeat)
            for heartbeat in latest_heartbeats.values():
                heartbeat["age_seconds"] = self._coverage_age_seconds(heartbeat)
            unresolved = db.execute(
                "SELECT COUNT(*) FROM usage_ledger WHERE attribution_status!='attributed'"
            ).fetchone()[0]
            unresolved_credentials = db.execute(
                "SELECT COUNT(*) FROM credentials WHERE user_id IS NULL AND status='active'"
            ).fetchone()[0]
            pending = db.execute(
                "SELECT COUNT(*) FROM upgrade_requests WHERE status='pending_manual_review'"
            ).fetchone()[0]
        raw_user_ids = [user["user_id"] for user in users]
        detailed_users = [self.admin_user_detail(user_id) for user_id in raw_user_ids]
        users = [
            {
                "user_id": detail["user_id"], "display_name": detail["display_name"],
                "plan": detail["plan"], "role": detail["role"], "status": detail["status"],
                "usage_by_pool_bytes": detail["usage_by_pool_bytes"],
                "usage_by_node": detail["usage_by_node"],
                "usage_bytes": detail["usage_bytes"],
                "effective_access": detail["effective_access"],
                "subscription_status": detail["subscription_status"],
                "subscription_entry_count": detail["subscription_entry_count"],
                "migration_latest": detail["migration_latest"],
            }
            for detail in detailed_users
        ]
        return {
            "nodes": nodes,
            "users": users,
            "customer_cycle_policy": {
                "policy_id": CUSTOMER_CYCLE_POLICY_ID,
                "timezone": CUSTOMER_CYCLE_TIMEZONE,
                "baseline_at": customer_cycle_baseline_utc(),
                "rule": "15T00:00 to next 15T00",
            },
            "infrastructure_resources": resources,
            "provider_resource_cycles": provider_cycles,
            "provider_resource_snapshots": list(latest_snapshots.values()),
            "collector_heartbeats": list(latest_heartbeats.values()),
            "unresolved_usage_records": int(unresolved),
            "unresolved_credentials": int(unresolved_credentials),
            "pending_upgrade_requests": int(pending),
            "premium_capacity_pressure": "conditional/observe — no numeric capacity policy configured",
        }


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        # Do not put bearer tokens or subscription paths into the default access log.
        return


class App:
    def __init__(self, control: ControlPlane):
        self.control = control

    @staticmethod
    def _bearer(environ: dict) -> str | None:
        value = environ.get("HTTP_AUTHORIZATION", "")
        if value.lower().startswith("bearer "):
            return value[7:].strip()
        return None

    @staticmethod
    def _read_json(environ: dict) -> dict:
        try:
            length = min(int(environ.get("CONTENT_LENGTH") or 0), 1_000_000)
            data = environ["wsgi.input"].read(length) if length else b"{}"
            value = json.loads(data or b"{}")
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ControlPlaneError("invalid JSON body") from exc

    @staticmethod
    def _reply(start_response, status: int, body: bytes, content_type: str = "application/json; charset=utf-8"):
        start_response(f"{status} {'OK' if status < 400 else 'Error'}", [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
        ])
        return [body]

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        try:
            if path == "/healthz":
                return self._reply(start_response, 200, b"ok\n", "text/plain; charset=utf-8")
            if path == "/" and method == "GET":
                page = (STATIC_DIR / "index.html").read_bytes()
                return self._reply(start_response, 200, page, "text/html; charset=utf-8")
            if path.startswith("/invite/") and method == "GET":
                token = unquote(path.split("/", 2)[2])
                body = ("<!doctype html><meta charset='utf-8'><script>sessionStorage.setItem('sparklink_token',"
                        + json.dumps(token) + ");location.replace('/')</script>").encode()
                return self._reply(start_response, 200, body, "text/html; charset=utf-8")
            if path.startswith("/subscription/") or path.startswith("/u/"):
                token = unquote(path.split("/", 2)[2])
                if path.startswith("/u/") and method == "GET":
                    body = self.control.subscription(token, token_kind="subscription").encode()
                    return self._reply(start_response, 200, body, "text/plain; charset=utf-8")
                if path.startswith("/subscription/") and method == "GET":
                    body = self.control.subscription(token, token_kind="subscription").encode()
                    return self._reply(start_response, 200, body, "text/plain; charset=utf-8")
            if path == "/subscription" and method == "GET":
                subscription_token = environ.get("HTTP_X_SPARKLINK_SUBSCRIPTION_TOKEN")
                if not subscription_token:
                    raise Unauthorized("subscription token required")
                body = self.control.subscription(
                    subscription_token, token_kind="subscription"
                ).encode()
                return self._reply(start_response, 200, body, "text/plain; charset=utf-8")
            if path == "/api/me" and method == "GET":
                return self._reply(start_response, 200, json_bytes(self.control.user_view(self._bearer(environ))))
            if path == "/api/me/subscription" and method == "GET":
                subscription_token = environ.get("HTTP_X_SPARKLINK_SUBSCRIPTION_TOKEN")
                if not subscription_token:
                    raise Unauthorized("subscription token required")
                body = self.control.subscription(subscription_token, token_kind="subscription").encode()
                return self._reply(start_response, 200, body, "text/plain; charset=utf-8")
            if path == "/api/me/upgrade" and method == "POST":
                body = self._read_json(environ)
                value = self.control.request_upgrade(self._bearer(environ), str(body.get("target_plan", "")))
                return self._reply(start_response, 202, json_bytes(value))
            if path == "/api/admin/overview" and method == "GET":
                self.control._require_admin(self._bearer(environ))
                return self._reply(start_response, 200, json_bytes(self.control.admin_overview()))
            if path == "/api/admin/users" and method == "GET":
                self.control._require_admin(self._bearer(environ))
                return self._reply(start_response, 200, json_bytes({"users": self.control.admin_users()}))
            if path.startswith("/api/admin/users/") and method == "GET":
                self.control._require_admin(self._bearer(environ))
                user_id = unquote(path[len("/api/admin/users/"):])
                return self._reply(start_response, 200, json_bytes(self.control.admin_user_detail(user_id)))
            if path == "/api/admin/token-issuance" and method == "POST":
                self.control._require_admin(self._bearer(environ))
                body = self._read_json(environ)
                value = self.control.issue_tokens(
                    str(body["user_id"]), str(body.get("token_kind", "portal")),
                    body.get("revoke_old", True),
                )
                return self._reply(start_response, 201, json_bytes(value))
            if path == "/api/admin/token-revoke" and method == "POST":
                self.control._require_admin(self._bearer(environ))
                body = self._read_json(environ)
                if body.get("token_kind") != "subscription_legacy":
                    raise ControlPlaneError("only legacy Subscription hash revocation is supported")
                value = self.control.revoke_legacy_subscription(
                    str(body["user_id"]), str(body.get("confirmation", ""))
                )
                return self._reply(start_response, 200, json_bytes(value))
            if path == "/api/admin/migration-event" and method == "POST":
                self.control._require_admin(self._bearer(environ))
                body = self._read_json(environ)
                value = self.control.record_migration_event(
                    str(body["user_id"]), str(body["subject_kind"]), str(body["subject_ref"]),
                    str(body["state"]), str(body["source"]), str(body.get("detail", "")),
                    body.get("observed_at"),
                )
                return self._reply(start_response, 201, json_bytes(value))
            if path == "/api/admin/collector-heartbeat" and method == "POST":
                self.control._require_admin(self._bearer(environ))
                body = self._read_json(environ)
                value = self.control.record_collector_heartbeat(
                    str(body["collector_id"]), str(body["status"]), int(body["attempted_nodes"]),
                    int(body["ingested_nodes"]), int(body["failed_nodes"]), str(body.get("source", "collector")),
                    str(body.get("detail", "")), body.get("observed_at"),
                )
                return self._reply(start_response, 201, json_bytes(value))
            if path == "/api/admin/entitlement" and method == "POST":
                self.control._require_admin(self._bearer(environ))
                body = self._read_json(environ)
                value = self.control.set_entitlement(
                    str(body["user_id"]), str(body["pool_id"]), str(body["plan"]),
                    body.get("allowance_bytes"), body.get("effective_from"),
                )
                return self._reply(start_response, 201, json_bytes({"entitlement_id": value}))
            if path == "/api/admin/provider-snapshot" and method == "POST":
                self.control._require_admin(self._bearer(environ))
                value = self.control.record_provider_resource_snapshot(self._read_json(environ))
                return self._reply(start_response, 201, json_bytes({"snapshot_id": value}))
            if path == "/api/admin/node-admission" and method == "POST":
                self.control._require_admin(self._bearer(environ))
                body = self._read_json(environ)
                value = self.control.admit_node(
                    str(body["node_id"]), str(body["pool_id"]), str(body.get("qualification", "conditional")),
                    str(body.get("source", "operator")), body.get("effective_from"),
                    str(body.get("metering_status", "unknown")), body.get("supported_protocols", ["vless"]),
                    str(body.get("detail", "")),
                )
                return self._reply(start_response, 201, json_bytes(value))
            if path == "/api/admin/runtime-admission" and method == "POST":
                self.control._require_admin(self._bearer(environ))
                body = self._read_json(environ)
                value = self.control.admit_runtime_entries(
                    str(body["node_id"]), str(body["pool_id"]), list(body["entries"]),
                    str(body.get("qualification", "verified")),
                    body.get("display_name"),
                    str(body.get("source", "runtime-admission")), body.get("effective_from"),
                    str(body.get("metering_status", "unknown")),
                    body.get("supported_protocols", ["vless"]), str(body.get("detail", "")),
                )
                return self._reply(start_response, 201, json_bytes(value))
            if path == "/api/admin/access-override" and method == "POST":
                self.control._require_admin(self._bearer(environ))
                body = self._read_json(environ)
                value = self.control.set_access_override(
                    str(body["user_id"]), str(body["node_id"]), str(body["decision"]),
                    str(body["allocation_role"]), str(body["reason"]), str(body["source"]),
                    body.get("effective_from"), body.get("effective_to"),
                )
                return self._reply(start_response, 201, json_bytes(value))
            if path == "/api/admin/operational-budget" and method == "POST":
                self.control._require_admin(self._bearer(environ))
                body = self._read_json(environ)
                value = self.control.set_operational_budget(
                    str(body["user_id"]), int(body["allowance_bytes"]), body.get("node_id"),
                    body.get("pool_id"), body.get("provider_cycle_id"), str(body.get("budget_kind", "policy_only")),
                    str(body["reason"]), str(body.get("source", "operator")),
                    body.get("effective_from"), body.get("effective_to"),
                )
                return self._reply(start_response, 201, json_bytes(value))
            if path == "/api/admin/coverage" and method == "POST":
                self.control._require_admin(self._bearer(environ))
                body = self._read_json(environ)
                value = self.control.set_coverage(str(body["node_id"]), str(body["source"]),
                                                   str(body["status"]), str(body.get("detail", "")), body.get("observed_at"))
                return self._reply(start_response, 201, json_bytes({"coverage_id": value}))
            if path == "/api/admin/ingest/observations" and method == "POST":
                self.control._require_admin(self._bearer(environ))
                body = self._read_json(environ)
                value = self.control.ingest_observations(str(body["node_id"]), str(body["source"]),
                                                         str(body["counter_epoch"]), list(body["observations"]),
                                                         body.get("observed_at"))
                return self._reply(start_response, 201, json_bytes(value))
            if path == "/api/admin/subscription-entry" and method == "POST":
                self.control._require_admin(self._bearer(environ))
                body = self._read_json(environ)
                value = self.control.add_subscription_entry(
                    str(body["user_id"]), body.get("node_id"), body.get("pool_id"),
                    str(body["protocol"]), str(body["uri"]), str(body.get("minimum_plan", "Free")),
                    body.get("credential_id"), str(body.get("projection_status", "current")),
                )
                return self._reply(start_response, 201, json_bytes({"entry_id": value}))
            if path == "/api/admin/subscription-aliases" and method == "POST":
                self.control._require_admin(self._bearer(environ))
                body = self._read_json(environ)
                value = self.control.rename_subscription_entries(
                    list(body["entries"]), str(body.get("source", "operator")),
                )
                return self._reply(start_response, 200, json_bytes(value))
            return self._reply(start_response, 404, json_bytes({"error": "not found"}))
        except ControlPlaneError as exc:
            return self._reply(start_response, exc.status, json_bytes({"error": str(exc)}))
        except (KeyError, ValueError, TypeError) as exc:
            return self._reply(start_response, 400, json_bytes({"error": f"invalid request: {exc}"}))
        except sqlite3.IntegrityError as exc:
            return self._reply(start_response, 409, json_bytes({"error": "database constraint conflict"}))


def serve(args: argparse.Namespace) -> None:
    control = ControlPlane(args.db, subscription_base_url=args.subscription_base_url)
    control.init_db()
    application = App(control)
    server = make_server(args.host, args.port, application, handler_class=QuietHandler)
    print(f"SparkLink control plane listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SparkLink MVP control plane")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--db", type=Path, required=True)
    init.add_argument("--seed-nodes", action="store_true")
    create = sub.add_parser("create-user")
    create.add_argument("--db", type=Path, required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--plan", choices=list(PLAN_ORDER), default="Free")
    create.add_argument("--user-id")
    cycle = sub.add_parser("create-cycle")
    cycle.add_argument("--db", type=Path, required=True)
    cycle.add_argument("--user-id", required=True)
    cycle.add_argument("--cycle-key", required=True)
    cycle.add_argument("--starts-at")
    cycle.add_argument("--ends-at")
    cred = sub.add_parser("add-credential")
    cred.add_argument("--db", type=Path, required=True)
    cred.add_argument("--node-id", required=True)
    cred.add_argument("--runtime-ref-hash", required=True)
    cred.add_argument("--runtime-family", default="xray")
    cred.add_argument("--protocol", default="vless")
    cred.add_argument("--user-id")
    ent = sub.add_parser("set-entitlement")
    ent.add_argument("--db", type=Path, required=True)
    ent.add_argument("--user-id", required=True)
    ent.add_argument("--pool-id", choices=POOL_NAMES, required=True)
    ent.add_argument("--plan", choices=list(PLAN_ORDER), required=True)
    ent.add_argument("--allowance-bytes", type=int)
    ent.add_argument("--effective-from")
    mapping = sub.add_parser("map-credential")
    mapping.add_argument("--db", type=Path, required=True)
    mapping.add_argument("--credential-id", required=True)
    mapping.add_argument("--user-id", required=True)
    approve = sub.add_parser("approve-upgrade")
    approve.add_argument("--db", type=Path, required=True)
    approve.add_argument("--request-id", required=True)
    approve.add_argument("--note", default="manual admin approval")
    entry = sub.add_parser("add-subscription-entry")
    entry.add_argument("--db", type=Path, required=True)
    entry.add_argument("--user-id", required=True)
    entry.add_argument("--node-id")
    entry.add_argument("--pool-id", choices=POOL_NAMES)
    entry.add_argument("--protocol", required=True)
    entry.add_argument("--uri-file", type=Path, required=True)
    entry.add_argument("--minimum-plan", choices=list(PLAN_ORDER), default="Free")
    server = sub.add_parser("serve")
    server.add_argument("--db", type=Path, required=True)
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8080)
    server.add_argument("--subscription-base-url", default="https://sub.enrpiglink.top")
    return parser


def cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        cp = ControlPlane(args.db)
        cp.init_db()
        if args.seed_nodes:
            cp.seed_nodes()
        print("database initialized")
        return 0
    if args.command == "serve":
        serve(args)
        return 0
    cp = ControlPlane(args.db)
    cp.init_db()
    if args.command == "create-user":
        result = cp.create_user(args.display_name, args.plan, args.user_id)
        # Never put either freshly issued plaintext token on CLI stdout. The
        # Admin operator API is the supported protected delivery path.
        print(json.dumps({"user_id": result["user_id"], "created": True,
                          "delivery": "use the Admin token-issuance workflow"}, ensure_ascii=False))
    elif args.command == "create-cycle":
        print(cp.create_cycle(args.user_id, args.cycle_key, args.starts_at, args.ends_at))
    elif args.command == "add-credential":
        print(cp.add_credential(args.node_id, args.runtime_ref_hash, args.runtime_family, args.protocol, args.user_id))
    elif args.command == "set-entitlement":
        print(cp.set_entitlement(args.user_id, args.pool_id, args.plan, args.allowance_bytes, args.effective_from))
    elif args.command == "map-credential":
        cp.map_credential(args.credential_id, args.user_id)
        print("credential mapped")
    elif args.command == "approve-upgrade":
        cp.approve_upgrade(args.request_id, args.note)
        print("upgrade approved")
    elif args.command == "add-subscription-entry":
        # The URI is read from an untracked protected runtime file and never echoed.
        uri = args.uri_file.read_text(encoding="utf-8").strip()
        print(cp.add_subscription_entry(args.user_id, args.node_id, args.pool_id, args.protocol, uri, args.minimum_plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
