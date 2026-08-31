from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.db import Database
from app.integrations.teambition import TeambitionClient, TeambitionError
from app.services.reports import ReportService
from app.services.teambition import TeambitionService, _normalized_project_name
from app.services.workflow_config import WorkflowConfigService
from app.time_utils import SHANGHAI


class FakeAI:
    def summarize(self, **kwargs):
        raise AssertionError("AI must not be called")


class FakeTeambitionClient:
    source = "native"

    def configured(self):
        return True

    def map_dingtalk_user_ids(self, user_ids):
        return {user_id: f"tb-{user_id}" for user_id in user_ids}

    def search_executor_tasks(self, query_user_id):
        if query_user_id != "tb-u1":
            return []
        return [
            {
                "id": "parent",
                "uniqueId": 100,
                "projectId": "project-1",
                "executorId": "tb-u1",
                "content": "父任务",
                "dueDate": "2026-08-31T10:00:00.000Z",
                "updated": "2026-08-20T10:00:00.000Z",
            },
            {
                "id": "overdue",
                "uniqueId": 101,
                "projectId": "project-1",
                "executorId": "tb-u1",
                "parentTaskId": "parent",
                "content": "完成接口联调",
                "priority": 2,
                "dueDate": "2026-08-24T10:00:00.000Z",
                "updated": "2026-08-23T10:00:00.000Z",
            },
            {
                "id": "done",
                "uniqueId": 102,
                "projectId": "project-1",
                "executorId": "tb-u1",
                "content": "完成看板原型",
                "isDone": True,
                "dueDate": "2026-08-19T10:00:00.000Z",
                "accomplishTime": "2026-08-18T10:00:00.000Z",
                "updated": "2026-08-18T10:00:00.000Z",
            },
        ]

    def query_projects(self, project_ids):
        return [{"id": "project-1", "name": "周报助手", "isArchived": False}]

    def search_projects(self, name):
        if name != "周报助手":
            return []
        return [
            {
                "id": "project-1",
                "name": "周报助手",
                "isArchived": False,
                "isSuspended": False,
                "updated": "2026-08-25T08:00:00.000Z",
                "ownerIds": ["tb-u1"],
                "customfields": [
                    {
                        "type": "text",
                        "value": [{"title": "D25/1511-85007-B1019"}],
                    },
                    {"type": "number", "value": [{"title": "85"}]},
                ],
            },
            {
                "id": "unrelated-project",
                "name": "周报助手二期",
                "isArchived": False,
                "customfields": [],
            },
            {
                "id": "same-name-wrong-code",
                "name": "周报助手",
                "isArchived": False,
                "customfields": [
                    {
                        "type": "text",
                        "value": [{"title": "D25/1511-85007-B9999"}],
                    }
                ],
            },
        ]

    def query_project_statuses(self, project_id, *, operator_id="", max_pages=20):
        if project_id != "project-1":
            return []
        return [
            {
                "projectId": project_id,
                "name": "批量生产及维护",
                "degree": "normal",
                "content": "完成服务迁移与生产验证。",
                "created": "2026-08-25T08:00:00.000Z",
            }
        ]

    def query_project_tasks(self, project_id, *, operator_id="", max_pages=500):
        return self.search_executor_tasks("tb-u1") if project_id == "project-1" else []


