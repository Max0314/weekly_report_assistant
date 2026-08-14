from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.config import Settings
from app.services.ai_summary import AISummaryClient, SECTION_KEYS


class AISummaryTests(unittest.TestCase):
    def test_prompt_is_bounded_risk_first_and_excludes_names_by_default(self) -> None:
        config = Settings(
            _env_file=None,
            ai_base_url="https://ai.example/v1",
            ai_api_key="token",
            ai_model="model",
            ai_max_items=10,
            ai_max_text_chars=100,
            ai_include_person_names=False,
        )
        items = [
            {
                "id": index, "title": f"事项{index}", "progressText": "x" * 200,
                "riskText": "存在风险" if index == 14 else "",
                "productManagerNames": ["姓名"],
            }
            for index in range(15)
        ]
        response = {"choices": [{"message": {"content": json.dumps({key: "内容" for key in SECTION_KEYS})}}]}
        with patch("app.services.ai_summary.request_json", return_value=response) as request:
            result = AISummaryClient(config).summarize(
                window={}, metrics={}, items=items, fallback={},
                project_baseline=[{
                    "name": "项目A", "direction": "平台", "owner": "负责人姓名",
                    "description": "基础描述", "visible": True
                }],
            )
        payload = request.call_args.kwargs["payload"]
        prompt = json.loads(payload["messages"][1]["content"])
        self.assertEqual(10, len(prompt["facts"]))
        self.assertEqual(14, prompt["facts"][0]["id"])
        self.assertNotIn("productManagers", prompt["facts"][0])
        self.assertEqual("项目A", prompt["projectBackground"][0]["name"])
        self.assertNotIn("owner", prompt["projectBackground"][0])
        self.assertIn("不得据此编造", prompt["rules"][-1])
        self.assertEqual(100, len(next(item for item in prompt["facts"] if item["id"] != 14)["progress"]))
        self.assertEqual("内容", result["executiveSummary"])


if __name__ == "__main__":
    unittest.main()
