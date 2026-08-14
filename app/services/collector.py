from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from ..config import settings
from ..db import Database, db
from ..integrations.aitable import AITableClient, TableResult, aitable_client
from ..source_catalog import SOURCE_TABLES
from ..time_utils import from_db, now_local, to_db
from .directory import DirectoryService, directory_service
from .workflow_config import WorkflowConfigService, workflow_config_service


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        values = [_text(item) for item in value]
        return "、".join(dict.fromkeys(item for item in values if item))
    if isinstance(value, dict):
        for key in ("markdown", "text", "name", "label", "title", "value"):
            if value.get(key) not in (None, ""):
                return _text(value.get(key))
        return ""
    return str(value).strip()


def _people(
    value: Any,
    *,
    employees_by_user_id: dict[str, dict[str, Any]],
    employees_by_union_id: dict[str, dict[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    values = value if isinstance(value, list) else [value]
    user_ids: list[str] = []
    names: list[str] = []
    display_names: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        user_id = str(item.get("userId") or item.get("staffId") or item.get("id") or "").strip()
        union_id = str(item.get("unionId") or "").strip()
        cell_name = str(item.get("name") or item.get("label") or "").strip()
        employee = employees_by_user_id.get(user_id, {}) if user_id else {}
        if not employee and union_id:
            employee = employees_by_union_id.get(union_id, {})
            user_id = str(employee.get("user_id") or "").strip()
        employee_name = str(employee.get("employee_name") or "").strip()
        display_name = employee_name or cell_name
        if display_name and display_name not in display_names:
            display_names.append(display_name)
        if user_id and user_id not in user_ids:
            user_ids.append(user_id)
            names.append(display_name or user_id)
    return user_ids, names, display_names


def _date_text(value: Any) -> str:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return to_db(datetime.fromtimestamp(timestamp).astimezone())
        except (OSError, OverflowError, ValueError):
            return ""
    parsed = from_db(_text(value))
    return to_db(parsed) if parsed else ""


def _first(values: dict[str, Any], names: list[str]) -> Any:
    for name in names:
        if name in values and values[name] not in (None, "", [], {}):
            return values[name]
    return None


def _join(values: dict[str, Any], names: list[str], *, separator: str = " / ") -> str:
    result = [_text(values.get(name)) for name in names]
    return separator.join(dict.fromkeys(item for item in result if item))


class SourceCollector:
    def __init__(
        self,
        database: Database | None = None,
        client: AITableClient | None = None,
        directory: DirectoryService | None = None,
        config_service: WorkflowConfigService | None = None,
    ) -> None:
        self.db = database or db
        self.client = client or aitable_client
        self.directory = directory or directory_service
        self.config_service = config_service or workflow_config_service

    @staticmethod
    def _field_values(result: TableResult, record: dict[str, Any]) -> dict[str, Any]:
        field_names: dict[str, str] = {}
        for field in result.fields:
            field_id = str(field.get("fieldId") or field.get("id") or field.get("name") or "").strip()
            name = str(field.get("fieldName") or field.get("name") or field.get("title") or field_id).strip()
            if field_id:
                field_names[field_id] = name
        cells = record.get("cells") or record.get("fields") or record.get("values") or record.get("data") or {}
        if not isinstance(cells, dict):
            cells = {}
        return {field_names.get(str(key), str(key)): value for key, value in cells.items()}

    @staticmethod
    def _metadata_time(record: dict[str, Any], names: tuple[str, ...]) -> str:
        for name in names:
            if record.get(name) not in (None, ""):
                return _date_text(record.get(name))
        return ""

    def _normalize_record(
        self,
        spec: dict[str, Any],
        result: TableResult,
        record: dict[str, Any],
        employees_by_user_id: dict[str, dict[str, Any]],
        employees_by_union_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        values = self._field_values(result, record)
        product_user_ids, product_names, product_display_names = _people(
            _first(values, spec.get("productManagerFields") or spec.get("userFields") or []),
            employees_by_user_id=employees_by_user_id,
            employees_by_union_id=employees_by_union_id,
        )
        overrides = self.config_service.get().get("projectManagerFieldOverrides") or {}
        override_fields = overrides.get(str(spec.get("tableId") or ""), []) if isinstance(overrides, dict) else []
        if isinstance(override_fields, str):
            override_fields = [override_fields]
        project_fields = [*(spec.get("projectManagerFields") or []), *(
            override_fields if isinstance(override_fields, list) else []
        )]
        project_user_ids, project_names, _ = _people(
            _first(values, project_fields),
            employees_by_user_id=employees_by_user_id,
            employees_by_union_id=employees_by_union_id,
        )
        if spec.get("projectView") and not project_user_ids:
            project_roster = {
                str(item.get("userId") or "") for item in self.config_service.get().get("projectManagerRoster") or []
                if item.get("enabled") is not False
            }
            project_user_ids = [item for item in product_user_ids if item in project_roster]
        title = _join(values, spec.get("titleFields") or [])
        if spec.get("roster"):
            title = "、".join(product_display_names)
        status = _text(_first(values, spec.get("statusFields") or []))
        priority = _text(_first(values, spec.get("priorityFields") or []))
        event_at = _date_text(_first(values, spec.get("eventDateFields") or []))
        due_at = _date_text(_first(values, spec.get("dueDateFields") or []))
        progress = _join(values, spec.get("progressFields") or [], separator="；")
        plan = _join(values, spec.get("planFields") or [], separator="；")
        risk_parts: list[str] = []
        if any(flag in status for flag in ("延期", "风险", "暂停", "阻塞")):
            risk_parts.append(f"状态：{status}")
        if priority in {"高", "紧急"}:
            risk_parts.append(f"紧急程度：{priority}")
        record_id = str(
            record.get("recordId") or record.get("record_id") or record.get("id") or record.get("rowId") or ""
        ).strip()
        if not record_id:
            raise ValueError(f"record ID missing in {spec.get('tableName')}")
        raw = {"record": record, "fieldValues": values}
        normalized = {
            "base_id": settings.aitable_base_id.strip(),
            "table_id": spec["tableId"],
            "table_name": spec["tableName"],
            "record_id": record_id,
            "category": "人员名单" if spec.get("roster") else str(spec.get("category") or ""),
            "title": title,
            "status": status,
            "priority": priority,
            "progress_text": progress,
            "plan_text": plan,
            "risk_text": "；".join(risk_parts),
            "product_manager_user_ids_json": json.dumps(product_user_ids, ensure_ascii=False),
            "project_manager_user_ids_json": json.dumps(project_user_ids, ensure_ascii=False),
            "product_manager_names_json": json.dumps(product_names, ensure_ascii=False),
            "project_manager_names_json": json.dumps(project_names, ensure_ascii=False),
            "event_at": event_at,
            "due_at": due_at,
            "source_created_at": self._metadata_time(record, ("createdAt", "createTime", "createdTime")),
            "source_updated_at": self._metadata_time(
                record, ("updatedAt", "updateTime", "modifiedTime", "lastModifiedTime")
            ),
            "raw_json": json.dumps(raw, ensure_ascii=False, separators=(",", ":"), default=str),
        }
        hash_payload = {key: value for key, value in normalized.items() if key != "raw_json"}
        normalized["record_hash"] = hashlib.sha256(
            json.dumps(hash_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return normalized

    def _store_table(self, spec: dict[str, Any], result: TableResult, *, seen_at: str) -> dict[str, int]:
        employees_by_user_id = self.directory.lookup_by_user_id()
        employees_by_union_id = self.directory.lookup_by_union_id()
        normalized = [
            self._normalize_record(
                spec,
                result,
                record,
                employees_by_user_id,
                employees_by_union_id,
            )
            for record in result.records
        ]
        changed = 0
        initial_imported = 0
        seen_ids = {item["record_id"] for item in normalized}
        with self.db.transaction() as connection:
            table_initialized = connection.execute(
                "SELECT 1 FROM source_record WHERE base_id=? AND table_id=? LIMIT 1",
                (settings.aitable_base_id.strip(), spec["tableId"]),
            ).fetchone() is not None
            for item in normalized:
                existing = connection.execute(
                    "SELECT id, record_hash, changed_at, is_deleted FROM source_record WHERE base_id=? AND table_id=? AND record_id=?",
                    (item["base_id"], item["table_id"], item["record_id"]),
                ).fetchone()
                if not existing and not table_initialized:
                    # The first full import establishes a baseline. Preserve source
                    # timestamps when available instead of making every historical
                    # record look like it changed in the current reporting week.
                    changed_at = item["source_updated_at"] or item["source_created_at"] or ""
                    initial_imported += 1
                elif existing and str(existing["record_hash"] or "") == item["record_hash"] and not int(existing["is_deleted"] or 0):
                    changed_at = str(existing["changed_at"] or seen_at)
                else:
                    changed_at = seen_at
                    changed += 1
                columns = [
                    "base_id", "table_id", "table_name", "record_id", "category", "title", "status", "priority",
                    "progress_text", "plan_text", "risk_text", "product_manager_user_ids_json",
                    "project_manager_user_ids_json", "product_manager_names_json", "project_manager_names_json",
                    "event_at", "due_at", "source_created_at", "source_updated_at", "record_hash", "raw_json",
                ]
                values = [item[column] for column in columns]
                connection.execute(
                    f"""
                    INSERT INTO source_record({','.join(columns)}, first_seen_at, last_seen_at, changed_at, is_deleted)
                    VALUES ({','.join(['?'] * len(columns))}, ?, ?, ?, 0)
                    ON CONFLICT(base_id, table_id, record_id) DO UPDATE SET
                        table_name=excluded.table_name, category=excluded.category, title=excluded.title,
                        status=excluded.status, priority=excluded.priority, progress_text=excluded.progress_text,
                        plan_text=excluded.plan_text, risk_text=excluded.risk_text,
                        product_manager_user_ids_json=excluded.product_manager_user_ids_json,
                        project_manager_user_ids_json=excluded.project_manager_user_ids_json,
                        product_manager_names_json=excluded.product_manager_names_json,
                        project_manager_names_json=excluded.project_manager_names_json,
                        event_at=excluded.event_at, due_at=excluded.due_at,
                        source_created_at=excluded.source_created_at, source_updated_at=excluded.source_updated_at,
                        record_hash=excluded.record_hash, raw_json=excluded.raw_json,
                        last_seen_at=excluded.last_seen_at, changed_at=?, is_deleted=0
                    """,
                    (*values, seen_at, seen_at, changed_at, changed_at),
                )
            if seen_ids:
                placeholders = ",".join("?" for _ in seen_ids)
                connection.execute(
                    f"UPDATE source_record SET is_deleted=1 WHERE base_id=? AND table_id=? AND record_id NOT IN ({placeholders})",
                    (settings.aitable_base_id.strip(), spec["tableId"], *sorted(seen_ids)),
                )
            else:
                connection.execute(
                    "UPDATE source_record SET is_deleted=1 WHERE base_id=? AND table_id=?",
                    (settings.aitable_base_id.strip(), spec["tableId"]),
                )
        return {"records": len(normalized), "changed": changed, "initialImported": initial_imported}

    def sync_all(self, *, actor: str = "manual") -> dict[str, Any]:
        started_at = to_db(now_local())
        run_id = self.db.execute(
            "INSERT INTO sync_run(run_type, status, started_at, detail_json) VALUES (?, 'running', ?, ?)",
            (actor, started_at, "{}"),
        )
        detail: list[dict[str, Any]] = []
        total_records = 0
        total_changed = 0
        try:
            for spec in SOURCE_TABLES:
                if spec.get("archive"):
                    continue
                result = self.client.fetch_table(str(spec["tableId"]))
                stored = self._store_table(spec, result, seen_at=to_db(now_local()))
                total_records += stored["records"]
                total_changed += stored["changed"]
                detail.append(
                    {
                        "tableId": spec["tableId"],
                        "tableName": spec["tableName"],
                        "pages": result.pages,
                        **stored,
                    }
                )
        except Exception as exc:
            finished_at = to_db(now_local())
            self.db.execute(
                """
                UPDATE sync_run SET status='error', finished_at=?, table_count=?, record_count=?,
                    changed_count=?, error_text=?, detail_json=? WHERE id=?
                """,
                (
                    finished_at,
                    len(detail),
                    total_records,
                    total_changed,
                    f"{type(exc).__name__}: {exc}"[:2000],
                    json.dumps(detail, ensure_ascii=False),
                    run_id,
                ),
            )
            raise
        finished_at = to_db(now_local())
        self.db.execute(
            """
            UPDATE sync_run SET status='success', finished_at=?, table_count=?, record_count=?,
                changed_count=?, error_text='', detail_json=? WHERE id=?
            """,
            (
                finished_at,
                len(detail),
                total_records,
                total_changed,
                json.dumps(detail, ensure_ascii=False),
                run_id,
            ),
        )
        return {
            "runId": run_id,
            "status": "success",
            "startedAt": started_at,
            "finishedAt": finished_at,
            "tableCount": len(detail),
            "recordCount": total_records,
            "changedCount": total_changed,
            "tables": detail,
        }


collector = SourceCollector()
source_collector = collector
