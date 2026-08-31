from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.integrations.bi_center import DirectorySnapshot
from app.services.directory import DirectoryService


class FakeBiCenterClient:
    def current_directory(self) -> DirectorySnapshot:
        return DirectorySnapshot(
            directory_version="v-20260814",
            policy_version="policy-1",
            items=[
                {
                    "employeeKey": "e-1",
                    "corpId": "corp",
                    "userId": "u-1",
                    "unionId": "union-1",
                    "jobNumber": "1001",
                    "employeeName": "产品甲",
                    "title": "产品经理",
                    "primaryDeptId": "dept-1",
                    "departmentName": "产品部",
                    "bizGroupName": "研发体系",
                    "emailNormalized": "one@example.test",
                    "affiliationType": "rd_system",
                    "employmentStatus": "active",
                    "primarySource": "dingtalk_main",
                    "isDepartmentLeader": True,
                    "isBizGroupLeader": False,
                    "isCompanyLeader": False,
                    "isActive": True,
                },
                {
                    "employeeKey": "e-2",
                    "corpId": "corp",
                    "userId": "u-2",
                    "employeeName": "项目乙",
                    "title": "项目经理",
                    "primaryDeptId": "dept-1",
                    "departmentName": "产品部",
                    "bizGroupName": "研发体系",
                    "isBizGroupLeader": True,
                    "isActive": True,
                },
                {"employeeKey": "inactive", "employeeName": "离职人员", "isActive": False},
            ],
        )

    def current_leaders(self):
        return [
            {
                "employeeKey": "e-1",
                "scopeType": "department",
                "departmentName": "产品部",
                "leaderTitle": "部门主管",
                "sourceType": "dingtalk_dept_manager",
            },
            {
                "employeeKey": "e-2",
                "scopeType": "biz_group",
                "bizGroupName": "研发体系",
                "leaderTitle": "业务组负责人",
                "sourceType": "dingtalk_dept_manager",
            },
        ]


class FakeBiCenterWithCrossScope(FakeBiCenterClient):
    def current_leaders(self):
        return [
            *super().current_leaders(),
            {
                "employeeKey": "e-1",
                "scopeType": "department",
                "departmentName": "支持部",
                "leaderTitle": "兼任负责人",
                "sourceType": "dingtalk_dept_manager",
            },
        ]


class DirectoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "directory.db")
        self.db.initialize()
        self.service = DirectoryService(self.db, FakeBiCenterClient())

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sync_caches_employees_organizations_relations_and_leader_roles(self) -> None:
        result = self.service.sync()
        self.assertEqual(2, result["count"])
        self.assertEqual(2, result["organizationCount"])
        self.assertEqual(4, result["relationCount"])
        self.assertEqual(2, result["leaderCount"])

        people = self.service.search(query="产品甲", limit=10)
        self.assertEqual(["产品甲"], [item["name"] for item in people])
        self.assertEqual(["department:dept-1", "biz_group:研发体系"], people[0]["organizationKeys"])
        self.assertTrue(people[0]["isDepartmentLeader"])
        self.assertEqual("部门主管", people[0]["leaderRoles"][0]["title"])

        organizations = self.service.search_organizations(limit=10)
        by_key = {item["organizationKey"]: item for item in organizations}
        self.assertEqual(2, by_key["department:dept-1"]["memberCount"])
        self.assertEqual(1, by_key["department:dept-1"]["leaderCount"])
        self.assertEqual(1, by_key["biz_group:研发体系"]["leaderCount"])

    def test_search_can_filter_by_organization_and_leader(self) -> None:
        self.service.sync()
        people = self.service.search(
            organization_key="department:dept-1", leaders_only=True, limit=10
        )
        self.assertEqual({"产品甲", "项目乙"}, {item["name"] for item in people})

    def test_cross_scope_leadership_is_not_counted_as_primary_membership(self) -> None:
        service = DirectoryService(self.db, FakeBiCenterWithCrossScope())
        service.sync()
        support = service.search_organizations(query="支持部", limit=10)[0]
        self.assertEqual(0, support["memberCount"])
        self.assertEqual(1, support["leaderCount"])
        relation = self.db.fetch_one(
            """
            SELECT is_primary, is_leader FROM employee_org_relation_cache
            WHERE employee_key='e-1' AND organization_key=?
            """,
            (support["organizationKey"],),
        )
        self.assertEqual(0, relation["is_primary"])
        self.assertEqual(1, relation["is_leader"])

    def test_personal_report_scope_is_self_or_led_organization(self) -> None:
        self.service.sync()
        self.db.execute(
            """
            INSERT INTO employee_cache(
              employee_key,user_id,employee_name,title,department_name,is_active,refreshed_at
            ) VALUES ('e-3','u-3','普通丙','工程师','支持部',1,'2026-08-14T09:00:00+08:00')
            """
        )
        leader_people = self.service.accessible_people("u-1")
        self.assertEqual({"u-1", "u-2"}, {item["userId"] for item in leader_people})
        self.assertTrue(self.service.can_view_person("u-1", "u-2"))
        self.assertFalse(self.service.can_view_person("u-1", "u-3"))
        self.assertEqual(["u-3"], [item["userId"] for item in self.service.accessible_people("u-3")])
        self.assertEqual(
            {"u-1", "u-2", "u-3"},
            {item["userId"] for item in self.service.accessible_people("u-3", full_scope=True)},
        )


if __name__ == "__main__":
    unittest.main()
