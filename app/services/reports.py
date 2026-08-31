from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from ..db import Database, db
from ..source_catalog import SOURCE_TABLES, SOURCE_TABLE_BY_ID, TEAMBITION_TABLE_ID
from ..time_utils import SHANGHAI, from_db, now_local, to_db, weekly_window
from .ai_summary import AISummaryClient, AISummaryError, ai_summary_client
from .workflow_config import WorkflowConfigService, workflow_config_service


REPORT_KINDS = {"combined", "product", "project"}
PROJECT_TABLE_IDS = {
    str(item["tableId"]) for item in SOURCE_TABLES if item.get("projectView")
}
KEY_PROJECT_TABLE_ID = next(
    str(item["tableId"]) for item in SOURCE_TABLES if item.get("key") == "projects"
)
ROSTER_TABLE_IDS = {str(item["tableId"]) for item in SOURCE_TABLES if item.get("roster")}
FINAL_STATES = {"formal_sent", "recalled", "cancelled"}
TB_DEGREE_LABELS = {
    "minor": "轻微",
    "low": "较低",
    "normal": "正常",
    "risky": "风险",
    "urgent": "紧急",
}


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


def _compact(value: Any, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ；;")
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


def _normalized_digest(value: Any, *, limit: int = 5) -> str:
    if isinstance(value, list):
        raw_lines = [str(item or "") for item in value]
    else:
        raw_lines = str(value or "").splitlines()
    lines = [
        _compact(re.sub(r"^(?:[-•*]|\d{1,2}[.、])\s*", "", line.strip()), 150)
        for line in raw_lines
        if line.strip()
    ]
    return _lines(lines, limit=limit)


def _is_closed(status: str) -> bool:
    return any(flag in str(status or "") for flag in ("已完成", "已结束", "中标成功", "关闭", "取消"))


def _tb_status_digest(value: Any, *, limit: int = 480) -> str:
    text = str(value or "").replace("\r", "\n")
    fragments: list[str] = []
    for raw in re.split(r"\n+", text):
        line = re.sub(r"^(?:[-•*]|[一二三四五六七八九十]+[、.]|\d{1,2}[.、])\s*", "", raw.strip())
        if not line or re.fullmatch(r"(?:本周重点工作|本周工作|项目进展|风险|下周计划)", line):
            continue
        fragments.append(_compact(line, 180))
        if len("；".join(fragments)) >= limit or len(fragments) >= 4:
            break
    digest = "；".join(fragments)
    return _compact(digest, limit)


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
        item = {
            "id": int(row.get("id") or 0),
            "tableId": str(row.get("table_id") or ""),
            "tableName": str(row.get("table_name") or ""),
            "recordId": str(row.get("record_id") or ""),
            "categoryKey": str(row.get("category_key") or ""),
            "categoryOrder": int(row.get("category_order") or 999),
            "category": str(row.get("category") or ""),
            "subcategory": str(row.get("subcategory") or ""),
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
            "assignees": _json(row.get("assignees_json"), []),
            "eventAt": str(row.get("event_at") or ""),
            "dueAt": str(row.get("due_at") or ""),
            "sourceUpdatedAt": str(row.get("source_updated_at") or ""),
            "changedAt": str(row.get("changed_at") or ""),
        }
        return ReportService._hydrate_source(item)

    @staticmethod
    def _hydrate_source(value: dict[str, Any]) -> dict[str, Any]:
        item = {**value}
        table_id = str(item.get("tableId") or "")
        spec = SOURCE_TABLE_BY_ID.get(table_id, {})
        had_category_key = bool(item.get("categoryKey"))
        if not had_category_key:
            item["categoryKey"] = str(spec.get("categoryKey") or spec.get("key") or table_id or "uncategorized")
            if spec.get("category"):
                # Rows and historical snapshots created before category keys
                # used broader labels. Bind those legacy facts to the current
                # table taxonomy without rewriting their stored snapshot.
                item["category"] = str(spec["category"])
        if not item.get("categoryOrder") or int(item.get("categoryOrder") or 999) == 999:
            item["categoryOrder"] = int(spec.get("categoryOrder") or (45 if table_id == TEAMBITION_TABLE_ID else 999))
        if not item.get("category"):
            item["category"] = str(spec.get("category") or item.get("tableName") or "未分类")
        assignees = [entry for entry in item.get("assignees") or [] if isinstance(entry, dict)]
        if not assignees:
            seen: set[tuple[str, str]] = set()
            for role, ids_key, names_key in (
                ("产品经理", "productManagerUserIds", "productManagerNames"),
                ("项目负责人", "projectManagerUserIds", "projectManagerNames"),
            ):
                user_ids = item.get(ids_key) or []
                names = item.get(names_key) or []
                for index, user_id in enumerate(user_ids):
                    normalized = str(user_id or "").strip()
                    if not normalized or (normalized, role) in seen:
                        continue
                    seen.add((normalized, role))
                    assignees.append(
                        {
                            "userId": normalized,
                            "name": str(names[index] if index < len(names) else normalized),
                            "role": role,
                        }
                    )
        item["assignees"] = assignees
        return item

    def _teambition_key_projects(self) -> dict[str, dict[str, Any]]:
        rows = self.db.fetch_all(
            """
            SELECT * FROM teambition_project
            WHERE is_key_project=1 AND is_archived=0 AND matched_record_id<>''
            """
        )
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                progress = float(row.get("progress_percent"))
            except (TypeError, ValueError):
                progress = -1
            progress_value: int | float | None = (
                None
                if progress < 0
                else (int(progress) if progress.is_integer() else progress)
            )
            result[str(row.get("matched_record_id") or "")] = {
                "projectId": str(row.get("project_id") or ""),
                "name": str(row.get("name") or ""),
                "projectCode": str(row.get("project_code") or ""),
                "progressPercent": progress_value,
                "suspended": bool(row.get("is_suspended")),
                "startAt": str(row.get("start_at") or ""),
                "endAt": str(row.get("end_at") or ""),
                "updatedAt": str(row.get("source_updated_at") or ""),
                "statusName": str(row.get("status_name") or ""),
                "statusDegree": str(row.get("status_degree") or ""),
                "statusDegreeLabel": TB_DEGREE_LABELS.get(
                    str(row.get("status_degree") or ""),
                    str(row.get("status_degree") or ""),
                ),
                "statusContent": str(row.get("status_content") or ""),
                "statusSummary": _tb_status_digest(row.get("status_content")),
                "statusCreatedAt": str(row.get("status_created_at") or ""),
                "matchType": str(row.get("match_type") or ""),
            }
        return result

    @staticmethod
    def _merge_teambition_project(
        item: dict[str, Any], project: dict[str, Any] | None
    ) -> dict[str, Any]:
        if not project:
            return item
        enriched = {**item, "teambitionProject": project}
        risks: list[str] = [str(item.get("riskText") or "").strip()]
        if project.get("suspended"):
            risks.append("TB 项目当前已暂停")
        if project.get("statusDegree") in {"risky", "urgent"}:
            status_name = str(project.get("statusName") or "项目状态")
            degree = str(project.get("statusDegreeLabel") or "风险")
            risks.append(f"TB {status_name}标记为{degree}")
        enriched["riskText"] = "；".join(dict.fromkeys(value for value in risks if value))
        return enriched

    def source_items(self, *, period_key: str = "", report_kind: str = "combined") -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if report_kind not in REPORT_KINDS:
            raise ValueError(f"unsupported report kind: {report_kind}")
        window = self._window(period_key)
        config = self.config_service.get()
        start_at = window["startAt"]
        end_at = window["endAt"]
        due_end = end_at + timedelta(days=int(config["dueSoonDays"]))
        rows = self.db.fetch_all(
            """
            SELECT * FROM source_record
            WHERE is_deleted=0 AND category<>'人员名单'
            ORDER BY category_order, table_name, title
            """
        )
        teambition_projects = (
            self._teambition_key_projects()
            if config.get("teambitionIncludeInReports")
            else {}
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            item = self._format_source(row)
            if item["tableId"] == TEAMBITION_TABLE_ID:
                continue
            if item["tableId"] == KEY_PROJECT_TABLE_ID:
                item = self._merge_teambition_project(
                    item, teambition_projects.get(item["recordId"])
                )
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
            teambition_status_at = from_db(
                (item.get("teambitionProject") or {}).get("statusCreatedAt")
                or (item.get("teambitionProject") or {}).get("updatedAt")
            )
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
            if teambition_status_at and start_at <= teambition_status_at <= end_at:
                include_reasons.append("teambition_project_status")
            if not include_reasons:
                continue
            item["includeReasons"] = include_reasons
            item["overdue"] = bool(due_at and due_at < now_local() and not _is_closed(status))
            result.append(item)
        return window, result

    @staticmethod
    def _metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
        by_category: dict[str, int] = {}
        by_subcategory: dict[str, dict[str, int]] = {}
        by_status: dict[str, int] = {}
        managers: set[str] = set()
        overdue = 0
        risk = 0
        high_priority = 0
        for item in items:
            category = item.get("category") or "未分类"
            status = item.get("status") or "未标记"
            by_category[category] = by_category.get(category, 0) + 1
            subcategory = str(item.get("subcategory") or "").strip()
            if subcategory:
                category_subcategories = by_subcategory.setdefault(category, {})
                category_subcategories[subcategory] = category_subcategories.get(subcategory, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
            assignees = [entry for entry in item.get("assignees") or [] if isinstance(entry, dict)]
            if assignees:
                managers.update(
                    str(entry.get("userId") or entry.get("name") or "").strip()
                    for entry in assignees
                    if str(entry.get("userId") or entry.get("name") or "").strip()
                )
            else:
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
            "bySubcategory": by_subcategory,
            "byStatus": by_status,
        }

    @staticmethod
    def _category_digest(
        key: str,
        label: str,
        category_items: list[dict[str, Any]],
        by_status: dict[str, int],
        by_subcategory: dict[str, int],
    ) -> str:
        if not category_items:
            return "- 暂无填报。"

        def count_text(values: dict[str, int], preferred: list[str] | None = None) -> str:
            order = preferred or []
            keys = [key for key in order if values.get(key)]
            keys.extend(key for key in values if key not in keys and values.get(key))
            return "、".join(f"{name} {values[name]} 项" for name in keys)

        def item_text(item: dict[str, Any], progress_limit: int = 72) -> str:
            title = _compact(item.get("title") or "未命名事项", 42)
            progress = _compact(
                item.get("progressText") or item.get("status") or "已纳入本周跟踪",
                progress_limit,
            )
            return f"{title}：{progress}"

        def representative_items(limit: int = 3) -> list[str]:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for item in category_items:
                title = str(item.get("title") or "未命名事项").strip()
                group_key = re.sub(r"\s+", "", title).lower()
                if key == "customer_visit":
                    group_key = re.split(r"[（(]", group_key, maxsplit=1)[0]
                grouped.setdefault(group_key, []).append(item)
            ranked = sorted(
                grouped.values(),
                key=lambda group: (
                    not any(item.get("riskText") or item.get("overdue") for item in group),
                    not any(item.get("progressText") for item in group),
                    -len(group),
                ),
            )
            result: list[str] = []
            for group in ranked[:limit]:
                chosen = max(group, key=lambda item: len(str(item.get("progressText") or "")))
                text = item_text(chosen)
                if len(group) > 1:
                    title, separator, progress = text.partition("：")
                    text = f"{title}（{len(group)} 次）{separator}{progress}"
                result.append(text)
            return result

        lines: list[str] = []
        item_count = len(category_items)
        if key == "key_project":
            lines.append(
                f"项目盘面共 {item_count} 项："
                f"{count_text(by_status, ['正常', '风险', '延期', '暂停', '未标记'])}。"
            )
            abnormal = [
                item for item in category_items
                if str(item.get("status") or "") in {"风险", "延期"} or item.get("riskText") or item.get("overdue")
            ]
            if abnormal:
                names = "、".join(_compact(item.get("title") or "未命名项目", 28) for item in abnormal[:5])
                suffix = f"等 {len(abnormal)} 项" if len(abnormal) > 5 else ""
                lines.append(f"风险/延期聚焦：{names}{suffix}，具体依据见“风险聚焦”。")
            paused = [item for item in category_items if "暂停" in str(item.get("status") or "")]
            if paused:
                names = "、".join(_compact(item.get("title") or "未命名项目", 30) for item in paused[:4])
                lines.append(f"暂停项目 {len(paused)} 项：{names}，待需求或送测节点明确后重启。")
            missing = sum(1 for item in category_items if not str(item.get("progressText") or "").strip())
            if missing:
                lines.append(f"有 {missing} 项未填写本周进展，建议正式发送前补齐。")
            normal = [
                item for item in category_items
                if str(item.get("status") or "") == "正常" and str(item.get("progressText") or "").strip()
            ]
            if normal:
                highlights = "；".join(item_text(item, 48) for item in normal[:2])
                lines.append(f"推进亮点：{highlights}。")
        elif key == "support_todo":
            lines.append(
                f"支持及待办共 {item_count} 项："
                f"{count_text(by_status, ['待处理', '进行中', '已完成'])}。"
            )
            high_pending = [
                item for item in category_items
                if item.get("priority") in {"高", "紧急"} and not _is_closed(str(item.get("status") or ""))
            ]
            if high_pending:
                lines.append("高优先级未完成：" + "；".join(item_text(item, 60) for item in high_pending[:3]) + "。")
            completed = [item for item in category_items if _is_closed(str(item.get("status") or ""))]
            if completed:
                lines.append(f"已完成 {len(completed)} 项：" + "、".join(_compact(item.get("title"), 44) for item in completed[:3]) + "。")
            unnamed = sum(1 for item in category_items if not str(item.get("title") or "").strip())
            if unnamed:
                lines.append(f"数据质量：{unnamed} 条待办未填写事项描述。")
        else:
            if by_subcategory:
                lines.append(f"本周共 {item_count} 项：{count_text(by_subcategory)}。")
            elif by_status:
                lines.append(
                    f"本周共 {item_count} 项："
                    f"{count_text(by_status, ['进行中', '已完成', '已结束', '待处理'])}。"
                )
            else:
                lines.append(f"本周共 {item_count} 项。")
            representatives = representative_items(3)
            if representatives:
                lines.append("重点进展：" + "；".join(representatives) + "。")
            remaining = item_count - min(item_count, 3)
            if remaining > 0:
                lines.append(f"其余 {remaining} 项已纳入详情页，消息中不再逐条展开。")
        return _lines(lines, limit=5)

    @staticmethod
    def _category_sections(
        items: list[dict[str, Any]],
        *,
        include_empty: bool = False,
        report_kind: str = "combined",
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
        if include_empty:
            for source in SOURCE_TABLES:
                if source.get("roster") or source.get("archive"):
                    continue
                is_project = bool(source.get("projectView"))
                if report_kind == "product" and is_project:
                    continue
                if report_kind == "project" and not is_project:
                    continue
                order = int(source.get("categoryOrder") or 999)
                key = str(source.get("categoryKey") or source.get("tableId") or "uncategorized")
                label = str(source.get("category") or source.get("tableName") or "未分类")
                grouped.setdefault((order, key, label), [])
        for item in items:
            order = int(item.get("categoryOrder") or 999)
            key = str(item.get("categoryKey") or item.get("tableId") or "uncategorized")
            label = str(item.get("category") or "未分类")
            grouped.setdefault((order, key, label), []).append(item)
        result: list[dict[str, Any]] = []
        for (order, key, label), category_items in sorted(grouped.items()):
            lines: list[str] = []
            by_status: dict[str, int] = {}
            by_subcategory: dict[str, int] = {}
            for item in category_items:
                status = str(item.get("status") or "未标记")
                by_status[status] = by_status.get(status, 0) + 1
                subcategory = str(item.get("subcategory") or "").strip()
                if subcategory:
                    by_subcategory[subcategory] = by_subcategory.get(subcategory, 0) + 1
                assignees = item.get("assignees") or []
                owner_text = "、".join(
                    dict.fromkeys(str(entry.get("name") or entry.get("userId") or "") for entry in assignees if isinstance(entry, dict))
                )
                details = str(item.get("progressText") or item.get("status") or "已纳入本周跟踪")
                prefix = f"【{subcategory}】" if subcategory else ""
                suffix = f"（{owner_text}）" if owner_text else ""
                lines.append(f"{prefix}{item.get('title') or '未命名事项'}：{details}{suffix}")
            result.append(
                {
                    "key": key,
                    "label": label,
                    "order": order,
                    "itemCount": len(category_items),
                    "riskCount": sum(1 for item in category_items if item.get("riskText")),
                    "overdueCount": sum(1 for item in category_items if item.get("overdue")),
                    "highPriorityCount": sum(1 for item in category_items if item.get("priority") in {"高", "紧急"}),
                    "byStatus": by_status,
                    "bySubcategory": by_subcategory,
                    "content": _lines(lines, limit=50),
                    "digest": ReportService._category_digest(
                        key,
                        label,
                        category_items,
                        by_status,
                        by_subcategory,
                    ),
                }
            )
        return result

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
    def _draft_sections(
        items: list[dict[str, Any]],
        metrics: dict[str, Any],
        *,
        report_kind: str,
    ) -> dict[str, Any]:
        product_lines: list[str] = []
        project_lines: list[str] = []
        risk_lines: list[str] = []
        plan_lines: list[str] = []
        support_lines: list[str] = []
        for item in items:
            managers = "、".join(item.get("productManagerNames") or item.get("projectManagerNames") or [])
            manager_suffix = f"（{managers}）" if managers else ""
            progress = item.get("progressText") or item.get("status") or "已纳入本周跟踪"
            teambition = item.get("teambitionProject") or {}
            if teambition:
                tb_parts = [
                    str(teambition.get("statusName") or "").strip(),
                    (
                        f"进度 {teambition['progressPercent']}%"
                        if teambition.get("progressPercent") is not None
                        else ""
                    ),
                    str(teambition.get("statusSummary") or "").strip(),
                ]
                tb_status = "；".join(value for value in tb_parts if value)
                if tb_status:
                    progress = f"{progress}；TB 项目状态：{tb_status}"
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
            f"本周团队共推进 {metrics['itemCount']} 项工作，涉及 {metrics['managerCount']} 名负责人。"
            f"当前需管理层关注风险 {metrics['riskCount']} 项、逾期 {metrics['overdueCount']} 项，"
            f"另有高优先级事项 {metrics['highPriorityCount']} 项。"
            "各分类已按成果、关键变化和待处理问题进行归并，完整明细保留在周报详情页。"
        )
        return {
            "executiveSummary": summary,
            "categorySections": ReportService._category_sections(
                items,
                include_empty=True,
                report_kind=report_kind,
            ),
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
        fallback = self._draft_sections(items, metrics, report_kind=report_kind)
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
                ai_category_digests = sections.pop("categoryDigests", {})
                sections["categorySections"] = [
                    {
                        **category,
                        "digest": _normalized_digest(
                            ai_category_digests.get(str(category.get("key") or ""))
                            or ai_category_digests.get(str(category.get("label") or ""))
                        )
                        if isinstance(ai_category_digests, dict)
                        and (
                            ai_category_digests.get(str(category.get("key") or ""))
                            or ai_category_digests.get(str(category.get("label") or ""))
                        )
                        else category.get("digest"),
                    }
                    for category in fallback["categorySections"]
                ]
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
                result["sources"] = [self._hydrate_source(item) for item in snapshot if isinstance(item, dict)]
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
            if not result["sections"].get("categorySections"):
                result["sections"] = {
                    **result["sections"],
                    "categorySections": self._category_sections(
                        result["sources"],
                        include_empty=True,
                        report_kind=str(result.get("reportKind") or "combined"),
                    ),
                }
        return result

    def personal(self, report_id: int, *, user_id: str, name: str = "") -> dict[str, Any]:
        report = self.get(report_id, include_sources=True)
        if report.get("reportKind") != "combined":
            raise ValueError("personal reports must be derived from a combined report")
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            raise ValueError("personal report user is required")
        personal_items: list[dict[str, Any]] = []
        display_name = str(name or "").strip()
        for source in report.get("sources") or []:
            roles = [
                str(entry.get("role") or "负责人")
                for entry in source.get("assignees") or []
                if isinstance(entry, dict) and str(entry.get("userId") or "").strip() == normalized_user_id
            ]
            if not roles:
                continue
            item = {**source, "roles": list(dict.fromkeys(roles))}
            personal_items.append(item)
            if not display_name:
                matched = next(
                    (
                        str(entry.get("name") or "")
                        for entry in source.get("assignees") or []
                        if isinstance(entry, dict) and str(entry.get("userId") or "").strip() == normalized_user_id
                    ),
                    "",
                )
                display_name = matched or display_name
        metrics = self._metrics(personal_items)
        completed = sum(1 for item in personal_items if _is_closed(str(item.get("status") or "")))
        metrics["completedCount"] = completed
        metrics["inProgressCount"] = len(personal_items) - completed
        role_counts: dict[str, int] = {}
        for item in personal_items:
            for role in item.get("roles") or []:
                role_counts[role] = role_counts.get(role, 0) + 1
        metrics["byRole"] = role_counts
        summary = (
            f"{display_name or normalized_user_id}本周共关联 {metrics['itemCount']} 项工作；"
            f"已完成 {completed} 项、进行中或待处理 {metrics['inProgressCount']} 项，"
            f"风险 {metrics['riskCount']} 项、逾期 {metrics['overdueCount']} 项、"
            f"高优先级 {metrics['highPriorityCount']} 项。"
        )
        return {
            "reportId": report["id"],
            "periodKey": report["periodKey"],
            "version": report["version"],
            "window": report["window"],
            "workflowState": report["workflowState"],
            "person": {"userId": normalized_user_id, "name": display_name or normalized_user_id},
            "summary": summary,
            "metrics": metrics,
            "categorySections": self._category_sections(personal_items),
            "items": personal_items,
        }

    def personal_members(self, report_id: int) -> list[dict[str, Any]]:
        report = self.get(report_id, include_sources=True)
        members: dict[str, dict[str, Any]] = {}
        for source in report.get("sources") or []:
            source_members: set[str] = set()
            for entry in source.get("assignees") or []:
                if not isinstance(entry, dict):
                    continue
                user_id = str(entry.get("userId") or "").strip()
                if not user_id:
                    continue
                member = members.setdefault(
                    user_id,
                    {"userId": user_id, "name": str(entry.get("name") or user_id), "itemCount": 0, "roles": []},
                )
                if user_id not in source_members:
                    member["itemCount"] += 1
                    source_members.add(user_id)
                role = str(entry.get("role") or "负责人")
                if role not in member["roles"]:
                    member["roles"].append(role)
        return sorted(members.values(), key=lambda item: (str(item.get("name") or ""), str(item.get("userId") or "")))

    def personal_report_options(self, *, limit: int = 52) -> list[dict[str, Any]]:
        rows = self.db.fetch_all(
            """
            SELECT * FROM weekly_report
            WHERE report_kind='combined'
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (max(1, min(200, int(limit))),),
        )
        return [self._format_report(row) for row in rows]

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
