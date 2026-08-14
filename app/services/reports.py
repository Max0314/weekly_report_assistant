from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from ..db import Database, db
from ..source_catalog import SOURCE_TABLES
from ..time_utils import SHANGHAI, from_db, now_local, to_db, weekly_window
from .ai_summary import AISummaryClient, AISummaryError, ai_summary_client
from .workflow_config import WorkflowConfigService, workflow_config_service


REPORT_KINDS = {"combined", "product", "project"}
PROJECT_TABLE_IDS = {str(item["tableId"]) for item in SOURCE_TABLES if item.get("projectView")}
ROSTER_TABLE_IDS = {str(item["tableId"]) for item in SOURCE_TABLES if item.get("roster")}
FINAL_STATES = {"formal_sent", "recalled", "cancelled"}


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, type(default)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _lines(values: list[str], *, limit: int = 12) -> str:
    cleaned = [str(item or "").strip() for item in values if str(item or "").strip()]
    return "\n".join(f"- {item}" for item in cleaned[:limit]) or "- 暂无"


def _is_closed(status: str) -> bool:
    return any(flag in str(status or "") for flag in ("已完成", "已结束", "中标成功", "关闭", "取消"))


class ReportService:
    def __init__(
        self,
        database: Database | None = None,
        config_service: WorkflowConfigService | None = None,
        ai_client: AISummaryClient | None = None,
    ) -> None:
        self.db = database or db
        self.config_service = config_service or workflow_config_service
        self.ai_client = ai_client or ai_summary_client

    def _window(self, period_key: str = "") -> dict[str, Any]:
        config = self.config_service.get()
        normalized = str(period_key or "").strip()
        if normalized:
            match = re.fullmatch(r"week:(\d{8})", normalized)
            if not match:
                raise ValueError("periodKey must use week:YYYYMMDD")
            start_at = datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=SHANGHAI)
            if start_at.weekday() != 0:
                raise ValueError("periodKey must point to a Monday")
            end_at = start_at + timedelta(
                days=int(config["periodEndWeekday"]),
                hours=int(config["periodEndHour"]),
            )
            return {
                "periodKey": normalized,
                "startAt": start_at,
                "endAt": end_at,
                "label": f"{start_at.strftime('%Y-%m-%d')} 至 {end_at.strftime('%Y-%m-%d %H:%M')}",
            }
        return weekly_window(
            now_local(),
            end_weekday=int(config["periodEndWeekday"]),
            end_hour=int(config["periodEndHour"]),
        )

    @staticmethod
    def _format_source(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(row.get("id") or 0),
            "tableId": str(row.get("table_id") or ""),
            "tableName": str(row.get("table_name") or ""),
            "recordId": str(row.get("record_id") or ""),
            "category": str(row.get("category") or ""),
            "title": str(row.get("title") or ""),
            "status": str(row.get("status") or ""),
            "priority": str(row.get("priority") or ""),
            "progressText": str(row.get("progress_text") or ""),
            "planText": str(row.get("plan_text") or ""),
            "riskText": str(row.get("risk_text") or ""),
            "productManagerUserIds": _json(row.get("product_manager_user_ids_json"), []),
            "projectManagerUserIds": _json(row.get("project_manager_user_ids_json"), []),
            "productManagerNames": _json(row.get("product_manager_names_json"), []),
            "projectManagerNames": _json(row.get("project_manager_names_json"), []),
            "eventAt": str(row.get("event_at") or ""),
            "dueAt": str(row.get("due_at") or ""),
            "sourceUpdatedAt": str(row.get("source_updated_at") or ""),
            "changedAt": str(row.get("changed_at") or ""),
        }

    def source_items(self, *, period_key: str = "", report_kind: str = "combined") -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if report_kind not in REPORT_KINDS:
            raise ValueError(f"unsupported report kind: {report_kind}")
        window = self._window(period_key)
        config = self.config_service.get()
        start_at = window["startAt"]
        end_at = window["endAt"]
        due_end = end_at + timedelta(days=int(config["dueSoonDays"]))
        rows = self.db.fetch_all(
            "SELECT * FROM source_record WHERE is_deleted=0 AND category<>'人员名单' ORDER BY table_name, title"
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            item = self._format_source(row)
            if report_kind == "product" and not item["productManagerUserIds"]:
                continue
            if report_kind == "project" and not (
                item["projectManagerUserIds"] or item["tableId"] in PROJECT_TABLE_IDS
            ):
                continue
            event_at = from_db(item["eventAt"])
            updated_at = from_db(item["sourceUpdatedAt"])
            changed_at = from_db(item["changedAt"])
            due_at = from_db(item["dueAt"])
            status = item["status"]
            include_reasons: list[str] = []
            if event_at and start_at <= event_at <= end_at:
                include_reasons.append("business_date")
            if updated_at and start_at <= updated_at <= end_at:
                include_reasons.append("source_updated")
            if changed_at and start_at <= changed_at <= end_at:
                include_reasons.append("snapshot_changed")
            if due_at and start_at <= due_at <= due_end and not _is_closed(status):
                include_reasons.append("due_soon")
            if item["riskText"] and not _is_closed(status):
                include_reasons.append("risk_open")
            if not include_reasons:
                continue
            item["includeReasons"] = include_reasons
            item["overdue"] = bool(due_at and due_at < now_local() and not _is_closed(status))
            result.append(item)
        return window, result

    @staticmethod
    def _metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
        by_category: dict[str, int] = {}
        by_status: dict[str, int] = {}
        managers: set[str] = set()
        overdue = 0
        risk = 0
        high_priority = 0
        for item in items:
            category = item.get("category") or "未分类"
            status = item.get("status") or "未标记"
            by_category[category] = by_category.get(category, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
            managers.update(str(value) for value in item.get("productManagerNames") or [])
            managers.update(str(value) for value in item.get("projectManagerNames") or [])
            overdue += 1 if item.get("overdue") else 0
            risk += 1 if item.get("riskText") else 0
            high_priority += 1 if item.get("priority") in {"高", "紧急"} else 0
        return {
            "itemCount": len(items),
            "managerCount": len(managers),
            "overdueCount": overdue,
            "riskCount": risk,
            "highPriorityCount": high_priority,
            "byCategory": by_category,
            "byStatus": by_status,
        }

    def _manager_coverage(
        self,
        *,
        window: dict[str, Any],
        items: list[dict[str, Any]],
        report_kind: str,
    ) -> dict[str, Any]:
        expected: dict[tuple[str, str], dict[str, Any]] = {}
        observed: set[tuple[str, str]] = set()

        def add_expected(
            role: str,
            user_id: str,
            name: str = "",
            department: str = "",
            source: str = "",
        ) -> None:
            normalized_user_id = str(user_id or "").strip()
            normalized_name = str(name or "").strip()
            identity = normalized_user_id or (f"name:{normalized_name}" if normalized_name else "")
            if not identity:
                return
            key = (role, identity)
            current = expected.get(key, {})
            expected[key] = {
                "role": role,
                "userId": normalized_user_id or str(current.get("userId") or ""),
                "name": normalized_name or str(current.get("name") or normalized_user_id),
                "department": str(department or current.get("department") or "").strip(),
                "source": source or str(current.get("source") or ""),
            }

        if report_kind in {"combined", "product"} and ROSTER_TABLE_IDS:
            placeholders = ",".join("?" for _ in ROSTER_TABLE_IDS)
            roster_rows = self.db.fetch_all(
                f"SELECT * FROM source_record WHERE is_deleted=0 AND table_id IN ({placeholders})",
                tuple(sorted(ROSTER_TABLE_IDS)),
            )
            for row in roster_rows:
                user_ids = _json(row.get("product_manager_user_ids_json"), [])
                names = _json(row.get("product_manager_names_json"), [])
                if user_ids:
                    for index, user_id in enumerate(user_ids):
                        add_expected(
                            "product",
                            str(user_id),
                            str(names[index] if index < len(names) else ""),
                            source="aitable_roster",
                        )
                else:
                    add_expected("product", "", str(row.get("title") or ""), source="aitable_roster")

        config = self.config_service.get()
        if report_kind in {"combined", "project"}:
            employees = {
                str(item.get("user_id") or ""): item
                for item in self.db.fetch_all(
                    "SELECT user_id, employee_name, department_name, title FROM employee_cache WHERE is_active=1"
                )
                if str(item.get("user_id") or "").strip()
            }
            for person in config.get("projectManagerRoster") or []:
                if person.get("enabled") is False:
                    continue
                user_id = str(person.get("userId") or "")
                employee = employees.get(user_id, {})
                add_expected(
                    "project",
                    user_id,
                    str(person.get("name") or employee.get("employee_name") or ""),
                    str(employee.get("department_name") or ""),
                    source="manual_roster",
                )
            keywords = [str(item).strip().lower() for item in config.get("projectManagerTitleKeywords") or [] if str(item).strip()]
            if keywords:
                for user_id, employee in employees.items():
                    title = str(employee.get("title") or "")
                    if any(keyword in title.lower() for keyword in keywords):
                        add_expected(
                            "project",
                            user_id,
                            str(employee.get("employee_name") or ""),
                            str(employee.get("department_name") or ""),
                            source="bi_center_title",
                        )

        for item in items:
            if report_kind in {"combined", "product"}:
                product_ids = item.get("productManagerUserIds") or []
                product_names = item.get("productManagerNames") or []
                for index, user_id in enumerate(product_ids):
                    identity = str(user_id or "").strip()
                    if identity:
                        observed.add(("product", identity))
                    elif index < len(product_names) and str(product_names[index]).strip():
                        observed.add(("product", f"name:{str(product_names[index]).strip()}"))
            if report_kind in {"combined", "project"}:
                project_ids = item.get("projectManagerUserIds") or []
                project_names = item.get("projectManagerNames") or []
                for index, user_id in enumerate(project_ids):
                    identity = str(user_id or "").strip()
                    if identity:
                        observed.add(("project", identity))
                    elif index < len(project_names) and str(project_names[index]).strip():
                        observed.add(("project", f"name:{str(project_names[index]).strip()}"))

        people: list[dict[str, Any]] = []
        for key, person in sorted(expected.items(), key=lambda pair: (pair[0][0], pair[1].get("name") or pair[0][1])):
            people.append({**person, "covered": key in observed})
        covered_count = sum(1 for person in people if person["covered"])
        missing = [person for person in people if not person["covered"]]
        return {
            "periodKey": str(window.get("periodKey") or ""),
            "label": str(window.get("label") or ""),
            "reportKind": report_kind,
            "expectedCount": len(people),
            "coveredCount": covered_count,
            "missingCount": len(missing),
            "people": people,
            "missing": missing,
        }

    def manager_coverage(self, *, period_key: str = "", report_kind: str = "combined") -> dict[str, Any]:
        window, items = self.source_items(period_key=period_key, report_kind=report_kind)
        return self._manager_coverage(window=window, items=items, report_kind=report_kind)

    @staticmethod
    def _draft_sections(items: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, str]:
        product_lines: list[str] = []
        project_lines: list[str] = []
        risk_lines: list[str] = []
        plan_lines: list[str] = []
        support_lines: list[str] = []
        for item in items:
            managers = "、".join(item.get("productManagerNames") or item.get("projectManagerNames") or [])
            manager_suffix = f"（{managers}）" if managers else ""
            progress = item.get("progressText") or item.get("status") or "已纳入本周跟踪"
            line = f"【{item.get('category') or '事项'}】{item.get('title') or '未命名事项'}：{progress}{manager_suffix}"
            if item.get("tableId") in PROJECT_TABLE_IDS:
                project_lines.append(line)
            else:
                product_lines.append(line)
            if item.get("riskText") or item.get("overdue"):
                evidence = item.get("riskText") or "已超过截止日期"
                risk_lines.append(f"{item.get('title') or '未命名事项'}：{evidence}")
            if item.get("planText"):
                plan_lines.append(f"{item.get('title') or '未命名事项'}：{item.get('planText')}")
            if item.get("priority") in {"高", "紧急"} and not _is_closed(str(item.get("status") or "")):
                support_lines.append(f"{item.get('title') or '未命名事项'}：高优先级，当前状态 {item.get('status') or '未标记'}")
        summary = (
            f"本周共纳入 {metrics['itemCount']} 项过程事项，涉及 {metrics['managerCount']} 名负责人；"
            f"其中风险事项 {metrics['riskCount']} 项、逾期事项 {metrics['overdueCount']} 项、"
            f"高优先级事项 {metrics['highPriorityCount']} 项。"
        )
        return {
            "executiveSummary": summary,
            "productHighlights": _lines(product_lines),
            "projectHighlights": _lines(project_lines),
            "risks": _lines(risk_lines),
            "nextPlans": _lines(plan_lines),
            "supportNeeds": _lines(support_lines),
        }

    def generate(
        self,
        *,
        period_key: str = "",
        report_kind: str = "combined",
        actor: str = "admin",
        use_ai: bool = True,
    ) -> dict[str, Any]:
        window, items = self.source_items(period_key=period_key, report_kind=report_kind)
        metrics = self._metrics(items)
        coverage = self._manager_coverage(window=window, items=items, report_kind=report_kind)
        metrics["coverage"] = {
            "expectedCount": coverage["expectedCount"],
            "coveredCount": coverage["coveredCount"],
            "missingCount": coverage["missingCount"],
        }
        config = self.config_service.get()
        fallback = self._draft_sections(items, metrics)
        sections = fallback
        ai_status = "deterministic"
        ai_error = ""
        if use_ai:
            try:
                sections = self.ai_client.summarize(
                    window={
                        "periodKey": window["periodKey"],
                        "label": window["label"],
                        "startAt": to_db(window["startAt"]),
                        "endAt": to_db(window["endAt"]),
                    },
                    metrics=metrics,
                    items=items,
                    fallback=fallback,
                    project_baseline=config.get("projectBaseline") or [],
                )
                ai_status = "success"
            except AISummaryError as exc:
                ai_status = "fallback"
                ai_error = str(exc)[:2000]
        latest = self.db.fetch_one(
            "SELECT MAX(version) AS version FROM weekly_report WHERE period_key=? AND report_kind=?",
            (window["periodKey"], report_kind),
        ) or {}
        version = int(latest.get("version") or 0) + 1
        timestamp = to_db(now_local())
        title_suffix = {"combined": "", "product": "（产品经理版）", "project": "（项目经理版）"}[report_kind]
        report_id = self.db.execute(
            """
            INSERT INTO weekly_report(
                period_key, report_kind, version, title, window_json, sections_json,
                metrics_json, source_record_ids_json, source_snapshot_json, coverage_json,
                workflow_state, ai_status, ai_error,
                created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft_generated', ?, ?, ?, ?, ?)
            """,
            (
                window["periodKey"],
                report_kind,
                version,
                f"{config['reportTitle']}{title_suffix}",
                json.dumps(
                    {
                        "periodKey": window["periodKey"],
                        "label": window["label"],
                        "startAt": to_db(window["startAt"]),
                        "endAt": to_db(window["endAt"]),
                    },
                    ensure_ascii=False,
                ),
                json.dumps(sections, ensure_ascii=False),
                json.dumps(metrics, ensure_ascii=False),
                json.dumps([item["id"] for item in items]),
                json.dumps(items, ensure_ascii=False, separators=(",", ":")),
                json.dumps(coverage, ensure_ascii=False, separators=(",", ":")),
                ai_status,
                ai_error,
                actor,
                timestamp,
                timestamp,
            ),
        )
        return self.get(report_id, include_sources=True)

    @staticmethod
    def _format_report(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(row.get("id") or 0),
            "periodKey": str(row.get("period_key") or ""),
            "reportKind": str(row.get("report_kind") or "combined"),
            "version": int(row.get("version") or 0),
            "title": str(row.get("title") or ""),
            "window": _json(row.get("window_json"), {}),
            "sections": _json(row.get("sections_json"), {}),
            "metrics": _json(row.get("metrics_json"), {}),
            "sourceRecordIds": _json(row.get("source_record_ids_json"), []),
            "coverage": _json(row.get("coverage_json"), {}),
            "workflowState": str(row.get("workflow_state") or ""),
            "aiStatus": str(row.get("ai_status") or ""),
            "aiError": str(row.get("ai_error") or ""),
            "imageReady": bool(str(row.get("image_path") or "")),
            "imageGeneratedAt": str(row.get("image_generated_at") or ""),
            "previewedAt": str(row.get("previewed_at") or ""),
            "confirmStatus": str(row.get("confirm_status") or ""),
            "confirmedBy": str(row.get("confirmed_by") or ""),
            "confirmedAt": str(row.get("confirmed_at") or ""),
            "changeRequest": str(row.get("change_request") or ""),
            "sendStatus": str(row.get("send_status") or ""),
            "sendError": str(row.get("send_error") or ""),
            "sentAt": str(row.get("sent_at") or ""),
            "archive": {
                "status": str(row.get("archive_status") or ""),
                "recordId": str(row.get("archive_record_id") or ""),
                "error": str(row.get("archive_error") or ""),
                "attemptedAt": str(row.get("archive_attempted_at") or ""),
                "archivedAt": str(row.get("archived_at") or ""),
            },
            "createdBy": str(row.get("created_by") or ""),
            "createdAt": str(row.get("created_at") or ""),
            "updatedAt": str(row.get("updated_at") or ""),
        }

    def get(self, report_id: int, *, include_sources: bool = False) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM weekly_report WHERE id=?", (int(report_id),))
        if not row:
            raise ValueError("weekly report not found")
        result = self._format_report(row)
        if include_sources:
            snapshot = _json(row.get("source_snapshot_json"), [])
            source_ids = result["sourceRecordIds"]
            if snapshot:
                result["sources"] = snapshot
                result["sourceSnapshot"] = True
            elif source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                source_rows = self.db.fetch_all(
                    f"SELECT * FROM source_record WHERE id IN ({placeholders}) ORDER BY table_name, title",
                    tuple(source_ids),
                )
                result["sources"] = [self._format_source(item) for item in source_rows]
                result["sourceSnapshot"] = False
            else:
                result["sources"] = []
                result["sourceSnapshot"] = True
        return result

    def latest(self, *, period_key: str = "", report_kind: str = "combined") -> dict[str, Any] | None:
        if period_key:
            row = self.db.fetch_one(
                "SELECT * FROM weekly_report WHERE period_key=? AND report_kind=? ORDER BY version DESC LIMIT 1",
                (period_key, report_kind),
            )
        else:
            row = self.db.fetch_one(
                "SELECT * FROM weekly_report WHERE report_kind=? ORDER BY created_at DESC, version DESC LIMIT 1",
                (report_kind,),
            )
        return self._format_report(row) if row else None

    def latest_any(self) -> dict[str, Any] | None:
        row = self.db.fetch_one(
            "SELECT * FROM weekly_report ORDER BY created_at DESC, id DESC LIMIT 1"
        )
        return self._format_report(row) if row else None

    def list(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.fetch_all("SELECT * FROM weekly_report ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))
        return [self._format_report(row) for row in rows]

    def update_sections(self, report_id: int, sections: dict[str, Any], *, actor: str) -> dict[str, Any]:
        report = self.get(report_id)
        if report["workflowState"] in FINAL_STATES:
            raise ValueError("final report cannot be edited")
        merged = {**report["sections"]}
        for key in (
            "executiveSummary", "productHighlights", "projectHighlights", "risks", "nextPlans", "supportNeeds"
        ):
            if key in sections:
                merged[key] = str(sections.get(key) or "").strip()
        timestamp = to_db(now_local())
        self.db.execute(
            """
            UPDATE weekly_report SET sections_json=?, workflow_state='draft_generated',
                confirm_status='', change_request='', image_path='', image_generated_at='', updated_at=? WHERE id=?
            """,
            (json.dumps(merged, ensure_ascii=False), timestamp, int(report_id)),
        )
        return self.get(report_id, include_sources=True)

    def approve(self, report_id: int, *, actor: str) -> dict[str, Any]:
        report = self.get(report_id)
        if report["workflowState"] in FINAL_STATES:
            raise ValueError("report is already final")
        if report["workflowState"] == "need_changes":
            raise ValueError("report requires changes before approval")
        if self.config_service.get().get("requirePreviewBeforeFormal"):
            if not report.get("previewedAt"):
                raise ValueError("report must be previewed before approval")
            if report.get("workflowState") != "awaiting_approval":
                raise ValueError("all preview messages must succeed before approval")
        timestamp = to_db(now_local())
        self.db.execute(
            """
            UPDATE weekly_report SET workflow_state='approved', confirm_status='confirmed',
                confirmed_by=?, confirmed_at=?, updated_at=? WHERE id=?
            """,
            (actor, timestamp, timestamp, int(report_id)),
        )
        return self.get(report_id)

    def request_changes(self, report_id: int, *, actor: str, reason: str) -> dict[str, Any]:
        report = self.get(report_id)
        if report["workflowState"] in FINAL_STATES:
            raise ValueError("final report cannot be returned")
        timestamp = to_db(now_local())
        self.db.execute(
            """
            UPDATE weekly_report SET workflow_state='need_changes', confirm_status='need_changes',
                confirmed_by=?, confirmed_at=?, change_request=?, updated_at=? WHERE id=?
            """,
            (actor, timestamp, str(reason or "").strip()[:2000], timestamp, int(report_id)),
        )
        return self.get(report_id)

    def cancel(self, report_id: int, *, actor: str) -> dict[str, Any]:
        report = self.get(report_id)
        if report["workflowState"] == "formal_sent":
            raise ValueError("sent report must be recalled instead of cancelled")
        timestamp = to_db(now_local())
        self.db.execute(
            """
            UPDATE weekly_report SET workflow_state='cancelled', confirm_status='cancelled',
                confirmed_by=?, confirmed_at=?, updated_at=? WHERE id=?
            """,
            (actor, timestamp, timestamp, int(report_id)),
        )
        return self.get(report_id)


report_service = ReportService()
