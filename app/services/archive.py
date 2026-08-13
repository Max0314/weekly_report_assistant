from __future__ import annotations

import json
from typing import Any

from ..db import Database, db
from ..integrations.aitable import AITableClient, TableResult, aitable_client
from ..time_utils import from_db, now_local, to_db
from .reports import ReportService, report_service
from .workflow_config import WorkflowConfigService, workflow_config_service


class ArchiveError(RuntimeError):
    pass


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "、".join(item for item in (_text(item) for item in value) if item)
    if isinstance(value, dict):
        for key in ("markdown", "text", "name", "value", "title"):
            if value.get(key) not in (None, ""):
                return _text(value.get(key))
    return str(value).strip()


def _field_id(field: dict[str, Any]) -> str:
    return _text(field.get("fieldId") or field.get("id"))


def _field_name(field: dict[str, Any]) -> str:
    return _text(field.get("fieldName") or field.get("name") or field.get("title"))


def _field_type(field: dict[str, Any]) -> str:
    value = field.get("type") or field.get("fieldType") or field.get("valueType") or ""
    if isinstance(value, dict):
        value = value.get("type") or value.get("name") or ""
    return _text(value).lower()


def _record_id(record: dict[str, Any]) -> str:
    return _text(record.get("recordId") or record.get("record_id") or record.get("id"))


