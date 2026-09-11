from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from ..db import Database, db
from ..time_utils import from_db, now_local, to_db


EDIT_IDLE_MINUTES = 30
GENERATION_STALE_MINUTES = 30


class TeamEditConflict(RuntimeError):
    def __init__(self, message: str, *, holder: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.holder = holder or {}


class TeamEditLeaseLost(RuntimeError):
    pass


class ReportGenerationDeferred(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        period_key: str,
        report_kind: str,
        holder: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.period_key = period_key
        self.report_kind = report_kind
        self.holder = holder or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "queued": True,
            "message": str(self),
            "periodKey": self.period_key,
            "reportKind": self.report_kind,
            "editor": self.holder,
        }


class TeamEditingService:
    """Durable single-editor lease and coalesced report-generation queue."""

    def __init__(self, database: Database | None = None) -> None:
        self.db = database or db

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _active(row: dict[str, Any] | None, now: datetime) -> bool:
        expires_at = from_db((row or {}).get("expires_at"))
        return bool(expires_at and expires_at > now)

    @staticmethod
    def _holder(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": str(row.get("owner_name") or "其他用户"),
            "lastActivityAt": str(row.get("last_activity_at") or ""),
            "expiresAt": str(row.get("expires_at") or ""),
        }

    @staticmethod
    def _scope(connection: Any, report_id: int) -> tuple[str, str]:
        row = connection.execute(
            "SELECT period_key,report_kind FROM weekly_report WHERE id=?", (int(report_id),)
        ).fetchone()
        if not row:
            raise ValueError("weekly report not found")
        return str(row["period_key"]), str(row["report_kind"] or "combined")

    @staticmethod
    def _running_generation_active(row: dict[str, Any] | None, now: datetime) -> bool:
        if not row or str(row.get("status") or "") != "running":
            return False
        updated_at = from_db(row.get("updated_at"))
        return bool(updated_at and updated_at + timedelta(minutes=GENERATION_STALE_MINUTES) > now)

    def acquire(
        self,
        report_id: int,
        *,
        actor: str,
        owner_name: str,
        lease_token: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or now_local()
        timestamp = to_db(current.replace(microsecond=0))
        expires_at = to_db((current + timedelta(minutes=EDIT_IDLE_MINUTES)).replace(microsecond=0))
        with self.db.transaction() as connection:
            period_key, report_kind = self._scope(connection, report_id)
            generation_row = connection.execute(
                "SELECT * FROM weekly_report_generation_queue WHERE period_key=? AND report_kind=?",
                (period_key, report_kind),
            ).fetchone()
            generation = dict(generation_row) if generation_row else None
            if self._running_generation_active(generation, current):
                raise TeamEditConflict("系统正在生成该期团队周报，请生成完成后再编辑")
            if generation and str(generation.get("status") or "") == "running":
                connection.execute(
                    "UPDATE weekly_report_generation_queue SET status='error', error_text=?, updated_at=? "
                    "WHERE period_key=? AND report_kind=?",
                    ("generation lease expired", timestamp, period_key, report_kind),
                )

            lease_row = connection.execute(
                "SELECT * FROM weekly_report_edit_lease WHERE period_key=? AND report_kind=?",
                (period_key, report_kind),
            ).fetchone()
            lease = dict(lease_row) if lease_row else None
            if self._active(lease, current):
                supplied_hash = self._token_hash(lease_token)
                if (
                    lease_token
                    and hmac.compare_digest(supplied_hash, str(lease.get("lease_token_hash") or ""))
                    and hmac.compare_digest(str(actor or ""), str(lease.get("owner_actor") or ""))
                ):
                    connection.execute(
                        "UPDATE weekly_report_edit_lease SET report_id=?,last_activity_at=?,expires_at=? "
                        "WHERE period_key=? AND report_kind=?",
                        (int(report_id), timestamp, expires_at, period_key, report_kind),
                    )
                    return {
                        "leaseToken": lease_token,
                        "periodKey": period_key,
                        "reportKind": report_kind,
                        "expiresAt": expires_at,
                        "idleTimeoutMinutes": EDIT_IDLE_MINUTES,
                    }
                holder = self._holder(lease)
                if str(lease.get("owner_actor") or "") == str(actor or ""):
                    message = "你已在另一个页面编辑该期团队周报"
                else:
                    message = f"{holder['name']}正在编辑该期团队周报，请稍后再试"
                raise TeamEditConflict(message, holder=holder)

            token = secrets.token_urlsafe(32)
            connection.execute(
                """
                INSERT INTO weekly_report_edit_lease(
                    period_key,report_kind,report_id,owner_actor,owner_name,lease_token_hash,
                    acquired_at,last_activity_at,expires_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(period_key,report_kind) DO UPDATE SET
                    report_id=excluded.report_id,owner_actor=excluded.owner_actor,
                    owner_name=excluded.owner_name,lease_token_hash=excluded.lease_token_hash,
                    acquired_at=excluded.acquired_at,last_activity_at=excluded.last_activity_at,
                    expires_at=excluded.expires_at
                """,
                (
                    period_key,
                    report_kind,
                    int(report_id),
                    str(actor or "")[:300],
                    str(owner_name or "其他用户")[:300],
                    self._token_hash(token),
                    timestamp,
                    timestamp,
                    expires_at,
                ),
            )
        return {
            "leaseToken": token,
            "periodKey": period_key,
            "reportKind": report_kind,
            "expiresAt": expires_at,
            "idleTimeoutMinutes": EDIT_IDLE_MINUTES,
        }

    def _validate(
        self,
        connection: Any,
        report_id: int,
        *,
        actor: str,
        lease_token: str,
        now: datetime,
    ) -> tuple[str, str, dict[str, Any]]:
        period_key, report_kind = self._scope(connection, report_id)
        row = connection.execute(
            "SELECT * FROM weekly_report_edit_lease WHERE period_key=? AND report_kind=?",
            (period_key, report_kind),
        ).fetchone()
        lease = dict(row) if row else None
        valid = bool(
            lease
            and self._active(lease, now)
            and lease_token
            and hmac.compare_digest(
                self._token_hash(lease_token), str(lease.get("lease_token_hash") or "")
            )
            and hmac.compare_digest(str(actor or ""), str(lease.get("owner_actor") or ""))
        )
        if not valid:
            raise TeamEditLeaseLost("编辑权已释放或已超时，请重新打开周报后再保存")
        return period_key, report_kind, lease

    def touch(
        self,
        report_id: int,
        *,
        actor: str,
        lease_token: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or now_local()
        timestamp = to_db(current.replace(microsecond=0))
        expires_at = to_db((current + timedelta(minutes=EDIT_IDLE_MINUTES)).replace(microsecond=0))
        with self.db.transaction() as connection:
            period_key, report_kind, _ = self._validate(
                connection, report_id, actor=actor, lease_token=lease_token, now=current
            )
            connection.execute(
                "UPDATE weekly_report_edit_lease SET report_id=?,last_activity_at=?,expires_at=? "
                "WHERE period_key=? AND report_kind=?",
                (int(report_id), timestamp, expires_at, period_key, report_kind),
            )
        return {"expiresAt": expires_at, "idleTimeoutMinutes": EDIT_IDLE_MINUTES}

    def assert_owner(
        self,
        report_id: int,
        *,
        actor: str,
        lease_token: str,
        now: datetime | None = None,
    ) -> tuple[str, str]:
        current = now or now_local()
        with self.db.transaction() as connection:
            period_key, report_kind, _ = self._validate(
                connection, report_id, actor=actor, lease_token=lease_token, now=current
            )
        return period_key, report_kind

    def release(
        self,
        report_id: int,
        *,
        actor: str,
        lease_token: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or now_local()
        with self.db.transaction() as connection:
            period_key, report_kind, _ = self._validate(
                connection, report_id, actor=actor, lease_token=lease_token, now=current
            )
            connection.execute(
                "DELETE FROM weekly_report_edit_lease WHERE period_key=? AND report_kind=?",
                (period_key, report_kind),
            )
            queue_row = connection.execute(
                "SELECT status FROM weekly_report_generation_queue WHERE period_key=? AND report_kind=?",
                (period_key, report_kind),
            ).fetchone()
        return {
            "released": True,
            "periodKey": period_key,
            "reportKind": report_kind,
            "generationQueued": bool(
                queue_row and str(queue_row["status"] or "") in {"pending", "error"}
            ),
        }

    def attach_manual_patch(
        self,
        *,
        period_key: str,
        report_kind: str,
        title: str | None,
        sections: dict[str, Any],
        actor: str,
    ) -> bool:
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT status,manual_patch_json FROM weekly_report_generation_queue "
                "WHERE period_key=? AND report_kind=?",
                (period_key, report_kind),
            ).fetchone()
            if not row or str(row["status"] or "") not in {"pending", "error"}:
                return False
            try:
                existing = json.loads(str(row["manual_patch_json"] or "{}"))
            except (TypeError, ValueError):
                existing = {}
            existing_sections = existing.get("sections")
            if not isinstance(existing_sections, dict):
                existing_sections = {}
            merged_sections = {**existing_sections}
            for key, value in sections.items():
                if key == "categorySections" and isinstance(value, list):
                    categories = {
                        str(item.get("key") or ""): item
                        for item in merged_sections.get(key) or []
                        if isinstance(item, dict) and str(item.get("key") or "")
                    }
                    categories.update(
                        {
                            str(item.get("key") or ""): item
                            for item in value
                            if isinstance(item, dict) and str(item.get("key") or "")
                        }
                    )
                    merged_sections[key] = list(categories.values())
                elif key == "sourceOverrides" and isinstance(value, dict):
                    overrides = {
                        str(item_key): dict(item_value)
                        for item_key, item_value in (merged_sections.get(key) or {}).items()
                        if isinstance(item_value, dict)
                    }
                    for item_key, item_value in value.items():
                        if isinstance(item_value, dict):
                            overrides[str(item_key)] = {
                                **overrides.get(str(item_key), {}),
                                **item_value,
                            }
                    merged_sections[key] = overrides
                else:
                    merged_sections[key] = value
            merged = {
                "title": title if title is not None else existing.get("title"),
                "sections": merged_sections,
                "actor": str(actor or existing.get("actor") or "")[:300],
            }
            connection.execute(
                "UPDATE weekly_report_generation_queue SET manual_patch_json=?,updated_at=? "
                "WHERE period_key=? AND report_kind=?",
                (
                    json.dumps(merged, ensure_ascii=False, separators=(",", ":")),
                    to_db(now_local()),
                    period_key,
                    report_kind,
                ),
            )
        return True

    def begin_generation(
        self,
        *,
        period_key: str,
        report_kind: str,
        actor: str,
        use_ai: bool,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or now_local()
        timestamp = to_db(current.replace(microsecond=0))
        with self.db.transaction() as connection:
            lease_row = connection.execute(
                "SELECT * FROM weekly_report_edit_lease WHERE period_key=? AND report_kind=?",
                (period_key, report_kind),
            ).fetchone()
            lease = dict(lease_row) if lease_row else None
            if lease and not self._active(lease, current):
                connection.execute(
                    "DELETE FROM weekly_report_edit_lease WHERE period_key=? AND report_kind=?",
                    (period_key, report_kind),
                )
                lease = None
            existing_row = connection.execute(
                "SELECT * FROM weekly_report_generation_queue WHERE period_key=? AND report_kind=?",
                (period_key, report_kind),
            ).fetchone()
            existing = dict(existing_row) if existing_row else {}
            deferred: ReportGenerationDeferred | None = None
            manual_patch: dict[str, Any] = {}
            if lease:
                connection.execute(
                    """
                    INSERT INTO weekly_report_generation_queue(
                        period_key,report_kind,status,use_ai,requested_by,request_count,
                        requested_at,updated_at,next_retry_at,error_text,manual_patch_json
                    ) VALUES (?,?,?,?,?,1,?,?,?,'','{}')
                    ON CONFLICT(period_key,report_kind) DO UPDATE SET
                        status='pending',use_ai=MAX(use_ai,excluded.use_ai),
                        requested_by=excluded.requested_by,request_count=request_count+1,
                        requested_at=excluded.requested_at,updated_at=excluded.updated_at,
                        next_retry_at='',error_text=''
                    """,
                    (
                        period_key,
                        report_kind,
                        "pending",
                        1 if use_ai else 0,
                        str(actor or "")[:300],
                        timestamp,
                        timestamp,
                        "",
                    ),
                )
                holder = self._holder(lease)
                deferred = ReportGenerationDeferred(
                    f"{holder['name']}正在编辑该期团队周报，生成任务已排队",
                    period_key=period_key,
                    report_kind=report_kind,
                    holder=holder,
                )
            else:
                if self._running_generation_active(existing, current):
                    raise ReportGenerationDeferred(
                        "该期团队周报正在生成，请稍后查看",
                        period_key=period_key,
                        report_kind=report_kind,
                    )
                try:
                    parsed_patch = json.loads(str(existing.get("manual_patch_json") or "{}"))
                except (TypeError, ValueError):
                    parsed_patch = {}
                manual_patch = parsed_patch if isinstance(parsed_patch, dict) else {}
                connection.execute(
                    """
                    INSERT INTO weekly_report_generation_queue(
                        period_key,report_kind,status,use_ai,requested_by,request_count,
                        requested_at,updated_at,next_retry_at,error_text,manual_patch_json
                    ) VALUES (?,?,?,?,?,1,?,?,?,'','{}')
                    ON CONFLICT(period_key,report_kind) DO UPDATE SET
                        status='running',use_ai=MAX(use_ai,excluded.use_ai),
                        requested_by=excluded.requested_by,
                        request_count=CASE WHEN status IN ('pending','error') THEN request_count ELSE request_count+1 END,
                        requested_at=CASE WHEN status IN ('pending','error') THEN requested_at ELSE excluded.requested_at END,
                        updated_at=excluded.updated_at,next_retry_at='',error_text=''
                    """,
                    (
                        period_key,
                        report_kind,
                        "running",
                        1 if use_ai else 0,
                        str(actor or "")[:300],
                        timestamp,
                        timestamp,
                        "",
                    ),
                )
        if deferred:
            raise deferred
        return manual_patch if isinstance(manual_patch, dict) else {}

    def complete_generation(
        self, *, period_key: str, report_kind: str, report_id: int
    ) -> None:
        self.db.execute(
            "UPDATE weekly_report_generation_queue SET status='success',manual_patch_json='{}',"
            "generated_report_id=?,next_retry_at='',error_text='',updated_at=? "
            "WHERE period_key=? AND report_kind=?",
            (int(report_id), to_db(now_local()), period_key, report_kind),
        )

    def fail_generation(
        self, *, period_key: str, report_kind: str, error: Exception
    ) -> None:
        retry_at = to_db((now_local() + timedelta(minutes=1)).replace(microsecond=0))
        self.db.execute(
            "UPDATE weekly_report_generation_queue SET status='error',next_retry_at=?,error_text=?,updated_at=? "
            "WHERE period_key=? AND report_kind=?",
            (
                retry_at,
                str(error)[:2000],
                to_db(now_local()),
                period_key,
                report_kind,
            ),
        )

    def pending_generations(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        current = now or now_local()
        rows = self.db.fetch_all(
            "SELECT * FROM weekly_report_generation_queue "
            "WHERE status IN ('pending','error','running') ORDER BY requested_at LIMIT 20"
        )
        return [
            row
            for row in rows
            if (
                (
                    str(row.get("status") or "") == "running"
                    and not self._running_generation_active(row, current)
                )
                or (
                    str(row.get("status") or "") != "running"
                    and (
                        not from_db(row.get("next_retry_at"))
                        or current >= from_db(row.get("next_retry_at"))
                    )
                )
            )
        ]


team_editing_service = TeamEditingService()
