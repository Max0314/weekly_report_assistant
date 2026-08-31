from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import Settings, settings
from ..db import Database, db
from ..integrations.teambition import TeambitionClient, teambition_client
from ..source_catalog import TEAMBITION_TABLE_ID
from ..time_utils import SHANGHAI, from_db, now_local, to_db
from .workflow_config import WorkflowConfigService, workflow_config_service


TEAMBITION_BASE_ID = "teambition"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    return _text(value).lower() in {"1", "true", "yes", "y", "deleted"}


def _is_deleted(task: dict[str, Any]) -> bool:
    return any(_truthy(task.get(key)) for key in ("isDeleted", "_isDeleted", "idDeleted"))


def _source_time(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return to_db(parsed.astimezone(SHANGHAI))


class TeambitionService:
    def __init__(
        self,
        database: Database | None = None,
        config_service: WorkflowConfigService | None = None,
        client: TeambitionClient | None = None,
        app_settings: Settings | None = None,
    ) -> None:
        self.db = database or db
        self.config_service = config_service or workflow_config_service
        self.client = client or teambition_client
        self.settings = app_settings or settings

    def _members(self) -> list[dict[str, Any]]:
        config = self.config_service.get()
        departments = [
            _text(value) for value in config.get("teambitionDepartmentNames") or [] if _text(value)
        ]
        sql = """
            SELECT employee_key,user_id,employee_name,title,department_name,biz_group_name
            FROM employee_cache
            WHERE is_active=1 AND user_id<>''
        """
        params: tuple[Any, ...] = ()
        if departments:
            placeholders = ",".join("?" for _ in departments)
            sql += (
                f" AND (department_name IN ({placeholders})"
                f" OR biz_group_name IN ({placeholders}))"
            )
            params = (*departments, *departments)
        sql += " ORDER BY department_name,biz_group_name,employee_name,user_id"
        return self.db.fetch_all(sql, params)

    @staticmethod
    def _task_values(
        task: dict[str, Any],
        *,
        dingtalk_user_id: str,
        teambition_user_id: str,
        synced_at: str,
        source_type: str,
    ) -> dict[str, Any]:
        unique_id = task.get("uniqueId")
        return {
            "task_id": _text(task.get("taskId") or task.get("id")),
            "unique_id": _text(unique_id),
            "project_id": _text(task.get("projectId")),
            "executor_user_id": dingtalk_user_id,
            "executor_tb_user_id": teambition_user_id,
            "creator_id": _text(task.get("creatorId")),
            "content": _text(task.get("content"))[:4000],
            "is_done": 1 if _truthy(task.get("isDone")) else 0,
            "is_archived": 1 if _truthy(task.get("isArchived")) else 0,
            "is_deleted": 1 if _is_deleted(task) else 0,
            "priority": int(task.get("priority") or 0),
            "parent_task_id": _text(task.get("parentTaskId")),
            "start_at": _source_time(task.get("startDate")),
            "due_at": _source_time(task.get("dueDate")),
            "accomplished_at": _source_time(task.get("accomplishTime")),
            "source_created_at": _source_time(task.get("created")),
            "source_updated_at": _source_time(task.get("updated")),
            "synced_at": synced_at,
            "raw_json": json.dumps(task, ensure_ascii=False, separators=(",", ":")),
            "source_type": source_type,
        }

    @staticmethod
    def _source_payload(
        task: dict[str, Any], employee: dict[str, Any], project_name: str, *, is_parent: bool = False
    ) -> dict[str, Any]:
        done = bool(task["is_done"])
        due_at = from_db(task["due_at"])
        overdue = bool(due_at and due_at < now_local() and not done)
        status = "已完成" if done else ("已逾期" if overdue else "进行中")
        project_label = project_name or task["project_id"] or "未归属项目"
        progress = f"TB 项目：{project_label}"
        if done and task["accomplished_at"]:
            progress += f"；完成于 {task['accomplished_at']}"
        payload = {
            "base_id": TEAMBITION_BASE_ID,
            "table_id": TEAMBITION_TABLE_ID,
            "table_name": "TB任务",
            "record_id": task["task_id"],
            "category_key": "teambition_task",
            "category_order": 45,
            "category": "TB任务",
            "subcategory": project_label,
            "title": task["content"] or f"TB任务 {task['unique_id'] or task['task_id']}",
            "status": status,
            "priority": "高" if int(task["priority"] or 0) >= 2 else ("中" if int(task["priority"] or 0) == 1 else ""),
            "progress_text": progress,
            "plan_text": "",
            "risk_text": "任务已逾期且尚未完成" if overdue else "",
            "product_manager_user_ids_json": "[]",
            "project_manager_user_ids_json": json.dumps([task["executor_user_id"]], ensure_ascii=False),
            "product_manager_names_json": "[]",
            "project_manager_names_json": json.dumps(
                [_text(employee.get("employee_name"))] if _text(employee.get("employee_name")) else [],
                ensure_ascii=False,
            ),
            "assignees_json": json.dumps(
                [
                    {
                        "userId": task["executor_user_id"],
                        "name": _text(employee.get("employee_name")) or task["executor_user_id"],
                        "role": "TB执行人",
                    }
                ]
                if task["executor_user_id"]
                else [],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "event_at": task["accomplished_at"] or task["source_updated_at"],
            "due_at": task["due_at"],
            "source_created_at": task["source_created_at"],
            "source_updated_at": task["source_updated_at"],
            "raw_json": task["raw_json"],
            "is_deleted": int(task["is_deleted"] or task["is_archived"] or is_parent),
        }
        hash_payload = {key: value for key, value in payload.items() if key != "raw_json"}
        payload["record_hash"] = hashlib.sha256(
            json.dumps(hash_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return payload

    def sync(self, *, actor: str = "manual") -> dict[str, Any]:
        if not self.client.configured():
            raise ValueError("Teambition credentials are not configured")
        members = self._members()
        if not members:
            raise ValueError("Teambition sync scope is empty; sync the bi_center directory first")
        started_at = to_db(now_local())
        source_type = self.client.source
        run_id = self.db.execute(
            """
            INSERT INTO teambition_sync_run(actor,source_type,status,started_at)
            VALUES (?,?, 'running',?)
            """,
            (actor, source_type, started_at),
        )
        user_ids = list(dict.fromkeys(_text(item.get("user_id")) for item in members))
        try:
            mapped = self.client.map_dingtalk_user_ids(user_ids)
        except Exception as exc:
            self.db.execute(
                """
                UPDATE teambition_sync_run SET status='error',finished_at=?,member_count=?,
                    fail_count=?,error_text=? WHERE id=?
                """,
                (to_db(now_local()), len(user_ids), len(user_ids), str(exc)[:2000], run_id),
            )
            raise

        task_rows: dict[str, dict[str, Any]] = {}
        fetched_by_user: dict[str, set[str]] = {}
        user_map_rows: list[tuple[str, str, str, str, str]] = []
        errors: list[str] = []
        ok_count = 0
        project_ids: set[str] = set()
        synced_at = to_db(now_local())
        members_by_user = {_text(item.get("user_id")): item for item in members}
        for member in members:
            user_id = _text(member.get("user_id"))
            query_user_id = _text(mapped.get(user_id))
            if not query_user_id:
                error = "Teambition userId mapping missing"
                user_map_rows.append((user_id, "", "error", error, synced_at))
                errors.append(f"{user_id}:{error}")
                continue
            try:
                tasks = self.client.search_executor_tasks(query_user_id)
                fetched_by_user[user_id] = set()
                for raw in tasks:
                    values = self._task_values(
                        raw,
                        dingtalk_user_id=user_id,
                        teambition_user_id=query_user_id,
                        synced_at=synced_at,
                        source_type=source_type,
                    )
                    task_id = values["task_id"]
                    if not task_id:
                        continue
                    fetched_by_user[user_id].add(task_id)
                    task_rows[task_id] = values
                    if values["project_id"]:
                        project_ids.add(values["project_id"])
                user_map_rows.append((user_id, query_user_id, "success", "", synced_at))
                ok_count += 1
            except Exception as exc:
                error = str(exc)[:500]
                user_map_rows.append((user_id, query_user_id, "error", error, synced_at))
                errors.append(f"{user_id}:{error}")

        projects: list[dict[str, Any]] = []
        if project_ids:
            try:
                projects = self.client.query_projects(sorted(project_ids))
            except Exception as exc:
                errors.append(f"projects:{str(exc)[:500]}")

        changed_count = 0
        project_count = 0
        with self.db.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO teambition_user_map(
                    dingtalk_user_id,teambition_user_id,sync_status,error_text,synced_at
                ) VALUES (?,?,?,?,?)
                ON CONFLICT(dingtalk_user_id) DO UPDATE SET
                    teambition_user_id=excluded.teambition_user_id,
                    sync_status=excluded.sync_status,error_text=excluded.error_text,
                    synced_at=excluded.synced_at
                """,
                user_map_rows,
            )
            for project in projects:
                project_id = _text(project.get("id") or project.get("projectId"))
                if not project_id:
                    continue
                connection.execute(
                    """
                    INSERT INTO teambition_project(project_id,name,is_archived,synced_at)
                    VALUES (?,?,?,?)
                    ON CONFLICT(project_id) DO UPDATE SET name=excluded.name,
                        is_archived=excluded.is_archived,synced_at=excluded.synced_at
                    """,
                    (
                        project_id,
                        _text(project.get("name"))[:1000],
                        1 if _truthy(project.get("isArchived")) else 0,
                        synced_at,
                    ),
                )
                project_count += 1
            task_columns = [
                "task_id", "unique_id", "project_id", "executor_user_id",
                "executor_tb_user_id", "creator_id", "content", "is_done",
                "is_archived", "is_deleted", "priority", "parent_task_id",
                "start_at", "due_at", "accomplished_at", "source_created_at",
                "source_updated_at", "synced_at", "raw_json", "source_type",
            ]
            for task in task_rows.values():
                connection.execute(
                    f"""
                    INSERT INTO teambition_task({','.join(task_columns)})
                    VALUES ({','.join('?' for _ in task_columns)})
                    ON CONFLICT(task_id) DO UPDATE SET
                        unique_id=excluded.unique_id,project_id=excluded.project_id,
                        executor_user_id=excluded.executor_user_id,
                        executor_tb_user_id=excluded.executor_tb_user_id,
                        creator_id=excluded.creator_id,content=excluded.content,
                        is_done=excluded.is_done,is_archived=excluded.is_archived,
                        is_deleted=excluded.is_deleted,priority=excluded.priority,
                        parent_task_id=excluded.parent_task_id,start_at=excluded.start_at,
                        due_at=excluded.due_at,accomplished_at=excluded.accomplished_at,
                        source_created_at=excluded.source_created_at,
                        source_updated_at=excluded.source_updated_at,
                        synced_at=excluded.synced_at,raw_json=excluded.raw_json,
                        source_type=excluded.source_type
                    """,
                    tuple(task[column] for column in task_columns),
                )
            for user_id, task_ids in fetched_by_user.items():
                if task_ids:
                    placeholders = ",".join("?" for _ in task_ids)
                    connection.execute(
                        f"""
                        UPDATE teambition_task SET is_deleted=1,synced_at=?
                        WHERE executor_user_id=? AND is_deleted=0
                          AND task_id NOT IN ({placeholders})
                        """,
                        (synced_at, user_id, *sorted(task_ids)),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE teambition_task SET is_deleted=1,synced_at=?
                        WHERE executor_user_id=? AND is_deleted=0
                        """,
                        (synced_at, user_id),
                    )

            project_names = {
                _text(row["project_id"]): _text(row["name"])
                for row in connection.execute("SELECT project_id,name FROM teambition_project")
            }
            source_columns = [
                "base_id", "table_id", "table_name", "record_id", "category_key",
                "category_order", "category", "subcategory", "title",
                "status", "priority", "progress_text", "plan_text", "risk_text",
                "product_manager_user_ids_json", "project_manager_user_ids_json",
                "product_manager_names_json", "project_manager_names_json", "assignees_json", "event_at",
                "due_at", "source_created_at", "source_updated_at", "record_hash", "raw_json",
            ]
            parent_task_ids = {
                _text(item.get("parent_task_id"))
                for item in task_rows.values()
                if _text(item.get("parent_task_id"))
            }
            for task in task_rows.values():
                employee = members_by_user.get(task["executor_user_id"], {})
                source = self._source_payload(
                    task,
                    employee,
                    project_names.get(task["project_id"], ""),
                    is_parent=task["task_id"] in parent_task_ids,
                )
                existing = connection.execute(
                    """
                    SELECT record_hash,changed_at,is_deleted FROM source_record
                    WHERE base_id=? AND table_id=? AND record_id=?
                    """,
                    (TEAMBITION_BASE_ID, TEAMBITION_TABLE_ID, source["record_id"]),
                ).fetchone()
                if not existing:
                    changed_at = source["source_updated_at"] or source["source_created_at"] or ""
                elif (
                    _text(existing["record_hash"]) == source["record_hash"]
                    and int(existing["is_deleted"] or 0) == source["is_deleted"]
                ):
                    changed_at = _text(existing["changed_at"])
                else:
                    changed_at = synced_at
                    changed_count += 1
                connection.execute(
                    f"""
                    INSERT INTO source_record(
                        {','.join(source_columns)},first_seen_at,last_seen_at,changed_at,is_deleted
                    ) VALUES ({','.join('?' for _ in source_columns)},?,?,?,?)
                    ON CONFLICT(base_id,table_id,record_id) DO UPDATE SET
                        table_name=excluded.table_name,category_key=excluded.category_key,
                        category_order=excluded.category_order,category=excluded.category,
                        subcategory=excluded.subcategory,
                        title=excluded.title,status=excluded.status,priority=excluded.priority,
                        progress_text=excluded.progress_text,plan_text=excluded.plan_text,
                        risk_text=excluded.risk_text,
                        project_manager_user_ids_json=excluded.project_manager_user_ids_json,
                        project_manager_names_json=excluded.project_manager_names_json,
                        assignees_json=excluded.assignees_json,
                        event_at=excluded.event_at,due_at=excluded.due_at,
                        source_created_at=excluded.source_created_at,
                        source_updated_at=excluded.source_updated_at,
                        record_hash=excluded.record_hash,raw_json=excluded.raw_json,
                        last_seen_at=excluded.last_seen_at,changed_at=excluded.changed_at,
                        is_deleted=excluded.is_deleted
                    """,
                    (
                        *(source[column] for column in source_columns),
                        synced_at,
                        synced_at,
                        changed_at,
                        source["is_deleted"],
                    ),
                )
            connection.execute(
                """
                UPDATE source_record SET is_deleted=1,last_seen_at=?
                WHERE base_id=? AND table_id=? AND record_id IN (
                    SELECT task_id FROM teambition_task WHERE is_deleted=1 OR is_archived=1
                )
                """,
                (synced_at, TEAMBITION_BASE_ID, TEAMBITION_TABLE_ID),
            )

        fail_count = len(members) - ok_count
        status = "success" if not errors else ("partial" if ok_count else "error")
        finished_at = to_db(now_local())
        self.db.execute(
            """
            UPDATE teambition_sync_run SET status=?,finished_at=?,member_count=?,ok_count=?,
                fail_count=?,task_count=?,changed_count=?,project_count=?,error_text=?,detail_json=?
            WHERE id=?
            """,
            (
                status,
                finished_at,
                len(members),
                ok_count,
                fail_count,
                len(task_rows),
                changed_count,
                project_count,
                "; ".join(errors)[:2000],
                json.dumps({"failedUsers": fail_count, "source": source_type}, ensure_ascii=False),
                run_id,
            ),
        )
        return {
            "runId": run_id,
            "status": status,
            "source": source_type,
            "members": len(members),
            "ok": ok_count,
            "fail": fail_count,
            "tasks": len(task_rows),
            "changed": changed_count,
            "projects": project_count,
            "finishedAt": finished_at,
        }

    def status(self) -> dict[str, Any]:
        try:
            latest = self.db.fetch_one(
                """
                SELECT id,actor,source_type,status,started_at,finished_at,member_count,
                       ok_count,fail_count,task_count,changed_count,project_count,error_text
                FROM teambition_sync_run ORDER BY id DESC LIMIT 1
                """
            )
            counts = self.db.fetch_one(
                """
                SELECT COUNT(*) AS task_count,
                       COUNT(DISTINCT CASE WHEN executor_user_id<>'' THEN executor_user_id END) AS member_count,
                       MAX(synced_at) AS synced_at
                FROM teambition_task WHERE is_deleted=0 AND is_archived=0
                """
            ) or {}
            projects = self.db.fetch_one(
                "SELECT COUNT(*) AS count FROM teambition_project WHERE is_archived=0"
            ) or {}
        except Exception:
            latest, counts, projects = None, {}, {}
        return {
            "enabled": bool(self.settings.teambition_sync_enabled),
            "configured": self.client.configured(),
            "source": self.settings.teambition_source.strip().lower() or "native",
            "accessMode": "read_only",
            "taskCount": int(counts.get("task_count") or 0),
            "memberCount": int(counts.get("member_count") or 0),
            "projectCount": int(projects.get("count") or 0),
            "syncedAt": _text(counts.get("synced_at")),
            "latestRun": latest or {},
        }

    @staticmethod
    def _month_window(month: str) -> tuple[str, datetime, datetime]:
        normalized = _text(month) or now_local().strftime("%Y-%m")
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", normalized):
            raise ValueError("month must use YYYY-MM")
        start = datetime.strptime(normalized + "-01", "%Y-%m-%d").replace(tzinfo=SHANGHAI)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return normalized, start, end

    def dashboard(
        self,
        *,
        month: str = "",
        query: str = "",
        department: str = "",
        status: str = "all",
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, Any]:
        month_key, month_start, month_end = self._month_window(month)
        now = now_local()
        due_soon_at = now + timedelta(days=7)
        rows = self.db.fetch_all(
            """
            SELECT t.*,p.name AS project_name,COALESCE(p.is_archived,0) AS project_archived,
                   e.employee_name,e.department_name,e.biz_group_name,e.title AS employee_title
            FROM teambition_task t
            LEFT JOIN teambition_project p ON p.project_id=t.project_id
            LEFT JOIN employee_cache e ON e.user_id=t.executor_user_id
            WHERE t.is_deleted=0 AND t.is_archived=0 AND COALESCE(p.is_archived,0)=0
              AND NOT EXISTS (
                  SELECT 1 FROM teambition_task child
                  WHERE child.parent_task_id=t.task_id AND child.is_deleted=0 AND child.is_archived=0
              )
            ORDER BY t.is_done ASC,(t.due_at='') ASC,t.due_at ASC,t.priority DESC,t.content ASC
            """
        )
        items: list[dict[str, Any]] = []
        completed_in_month = 0
        on_time_in_month = 0
        due_in_month = 0
        for row in rows:
            due_at = from_db(row.get("due_at"))
            accomplished_at = from_db(row.get("accomplished_at"))
            done = bool(row.get("is_done"))
            overdue = bool(due_at and due_at < now and not done)
            due_soon = bool(due_at and now <= due_at <= due_soon_at and not done)
            if done:
                bucket = "completed"
            elif overdue:
                bucket = "overdue"
            elif due_soon:
                bucket = "due_soon"
            else:
                bucket = "in_progress"
            if due_at and month_start <= due_at < month_end:
                due_in_month += 1
            if done and accomplished_at and month_start <= accomplished_at < month_end:
                completed_in_month += 1
                if not due_at or accomplished_at <= due_at:
                    on_time_in_month += 1
            if done and not (
                accomplished_at and month_start <= accomplished_at < month_end
            ):
                continue
            items.append(
                {
                    "taskId": _text(row.get("task_id")),
                    "uniqueId": _text(row.get("unique_id")),
                    "title": _text(row.get("content")) or "未命名 TB 任务",
                    "projectId": _text(row.get("project_id")),
                    "projectName": _text(row.get("project_name")) or _text(row.get("project_id")),
                    "executorUserId": _text(row.get("executor_user_id")),
                    "executorName": _text(row.get("employee_name")) or _text(row.get("executor_user_id")),
                    "department": _text(row.get("department_name")),
                    "bizGroup": _text(row.get("biz_group_name")),
                    "priority": int(row.get("priority") or 0),
                    "status": bucket,
                    "isDone": done,
                    "overdue": overdue,
                    "startAt": _text(row.get("start_at")),
                    "dueAt": _text(row.get("due_at")),
                    "accomplishedAt": _text(row.get("accomplished_at")),
                    "updatedAt": _text(row.get("source_updated_at")),
                }
            )

        all_items = items
        normalized_query = _text(query).casefold()
        normalized_department = _text(department)
        normalized_status = _text(status) or "all"
        if normalized_query:
            items = [
                item
                for item in items
                if normalized_query
                in " ".join(
                    _text(item.get(key))
                    for key in ("title", "projectName", "executorName", "department", "bizGroup")
                ).casefold()
            ]
        if normalized_department:
            items = [
                item for item in items
                if normalized_department in {item.get("department"), item.get("bizGroup")}
            ]
        if normalized_status != "all":
            items = [item for item in items if item.get("status") == normalized_status]
        total = len(items)
        page = items[max(0, offset):max(0, offset) + max(1, min(limit, 1000))]
        open_items = [item for item in all_items if not item["isDone"]]
        return {
            "month": month_key,
            "configured": self.client.configured(),
            "source": self.settings.teambition_source.strip().lower() or "native",
            "summary": {
                "openCount": len(open_items),
                "inProgressCount": sum(1 for item in open_items if item["status"] == "in_progress"),
                "overdueCount": sum(1 for item in open_items if item["overdue"]),
                "dueSoonCount": sum(1 for item in open_items if item["status"] == "due_soon"),
                "noDueCount": sum(1 for item in open_items if not item["dueAt"]),
                "dueInMonthCount": due_in_month,
                "completedInMonthCount": completed_in_month,
                "onTimeInMonthCount": on_time_in_month,
                "executorCount": len({item["executorUserId"] for item in all_items if item["executorUserId"]}),
                "projectCount": len({item["projectId"] for item in all_items if item["projectId"]}),
            },
            "filters": {
                "departments": sorted({
                    value for item in all_items
                    for value in (item.get("department"), item.get("bizGroup")) if value
                }),
                "projects": sorted({item["projectName"] for item in all_items if item["projectName"]}),
            },
            "total": total,
            "offset": max(0, offset),
            "items": page,
            "sync": self.status(),
        }


teambition_service = TeambitionService()
