from __future__ import annotations

import json
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
        leaders = self.client.current_leaders()
        refreshed_at = to_db(now_local())
        rows = [item for item in snapshot.items if item.get("employeeKey") and item.get("isActive") is not False]
        leader_roles = self._leader_roles(leaders)
        organizations, relations = self._organization_rows(
            rows,
            leader_roles=leader_roles,
            directory_version=snapshot.directory_version,
            refreshed_at=refreshed_at,
        )
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM employee_org_relation_cache")
            connection.execute("DELETE FROM organization_cache")
            connection.execute("DELETE FROM employee_cache")
            connection.executemany(
                """
                INSERT INTO employee_cache(
                    employee_key, corp_id, user_id, union_id, job_number, employee_name, title,
                    primary_dept_id, department_name, biz_group_name, email_normalized,
                    affiliation_type, employment_status, primary_source,
                    is_department_leader, is_biz_group_leader, is_company_leader,
                    leader_roles_json, is_active, directory_version, refreshed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(item.get("employeeKey") or ""),
                        str(item.get("corpId") or ""),
                        str(item.get("userId") or ""),
                        str(item.get("unionId") or ""),
                        str(item.get("jobNumber") or ""),
                        str(item.get("employeeName") or ""),
                        str(item.get("title") or ""),
                        str(item.get("primaryDeptId") or ""),
                        str(item.get("departmentName") or ""),
                        str(item.get("bizGroupName") or ""),
                        str(item.get("emailNormalized") or ""),
                        str(item.get("affiliationType") or ""),
                        str(item.get("employmentStatus") or ""),
                        str(item.get("primarySource") or ""),
                        1 if self._is_scope_leader(
                            item, leader_roles.get(str(item.get("employeeKey") or ""), []), "department"
                        ) else 0,
                        1 if self._is_scope_leader(
                            item, leader_roles.get(str(item.get("employeeKey") or ""), []), "biz_group"
                        ) else 0,
                        1 if item.get("isCompanyLeader") else 0,
                        json.dumps(
                            leader_roles.get(str(item.get("employeeKey") or ""), []),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        1 if item.get("isActive") is not False else 0,
                        snapshot.directory_version,
                        refreshed_at,
                    )
                    for item in rows
                ],
            )
            connection.executemany(
                """
                INSERT INTO organization_cache(
                    organization_key, organization_type, organization_id, organization_name,
                    member_count, leader_count, directory_version, refreshed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                organizations,
            )
            connection.executemany(
                """
                INSERT INTO employee_org_relation_cache(
                    employee_key, organization_key, relation_type, is_primary, is_leader,
                    directory_version, refreshed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                relations,
            )
        return {
            "count": len(rows),
            "organizationCount": len(organizations),
            "relationCount": len(relations),
            "leaderCount": sum(
                1
                for item in rows
                if self._is_leader(item)
                or leader_roles.get(str(item.get("employeeKey") or ""))
            ),
            "directoryVersion": snapshot.directory_version,
            "policyVersion": snapshot.policy_version,
            "refreshedAt": refreshed_at,
        }

    @staticmethod
    def _is_leader(item: dict[str, Any]) -> bool:
        return bool(
            item.get("isDepartmentLeader")
            or item.get("isBizGroupLeader")
            or item.get("isCompanyLeader")
        )

    @staticmethod
    def _is_scope_leader(
        item: dict[str, Any], roles: list[dict[str, str]], scope_type: str
    ) -> bool:
        flag_name = "isDepartmentLeader" if scope_type == "department" else "isBizGroupLeader"
        return bool(item.get(flag_name)) or any(
            role.get("scopeType") == scope_type for role in roles
        )

    @staticmethod
    def _leader_roles(leaders: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
        result: dict[str, list[dict[str, str]]] = {}
        for item in leaders:
            if item.get("isActive") is False:
                continue
            employee_key = str(item.get("employeeKey") or "").strip()
            if not employee_key:
                continue
            scope_type = str(item.get("scopeType") or "").strip()
            scope_value = (
                item.get("departmentName")
                if scope_type == "department"
                else item.get("bizGroupName")
            )
            scope_name = str(scope_value or "").strip()
            role = {
                "scopeType": scope_type,
                "scopeName": scope_name,
                "title": str(item.get("leaderTitle") or "").strip(),
                "sourceType": str(item.get("sourceType") or "").strip(),
            }
            roles = result.setdefault(employee_key, [])
            if role not in roles:
                roles.append(role)
        return result

    @classmethod
    def _organization_rows(
        cls,
        employees: list[dict[str, Any]],
        *,
        leader_roles: dict[str, list[dict[str, str]]],
        directory_version: str,
        refreshed_at: str,
    ) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
        organizations: dict[str, dict[str, Any]] = {}
        relations: dict[tuple[str, str], tuple[Any, ...]] = {}
        organization_keys_by_name: dict[tuple[str, str], list[str]] = {}

        def ensure_organization(
            organization_type: str, organization_id: str, organization_name: str
        ) -> str:
            name = str(organization_name or "").strip()
            if not name:
                return ""
            org_id = str(organization_id or "").strip()
            organization_key = f"{organization_type}:{org_id or name}"
            organizations.setdefault(
                organization_key,
                {
                    "type": organization_type,
                    "id": org_id,
                    "name": name,
                    "members": set(),
                    "leaders": set(),
                },
            )
            name_key = (organization_type, name)
            keys = organization_keys_by_name.setdefault(name_key, [])
            if organization_key not in keys:
                keys.append(organization_key)
            return organization_key

        def add_relation(
            employee_key: str,
            organization_key: str,
            relation_type: str,
            *,
            is_primary: bool,
            is_leader: bool,
        ) -> None:
            if not employee_key or not organization_key:
                return
            organization = organizations[organization_key]
            if is_primary:
                organization["members"].add(employee_key)
            if is_leader:
                organization["leaders"].add(employee_key)
            current = relations.get((employee_key, organization_key))
            primary_value = 1 if is_primary or (current and current[3]) else 0
            leader_value = 1 if is_leader or (current and current[4]) else 0
            relations[(employee_key, organization_key)] = (
                employee_key,
                organization_key,
                relation_type,
                primary_value,
                leader_value,
                directory_version,
                refreshed_at,
            )

        # Primary organization membership comes from the current employee snapshot.
        for item in employees:
            employee_key = str(item.get("employeeKey") or "")
            roles = leader_roles.get(employee_key, [])
            department_name = str(item.get("departmentName") or "")
            biz_group_name = str(item.get("bizGroupName") or "")
            department_roles = [role for role in roles if role.get("scopeType") == "department"]
            biz_group_roles = [role for role in roles if role.get("scopeType") == "biz_group"]
            department_leader = (
                any(role.get("scopeName") == department_name for role in department_roles)
                if department_roles
                else bool(item.get("isDepartmentLeader"))
            )
            biz_group_leader = (
                any(role.get("scopeName") == biz_group_name for role in biz_group_roles)
                if biz_group_roles
                else bool(item.get("isBizGroupLeader"))
            )
            department_key = ensure_organization(
                "department", str(item.get("primaryDeptId") or ""), department_name
            )
            biz_group_key = ensure_organization("biz_group", "", biz_group_name)
            add_relation(
                employee_key,
                department_key,
                "department",
                is_primary=True,
                is_leader=department_leader,
            )
            add_relation(
                employee_key,
                biz_group_key,
                "biz_group",
                is_primary=True,
                is_leader=biz_group_leader,
            )

        # A manager can lead an organization other than their own primary organization.
        # Preserve that scope as a non-primary relation instead of assigning the manager
        # to the wrong department or business group.
        employee_keys = {str(item.get("employeeKey") or "") for item in employees}
        for employee_key, roles in leader_roles.items():
            if employee_key not in employee_keys:
                continue
            for role in roles:
                scope_type = str(role.get("scopeType") or "")
                scope_name = str(role.get("scopeName") or "")
                if scope_type not in {"department", "biz_group"} or not scope_name:
                    continue
                candidates = organization_keys_by_name.get((scope_type, scope_name), [])
                organization_key = (
                    candidates[0]
                    if len(candidates) == 1
                    else ensure_organization(scope_type, "", scope_name)
                )
                current = relations.get((employee_key, organization_key))
                add_relation(
                    employee_key,
                    organization_key,
                    scope_type,
                    is_primary=bool(current and current[3]),
                    is_leader=True,
                )

        organization_rows = [
            (
                key,
                item["type"],
                item["id"],
                item["name"],
                len(item["members"]),
                len(item["leaders"]),
                directory_version,
                refreshed_at,
            )
            for key, item in sorted(organizations.items())
        ]
        return organization_rows, list(relations.values())

    def refresh(self, *, actor: str = "manual") -> dict[str, Any]:
        # ``actor`` is accepted for a uniform manual/scheduler service API. The
        # upstream directory version remains the authoritative audit marker.
        return {**self.sync(), "actor": actor}

    def lookup_by_user_id(self) -> dict[str, dict[str, Any]]:
        rows = self.db.fetch_all("SELECT * FROM employee_cache WHERE is_active=1 AND user_id<>''")
        return {str(item.get("user_id") or ""): item for item in rows if item.get("user_id")}

    def lookup_by_union_id(self) -> dict[str, dict[str, Any]]:
        rows = self.db.fetch_all("SELECT * FROM employee_cache WHERE is_active=1 AND union_id<>''")
        return {str(item.get("union_id") or ""): item for item in rows if item.get("union_id")}

    def cache_status(self) -> dict[str, Any]:
        row = self.db.fetch_one(
            """
            SELECT COUNT(*) AS count,
                   SUM(CASE WHEN is_department_leader=1 OR is_biz_group_leader=1 OR is_company_leader=1 THEN 1 ELSE 0 END) AS leader_count,
                   MAX(directory_version) AS directory_version,
                   MAX(refreshed_at) AS refreshed_at
            FROM employee_cache
            """
        ) or {}
        organization_row = self.db.fetch_one("SELECT COUNT(*) AS count FROM organization_cache") or {}
        relation_row = self.db.fetch_one("SELECT COUNT(*) AS count FROM employee_org_relation_cache") or {}
        return {
            "count": int(row.get("count") or 0),
            "organizationCount": int(organization_row.get("count") or 0),
            "relationCount": int(relation_row.get("count") or 0),
            "leaderCount": int(row.get("leader_count") or 0),
            "directoryVersion": str(row.get("directory_version") or ""),
            "refreshedAt": str(row.get("refreshed_at") or ""),
        }

    def search(
        self,
        *,
        query: str = "",
        organization_key: str = "",
        leaders_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        keyword = str(query or "").strip()
        params: list[Any] = []
        joins = ""
        where = "WHERE e.is_active=1 AND e.user_id<>''"
        if organization_key:
            joins = " JOIN employee_org_relation_cache r ON r.employee_key=e.employee_key"
            where += " AND r.organization_key=?"
            params.append(str(organization_key).strip())
        if leaders_only:
            where += " AND (e.is_department_leader=1 OR e.is_biz_group_leader=1 OR e.is_company_leader=1)"
        if keyword:
            where += " AND (e.employee_name LIKE ? OR e.department_name LIKE ? OR e.biz_group_name LIKE ? OR e.title LIKE ? OR e.job_number LIKE ?)"
            pattern = f"%{keyword}%"
            params.extend([pattern, pattern, pattern, pattern, pattern])
        params.append(max(1, min(500, int(limit))))
        rows = self.db.fetch_all(
            f"""
            SELECT DISTINCT e.employee_key, e.user_id, e.employee_name, e.job_number, e.title,
                   e.primary_dept_id, e.department_name, e.biz_group_name,
                   e.is_department_leader, e.is_biz_group_leader, e.is_company_leader,
                   e.leader_roles_json
            FROM employee_cache e {joins} {where}
            ORDER BY e.employee_name, e.department_name LIMIT ?
            """,
            tuple(params),
        )
        employee_keys = [str(item.get("employee_key") or "") for item in rows]
        organization_keys: dict[str, list[str]] = {key: [] for key in employee_keys}
        if employee_keys:
            placeholders = ",".join("?" for _ in employee_keys)
            for relation in self.db.fetch_all(
                f"""
                SELECT employee_key, organization_key
                FROM employee_org_relation_cache
                WHERE employee_key IN ({placeholders})
                ORDER BY CASE relation_type WHEN 'department' THEN 0 ELSE 1 END, organization_key
                """,
                tuple(employee_keys),
            ):
                organization_keys.setdefault(str(relation.get("employee_key") or ""), []).append(
                    str(relation.get("organization_key") or "")
                )
        return [
            {
                "userId": str(item.get("user_id") or ""),
                "name": str(item.get("employee_name") or ""),
                "jobNumber": str(item.get("job_number") or ""),
                "title": str(item.get("title") or ""),
                "primaryDeptId": str(item.get("primary_dept_id") or ""),
                "department": str(item.get("department_name") or ""),
                "bizGroup": str(item.get("biz_group_name") or ""),
                "organizationKeys": organization_keys.get(str(item.get("employee_key") or ""), []),
                "leaderRoles": self._json_list(item.get("leader_roles_json")),
                "isDepartmentLeader": bool(item.get("is_department_leader")),
                "isBizGroupLeader": bool(item.get("is_biz_group_leader")),
                "isCompanyLeader": bool(item.get("is_company_leader")),
            }
            for item in rows
        ]

    @staticmethod
    def _json_list(value: Any) -> list[dict[str, Any]]:
        try:
            result = json.loads(str(value or "[]"))
        except (TypeError, ValueError):
            return []
        return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []

    def search_organizations(
        self, *, query: str = "", organization_type: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where: list[str] = []
        keyword = str(query or "").strip()
        if keyword:
            where.append("organization_name LIKE ?")
            params.append(f"%{keyword}%")
        normalized_type = str(organization_type or "").strip()
        if normalized_type:
            where.append("organization_type=?")
            params.append(normalized_type)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(max(1, min(500, int(limit))))
        rows = self.db.fetch_all(
            f"""
            SELECT organization_key, organization_type, organization_id, organization_name,
                   member_count, leader_count, directory_version, refreshed_at
            FROM organization_cache {clause}
            ORDER BY organization_type, organization_name LIMIT ?
            """,
            tuple(params),
        )
        return [
            {
                "organizationKey": str(item.get("organization_key") or ""),
                "type": str(item.get("organization_type") or ""),
                "organizationId": str(item.get("organization_id") or ""),
                "name": str(item.get("organization_name") or ""),
                "memberCount": int(item.get("member_count") or 0),
                "leaderCount": int(item.get("leader_count") or 0),
                "directoryVersion": str(item.get("directory_version") or ""),
                "refreshedAt": str(item.get("refreshed_at") or ""),
            }
            for item in rows
        ]


directory_service = DirectoryService()
