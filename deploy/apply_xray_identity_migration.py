#!/usr/bin/env python3
"""Apply a staged per-user Xray identity plan to one Node.

The plan is supplied through stdin at runtime and must never be committed.  It
contains transient UUIDs and therefore this utility deliberately emits only
redacted operation results.  The utility is conservative: it validates every
source client before changing the config, keeps a rollback copy, preserves
x-ui's persistent client rows when present, and restores both artifacts if
config validation or service recovery fails.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


class MigrationError(RuntimeError):
    pass


def run(command: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MigrationError("command_failed") from exc


def require_ok(result: subprocess.CompletedProcess[str], code: str) -> None:
    if result.returncode != 0:
        raise MigrationError(code)


def file_metadata(path: Path) -> os.stat_result:
    try:
        return path.stat()
    except OSError as exc:
        raise MigrationError("artifact_unreadable") from exc


def copy_preserving_metadata(source: Path, target: Path) -> None:
    try:
        shutil.copy2(source, target)
        stat = source.stat()
        os.chmod(target, stat.st_mode & 0o7777)
        if hasattr(os, "chown"):
            os.chown(target, stat.st_uid, stat.st_gid)
    except OSError as exc:
        raise MigrationError("artifact_restore_failed") from exc


def atomic_json_write(path: Path, value: dict, source_stat: os.stat_result) -> None:
    directory = path.parent
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, source_stat.st_mode & 0o7777)
        if hasattr(os, "chown"):
            os.chown(temporary, source_stat.st_uid, source_stat.st_gid)
        os.replace(temporary, path)
    except OSError as exc:
        raise MigrationError("config_write_failed") from exc
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def backup_sqlite(path: Path, target: Path) -> None:
    try:
        source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()
        copy_stat = path.stat()
        os.chmod(target, copy_stat.st_mode & 0o7777)
        if hasattr(os, "chown"):
            os.chown(target, copy_stat.st_uid, copy_stat.st_gid)
    except (OSError, sqlite3.Error) as exc:
        raise MigrationError("database_backup_failed") from exc


def load_plan() -> dict:
    try:
        plan = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError) as exc:
        raise MigrationError("plan_invalid") from exc
    if not isinstance(plan, dict):
        raise MigrationError("plan_invalid")
    required = {"node_id", "config_path", "binary", "service", "test_flag", "entries"}
    if not required.issubset(plan):
        raise MigrationError("plan_incomplete")
    if not isinstance(plan["entries"], list) or not plan["entries"]:
        raise MigrationError("plan_entries_empty")
    return plan


def validate_entry(entry: dict) -> None:
    if not isinstance(entry, dict):
        raise MigrationError("plan_entry_invalid")
    for key in ("source_entry_id", "old_uuid", "new_uuid", "new_email"):
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            raise MigrationError("plan_entry_invalid")
    try:
        old_uuid = uuid.UUID(entry["old_uuid"])
        new_uuid = uuid.UUID(entry["new_uuid"])
    except ValueError as exc:
        raise MigrationError("plan_uuid_invalid") from exc
    if old_uuid == new_uuid:
        raise MigrationError("plan_uuid_not_rotated")
    if len(entry["new_email"]) > 128 or any(c.isspace() for c in entry["new_email"]):
        raise MigrationError("plan_email_invalid")
    for key in ("source_tag",):
        if key in entry and (
            not isinstance(entry[key], str)
            or not entry[key].strip()
            or len(entry[key]) > 128
            or any(c.isspace() for c in entry[key])
        ):
            raise MigrationError("plan_entry_invalid")


def config_clients(config: dict):
    for inbound in config.get("inbounds", []):
        settings = inbound.get("settings") or {}
        clients = settings.get("clients") or []
        for client in clients:
            if isinstance(client, dict) and isinstance(client.get("id"), str):
                yield inbound, clients, client


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def db_tables(db: sqlite3.Connection) -> set[str]:
    return {
        row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def table_columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()]


def insert_row(db: sqlite3.Connection, table: str, values: dict) -> int | None:
    columns = [column for column in table_columns(db, table) if column in values and column != "id"]
    if not columns:
        raise MigrationError("persistent_schema_unsupported")
    placeholders = ",".join("?" for _ in columns)
    cursor = db.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        [values[column] for column in columns],
    )
    return cursor.lastrowid


def clone_xui_rows(db_path: Path, source_by_uuid: list[tuple[str, str, str, str]]) -> int:
    """Clone persistent x-ui client rows; return the number of client rows added."""
    try:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        tables = db_tables(db)
        if not {"clients", "client_inbounds", "client_traffics"}.issubset(tables):
            raise MigrationError("persistent_schema_unsupported")
        now_ms = int(time.time() * 1000)
        added = 0
        with db:
            for old_uuid, old_email, new_uuid, new_email in source_by_uuid:
                existing_new = db.execute(
                    "SELECT id FROM clients WHERE uuid=?", (new_uuid,)
                ).fetchone()
                if existing_new:
                    continue
                source_rows = db.execute(
                    "SELECT * FROM clients WHERE uuid=? AND email=?",
                    (old_uuid, old_email),
                ).fetchall()
                if len(source_rows) != 1:
                    raise MigrationError("persistent_source_client_ambiguous")
                source = dict(source_rows[0])
                source_id = source.get("id")
                old_email = source.get("email") or ""
                values = dict(source)
                values.pop("id", None)
                if "uuid" in values:
                    values["uuid"] = new_uuid
                if "email" in values:
                    values["email"] = new_email
                if "sub_id" in values:
                    values["sub_id"] = secrets.token_urlsafe(18)
                if "created_at" in values:
                    values["created_at"] = now_ms
                if "updated_at" in values:
                    values["updated_at"] = now_ms
                new_id = insert_row(db, "clients", values)
                if new_id is None:
                    raise MigrationError("persistent_client_insert_failed")

                inbound_rows = db.execute(
                    "SELECT * FROM client_inbounds WHERE client_id=?", (source_id,)
                ).fetchall()
                inbound_ids = []
                for row in inbound_rows:
                    values = dict(row)
                    values["client_id"] = new_id
                    if "created_at" in values:
                        values["created_at"] = now_ms
                    insert_row(db, "client_inbounds", values)
                    inbound_ids.append(row["inbound_id"])

                traffic_rows = db.execute(
                    "SELECT * FROM client_traffics WHERE email=?", (old_email,)
                ).fetchall()
                for row in traffic_rows:
                    if inbound_ids and row["inbound_id"] not in inbound_ids:
                        continue
                    values = dict(row)
                    values.pop("id", None)
                    values["email"] = new_email
                    for counter in ("up", "down"):
                        if counter in values:
                            values[counter] = 0
                    if "last_online" in values:
                        values["last_online"] = 0
                    insert_row(db, "client_traffics", values)
                added += 1
        db.close()
        return added
    except MigrationError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise MigrationError("persistent_update_failed") from exc


def mutate_config(config: dict, entries: list[dict]) -> tuple[int, list[tuple[str, str, str, str]]]:
    by_old: dict[str, list[tuple[dict, list, dict]]] = {}
    by_new: dict[str, dict] = {}
    for inbound, clients, client in config_clients(config):
        by_old.setdefault(client["id"], []).append((inbound, clients, client))
        by_new[client["id"]] = client
    additions = 0
    persistent_sources: list[tuple[str, str, str, str]] = []
    for entry in entries:
        validate_entry(entry)
        old_uuid = entry["old_uuid"]
        new_uuid = entry["new_uuid"]
        new_email = entry["new_email"]
        sources = by_old.get(old_uuid, [])
        if entry.get("source_tag"):
            sources = [source for source in sources if source[0].get("tag") == entry["source_tag"]]
        existing_new = by_new.get(new_uuid)
        if existing_new is not None:
            if existing_new.get("email") != new_email:
                raise MigrationError("existing_target_identity_conflict")
            old_email = str(sources[0][2].get("email") or "") if len(sources) == 1 else ""
            persistent_sources.append((old_uuid, old_email, new_uuid, new_email))
            continue
        if len(sources) != 1:
            raise MigrationError("source_client_missing_or_ambiguous")
        _inbound, clients, source = sources[0]
        clone = copy.deepcopy(source)
        clone["id"] = new_uuid
        clone["email"] = new_email
        clients.append(clone)
        by_new[new_uuid] = clone
        additions += 1
        persistent_sources.append((old_uuid, str(source.get("email") or ""), new_uuid, new_email))
    return additions, persistent_sources


def service_active(service: str) -> bool:
    return run(["systemctl", "is-active", "--quiet", service], timeout=15).returncode == 0


def restore(service: str, config_path: Path, config_backup: Path, config_stat: os.stat_result,
            db_path: Path | None, db_backup: Path | None, db_stat: os.stat_result | None) -> bool:
    try:
        run(["systemctl", "stop", service], timeout=30)
        copy_preserving_metadata(config_backup, config_path)
        if db_path and db_backup and db_stat:
            for suffix in ("-wal", "-shm"):
                try:
                    Path(str(db_path) + suffix).unlink()
                except FileNotFoundError:
                    pass
            copy_preserving_metadata(db_backup, db_path)
        result = run(["systemctl", "start", service], timeout=45)
        return result.returncode == 0 and service_active(service)
    except MigrationError:
        return False


def apply(plan: dict) -> dict:
    if os.name != "posix" or getattr(os, "geteuid", lambda: 1)() != 0:
        raise MigrationError("root_required")
    node_id = str(plan["node_id"])
    config_path = Path(str(plan["config_path"]))
    binary = str(plan["binary"])
    service = str(plan["service"])
    test_flag = str(plan["test_flag"])
    entries = plan["entries"]
    if not config_path.is_file() or not Path(binary).is_file():
        raise MigrationError("runtime_artifact_missing")
    config_stat = file_metadata(config_path)
    expected_config_sha256 = plan.get("expected_config_sha256")
    if expected_config_sha256 is not None:
        if (not isinstance(expected_config_sha256, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_config_sha256)
                or sha256_file(config_path).lower() != expected_config_sha256.lower()):
            raise MigrationError("config_changed_since_discovery")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError("config_invalid") from exc
    additions, persistent_sources = mutate_config(config, entries)
    already_present = len(entries) - additions
    db_path: Path | None = None
    db_stat: os.stat_result | None = None
    db_backup: Path | None = None
    if Path("/etc/x-ui/x-ui.db").is_file():
        db_path = Path("/etc/x-ui/x-ui.db")
        db_stat = file_metadata(db_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = Path("/var/backups/sparklink-identity-migration") / f"{stamp}-{node_id}"
    persistent_added = 0
    try:
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        config_backup = backup_dir / "xray-config.json"
        shutil.copy2(config_path, config_backup)
        if db_path:
            db_backup = backup_dir / "x-ui.db"
            backup_sqlite(db_path, db_backup)
        else:
            db_backup = None
        if additions or (db_path and persistent_sources):
            if db_path and persistent_sources:
                persistent_added = clone_xui_rows(db_path, persistent_sources)
            if additions:
                atomic_json_write(config_path, config, config_stat)
            require_ok(run([binary, "run", "-test", test_flag, str(config_path)], timeout=45), "config_test_failed")
            require_ok(run(["systemctl", "restart", service], timeout=60), "service_restart_failed")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not service_active(service):
                time.sleep(1)
            if not service_active(service):
                raise MigrationError("service_not_active")
            try:
                after = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise MigrationError("post_restart_config_invalid") from exc
            for entry in entries:
                matches = [client for _inbound, _clients, client in config_clients(after)
                           if client.get("id") == entry["new_uuid"]]
                if len(matches) != 1 or matches[0].get("email") != entry["new_email"]:
                    raise MigrationError("post_restart_identity_missing")
        return {
            "ok": True,
            "node_id": node_id,
            "added": additions,
            "already_present": already_present,
            "persistent_client_rows_added": persistent_added if db_path else 0,
            "backup": str(backup_dir),
            "config_sha256": sha256_file(config_path),
        }
    except MigrationError as exc:
        restored = restore(service, config_path, backup_dir / "xray-config.json", config_stat,
                           db_path, db_backup, db_stat)
        raise MigrationError(f"{exc};rollback={'ok' if restored else 'failed'}") from exc


def main() -> int:
    try:
        result = apply(load_plan())
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except MigrationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:120]}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
