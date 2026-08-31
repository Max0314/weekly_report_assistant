from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import Settings, settings
from ..db import Database, db
from ..integrations.teambition import TeambitionClient, teambition_client
from ..source_catalog import SOURCE_TABLES, TEAMBITION_TABLE_ID
from ..time_utils import SHANGHAI, from_db, now_local, to_db
from .workflow_config import WorkflowConfigService, workflow_config_service


TEAMBITION_BASE_ID = "teambition"
KEY_PROJECT_TABLE_ID = next(
    str(item["tableId"]) for item in SOURCE_TABLES if item.get("key") == "projects"
)
PROJECT_CODE_PATTERN = re.compile(
    r"(?<![A-Z0-9])[A-Z]\d{2}/[A-Z0-9]+(?:-[A-Z0-9]+){1,}(?![A-Z0-9])",
    re.IGNORECASE,
)


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


def _field_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(filter(None, (_field_text(item) for item in value))).strip()
    if isinstance(value, dict):
        for key in ("markdown", "text", "name", "title", "value"):
            if key in value and value[key] is not value:
                rendered = _field_text(value[key])
                if rendered:
                    return rendered
        return " ".join(filter(None, (_field_text(item) for item in value.values()))).strip()
    return _text(value)


def _project_code(value: Any) -> str:
    match = PROJECT_CODE_PATTERN.search(_field_text(value).upper())
    return match.group(0).upper() if match else ""


