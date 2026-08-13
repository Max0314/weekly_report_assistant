from __future__ import annotations

from typing import Any

from ..db import Database, db
from ..integrations.bi_center import BiCenterClient, bi_center_client
from ..time_utils import now_local, to_db


class DirectoryService:
    def __init__(
        self,
        database: Database | None = None,
        client: BiCenterClient | None = None,
    ) -> None:
        self.db = database or db
        self.client = client or bi_center_client

    def sync(self) -> dict[str, Any]:
        snapshot = self.client.current_directory()
        refreshed_at = to_db(now_local())
        rows = [item for item in snapshot.items if item.get("employeeKey") and item.get("isActive") is not False]
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM employee_cache")
            connection.executemany(
                """
                INSERT INTO employee_cache(
                    employee_key, corp_id, user_id, union_id, employee_name, title,
                    department_name, biz_group_name, is_active, directory_version, refreshed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(item.get("employeeKey") or ""),
                        str(item.get("corpId") or ""),
                        str(item.get("userId") or ""),
                        str(item.get("unionId") or ""),
                        str(item.get("employeeName") or ""),
                        str(item.get("title") or ""),
                        str(item.get("departmentName") or ""),
                        str(item.get("bizGroupName") or ""),
                        1 if item.get("isActive") is not False else 0,
                        snapshot.directory_version,
                        refreshed_at,
                    )
                    for item in rows
                ],
            )
        return {
            "count": len(rows),
            "directoryVersion": snapshot.directory_version,
            "policyVersion": snapshot.policy_version,
            "refreshedAt": refreshed_at,
        }

    def refresh(self, *, actor: str = "manual") -> dict[str, Any]:
        # ``actor`` is accepted for a uniform manual/scheduler service API. The
        # upstream directory version remains the authoritative audit marker.
        return {**self.sync(), "actor": actor}

    def lookup_by_user_id(self) -> dict[str, dict[str, Any]]:
        rows = self.db.fetch_all("SELECT * FROM employee_cache WHERE is_active=1 AND user_id<>''")
        return {str(item.get("user_id") or ""): item for item in rows if item.get("user_id")}

    def cache_status(self) -> dict[str, Any]:
        row = self.db.fetch_one(
            "SELECT COUNT(*) AS count, MAX(directory_version) AS directory_version, MAX(refreshed_at) AS refreshed_at FROM employee_cache"
        ) or {}
        return {
            "count": int(row.get("count") or 0),
            "directoryVersion": str(row.get("directory_version") or ""),
            "refreshedAt": str(row.get("refreshed_at") or ""),
        }

    def search(self, *, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        keyword = str(query or "").strip()
        params: list[Any] = []
        where = "WHERE is_active=1 AND user_id<>''"
        if keyword:
            where += " AND (employee_name LIKE ? OR department_name LIKE ? OR biz_group_name LIKE ? OR title LIKE ?)"
            pattern = f"%{keyword}%"
            params.extend([pattern, pattern, pattern, pattern])
        params.append(max(1, min(500, int(limit))))
        rows = self.db.fetch_all(
            f"""
            SELECT user_id, employee_name, title, department_name, biz_group_name
            FROM employee_cache {where}
            ORDER BY employee_name, department_name LIMIT ?
            """,
            tuple(params),
        )
        return [
            {
                "userId": str(item.get("user_id") or ""),
                "name": str(item.get("employee_name") or ""),
                "title": str(item.get("title") or ""),
                "department": str(item.get("department_name") or ""),
                "bizGroup": str(item.get("biz_group_name") or ""),
            }
            for item in rows
        ]


directory_service = DirectoryService()
