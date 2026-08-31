from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.services.delivery import DeliveryError, DeliveryService
from app.services.reports import ReportService
from app.services.workflow_config import WorkflowConfigService


class FakeAI:
    def summarize(self, **kwargs):
        raise AssertionError("AI must not be called in deterministic test")


class FakeRenderer:
    def public_urls(self, report_id: int):
        return {"reportUrl": f"https://example.test/reports/{report_id}", "imageUrl": ""}

    def personal_report_url(self, report_id: int):
        return f"https://example.test/#/personal-reports?reportId={report_id}"


class FakeRobot:
    def __init__(self):
        self.group_calls = []
        self.private_calls = []
        self.private_recall_calls = []

    def send_group(self, **kwargs):
        self.group_calls.append(kwargs)
        return {"sent": True, "error": "", "processQueryKey": f"key-{len(self.group_calls)}"}

    def send_private(self, *args, **kwargs):
        self.private_calls.append((args, kwargs))
        return {"sent": True, "error": "", "processQueryKey": "private-key"}

    def recall_group(self, **kwargs):
        return {"recalled": True, "error": ""}

    def recall_private(self, **kwargs):
        self.private_recall_calls.append(kwargs)
        return {"recalled": True, "error": ""}


class FakeDirectory:
    def cache_status(self):
        return {"count": 2}


class RetryArchive:
    def __init__(self):
        self.calls = 0

    def write(self, report_id: int, *, report_url: str = ""):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("archive temporarily unavailable")
        return {"status": "sent", "skipped": False, "recordId": "rec-1", "error": ""}