def _normalized_project_name(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", _field_text(value)).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


def _project_search_terms(value: Any) -> list[str]:
    name = _field_text(value)
    if not name:
        return []
    terms = [name]
    base_name = re.split(r"[（(]", name, maxsplit=1)[0].strip()
    if len(base_name) >= 6:
        terms.append(base_name)
    compact_name = re.sub(r"\s+", "", base_name or name)
    for length in (12, 6):
        if len(compact_name) > length:
            terms.append(compact_name[:length])
    return list(dict.fromkeys(term for term in terms if term))


def _key_project_fields(row: dict[str, Any]) -> tuple[str, str, str]:
    try:
        raw = json.loads(_text(row.get("raw_json")) or "{}")
    except (TypeError, ValueError):
        raw = {}
    fields = raw.get("fieldValues") if isinstance(raw, dict) else {}
    fields = fields if isinstance(fields, dict) else {}
    code = _project_code(fields.get("项目编号"))
    name = _field_text(fields.get("项目名称")) or _text(row.get("title"))
    return code, name, _normalized_project_name(name)


def _key_project_identity(row: dict[str, Any]) -> tuple[str, str]:
    code, _, normalized_name = _key_project_fields(row)
    return code, normalized_name


def _teambition_project_identity(project: dict[str, Any]) -> tuple[str, str, float]:
    codes: list[str] = []
    progress_candidates: list[float] = []
    for field in project.get("customfields") or []:
        if not isinstance(field, dict):
            continue
        value = field.get("value")
        code = _project_code(value)
        if code:
            codes.append(code)
        if _text(field.get("type")).lower() == "number":
            try:
                number = float(_field_text(value))
            except (TypeError, ValueError):
                continue
            if 0 <= number <= 100:
                progress_candidates.append(number)
    code = codes[0] if len(set(codes)) == 1 else ""
    progress = progress_candidates[0] if len(progress_candidates) == 1 else -1.0
    return code, _normalized_project_name(project.get("name")), progress


def _match_key_projects(
    key_records: list[dict[str, Any]], projects: list[dict[str, Any]]
) -> dict[str, dict[str, str]]:
    by_code: dict[str, list[dict[str, Any]]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    identities: list[tuple[dict[str, Any], str, str]] = []
    for record in key_records:
        code, name = _key_project_identity(record)
        identities.append((record, code, name))
        if code:
            by_code.setdefault(code, []).append(record)
        if name:
            by_name.setdefault(name, []).append(record)

    project_identities = {
        _text(project.get("id") or project.get("projectId")): _teambition_project_identity(project)
        for project in projects
        if _text(project.get("id") or project.get("projectId"))
    }
    tb_code_counts: dict[str, int] = {}
    tb_name_counts: dict[str, int] = {}
    for code, name, _ in project_identities.values():
        if code:
            tb_code_counts[code] = tb_code_counts.get(code, 0) + 1
        if name:
            tb_name_counts[name] = tb_name_counts.get(name, 0) + 1

    matches: dict[str, dict[str, str]] = {}
    claimed_records: set[str] = set()
    for project in projects:
        project_id = _text(project.get("id") or project.get("projectId"))
        if not project_id:
            continue
        code, name, _ = project_identities[project_id]
        record: dict[str, Any] | None = None
        match_type = ""
        if (
            code
            and tb_code_counts.get(code) == 1
            and len(by_code.get(code) or []) == 1
        ):
            record = by_code[code][0]
            match_type = "project_code"
        elif (
            name
            and tb_name_counts.get(name) == 1
            and len(by_name.get(name) or []) == 1
        ):
            record = by_name[name][0]
            match_type = "project_name"
        elif name and len(name) >= 8 and tb_name_counts.get(name) == 1:
            candidates = [
                item
                for item, _, source_name in identities
                if source_name and len(source_name) >= 8 and (source_name in name or name in source_name)
            ]
            if len(candidates) == 1:
                record = candidates[0]
                match_type = "project_name_contains"
        record_id = _text((record or {}).get("record_id"))
        if record and record_id not in claimed_records:
            claimed_records.add(record_id)
            matches[project_id] = {
                "recordId": record_id,
                "matchType": match_type,
                "projectCode": code,
            }
    return matches


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
        synced_at = to_db(now_local())
        teambition_to_dingtalk: dict[str, str] = {}
        for member in members:
            user_id = _text(member.get("user_id"))
            query_user_id = _text(mapped.get(user_id))
            if not query_user_id:
                error = "Teambition userId mapping missing"
                user_map_rows.append((user_id, "", "error", error, synced_at))
                errors.append(f"{user_id}:{error}")
                continue
            fetched_by_user[user_id] = set()
            teambition_to_dingtalk.setdefault(query_user_id, user_id)
            user_map_rows.append((user_id, query_user_id, "success", "", synced_at))
            ok_count += 1

        key_records = self.db.fetch_all(
            """
            SELECT record_id,title,raw_json FROM source_record
            WHERE table_id=? AND is_deleted=0
            """,
            (KEY_PROJECT_TABLE_ID,),
        )
        discovered_projects: dict[str, dict[str, Any]] = {}
        project_search_error_count = 0
        for record in key_records:
            _, project_name, _ = _key_project_fields(record)
            if not project_name:
                continue
            try:
                _, _, source_name = _key_project_fields(record)
                for search_term in _project_search_terms(project_name):
                    found = self.client.search_projects(search_term)
                    for project in found:
                        project_id = _text(project.get("id") or project.get("projectId"))
                        if project_id:
                            discovered_projects[project_id] = project
                    if any(
                        _normalized_project_name(project.get("name")) == source_name
                        for project in found
                    ):
                        break
            except Exception as exc:
                project_search_error_count += 1
                if project_search_error_count <= 5:
                    errors.append(
                        f"project-search:{_text(record.get('record_id'))}:{str(exc)[:300]}"
                    )
        project_matches = _match_key_projects(key_records, list(discovered_projects.values()))
        projects = [
            project
            for project_id, project in discovered_projects.items()
            if project_id in project_matches
        ]
        existing_projects = {
            _text(row.get("project_id")): row
            for row in self.db.fetch_all("SELECT * FROM teambition_project")
        }
        status_results: dict[str, tuple[bool, dict[str, Any]]] = {}
        status_error_count = 0
        project_task_error_count = 0
        for project in projects:
            project_id = _text(project.get("id") or project.get("projectId"))
            if project_id not in project_matches:
                continue
            operator_id = next(
                (_text(value) for value in project.get("ownerIds") or [] if _text(value)),
                "",
            )
            try:
                for raw in self.client.query_project_tasks(
                    project_id,
                    operator_id=operator_id,
                ):
                    teambition_user_id = _text(raw.get("executorId"))
                    dingtalk_user_id = teambition_to_dingtalk.get(teambition_user_id, "")
                    if not dingtalk_user_id:
                        continue
                    values = self._task_values(
                        raw,
                        dingtalk_user_id=dingtalk_user_id,
                        teambition_user_id=teambition_user_id,
                        synced_at=synced_at,
                        source_type=source_type,
                    )
                    task_id = values["task_id"]
                    if not task_id:
                        continue
                    fetched_by_user[dingtalk_user_id].add(task_id)
                    task_rows[task_id] = values
            except Exception as exc:
                project_task_error_count += 1
                if project_task_error_count <= 5:
                    errors.append(f"project-tasks:{project_id}:{str(exc)[:300]}")
            try:
                statuses = self.client.query_project_statuses(
                    project_id,
                    operator_id=operator_id,
                )
                latest = max(
                    statuses,
                    key=lambda item: _source_time(item.get("created")),
                    default={},
                )
                status_results[project_id] = (True, latest)
            except Exception as exc:
                status_error_count += 1
                status_results[project_id] = (False, {})
                if status_error_count <= 5:
                    errors.append(f"project-status:{project_id}:{str(exc)[:300]}")

        changed_count = 0
        project_count = 0
        key_project_count = 0
        project_status_count = 0
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
            if project_search_error_count == 0:
                connection.execute(
                    """
                    UPDATE teambition_project
                    SET is_key_project=0,matched_record_id='',match_type=''
                    WHERE is_key_project=1
                    """
                )
            for project in projects:
                project_id = _text(project.get("id") or project.get("projectId"))
                if not project_id:
                    continue
                match = project_matches.get(project_id) or {}
                project_code, _, progress_percent = _teambition_project_identity(project)
                existing_project = existing_projects.get(project_id) or {}
                status_ok, latest_status = status_results.get(project_id, (False, {}))
                if status_ok:
                    status_name = _text(latest_status.get("name"))
                    status_degree = _text(latest_status.get("degree"))
                    status_content = _text(latest_status.get("content"))[:12000]
                    status_created_at = _source_time(latest_status.get("created"))
                else:
                    status_name = _text(existing_project.get("status_name"))
                    status_degree = _text(existing_project.get("status_degree"))
                    status_content = _text(existing_project.get("status_content"))
                    status_created_at = _text(existing_project.get("status_created_at"))
                is_key_project = 1 if match else 0
                if is_key_project:
                    key_project_count += 1
                    if status_name or status_content:
                        project_status_count += 1
                project_values = {
                    "name": _text(project.get("name"))[:1000],
                    "project_code": project_code or _text(match.get("projectCode")),
                    "progress_percent": progress_percent,
                    "is_archived": 1 if _truthy(project.get("isArchived")) else 0,
                    "is_suspended": 1 if _truthy(project.get("isSuspended")) else 0,
                    "is_key_project": is_key_project,
                    "matched_record_id": _text(match.get("recordId")),
                    "match_type": _text(match.get("matchType")),
                    "start_at": _source_time(project.get("startDate")),
                    "end_at": _source_time(project.get("endDate")),
                    "source_updated_at": _source_time(project.get("updated")),
                    "status_name": status_name,
                    "status_degree": status_degree,
                    "status_content": status_content,
                    "status_created_at": status_created_at,
                    "raw_json": json.dumps(
                        {"project": project, "latestStatus": latest_status},
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ),
                }
                tracked_columns = (
                    "project_code", "progress_percent", "is_archived", "is_suspended",
                    "is_key_project", "matched_record_id", "status_name", "status_degree",
                    "status_content", "status_created_at",
                )
                if is_key_project and (
                    not existing_project
                    or any(
                        str(existing_project.get(column) or "")
                        != str(project_values.get(column) or "")
                        for column in tracked_columns
                    )
                ):
                    changed_count += 1
                connection.execute(
                    """
                    INSERT INTO teambition_project(
                        project_id,name,project_code,progress_percent,is_archived,is_suspended,
                        is_key_project,matched_record_id,match_type,start_at,end_at,
                        source_updated_at,status_name,status_degree,status_content,
                        status_created_at,raw_json,synced_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(project_id) DO UPDATE SET name=excluded.name,
                        project_code=excluded.project_code,
                        progress_percent=excluded.progress_percent,
                        is_archived=excluded.is_archived,
                        is_suspended=excluded.is_suspended,
                        is_key_project=excluded.is_key_project,
                        matched_record_id=excluded.matched_record_id,
                        match_type=excluded.match_type,
                        start_at=excluded.start_at,end_at=excluded.end_at,
                        source_updated_at=excluded.source_updated_at,
                        status_name=excluded.status_name,
                        status_degree=excluded.status_degree,
                        status_content=excluded.status_content,
                        status_created_at=excluded.status_created_at,
                        raw_json=excluded.raw_json,synced_at=excluded.synced_at
                    """,
                    (
                        project_id,
                        *(project_values[column] for column in (
                            "name", "project_code", "progress_percent", "is_archived",
                            "is_suspended", "is_key_project", "matched_record_id", "match_type",
                            "start_at", "end_at", "source_updated_at", "status_name",
                            "status_degree", "status_content", "status_created_at", "raw_json",
                        )),
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
            if project_search_error_count == 0 and project_task_error_count == 0:
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

            # TB tasks remain available to the execution dashboard, but they are no
            # longer independent weekly-report facts. Only matched key-project
            # status is merged into the corresponding AITable source record later.
            connection.execute(
                """
                UPDATE source_record SET is_deleted=1,last_seen_at=?
                WHERE base_id=? AND table_id=? AND is_deleted=0
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
                json.dumps(
                    {
                        "failedUsers": fail_count,
                        "source": source_type,
                        "keyProjects": key_project_count,
                        "projectStatuses": project_status_count,
                        "projectStatusErrors": status_error_count,
                        "projectSearchErrors": project_search_error_count,
                        "projectTaskErrors": project_task_error_count,
                    },
                    ensure_ascii=False,
                ),
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
            "keyProjects": key_project_count,
            "projectStatuses": project_status_count,
            "projectStatusErrors": status_error_count,
            "projectSearchErrors": project_search_error_count,
            "projectTaskErrors": project_task_error_count,
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
                """
                SELECT COUNT(*) AS count,
                       SUM(CASE WHEN is_key_project=1 THEN 1 ELSE 0 END) AS key_count,
                       SUM(CASE WHEN is_key_project=1 AND (status_name<>'' OR status_content<>'')
                                THEN 1 ELSE 0 END) AS status_count
                FROM teambition_project WHERE is_archived=0
                """
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
            "keyProjectCount": int(projects.get("key_count") or 0),
            "projectStatusCount": int(projects.get("status_count") or 0),
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
            WHERE t.is_deleted=0 AND t.is_archived=0
              AND COALESCE(p.is_archived,0)=0 AND COALESCE(p.is_key_project,0)=1
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
