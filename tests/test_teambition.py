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
from app.services.teambition import TeambitionService
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


class TeambitionClientTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sync_builds_board_and_project_report_facts(self) -> None:
        now = datetime(2026, 8, 26, 12, 0, tzinfo=SHANGHAI)
        with patch("app.services.teambition.now_local", return_value=now):
            result = self.service.sync(actor="test")
            board = self.service.dashboard(month="2026-08")
        self.assertEqual("success", result["status"])
        self.assertEqual(3, result["members"])
        self.assertEqual(3, result["tasks"])
        self.assertEqual(0, board["summary"]["inProgressCount"])
        self.assertEqual(1, board["summary"]["overdueCount"])
        self.assertEqual(1, board["summary"]["completedInMonthCount"])
        self.assertEqual({"overdue", "completed"}, {item["status"] for item in board["items"]})
        self.assertNotIn("parent", {item["taskId"] for item in board["items"]})

        parent_source = self.db.fetch_one(
            "SELECT is_deleted FROM source_record WHERE record_id='parent'"
        )
        self.assertEqual(1, int(parent_source["is_deleted"]))
        reports = ReportService(self.db, self.config, FakeAI())
        with patch("app.services.reports.now_local", return_value=now):
            report = reports.generate(
                period_key="week:20260824", report_kind="project", use_ai=False
            )
        self.assertEqual(1, report["metrics"]["itemCount"])
        self.assertEqual("TB任务", report["sources"][0]["category"])
        self.assertIn("完成接口联调", report["sections"]["projectHighlights"])

    def test_status_never_exposes_credentials(self) -> None:
        payload = self.service.status()
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertTrue(payload["configured"])
        self.assertNotIn("app-secret", serialized)
        self.assertNotIn("app-id", serialized)


if __name__ == "__main__":
    unittest.main()