class ReportsAndDeliveryTests(unittest.TestCase):
    def test_preview_markdown_uses_structured_sections(self) -> None:
        message = DeliveryService._markdown(
            {
                "title": "产品与项目管理周报",
                "version": 3,
                "window": {"label": "2026-08-24 至 2026-08-28 18:00"},
                "sections": {
                    "executiveSummary": "本周完成重点交付。当前存在延期风险。下周继续推进。",
                    "risks": "1. 第一项风险需要协调。\n2. 第二项风险需要跟进。\n3. 第三项不应出现在卡片中。",
                    "categorySections": [
                        {
                            "label": "市场招投标",
                            "order": 20,
                            "itemCount": 2,
                            "content": "- 移动路由器招标：完成价格沟通\n- FTTR 招标：确认技术评分规则",
                            "digest": "- 两项招标均进入准备阶段，本周完成价格与技术规则沟通。",
                        },
                        {
                            "label": "客户拜访与交流",
                            "order": 10,
                            "itemCount": 3,
                            "content": "- 客户甲：完成现场拜访\n- 客户乙：完成视频交流\n- 客户丙：完成线上沟通",
                            "digest": "- 完成 3 项客户交流，覆盖现场、视频和线上渠道。\n- 重点围绕合作机会与技术方案达成下一步共识。",
                        },
                    ],
                },
                "metrics": {
                    "itemCount": 12,
                    "managerCount": 4,
                    "riskCount": 3,
                    "overdueCount": 2,
                    "highPriorityCount": 1,
                    "byStatus": {"已完成": 7, "进行中": 3},
                },
            },
            preview=True,
        )
        self.assertIn("**核心数据**", message)
        self.assertIn("纳入事项：**12**", message)
        self.assertIn("已完成：**7**", message)
        self.assertIn("**分类概览**", message)
        self.assertLess(message.index("客户拜访与交流"), message.index("市场招投标"))
        self.assertIn("**客户拜访与交流（3 项）**", message)
        self.assertIn("- 完成 3 项客户交流", message)
        self.assertIn("两项招标均进入准备阶段", message)
        self.assertNotIn("客户甲：完成现场拜访", message)
        self.assertNotIn("移动路由器招标：完成价格沟通", message)
        self.assertIn("**本周进展摘要**", message)
        self.assertNotIn("**管理摘要**", message)
        self.assertIn("> 本周完成重点交付。", message)
        self.assertIn("**风险聚焦**", message)
        self.assertIn("1. 第一项风险需要协调。", message)
        self.assertIn("3. 第三项不应出现在卡片中。", message)
        self.assertIn("消息中仅展示管理摘要", message)
        self.assertIn("**审核操作**", message)
        self.assertIn("`确认发送`", message)

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        self.db.initialize()
        self.config = WorkflowConfigService(self.db)
        self.reports = ReportService(self.db, self.config, FakeAI())

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def seed_source(self) -> None:
        self.db.execute(
            """
            INSERT INTO source_record(
              base_id, table_id, table_name, record_id, category, title, status,
              product_manager_user_ids_json, product_manager_names_json,
              event_at, first_seen_at, last_seen_at, changed_at, record_hash, raw_json
            ) VALUES ('base', 'PoYFuV8', '产品管理事项', 'r1', '产品管理', '版本规划',
              '进行中', '["u1"]', '["产品甲"]', '2026-08-12T09:00:00+08:00',
              '2026-08-12T09:00:00+08:00', '2026-08-12T09:00:00+08:00',
              '2026-08-12T09:00:00+08:00', 'hash', '{}')
            """
        )

    def test_report_generation_uses_weekly_facts(self) -> None:
        self.seed_source()
        report = self.reports.generate(period_key="week:20260810", use_ai=False)
        self.assertEqual(1, report["metrics"]["itemCount"])
        self.assertEqual(1, report["metrics"]["managerCount"])
        self.assertIn("版本规划", report["sections"]["productHighlights"])
        category_sections = {
            item["label"]: item for item in report["sections"]["categorySections"]
        }
        self.assertEqual(1, category_sections["产品管理事项"]["itemCount"])
        self.assertEqual(0, category_sections["客户拜访与交流"]["itemCount"])
        self.assertEqual("- 暂无", category_sections["客户拜访与交流"]["content"])
        self.assertEqual("deterministic", report["aiStatus"])

    def test_personal_report_filters_frozen_snapshot_and_preserves_roles(self) -> None:
        common = (
            "'base','dEOVLJG','拜访交流记录',?,?,?,?,?,?,?,'[]','[]','[]','[]',?,"
            "'2026-08-12T09:00:00+08:00','2026-08-12T09:00:00+08:00',"
            "'2026-08-12T09:00:00+08:00','2026-08-12T09:00:00+08:00',?, '{}'"
        )
        self.db.execute(
            f"""
            INSERT INTO source_record(
              base_id,table_id,table_name,record_id,category_key,category_order,category,
              subcategory,title,status,product_manager_user_ids_json,
              project_manager_user_ids_json,product_manager_names_json,
              project_manager_names_json,assignees_json,event_at,first_seen_at,last_seen_at,
              changed_at,record_hash,raw_json
            ) VALUES ({common})
            """,
            (
                "visit-1", "customer_visit", 10, "客户拜访与交流", "现场交流", "客户甲", "进行中",
                '[{"userId":"u1","name":"产品甲","role":"产品经理"},'
                '{"userId":"u1","name":"产品甲","role":"协同负责人"},'
                '{"userId":"u2","name":"产品乙","role":"产品经理"}]',
                "hash-visit-1",
            ),
        )
        self.db.execute(
            f"""
            INSERT INTO source_record(
              base_id,table_id,table_name,record_id,category_key,category_order,category,
              subcategory,title,status,product_manager_user_ids_json,
              project_manager_user_ids_json,product_manager_names_json,
              project_manager_names_json,assignees_json,event_at,first_seen_at,last_seen_at,
              changed_at,record_hash,raw_json
            ) VALUES ({common})
            """,
            (
                "visit-2", "customer_visit", 10, "客户拜访与交流", "线上交流", "客户乙", "已完成",
                '[{"userId":"u2","name":"产品乙","role":"产品经理"}]',
                "hash-visit-2",
            ),
        )
        report = self.reports.generate(period_key="week:20260810", use_ai=False)
        self.assertEqual(2, report["metrics"]["managerCount"])
        members = {item["userId"]: item for item in self.reports.personal_members(report["id"])}
        self.assertEqual(1, members["u1"]["itemCount"])
        self.assertEqual(["产品经理", "协同负责人"], members["u1"]["roles"])

        self.db.execute("UPDATE source_record SET assignees_json='[]' WHERE record_id='visit-1'")
        personal = self.reports.personal(report["id"], user_id="u1", name="产品甲")
        self.assertEqual(1, personal["metrics"]["itemCount"])
        self.assertEqual(["客户甲"], [item["title"] for item in personal["items"]])
        self.assertEqual(["产品经理", "协同负责人"], personal["items"][0]["roles"])
        self.assertEqual("客户拜访与交流", personal["categorySections"][0]["label"])

    def test_report_preserves_fact_snapshot_after_source_changes(self) -> None:
        self.seed_source()
        report = self.reports.generate(period_key="week:20260810", use_ai=False)
        self.db.execute("UPDATE source_record SET title='later title' WHERE record_id='r1'")
        historical = self.reports.get(report["id"], include_sources=True)
        self.assertTrue(historical["sourceSnapshot"])
        self.assertEqual("版本规划", historical["sources"][0]["title"])

    def test_project_manager_coverage_combines_roster_and_weekly_facts(self) -> None:
        refreshed_at = "2026-08-13T09:00:00+08:00"
        for user_id, name in (("u-covered", "已填经理"), ("u-missing", "缺报经理")):
            self.db.execute(
                """
                INSERT INTO employee_cache(
                  employee_key,user_id,employee_name,title,department_name,refreshed_at
                ) VALUES (?,?,?,'项目经理','产品部',?)
                """,
                (user_id, user_id, name, refreshed_at),
            )
        self.config.update({
            "projectManagerRoster": [
                {"name": "已填经理", "userId": "u-covered"},
                {"name": "缺报经理", "userId": "u-missing"},
            ]
        })
        self.db.execute(
            """
            INSERT INTO source_record(
              base_id,table_id,table_name,record_id,category,title,status,
              project_manager_user_ids_json,project_manager_names_json,event_at,
              first_seen_at,last_seen_at,changed_at,record_hash,raw_json
            ) VALUES ('base','uRM5L3r','重点项目跟踪','project-1','重点项目','项目一','进行中',
              '["u-covered"]','["已填经理"]','2026-08-12T09:00:00+08:00',
              '2026-08-12T09:00:00+08:00','2026-08-12T09:00:00+08:00',
              '2026-08-12T09:00:00+08:00','hash-project','{}')
            """
        )
        coverage = self.reports.manager_coverage(
            period_key="week:20260810", report_kind="project"
        )
        self.assertEqual(2, coverage["expectedCount"])
        self.assertEqual(1, coverage["coveredCount"])
        self.assertEqual(["缺报经理"], [item["name"] for item in coverage["missing"]])

    def test_coverage_reminder_only_sends_once_to_missing_people(self) -> None:
        self.db.execute(
            """
            INSERT INTO employee_cache(
              employee_key,user_id,employee_name,title,department_name,refreshed_at
            ) VALUES ('u-missing','u-missing','缺报经理','项目经理','产品部','2026-08-13T09:00:00+08:00')
            """
        )
        self.config.update({
            "defaultRobotCode": "robot",
            "projectManagerRoster": [{"name": "缺报经理", "userId": "u-missing"}],
        })
        robot = FakeRobot()
        delivery = DeliveryService(
            database=self.db, reports=self.reports, renderer=FakeRenderer(),
            config_service=self.config, robot=robot, directory=FakeDirectory(),
        )
        first = delivery.send_coverage_reminders(
            period_key="week:20260810", report_kind="project"
        )
        second = delivery.send_coverage_reminders(
            period_key="week:20260810", report_kind="project"
        )
        self.assertEqual(1, first["sent"])
        self.assertEqual(0, second["sent"])
        self.assertTrue(second["results"][0]["skipped"])
        self.assertEqual(1, len(robot.private_calls))

    def test_formal_send_requires_approval_and_is_idempotent_after_success(self) -> None:
        report = self.reports.generate(period_key="week:20260810", use_ai=False)
        self.config.update({
            "sendGroupImages": False,
            "previewGroupTargets": [{"name": "预览群", "openConversationId": "preview", "robotCode": "robot"}],
            "formalGroupTargets": [{"name": "正式群", "openConversationId": "cid", "robotCode": "robot"}],
        })
        robot = FakeRobot()
        delivery = DeliveryService(
            database=self.db, reports=self.reports, renderer=FakeRenderer(),
            config_service=self.config, robot=robot, directory=FakeDirectory(),
        )
        with self.assertRaises(DeliveryError):
            delivery.formal(report["id"])
        delivery.preview(report["id"])
        preview_param = robot.group_calls[0]["msg_param"]
        self.assertEqual("sampleActionCard", robot.group_calls[0]["msg_key"])
        self.assertEqual(
            ["查看团队周报", "查看个人周报"],
            [button["title"] for button in preview_param["btns"]],
        )
        self.assertEqual(
            [
                f"https://example.test/reports/{report['id']}",
                f"https://example.test/#/personal-reports?reportId={report['id']}",
            ],
            [button["actionURL"] for button in preview_param["btns"]],
        )
        self.assertNotIn("singleTitle", preview_param)
        with self.assertRaises(DeliveryError):
            delivery.formal(report["id"])
        self.reports.approve(report["id"], actor="approver")
        first = delivery.formal(report["id"])
        second = delivery.formal(report["id"])
        self.assertEqual(1, first["sent"])
        self.assertTrue(second["skipped"])
        self.assertEqual(2, len(robot.group_calls))

    def test_personal_preview_formal_and_recall(self) -> None:
        report = self.reports.generate(period_key="week:20260810", use_ai=False)
        self.config.update({
            "sendGroupImages": False,
            "defaultRobotCode": "robot",
            "previewPersonalTargets": [{"name": "审核人", "userId": "u-review"}],
            "formalPersonalTargets": [{"name": "审核人", "userId": "u-review"}],
            "approverTargets": [{"name": "审核人", "userId": "u-review"}],
        })
        robot = FakeRobot()
        delivery = DeliveryService(
            database=self.db, reports=self.reports, renderer=FakeRenderer(),
            config_service=self.config, robot=robot, directory=FakeDirectory(),
        )
        preview = delivery.preview(report["id"])
        self.assertEqual(1, preview["sent"])
        self.assertEqual(1, len(robot.private_calls))
        _, preview_kwargs = robot.private_calls[0]
        self.assertEqual("sampleActionCard", preview_kwargs["msg_key"])
        self.assertEqual("查看我的个人周报", preview_kwargs["msg_param"]["singleTitle"])
        self.assertNotIn("btns", preview_kwargs["msg_param"])
        self.assertEqual(
            f"https://example.test/#/personal-reports?reportId={report['id']}",
            preview_kwargs["msg_param"]["singleURL"],
        )
        self.assertIn(
            f"[查看团队周报详情](https://example.test/reports/{report['id']})",
            preview_kwargs["msg_param"]["text"],
        )
        self.reports.approve(report["id"], actor="u-review")
        formal = delivery.formal(report["id"])
        self.assertEqual(1, formal["sent"])
        self.assertEqual(2, len(robot.private_calls))
        recalled = delivery.recall(report["id"])
        self.assertEqual(1, recalled["recalled"])
        self.assertEqual(1, len(robot.private_recall_calls))

    def test_archive_retry_does_not_repeat_a_formal_message(self) -> None:
        report = self.reports.generate(period_key="week:20260810", use_ai=False)
        self.config.update({
            "sendGroupImages": False,
            "previewGroupTargets": [{"openConversationId": "preview", "robotCode": "robot"}],
            "formalGroupTargets": [{"openConversationId": "formal", "robotCode": "robot"}],
            "archiveWriteEnabled": True,
            "archiveTableId": "archive",
            "archiveFieldMap": {
                "archiveKey": "fldKey", "title": "fldTitle", "periodKey": "fldPeriod"
            },
        })
        robot = FakeRobot()
        archive = RetryArchive()
        delivery = DeliveryService(
            database=self.db, reports=self.reports, renderer=FakeRenderer(),
            config_service=self.config, robot=robot, directory=FakeDirectory(), archive=archive,
        )
        delivery.preview(report["id"])
        self.reports.approve(report["id"], actor="approver")
        first = delivery.formal(report["id"])
        second = delivery.formal(report["id"])
        self.assertEqual("error", first["archive"]["status"])
        self.assertEqual("sent", second["archive"]["status"])
        self.assertTrue(second["skipped"])
        self.assertEqual(2, len(robot.group_calls))  # one preview and one formal


if __name__ == "__main__":
    unittest.main()
