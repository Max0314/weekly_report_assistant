from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .config import settings
from .time_utils import now_local, to_db


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_config (
    config_key TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    table_count INTEGER NOT NULL DEFAULT 0,
    record_count INTEGER NOT NULL DEFAULT 0,
    changed_count INTEGER NOT NULL DEFAULT 0,
    error_text TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sync_run_started ON sync_run(started_at DESC);

CREATE TABLE IF NOT EXISTS employee_cache (
    employee_key TEXT PRIMARY KEY,
    corp_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    union_id TEXT NOT NULL DEFAULT '',
    job_number TEXT NOT NULL DEFAULT '',
    employee_name TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    primary_dept_id TEXT NOT NULL DEFAULT '',
    department_name TEXT NOT NULL DEFAULT '',
    biz_group_name TEXT NOT NULL DEFAULT '',
    email_normalized TEXT NOT NULL DEFAULT '',
    affiliation_type TEXT NOT NULL DEFAULT '',
    employment_status TEXT NOT NULL DEFAULT '',
    primary_source TEXT NOT NULL DEFAULT '',
    is_department_leader INTEGER NOT NULL DEFAULT 0,
    is_biz_group_leader INTEGER NOT NULL DEFAULT 0,
    is_company_leader INTEGER NOT NULL DEFAULT 0,
    leader_roles_json TEXT NOT NULL DEFAULT '[]',
    is_active INTEGER NOT NULL DEFAULT 1,
    directory_version TEXT NOT NULL DEFAULT '',
    refreshed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_employee_cache_user ON employee_cache(user_id);
CREATE INDEX IF NOT EXISTS idx_employee_cache_name ON employee_cache(employee_name);

CREATE TABLE IF NOT EXISTS organization_cache (
    organization_key TEXT PRIMARY KEY,
    organization_type TEXT NOT NULL,
    organization_id TEXT NOT NULL DEFAULT '',
    organization_name TEXT NOT NULL,
    member_count INTEGER NOT NULL DEFAULT 0,
    leader_count INTEGER NOT NULL DEFAULT 0,
    directory_version TEXT NOT NULL DEFAULT '',
    refreshed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_organization_cache_name ON organization_cache(organization_name);
CREATE INDEX IF NOT EXISTS idx_organization_cache_type ON organization_cache(organization_type, organization_name);

CREATE TABLE IF NOT EXISTS employee_org_relation_cache (
    employee_key TEXT NOT NULL,
    organization_key TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 1,
    is_leader INTEGER NOT NULL DEFAULT 0,
    directory_version TEXT NOT NULL DEFAULT '',
    refreshed_at TEXT NOT NULL,
    PRIMARY KEY(employee_key, organization_key),
    FOREIGN KEY(employee_key) REFERENCES employee_cache(employee_key) ON DELETE CASCADE,
    FOREIGN KEY(organization_key) REFERENCES organization_cache(organization_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_employee_org_relation_org ON employee_org_relation_cache(organization_key, is_leader);

CREATE TABLE IF NOT EXISTS source_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    record_id TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT '',
    progress_text TEXT NOT NULL DEFAULT '',
    plan_text TEXT NOT NULL DEFAULT '',
    risk_text TEXT NOT NULL DEFAULT '',
    product_manager_user_ids_json TEXT NOT NULL DEFAULT '[]',
    project_manager_user_ids_json TEXT NOT NULL DEFAULT '[]',
    product_manager_names_json TEXT NOT NULL DEFAULT '[]',
    project_manager_names_json TEXT NOT NULL DEFAULT '[]',
    event_at TEXT NOT NULL DEFAULT '',
    due_at TEXT NOT NULL DEFAULT '',
    source_created_at TEXT NOT NULL DEFAULT '',
    source_updated_at TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    UNIQUE(base_id, table_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_source_record_changed ON source_record(changed_at);
CREATE INDEX IF NOT EXISTS idx_source_record_event ON source_record(event_at);
CREATE INDEX IF NOT EXISTS idx_source_record_due ON source_record(due_at);
CREATE INDEX IF NOT EXISTS idx_source_record_table ON source_record(table_id, is_deleted);

CREATE TABLE IF NOT EXISTS weekly_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_key TEXT NOT NULL,
    report_kind TEXT NOT NULL DEFAULT 'combined',
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    window_json TEXT NOT NULL,
    sections_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    source_record_ids_json TEXT NOT NULL,
    source_snapshot_json TEXT NOT NULL DEFAULT '[]',
    coverage_json TEXT NOT NULL DEFAULT '{}',
    workflow_state TEXT NOT NULL DEFAULT 'draft_generated',
    ai_status TEXT NOT NULL DEFAULT '',
    ai_error TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    image_generated_at TEXT NOT NULL DEFAULT '',
    previewed_at TEXT NOT NULL DEFAULT '',
    confirm_status TEXT NOT NULL DEFAULT '',
    confirmed_by TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT NOT NULL DEFAULT '',
    change_request TEXT NOT NULL DEFAULT '',
    send_status TEXT NOT NULL DEFAULT '',
    send_error TEXT NOT NULL DEFAULT '',
    sent_at TEXT NOT NULL DEFAULT '',
    archive_status TEXT NOT NULL DEFAULT '',
    archive_record_id TEXT NOT NULL DEFAULT '',
    archive_error TEXT NOT NULL DEFAULT '',
    archive_attempted_at TEXT NOT NULL DEFAULT '',
    archived_at TEXT NOT NULL DEFAULT '',
    archive_payload_json TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(period_key, report_kind, version)
);
CREATE INDEX IF NOT EXISTS idx_weekly_report_period ON weekly_report(period_key, report_kind, version DESC);
CREATE INDEX IF NOT EXISTS idx_weekly_report_state ON weekly_report(workflow_state, updated_at DESC);

CREATE TABLE IF NOT EXISTS dingtalk_robot_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL DEFAULT '',
    conversation_title TEXT NOT NULL DEFAULT '',
    conversation_type TEXT NOT NULL DEFAULT '',
    sender_nick TEXT NOT NULL DEFAULT '',
    sender_id TEXT NOT NULL DEFAULT '',
    robot_code TEXT NOT NULL DEFAULT '',
    message_text TEXT NOT NULL DEFAULT '',
    command TEXT NOT NULL DEFAULT '',
    command_payload TEXT NOT NULL DEFAULT '',
    handle_status TEXT NOT NULL DEFAULT '',
    response_text TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    handled_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_robot_event_created ON dingtalk_robot_event(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_robot_event_conversation ON dingtalk_robot_event(conversation_id);
CREATE INDEX IF NOT EXISTS idx_robot_event_sender ON dingtalk_robot_event(sender_id);

CREATE TABLE IF NOT EXISTS dingtalk_robot_send_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_type TEXT NOT NULL DEFAULT '',
    business_id TEXT NOT NULL DEFAULT '',
    target_type TEXT NOT NULL DEFAULT 'group',
    target_name TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    robot_code TEXT NOT NULL DEFAULT '',
    msg_key TEXT NOT NULL DEFAULT '',
    process_query_key TEXT NOT NULL DEFAULT '',
    send_status TEXT NOT NULL DEFAULT '',
    error_text TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_send_log_created ON dingtalk_robot_send_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_send_log_business ON dingtalk_robot_send_log(business_type, business_id);

CREATE TABLE IF NOT EXISTS job_status (
    job_key TEXT NOT NULL,
    period_key TEXT NOT NULL,
    status TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT NOT NULL DEFAULT '',
    error_text TEXT NOT NULL DEFAULT '',
    ran_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(job_key, period_key)
);
"""


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = (path or settings.database_file).resolve()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            self._migrate(connection)
            connection.commit()

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Apply additive, idempotent migrations for databases created by older releases."""
        weekly_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(weekly_report)").fetchall()
        }
        additions = {
            "source_snapshot_json": "TEXT NOT NULL DEFAULT '[]'",
            "coverage_json": "TEXT NOT NULL DEFAULT '{}'",
            "archive_status": "TEXT NOT NULL DEFAULT ''",
            "archive_record_id": "TEXT NOT NULL DEFAULT ''",
            "archive_error": "TEXT NOT NULL DEFAULT ''",
            "archive_attempted_at": "TEXT NOT NULL DEFAULT ''",
            "archived_at": "TEXT NOT NULL DEFAULT ''",
            "archive_payload_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for column, definition in additions.items():
            if column not in weekly_columns:
                connection.execute(f"ALTER TABLE weekly_report ADD COLUMN {column} {definition}")

        employee_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(employee_cache)").fetchall()
        }
        employee_additions = {
            "job_number": "TEXT NOT NULL DEFAULT ''",
            "primary_dept_id": "TEXT NOT NULL DEFAULT ''",
            "email_normalized": "TEXT NOT NULL DEFAULT ''",
            "affiliation_type": "TEXT NOT NULL DEFAULT ''",
            "employment_status": "TEXT NOT NULL DEFAULT ''",
            "primary_source": "TEXT NOT NULL DEFAULT ''",
            "is_department_leader": "INTEGER NOT NULL DEFAULT 0",
            "is_biz_group_leader": "INTEGER NOT NULL DEFAULT 0",
            "is_company_leader": "INTEGER NOT NULL DEFAULT 0",
            "leader_roles_json": "TEXT NOT NULL DEFAULT '[]'",
        }
        for column, definition in employee_additions.items():
            if column not in employee_columns:
                connection.execute(f"ALTER TABLE employee_cache ADD COLUMN {column} {definition}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_employee_cache_department ON employee_cache(primary_dept_id, department_name)"
        )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(sql, tuple(params))
            return int(cursor.lastrowid or 0)

    def load_config(self, key: str, default: dict[str, Any]) -> dict[str, Any]:
        row = self.fetch_one("SELECT config_json FROM app_config WHERE config_key=?", (key,))
        if not row:
            return json.loads(json.dumps(default, ensure_ascii=False))
        try:
            payload = json.loads(row.get("config_json") or "{}")
        except (TypeError, ValueError):
            return json.loads(json.dumps(default, ensure_ascii=False))
        merged = json.loads(json.dumps(default, ensure_ascii=False))
        if isinstance(payload, dict):
            merged.update(payload)
        return merged

    def save_config(self, key: str, value: dict[str, Any], *, actor: str = "admin") -> None:
        timestamp = to_db(now_local())
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO app_config(config_key, config_json, updated_by, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(config_key) DO UPDATE SET
                    config_json=excluded.config_json,
                    updated_by=excluded.updated_by,
                    updated_at=excluded.updated_at
                """,
                (key, payload, actor, timestamp),
            )


db = Database()