class TeambitionClientTests(unittest.TestCase):
    def test_project_name_matching_normalizes_cjk_compatibility_characters(self) -> None:
        self.assertEqual(
            _normalized_project_name("专利撰写AI助手Pat-A研发"),
            _normalized_project_name("专利撰写AI助⼿Pat-A研发"),
        )

    def test_default_source_matches_bi_center_production_native(self) -> None:
        self.assertEqual("native", Settings(_env_file=None).teambition_source)

    def test_accepts_bi_center_native_environment_names(self) -> None:
        config = Settings(
            _env_file=None,
            BI_CENTER_TEAMBITION_SOURCE="native",
            BI_CENTER_TEAMBITION_OPEN_API_BASE="https://open.teambition.com/api",
            BI_CENTER_TEAMBITION_OPEN_APP_ID="app-id",
            BI_CENTER_TEAMBITION_OPEN_APP_SECRET="app-secret",
            BI_CENTER_TEAMBITION_OPEN_ORGANIZATION_ID="org-id",
            BI_CENTER_TEAMBITION_OPEN_REQUEST_TIMEOUT=35,
        )
        self.assertTrue(config.teambition_configured)
        self.assertEqual("native", config.teambition_source)
        self.assertEqual(35, config.teambition_request_timeout)

    def test_dingtalk_client_reuses_bi_center_protocol_and_paginates(self) -> None:
        config = Settings(
            teambition_source="dingtalk",
            teambition_dingtalk_app_key="ding-app",
            teambition_dingtalk_app_secret="ding-secret",
        )
        client = TeambitionClient(config)
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/v1.0/oauth2/accessToken"):
                return {"accessToken": "access-token", "expireIn": 7200}
            token = (kwargs.get("params") or {}).get("nextToken")
            if token:
                return {"result": [{"taskId": "task-2"}], "nextToken": ""}
            return {"result": [{"taskId": "task-1"}], "nextToken": "next-page"}

        with patch("app.integrations.teambition.request_json", side_effect=fake_request):
            rows = client.search_executor_tasks("ding-user")
        self.assertEqual(["task-1", "task-2"], [item["taskId"] for item in rows])
        task_calls = [item for item in calls if "/tasks/search" in item[0]]
        self.assertEqual(2, len(task_calls))
        self.assertEqual("POST", task_calls[0][1]["method"])
        self.assertEqual("executor", task_calls[0][1]["params"]["roleTypes"])
        self.assertEqual(
            "access-token",
            task_calls[0][1]["headers"]["x-acs-dingtalk-access-token"],
        )
        self.assertNotIn("ding-secret", json.dumps(task_calls, ensure_ascii=False))

    def test_native_client_maps_users_and_queries_executor_tasks(self) -> None:
        config = Settings(
            teambition_source="native",
            teambition_open_app_id="app-id",
            teambition_open_app_secret="app-secret",
            teambition_open_organization_id="org-id",
        )
        client = TeambitionClient(config)
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/idmap/dingtalk/getTbUserId"):
                return {"result": [{"dingtalkUserId": "u1", "tbUserId": "tb-u1"}]}
            if url.endswith("/all-task/search"):
                return {"result": ["task-1"], "nextPageToken": ""}
            if url.endswith("/v3/task/query"):
                return {"result": [{"id": "task-1", "executorId": "tb-u1"}]}
            raise AssertionError(url)

        with patch("app.integrations.teambition.request_json", side_effect=fake_request):
            self.assertEqual({"u1": "tb-u1"}, client.map_dingtalk_user_ids(["u1"]))
            self.assertEqual("task-1", client.search_executor_tasks("tb-u1")[0]["id"])
        headers = calls[0][1]["headers"]
        self.assertEqual("organization", headers["X-Tenant-Type"])
        self.assertEqual("org-id", headers["X-Tenant-Id"])
        self.assertTrue(headers["Authorization"].startswith("Bearer "))
        task_calls = [item for item in calls if item[0].endswith("/all-task/search")]
        self.assertEqual("tb-u1", task_calls[0][1]["headers"]["x-operator-id"])
        self.assertNotIn("app-secret", json.dumps(calls, ensure_ascii=False))

    def test_native_executor_pagination_rejects_stalled_pages(self) -> None:
        config = Settings(
            teambition_source="native",
            teambition_open_app_id="app-id",
            teambition_open_app_secret="app-secret",
            teambition_open_organization_id="org-id",
        )
        client = TeambitionClient(config)

        with patch(
            "app.integrations.teambition.request_json",
            return_value={"result": ["task-1"], "nextPageToken": "same-page"},
        ):
            with self.assertRaisesRegex(TeambitionError, "no new task"):
                client.search_executor_tasks("tb-u1", max_pages=4)

    def test_native_client_searches_whitelisted_project_and_reads_status(self) -> None:
        config = Settings(
            teambition_source="native",
            teambition_open_app_id="app-id",
            teambition_open_app_secret="app-secret",
            teambition_open_organization_id="org-id",
        )
        client = TeambitionClient(config)
        calls = []

        def fake_request(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/v3/project/query"):
                self.assertEqual("周报助手", kwargs["params"]["name"])
                return {"result": [{"id": "project-1", "name": "周报助手"}]}
            if url.endswith("/v3/project/project-1/status/list"):
                return {
                    "result": [
                        {
                            "projectId": "project-1",
                            "name": "批量生产及维护",
                            "degree": "normal",
                        }
                    ]
                }
            if url.endswith("/v3/project/project-1/task/query"):
                return {"result": [{"id": "task-1", "projectId": "project-1"}]}
            raise AssertionError(url)

        with patch("app.integrations.teambition.request_json", side_effect=fake_request):
            projects = client.search_projects("周报助手")
            statuses = client.query_project_statuses("project-1", operator_id="tb-u1")
            tasks = client.query_project_tasks("project-1", operator_id="tb-u1")
        self.assertEqual("project-1", projects[0]["id"])
        self.assertEqual("批量生产及维护", statuses[0]["name"])
        self.assertEqual("task-1", tasks[0]["id"])
        self.assertEqual(
            "tb-u1",
            calls[1][1]["headers"]["x-operator-id"],
        )
        self.assertEqual(
            "tb-u1",
            calls[2][1]["headers"]["x-operator-id"],
        )


class TeambitionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        self.db.initialize()
        self.config = WorkflowConfigService(self.db)
        self.config.update(
            {
                "teambitionDepartmentNames": ["AI应用研发部"],
                "teambitionIncludeInReports": True,
            }
        )
        employees = (
            ("u1", "产品甲", "AI应用研发部", "AI应用开发组"),
            ("u2", "项目乙", "AI应用研发部", "AI应用开发组"),
            ("u3", "测试丙", "质量保障部", "AI应用研发部"),
        )
        for user_id, name, department, biz_group in employees:
            self.db.execute(
                """
                INSERT INTO employee_cache(
                    employee_key,user_id,employee_name,title,department_name,biz_group_name,refreshed_at
                ) VALUES (?,?,?,'项目经理',?,?,'2026-08-26T09:00:00+08:00')
                """,
                (user_id, user_id, name, department, biz_group),
            )
        self.settings = Settings(
            teambition_sync_enabled=True,
            teambition_source="native",
            teambition_open_app_id="app-id",
            teambition_open_app_secret="app-secret",
            teambition_open_organization_id="org-id",
        )
        self.service = TeambitionService(
            self.db, self.config, FakeTeambitionClient(), self.settings
        )
        self.db.execute(
            """
            INSERT INTO source_record(
              base_id,table_id,table_name,record_id,category_key,category_order,
              category,title,status,progress_text,product_manager_user_ids_json,
              product_manager_names_json,assignees_json,event_at,source_updated_at,
              first_seen_at,last_seen_at,changed_at,record_hash,raw_json
            ) VALUES (
              'base','uRM5L3r','重点项目跟踪','key-project-1','key_project',40,
              '重点项目跟踪','周报助手','正常','完成基础联调','["u1"]','["产品甲"]',
              '[{"userId":"u1","name":"产品甲","role":"产品经理"}]',
              '2026-08-25T16:00:00+08:00','2026-08-25T16:00:00+08:00',
              '2026-08-25T16:00:00+08:00','2026-08-25T16:00:00+08:00',
              '2026-08-25T16:00:00+08:00','hash-key-project',?
            )
            """,
            (
                json.dumps(
                    {
                        "fieldValues": {
                            "项目名称": "周报助手",
                            "项目编号": "D25/1511-85007-B1019",
                        }
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sync_only_enriches_aitable_key_project_with_tb_status(self) -> None:
        now = datetime(2026, 8, 26, 12, 0, tzinfo=SHANGHAI)
        with patch("app.services.teambition.now_local", return_value=now):
            result = self.service.sync(actor="test")
            board = self.service.dashboard(month="2026-08")
        self.assertEqual("success", result["status"])
        self.assertEqual(3, result["members"])
        self.assertEqual(3, result["tasks"])
        self.assertEqual(1, result["keyProjects"])
        self.assertEqual(1, result["projectStatuses"])
        self.assertEqual(1, result["projects"])
        self.assertEqual(0, board["summary"]["inProgressCount"])
        self.assertEqual(1, board["summary"]["overdueCount"])
        self.assertEqual(1, board["summary"]["completedInMonthCount"])
        self.assertEqual({"overdue", "completed"}, {item["status"] for item in board["items"]})
        self.assertNotIn("parent", {item["taskId"] for item in board["items"]})

        self.assertIsNone(
            self.db.fetch_one("SELECT is_deleted FROM source_record WHERE record_id='parent'")
        )
        project = self.db.fetch_one(
            "SELECT * FROM teambition_project WHERE project_id='project-1'"
        )
        self.assertEqual("key-project-1", project["matched_record_id"])
        self.assertEqual("D25/1511-85007-B1019", project["project_code"])
        self.assertEqual("批量生产及维护", project["status_name"])
        self.assertEqual(85, project["progress_percent"])
        self.assertIsNone(
            self.db.fetch_one(
                "SELECT project_id FROM teambition_project WHERE project_id='unrelated-project'"
            )
        )
        self.assertIsNone(
            self.db.fetch_one(
                "SELECT project_id FROM teambition_project WHERE project_id='same-name-wrong-code'"
            )
        )
        reports = ReportService(self.db, self.config, FakeAI())
        with patch("app.services.reports.now_local", return_value=now):
            report = reports.generate(
                period_key="week:20260824", report_kind="project", use_ai=False
            )
        self.assertEqual(1, report["metrics"]["itemCount"])
        self.assertEqual("重点项目跟踪", report["sources"][0]["category"])
        self.assertEqual(
            "批量生产及维护",
            report["sources"][0]["teambitionProject"]["statusName"],
        )
        self.assertIn("批量生产及维护", report["sections"]["projectHighlights"])
        self.assertNotIn("TB任务", report["metrics"]["byCategory"])

    def test_status_never_exposes_credentials(self) -> None:
        payload = self.service.status()
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertTrue(payload["configured"])
        self.assertNotIn("app-secret", serialized)
        self.assertNotIn("app-id", serialized)


if __name__ == "__main__":
    unittest.main()
