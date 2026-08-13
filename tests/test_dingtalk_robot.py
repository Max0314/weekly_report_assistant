from __future__ import annotations

import unittest
from unittest.mock import patch

from app.config import Settings
from app.integrations.dingtalk_robot.dingtalk_robot import DingTalkRobotClient


class DingTalkRobotClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = DingTalkRobotClient(
            Settings(dingtalk_app_key="robot-code", dingtalk_app_secret="secret")
        )

    def test_private_recall_uses_batch_recall_endpoint(self) -> None:
        with patch.object(
            self.client,
            "_post",
            return_value={"successResult": ["process-1"], "failedResult": {}},
        ) as post:
            result = self.client.recall_private(
                robot_code="robot-code", process_query_keys=["process-1", "process-1"]
            )

        self.assertTrue(result["recalled"])
        post.assert_called_once_with(
            "/v1.0/robot/otoMessages/batchRecall",
            {"robotCode": "robot-code", "processQueryKeys": ["process-1"]},
        )

    def test_private_recall_reports_failed_result(self) -> None:
        with patch.object(
            self.client,
            "_post",
            return_value={"successResult": [], "failedResult": {"process-1": "expired"}},
        ):
            result = self.client.recall_private(
                robot_code="robot-code", process_query_keys="process-1"
            )

        self.assertFalse(result["recalled"])
        self.assertIn("process-1: expired", result["error"])

    def test_recall_requires_explicit_success_confirmation(self) -> None:
        with patch.object(self.client, "_post", return_value={}):
            result = self.client.recall_private(
                robot_code="robot-code", process_query_keys="process-1"
            )

        self.assertFalse(result["recalled"])
        self.assertIn("successResult", result["error"])


if __name__ == "__main__":
    unittest.main()
