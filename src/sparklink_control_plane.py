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
import secrets
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote
from wsgiref.simple_server import WSGIRequestHandler, make_server


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "web"
PLAN_ORDER = {"Free": 0, "Basic": 1, "Plus": 2}
POOL_NAMES = ("STANDARD", "PREMIUM")


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

CREATE TABLE IF NOT EXISTS node_pool_memberships (
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    pool_id TEXT NOT NULL REFERENCES resource_pools(pool_id),
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    status TEXT NOT NULL,
    PRIMARY KEY (node_id, pool_id, effective_from)
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    plan TEXT NOT NULL CHECK(plan IN ('Free', 'Basic', 'Plus')),
    status TEXT NOT NULL,
    portal_token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_cycles (
    cycle_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    cycle_key TEXT NOT NULL,
    starts_at TEXT,
    ends_at TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
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
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(node_id, runtime_ref_hash)
);

CREATE TABLE IF NOT EXISTS coverage_events (
    coverage_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    source TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('available', 'gap', 'stale', 'unknown')),
    observed_at TEXT NOT NULL,
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
    protocol TEXT NOT NULL,
    uri TEXT NOT NULL,
    minimum_plan TEXT NOT NULL CHECK(minimum_plan IN ('Free', 'Basic', 'Plus')),
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_observation_lookup
    ON usage_observations(node_id, runtime_ref_hash, counter_epoch, observed_at);
CREATE INDEX IF NOT EXISTS idx_ledger_user_cycle
    ON usage_ledger(user_id, cycle_id, pool_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_ledger_node_pool
    ON usage_ledger(node_id, pool_id, observed_at);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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
    def __init__(self, db_path: str | Path, admin_token: str | None = None, subscription_base_url: str | None = None):
        self.db_path = Path(db_path)
        self.admin_token = admin_token or os.environ.get("SPARKLINK_ADMIN_TOKEN", "")
        self.subscription_base_url = (subscription_base_url or os.environ.get(
            "SPARKLINK_SUBSCRIPTION_BASE_URL", "https://sub.enrpiglink.top"
        )).rstrip("/")

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

    def create_user(self, display_name: str, plan: str = "Free", user_id: str | None = None,
                    portal_token: str | None = None) -> dict:
        if plan not in PLAN_ORDER:
            raise ControlPlaneError("invalid plan")
        uid = user_id or f"usr_{uuid.uuid4().hex}"
        token = portal_token or new_token()
        now = utc_now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO users(user_id, display_name, plan, status, portal_token_hash, created_at) VALUES (?, ?, ?, 'active', ?, ?)",
                (uid, display_name, plan, token_hash(token), now),
            )
        return {"user_id": uid, "portal_token": token}

    def create_cycle(self, user_id: str, cycle_key: str, starts_at: str | None, ends_at: str | None) -> str:
        cycle_id = f"cyc_{uuid.uuid4().hex}"
        now = utc_now()
        with self.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is None:
                raise NotFound("user not found")
            db.execute("UPDATE billing_cycles SET status='closed' WHERE user_id=? AND status='active'", (user_id,))
            db.execute(
                "INSERT INTO billing_cycles(cycle_id,user_id,cycle_key,starts_at,ends_at,status,created_at) VALUES (?,?,?,?,?,'active',?)",
                (cycle_id, user_id, cycle_key, starts_at, ends_at, now),
            )
        return cycle_id

    def set_entitlement(self, user_id: str, pool_id: str, plan: str,
                        allowance_bytes: int | None, effective_from: str | None = None) -> str:
        if pool_id not in POOL_NAMES or plan not in PLAN_ORDER:
            raise ControlPlaneError("invalid pool or plan")
        entitlement_id = f"ent_{uuid.uuid4().hex}"
        with self.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None:
                raise NotFound("user not found")
            if db.execute("SELECT 1 FROM resource_pools WHERE pool_id=?", (pool_id,)).fetchone() is None:
                raise NotFound("pool not found")
            db.execute(
                "UPDATE entitlements SET effective_to=?, status='superseded' WHERE user_id=? AND pool_id=? AND status='active'",
                (effective_from or utc_now(), user_id, pool_id),
            )
            db.execute(
                """INSERT INTO entitlements
                   (entitlement_id,user_id,pool_id,plan,allowance_bytes,effective_from,effective_to,status)
                   VALUES (?,?,?,?,?,?,NULL,'active')""",
                (entitlement_id, user_id, pool_id, plan, allowance_bytes, effective_from or utc_now()),
            )
        return entitlement_id

    def add_credential(self, node_id: str, runtime_ref_hash: str, runtime_family: str,
                       protocol: str, user_id: str | None = None) -> str:
        if len(runtime_ref_hash) != 64 or any(c not in "0123456789abcdef" for c in runtime_ref_hash.lower()):
            raise ControlPlaneError("runtime_ref_hash must be a SHA-256 hex string")
        credential_id = f"cred_{uuid.uuid4().hex}"
        with self.connect() as db:
            if db.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone() is None:
                raise NotFound("node not found")
            if user_id and db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None:
                raise NotFound("user not found")
            db.execute(
                """INSERT INTO credentials
                   (credential_id,node_id,user_id,runtime_ref_hash,runtime_family,protocol,status,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (credential_id, node_id, user_id, runtime_ref_hash.lower(), runtime_family, protocol, "active", utc_now()),
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
        coverage_id = f"cov_{uuid.uuid4().hex}"
        with self.connect() as db:
            if db.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone() is None:
                raise NotFound("node not found")
            db.execute(
                "INSERT INTO coverage_events(coverage_id,node_id,source,status,observed_at,detail) VALUES (?,?,?,?,?,?)",
                (coverage_id, node_id, source, status, observed_at or utc_now(), detail[:500]),
            )
        return coverage_id

    def _pool_at(self, db: sqlite3.Connection, node_id: str, observed_at: str) -> str | None:
        row = db.execute(
            """SELECT pool_id FROM node_pool_memberships
               WHERE node_id=? AND effective_from <= ? AND (effective_to IS NULL OR effective_to > ?)
                 AND status='active' ORDER BY effective_from DESC LIMIT 1""",
            (node_id, observed_at, observed_at),
        ).fetchone()
        return row[0] if row else None

    def _cycle_for(self, db: sqlite3.Connection, user_id: str, observed_at: str) -> str | None:
        row = db.execute(
            """SELECT cycle_id FROM billing_cycles
               WHERE user_id=? AND status='active'
                 AND (starts_at IS NULL OR starts_at <= ?)
                 AND (ends_at IS NULL OR ends_at > ?)
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, observed_at, observed_at),
        ).fetchone()
        return row[0] if row else None

    def ingest_observations(self, node_id: str, source: str, counter_epoch: str,
                            observations: list[dict], observed_at: str | None = None) -> dict:
        if not observations:
            raise ControlPlaneError("observations must not be empty")
        sample_time = observed_at or utc_now()
        inserted = 0
        duplicates = 0
        unresolved = 0
        with self.connect() as db:
            if db.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone() is None:
                raise NotFound("node not found")
            for item in observations:
                runtime_hash = str(item.get("runtime_ref_hash", "")).lower()
                if len(runtime_hash) != 64:
                    raise ControlPlaneError("observation runtime_ref_hash is invalid")
                up = int(item.get("uplink_bytes", 0))
                down = int(item.get("downlink_bytes", 0))
                if up < 0 or down < 0:
                    raise ControlPlaneError("counters cannot be negative")
                item_time = str(item.get("observed_at") or sample_time)
                oid = str(item.get("observation_id") or hashlib.sha256(
                    f"{node_id}|{runtime_hash}|{counter_epoch}|{item_time}|{source}".encode()
                ).hexdigest())
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
                    duplicates += 1
                    continue
                inserted += 1
                prev = db.execute(
                    """SELECT uplink_bytes,downlink_bytes FROM usage_observations
                       WHERE node_id=? AND runtime_ref_hash=? AND counter_epoch=?
                         AND observed_at < ? ORDER BY observed_at DESC LIMIT 1""",
                    (node_id, runtime_hash, counter_epoch, item_time),
                ).fetchone()
                detail = "baseline"
                delta_up = delta_down = 0
                if prev:
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
                        delta_uplink_bytes,delta_downlink_bytes,attribution_status,detail)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"led_{uuid.uuid4().hex}", oid, user_id, node_id, pool_id, cycle_id,
                     item_time, delta_up, delta_down, status, detail),
                )
            db.execute(
                "INSERT INTO coverage_events(coverage_id,node_id,source,status,observed_at,detail) VALUES (?,?,?,?,?,?)",
                (f"cov_{uuid.uuid4().hex}", node_id, source, "available", sample_time,
                 f"ingested={inserted};duplicates={duplicates};unresolved={unresolved}"),
            )
        return {"inserted": inserted, "duplicates": duplicates, "unresolved": unresolved}

    def _latest_coverage(self, db: sqlite3.Connection, node_id: str) -> str | None:
        row = db.execute(
            "SELECT status FROM coverage_events WHERE node_id=? ORDER BY observed_at DESC LIMIT 1", (node_id,)
        ).fetchone()
        return row[0] if row else None

    def _active_pool_nodes(self, db: sqlite3.Connection, pool_id: str) -> list[str]:
        rows = db.execute(
            """SELECT node_id FROM node_pool_memberships
               WHERE pool_id=? AND status='active' AND effective_to IS NULL""", (pool_id,)
        ).fetchall()
        return [r[0] for r in rows]

    @staticmethod
    def _display_bytes(value: int | None) -> int | None:
        return None if value is None else int(value)

    def user_view(self, token: str) -> dict:
        user = self._user_by_token(token)
        with self.connect() as db:
            cycle = db.execute(
                "SELECT * FROM billing_cycles WHERE user_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
                (user["user_id"],),
            ).fetchone()
            cycle_id = cycle["cycle_id"] if cycle else None
            cycle_view = {
                "cycle_id": cycle["cycle_id"], "cycle_key": cycle["cycle_key"],
                "starts_at": cycle["starts_at"], "ends_at": cycle["ends_at"]
            } if cycle else None
            pools = []
            unknown_pool = False
            for pool_id in POOL_NAMES:
                nodes = self._active_pool_nodes(db, pool_id)
                allowance_row = db.execute(
                    """SELECT allowance_bytes FROM entitlements
                       WHERE user_id=? AND pool_id=? AND status='active'
                       ORDER BY effective_from DESC LIMIT 1""",
                    (user["user_id"], pool_id),
                ).fetchone()
                has_entitlement = allowance_row is not None
                if nodes:
                    placeholders = ",".join("?" for _ in nodes)
                    relevant_rows = db.execute(
                        f"SELECT DISTINCT node_id FROM credentials WHERE user_id=? AND status='active' AND node_id IN ({placeholders})",
                        (user["user_id"], *nodes),
                    ).fetchall()
                    relevant_nodes = [r[0] for r in relevant_rows]
                else:
                    relevant_nodes = []
                statuses = [self._latest_coverage(db, n) for n in relevant_nodes]
                not_applicable = not relevant_nodes and not has_entitlement
                coverage_known = not_applicable or (bool(relevant_nodes) and all(s == "available" for s in statuses))
                unresolved = 0
                missing_counters = 0
                if cycle_id and relevant_nodes:
                    placeholders = ",".join("?" for _ in relevant_nodes)
                    credential_hashes = db.execute(
                        f"SELECT runtime_ref_hash FROM credentials WHERE user_id=? AND status='active' AND node_id IN ({placeholders})",
                        (user["user_id"], *relevant_nodes),
                    ).fetchall()
                    hashes = [row[0] for row in credential_hashes]
                    if hashes:
                        hash_placeholders = ",".join("?" for _ in hashes)
                        observation_where = [
                            f"o.node_id IN ({placeholders})",
                            f"o.runtime_ref_hash IN ({hash_placeholders})",
                        ]
                        observation_params: list[object] = [*relevant_nodes, *hashes]
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
                        missing_counters = max(0, len(hashes) - int(observed_hashes))
                    unresolved = db.execute(
                        f"""SELECT COUNT(*)
                            FROM usage_ledger l JOIN usage_observations o ON o.observation_id=l.observation_id
                            WHERE l.node_id IN ({placeholders}) AND l.cycle_id=?
                              AND l.attribution_status!='attributed'
                              AND o.runtime_ref_hash IN (
                                  SELECT runtime_ref_hash FROM credentials
                                  WHERE user_id=? AND status='active'
                              )""",
                        (*relevant_nodes, cycle_id, user["user_id"]),
                    ).fetchone()[0]
                if unresolved or missing_counters:
                    coverage_known = False
                used = None
                if coverage_known and cycle_id and relevant_nodes:
                    placeholders = ",".join("?" for _ in relevant_nodes)
                    row = db.execute(
                        f"""SELECT COALESCE(SUM(delta_uplink_bytes+delta_downlink_bytes),0)
                            FROM usage_ledger WHERE user_id=? AND pool_id=? AND cycle_id=?
                              AND node_id IN ({placeholders})""",
                        (user["user_id"], pool_id, cycle_id, *relevant_nodes),
                    ).fetchone()
                    used = int(row[0])
                allowance = int(allowance_row[0]) if allowance_row and allowance_row[0] is not None else None
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
                })
            total = None if unknown_pool else sum(p["used_bytes"] or 0 for p in pools)
            upgrade = db.execute(
                """SELECT request_id,to_plan,status,requested_at FROM upgrade_requests
                   WHERE user_id=? ORDER BY requested_at DESC LIMIT 1""", (user["user_id"],)
            ).fetchone()
            subscription_count = db.execute(
                "SELECT COUNT(*) FROM subscription_entries WHERE user_id=? AND enabled=1 AND protocol!='anytls'",
                (user["user_id"],),
            ).fetchone()[0]
        return {
            "user_id": user["user_id"],
            "display_name": user["display_name"],
            "plan": user["plan"],
            "subscription_status": "available" if subscription_count else "not_configured",
            "subscription_url": f"{self.subscription_base_url}/u/{token}" if subscription_count else None,
            "customer_billing_cycle": cycle_view,
            "pools": pools,
            "total_usage_bytes": total,
            "latest_upgrade_request": dict(upgrade) if upgrade else None,
        }

    def subscription(self, token: str) -> str:
        user = self._user_by_token(token)
        with self.connect() as db:
            rows = db.execute(
                """SELECT uri FROM subscription_entries
                   WHERE user_id=? AND enabled=1 AND protocol!='anytls'
                   AND ? >= CASE minimum_plan WHEN 'Free' THEN 0 WHEN 'Basic' THEN 1 ELSE 2 END
                   ORDER BY entry_id""",
                (user["user_id"], PLAN_ORDER[user["plan"]]),
            ).fetchall()
        if not rows:
            raise ServiceUnavailable("subscription is not configured")
        return base64.b64encode(("\n".join(r[0] for r in rows) + "\n").encode()).decode() + "\n"

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
                               protocol: str, uri: str, minimum_plan: str = "Free") -> str:
        if protocol.lower() == "anytls" or uri.lower().startswith("anytls://"):
            raise Conflict("AnyTLS is deferred until reliable per-user accounting")
        if minimum_plan not in PLAN_ORDER:
            raise ControlPlaneError("invalid minimum plan")
        entry_id = f"sub_{uuid.uuid4().hex}"
        with self.connect() as db:
            if db.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None:
                raise NotFound("user not found")
            if node_id and db.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone() is None:
                raise NotFound("node not found")
            if pool_id and pool_id not in POOL_NAMES:
                raise ControlPlaneError("invalid pool")
            db.execute(
                """INSERT INTO subscription_entries
                   (entry_id,user_id,node_id,pool_id,protocol,uri,minimum_plan,enabled,created_at)
                   VALUES (?,?,?,?,?,?,?,1,?)""",
                (entry_id, user_id, node_id, pool_id, protocol, uri.strip(), minimum_plan, utc_now()),
            )
        return entry_id

    def admin_overview(self) -> dict:
        with self.connect() as db:
            nodes = []
            for row in db.execute("SELECT * FROM nodes ORDER BY node_id"):
                pool = db.execute(
                    """SELECT pool_id,status FROM node_pool_memberships
                       WHERE node_id=? AND status='active' AND effective_to IS NULL
                       ORDER BY effective_from DESC LIMIT 1""", (row["node_id"],)
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
                    "coverage_status": self._latest_coverage(db, row["node_id"]) or "unknown",
                })
            users = []
            for row in db.execute("SELECT user_id,display_name,plan,status FROM users ORDER BY user_id"):
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
            unresolved = db.execute(
                "SELECT COUNT(*) FROM usage_ledger WHERE attribution_status!='attributed'"
            ).fetchone()[0]
            unresolved_credentials = db.execute(
                "SELECT COUNT(*) FROM credentials WHERE user_id IS NULL AND status='active'"
            ).fetchone()[0]
            pending = db.execute(
                "SELECT COUNT(*) FROM upgrade_requests WHERE status='pending_manual_review'"
            ).fetchone()[0]
        return {
            "nodes": nodes,
            "users": users,
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
                    body = self.control.subscription(token).encode()
                    return self._reply(start_response, 200, body, "text/plain; charset=utf-8")
                if path.startswith("/subscription/") and method == "GET":
                    body = self.control.subscription(token).encode()
                    return self._reply(start_response, 200, body, "text/plain; charset=utf-8")
            if path == "/subscription" and method == "GET":
                body = self.control.subscription(self._bearer(environ)).encode()
                return self._reply(start_response, 200, body, "text/plain; charset=utf-8")
            if path == "/api/me" and method == "GET":
                return self._reply(start_response, 200, json_bytes(self.control.user_view(self._bearer(environ))))
            if path == "/api/me/subscription" and method == "GET":
                body = self.control.subscription(self._bearer(environ)).encode()
                return self._reply(start_response, 200, body, "text/plain; charset=utf-8")
            if path == "/api/me/upgrade" and method == "POST":
                body = self._read_json(environ)
                value = self.control.request_upgrade(self._bearer(environ), str(body.get("target_plan", "")))
                return self._reply(start_response, 202, json_bytes(value))
            if path == "/api/admin/overview" and method == "GET":
                self.control._require_admin(self._bearer(environ))
                return self._reply(start_response, 200, json_bytes(self.control.admin_overview()))
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
                )
                return self._reply(start_response, 201, json_bytes({"entry_id": value}))
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
        print(json.dumps(cp.create_user(args.display_name, args.plan, args.user_id), ensure_ascii=False))
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
