from __future__ import annotations

import unittest
from unittest.mock import patch

from app.config import Settings
from app.integrations.aitable import AITableClient


class FakeDingTalk:
    def access_token(self):
        return "token"


class AITableClientTests(unittest.TestCase):
    def test_create_record_uses_records_payload_with_field_ids(self) -> None:
        config = Settings(
            aitable_base_id="base",
            dingtalk_aitable_operator_id="operator",
            dingtalk_app_key="key",
            dingtalk_app_secret="secret",
        )
        client = AITableClient(config, FakeDingTalk())
        with patch(
            "app.integrations.aitable.request_json",
            return_value={"data": {"records": [{"recordId": "rec-1"}]}},
        ) as request:
            result = client.create_record("table", {"fldTitle": "周报"})
        self.assertEqual("rec-1", result["recordId"])
        _, kwargs = request.call_args
        self.assertEqual("POST", kwargs["method"])
        self.assertEqual({"operatorId": "operator"}, kwargs["params"])
        self.assertEqual(
            {"records": [{"cells": {"fldTitle": "周报"}}]}, kwargs["payload"]
        )


if __name__ == "__main__":
    unittest.main()