class ArchiveService:
    """Idempotently write a formally sent report into the configured archive table."""

    def __init__(
        self,
        database: Database | None = None,
        reports: ReportService | None = None,
        config_service: WorkflowConfigService | None = None,
        client: AITableClient | None = None,
    ) -> None:
        self.db = database or db
        self.reports = reports or report_service
        self.config_service = config_service or workflow_config_service
        self.client = client or aitable_client

    @staticmethod
    def _semantic_payload(report: dict[str, Any], report_url: str) -> dict[str, Any]:
        metrics = report.get("metrics") or {}
        sections = report.get("sections") or {}
        period_key = _text(report.get("periodKey"))
        report_kind = _text(report.get("reportKind") or "combined")
        version = int(report.get("version") or 0)
        return {
            "archiveKey": f"weekly-report:{period_key}:{report_kind}:v{version}",
            "title": _text(report.get("title")),
            "periodKey": period_key,
            "reportKind": report_kind,
            "version": version,
            "summary": _text(sections.get("executiveSummary")),
            "sentAt": _text(report.get("sentAt")),
            "reportUrl": _text(report_url),
            "status": "已发送",
            "itemCount": int(metrics.get("itemCount") or 0),
            "riskCount": int(metrics.get("riskCount") or 0),
            "overdueCount": int(metrics.get("overdueCount") or 0),
            "coverageMissingCount": int((metrics.get("coverage") or {}).get("missingCount") or 0),
        }

    @staticmethod
    def _resolve_fields(
        result: TableResult, field_map: dict[str, str]
    ) -> dict[str, dict[str, Any]]:
        by_reference: dict[str, dict[str, Any]] = {}
        for field in result.fields:
            field_id = _field_id(field)
            field_name = _field_name(field)
            if field_id:
                by_reference[field_id] = field
            if field_name:
                by_reference[field_name] = field
        resolved: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        for semantic, reference in field_map.items():
            field = by_reference.get(_text(reference))
            if not field or not _field_id(field):
                missing.append(f"{semantic}={reference}")
            else:
                resolved[semantic] = field
        if missing:
            raise ArchiveError("archive fields were not found: " + ", ".join(missing))
        return resolved

    @staticmethod
    def _cell_value(field: dict[str, Any], semantic: str, value: Any) -> Any:
        kind = _field_type(field)
        if kind == "richtext":
            return {"markdown": _text(value)}
        if kind == "url" and value:
            return {"text": "查看周报", "link": _text(value)}
        if kind in {"number", "currency", "rating"}:
            return value
        if kind == "checkbox":
            return bool(value)
        if kind in {
            "text", "date", "singleselect", "telephone", "email", "barcode", "idcard"
        }:
            return value
        raise ArchiveError(
            f"archive semantic field {semantic} does not support target field type {kind or 'unknown'}"
        )

    @staticmethod
    def _find_existing(
        result: TableResult, archive_field_id: str, archive_key: str
    ) -> str:
        for record in result.records:
            cells = record.get("cells") or record.get("fields") or record.get("values") or {}
            if isinstance(cells, dict) and _text(cells.get(archive_field_id)) == archive_key:
                return _record_id(record)
        return ""

    def _mark_error(self, report_id: int, message: str) -> None:
        timestamp = to_db(now_local())
        self.db.execute(
            """
            UPDATE weekly_report SET archive_status='error', archive_error=?,
                archive_attempted_at=?, updated_at=? WHERE id=?
            """,
            (message[:2000], timestamp, timestamp, int(report_id)),
        )

    def write(self, report_id: int, *, report_url: str = "") -> dict[str, Any]:
        config = self.config_service.get()
        if not config.get("archiveWriteEnabled"):
            return {"status": "disabled", "skipped": True, "recordId": "", "error": ""}

        timestamp = to_db(now_local())
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT workflow_state, archive_status, archive_record_id, archive_attempted_at
                FROM weekly_report WHERE id=?
                """,
                (int(report_id),),
            ).fetchone()
            if not row:
                raise ArchiveError("weekly report not found")
            if str(row["workflow_state"] or "") != "formal_sent":
                raise ArchiveError("only a formally sent report can be archived")
            if str(row["archive_status"] or "") == "sent":
                return {
                    "status": "sent",
                    "skipped": True,
                    "recordId": str(row["archive_record_id"] or ""),
                    "error": "",
                }
            if str(row["archive_status"] or "") == "pending":
                attempted_at = from_db(row["archive_attempted_at"])
                if attempted_at and (now_local() - attempted_at).total_seconds() < 600:
                    raise ArchiveError("archive write is already in progress")
            connection.execute(
                """
                UPDATE weekly_report SET archive_status='pending', archive_error='',
                    archive_attempted_at=?, updated_at=? WHERE id=?
                """,
                (timestamp, timestamp, int(report_id)),
            )

        try:
            report = self.reports.get(report_id)
            semantic = self._semantic_payload(report, report_url)
            table_id = _text(config.get("archiveTableId"))
            field_map = config.get("archiveFieldMap") or {}
            result = self.client.fetch_table(table_id, page_limit=100)
            resolved = self._resolve_fields(result, field_map)
            archive_field_id = _field_id(resolved["archiveKey"])
            existing_id = self._find_existing(result, archive_field_id, semantic["archiveKey"])
            cells = {
                _field_id(field): self._cell_value(field, key, semantic.get(key))
                for key, field in resolved.items()
                if key in semantic and semantic.get(key) not in (None, "")
            }
            record_id = existing_id
            recovered = bool(existing_id)
            if not record_id:
                created = self.client.create_record(table_id, cells)
                record_id = _text(created.get("recordId"))
                if not record_id:
                    raise ArchiveError(
                        "archive create returned no recordId; retry will first search archiveKey to avoid duplicates"
                    )
            completed_at = to_db(now_local())
            self.db.execute(
                """
                UPDATE weekly_report SET archive_status='sent', archive_record_id=?, archive_error='',
                    archived_at=?, archive_payload_json=?, updated_at=? WHERE id=?
                """,
                (
                    record_id,
                    completed_at,
                    json.dumps(semantic, ensure_ascii=False, separators=(",", ":")),
                    completed_at,
                    int(report_id),
                ),
            )
            return {
                "status": "sent",
                "skipped": recovered,
                "recovered": recovered,
                "recordId": record_id,
                "error": "",
            }
        except Exception as exc:
            self._mark_error(report_id, str(exc))
            if isinstance(exc, ArchiveError):
                raise
            raise ArchiveError(str(exc)) from exc


archive_service = ArchiveService()
