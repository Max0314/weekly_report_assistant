from __future__ import annotations

import unittest

from app.services.robot_commands import parse_command
from app.services.workflow_config import normalize_config


class WorkflowConfigTests(unittest.TestCase):
    def test_formal_send_is_always_manual_and_targets_are_deduplicated(self) -> None:
        config = normalize_config(
            {
                "autoFormalSendEnabled": True,
                "previewGroupTargets": [
                    {"name": "测试群", "openConversationId": "cid-1", "robotCode": "robot-1"},
                    {"name": "重复群", "openConversationId": "cid-1", "robotCode": "robot-1"},
                    {"name": "缺参数", "openConversationId": "cid-2"},
                ],
            }
        )
        self.assertFalse(config["autoFormalSendEnabled"])
        self.assertEqual(1, len(config["previewGroupTargets"]))

    def test_command_parser_extracts_report_and_reason(self) -> None:
        parsed = parse_command("@周报助手 需要修改 #42：补充项目风险")
        self.assertEqual("changes", parsed["command"])
        self.assertEqual(42, parsed["reportId"])
        self.assertEqual("补充项目风险", parsed["reason"])

    def test_command_parser_selects_project_report(self) -> None:
        parsed = parse_command("生成项目经理周报")
        self.assertEqual("generate", parsed["command"])
        self.assertEqual("project", parsed["reportKind"])

    def test_informational_sentence_does_not_trigger_generation(self) -> None:
        self.assertEqual("unknown", parse_command("生成周报的规则是什么？")["command"])

    def test_number_in_change_reason_is_not_report_id(self) -> None:
        parsed = parse_command("需要修改：补充3个项目风险")
        self.assertEqual("changes", parsed["command"])
        self.assertIsNone(parsed["reportId"])

    def test_preview_and_formal_groups_cannot_overlap_on_update(self) -> None:
        import tempfile
        from pathlib import Path
        from app.db import Database
        from app.services.workflow_config import WorkflowConfigService

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            service = WorkflowConfigService(database)
            with self.assertRaisesRegex(ValueError, "different openConversationId"):
                service.update({
                    "previewGroupTargets": [{"openConversationId": "same", "robotCode": "r"}],
                    "formalGroupTargets": [{"openConversationId": "same", "robotCode": "r"}],
                })

    def test_personal_targets_require_a_robot_code(self) -> None:
        import tempfile
        from pathlib import Path
        from app.db import Database
        from app.services.workflow_config import WorkflowConfigService

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            service = WorkflowConfigService(database)
            with self.assertRaisesRegex(ValueError, "defaultRobotCode"):
                service.update({"formalPersonalTargets": [{"name": "审核人", "userId": "u1"}]})
            configured = service.update({
                "defaultRobotCode": "robot",
                "previewPersonalTargets": [{"name": "审核人", "userId": "u1"}],
                "formalPersonalTargets": [{"name": "审核人", "userId": "u1"}],
            })
            self.assertEqual("u1", configured["formalPersonalTargets"][0]["userId"])

    def test_archive_requires_explicit_idempotency_and_identity_fields(self) -> None:
        import tempfile
        from pathlib import Path
        from app.db import Database
        from app.services.workflow_config import WorkflowConfigService

        with tempfile.TemporaryDirectory() as temp_dir:
            database = Database(Path(temp_dir) / "test.db")
            database.initialize()
            service = WorkflowConfigService(database)
            with self.assertRaisesRegex(ValueError, "archiveFieldMap"):
                service.update({"archiveWriteEnabled": True, "archiveFieldMap": {}})
            configured = service.update({
                "archiveWriteEnabled": True,
                "archiveTableId": "archive",
                "archiveFieldMap": {
                    "archiveKey": "fldKey", "title": "fldTitle", "periodKey": "fldPeriod"
                },
            })
            self.assertTrue(configured["archiveWriteEnabled"])


if __name__ == "__main__":
    unittest.main()
