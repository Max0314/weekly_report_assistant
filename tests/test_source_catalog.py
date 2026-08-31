from __future__ import annotations

import unittest

from app.source_catalog import SOURCE_TABLE_BY_ID, SOURCE_TABLES


class SourceCatalogTests(unittest.TestCase):
    def test_business_sources_have_stable_categories_and_assignee_mapping(self) -> None:
        business = [item for item in SOURCE_TABLES if not item.get("roster") and not item.get("archive")]
        ordered = sorted(business, key=lambda item: item["categoryOrder"])
        self.assertEqual(
            [
                "客户拜访与交流", "市场招投标", "产品策划分析调研", "重点项目跟踪",
                "产品管理事项", "支持及待办", "其他事项", "使用反馈与改进",
            ],
            [item["category"] for item in ordered],
        )
        self.assertTrue(all(item.get("categoryKey") for item in business))
        self.assertTrue(all(item.get("assigneeFields") for item in business))

    def test_usage_feedback_fields_are_included_in_weekly_facts(self) -> None:
        feedback = SOURCE_TABLE_BY_ID["6YCHLaR"]
        self.assertEqual(["反馈类型"], feedback["subcategoryFields"])
        self.assertEqual(["反馈日期"], feedback["eventDateFields"])
        self.assertEqual(["责任人"], feedback["projectManagerFields"])
        self.assertEqual("责任人", feedback["assigneeFields"][0]["role"])


if __name__ == "__main__":
    unittest.main()
