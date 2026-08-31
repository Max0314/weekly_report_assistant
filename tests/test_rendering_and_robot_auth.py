from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.services.rendering import _section_html, _summary_html, report_html
from app.services.reports import ReportService
from app.services.robot_commands import RobotCommandService
from app.services.workflow_config import WorkflowConfigService


class RenderingAndAuthTests(unittest.TestCase):
    def test_inline_numbered_sections_are_split_without_breaking_decimals(self) -> None:
        output = _section_html("1. 完成流程优化，周期压缩至3.5天。 2. 样机功耗达到2.3W。 3. 发布规范。")
        self.assertEqual(3, output.count("<li>"))
        self.assertIn("周期压缩至3.5天。", output)
        self.assertIn("样机功耗达到2.3W。", output)
        self.assertNotIn("1. 完成", output)

    def test_summary_is_rendered_as_separate_sentences(self) -> None:
        output = _summary_html("本周完成首轮交付。重点项目按计划推进！风险事项持续跟踪。")
        self.assertEqual(3, output.count("<p>"))
        self.assertIn("<p>重点项目按计划推进！</p>", output)

    def test_report_html_escapes_source_text(self) -> None:
        report = {
            "id": 1, "title": "<script>alert(1)</script>", "version": 1,
            "window": {"label": "本周"}, "metrics": {}, "sections": {},
            "sources": [{"title": "<b>事项</b>", "category": "测试"}],
        }
        output = report_html(report, interactive=True)
        self.assertNotIn("<script>alert(1)</script>", output)
        self.assertIn("&lt;script&gt;", output)
        self.assertIn("&lt;b&gt;事项&lt;/b&gt;", output)
        self.assertIn('data-label="事项"', output)
        self.assertIn("thead{display:none}", output)
        self.assertIn('<details class="card table-card fact-details">', output)
        self.assertNotIn('<details class="card table-card fact-details" open>', output)
        self.assertIn('class="readonly-pill">只读浏览</span>', output)
        self.assertIn('class="external-open"', output)
        self.assertIn("max-width:1480px", output)
        self.assertNotIn('class="external-open"', report_html(report))

    def test_report_html_uses_category_digest_instead_of_raw_details(self) -> None:
        report = {
            "id": 1,
            "title": "分类明细版",
            "version": 1,
            "window": {"label": "本周"},
            "metrics": {},
            "sections": {
                "categorySections": [
                    {
                        "label": "客户拜访与交流",
                        "itemCount": 9,
                        "digest": "- 完成 9 项客户交流，重点推进合作机会与方案确认。",
                        "content": "客户 A 原始拜访记录；客户 B 原始会议记录。",
                    }
                ]
            },
            "sources": [],
        }

        output = report_html(report)

        self.assertIn("完成 9 项客户交流", output)
        self.assertNotIn("客户 A 原始拜访记录", output)

    def test_sensitive_robot_command_requires_approver_and_configured_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            config = WorkflowConfigService(database)
            config.update({
                "previewGroupTargets": [{"name": "预览群", "openConversationId": "cid-ok", "robotCode": "r"}],
                "approverTargets": [{"name": "审核人", "userId": "u-ok"}],
            })
            service = RobotCommandService(database=database, config_service=config)
            allowed, _ = service._authorized(
                {"sender_id": "u-ok", "conversation_id": "cid-ok", "conversation_type": "2", "robot_code": "r"}, "confirm"
            )
            wrong_user, _ = service._authorized(
                {"sender_id": "u-no", "conversation_id": "cid-ok", "conversation_type": "2", "robot_code": "r"}, "confirm"
            )
            wrong_group, _ = service._authorized(
                {"sender_id": "u-ok", "conversation_id": "cid-no", "conversation_type": "2", "robot_code": "r"}, "confirm"
            )
            wrong_robot, _ = service._authorized(
                {"sender_id": "u-ok", "conversation_id": "cid-ok", "conversation_type": "2", "robot_code": "other"}, "confirm"
            )
            self.assertTrue(allowed)
            self.assertFalse(wrong_user)
            self.assertFalse(wrong_group)
            self.assertFalse(wrong_robot)

    def test_bare_confirm_uses_context_and_rejects_ambiguous_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            config = WorkflowConfigService(database)
            reports = ReportService(database=database, config_service=config)
            first = reports.generate(period_key="week:20260810", report_kind="combined", use_ai=False)
            second = reports.generate(period_key="week:20260810", report_kind="project", use_ai=False)
            for report in (first, second):
                database.execute(
                    "UPDATE weekly_report SET workflow_state='awaiting_approval', confirm_status='awaiting', previewed_at='2026-08-14T18:00:00+08:00' WHERE id=?",
                    (report["id"],),
                )
            database.execute(
                """
                INSERT INTO dingtalk_robot_send_log(
                  business_type,business_id,target_type,target_name,target_id,conversation_id,
                  robot_code,msg_key,process_query_key,send_status,snapshot_json,idempotency_key,created_at
                ) VALUES ('weekly_report',?,'group','预览群','cid','cid','r','sampleMarkdown','k','sent',
                  '{"phase":"preview"}',?,'2026-08-14T18:00:00+08:00')
                """,
                (str(first["id"]), f"preview-{first['id']}"),
            )
            service = RobotCommandService(database=database, reports=reports, config_service=config)
            row = {"conversation_id": "cid", "conversation_type": "2", "sender_id": "u"}
            selected = service._resolve_report(None, row, "confirm")
            self.assertEqual(first["id"], selected["id"])
            database.execute(
                """
                INSERT INTO dingtalk_robot_send_log(
                  business_type,business_id,target_type,target_name,target_id,conversation_id,
                  robot_code,msg_key,process_query_key,send_status,snapshot_json,idempotency_key,created_at
                ) VALUES ('weekly_report',?,'group','预览群','cid','cid','r','sampleMarkdown','k2','sent',
                  '{"phase":"preview"}',?,'2026-08-14T18:01:00+08:00')
                """,
                (str(second["id"]), f"preview-{second['id']}"),
            )
            with self.assertRaisesRegex(ValueError, "多个可处理版本"):
                service._resolve_report(None, row, "confirm")


if __name__ == "__main__":
    unittest.main()
