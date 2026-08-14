from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.integrations.aitable import TableResult
from app.services.collector import SourceCollector
from app.services.workflow_config import WorkflowConfigService


class FakeDirectory:
    def lookup_by_user_id(self):
        return {
            "u-product": {"employee_name": "产品甲"},
            "u-project": {"employee_name": "项目乙"},
        }

    def lookup_by_union_id(self):
        return {
            "union-product": {"user_id": "u-product", "employee_name": "产品甲"},
        }


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        self.db.initialize()
        config = WorkflowConfigService(self.db)
        config.update({"projectManagerFieldOverrides": {"table-1": ["项目负责人"]}})
        self.collector = SourceCollector(
            database=self.db,
            directory=FakeDirectory(),
            config_service=config,
        )
        self.spec = {
            "tableId": "table-1", "tableName": "测试表", "category": "重点项目",
            "titleFields": ["事项"], "statusFields": ["状态"],
            "productManagerFields": ["产品经理"], "projectView": True,
        }
        self.fields = [
            {"fieldId": "f1", "fieldName": "事项"},
            {"fieldId": "f2", "fieldName": "状态"},
            {"fieldId": "f3", "fieldName": "产品经理"},
            {"fieldId": "f4", "fieldName": "项目负责人"},
        ]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def result(self, status: str = "进行中") -> TableResult:
        return TableResult(
            table_id="table-1", fields=self.fields, pages=1,
            records=[{
                "recordId": "record-1",
                "cells": {
                    "f1": "研发项目", "f2": status,
                    "f3": [{"userId": "u-product"}],
                    "f4": [{"userId": "u-project"}],
                },
            }],
        )

    def test_normalizes_names_and_detects_real_changes(self) -> None:
        first = self.collector._store_table(self.spec, self.result(), seen_at="2026-08-13T10:00:00+08:00")
        second = self.collector._store_table(self.spec, self.result(), seen_at="2026-08-13T11:00:00+08:00")
        third = self.collector._store_table(self.spec, self.result("风险"), seen_at="2026-08-13T12:00:00+08:00")
        self.assertEqual(0, first["changed"])
        self.assertEqual(1, first["initialImported"])
        self.assertEqual(0, second["changed"])
        self.assertEqual(1, third["changed"])
        row = self.db.fetch_one("SELECT * FROM source_record WHERE record_id='record-1'")
        self.assertEqual(["产品甲"], json.loads(row["product_manager_names_json"]))
        self.assertEqual(["项目乙"], json.loads(row["project_manager_names_json"]))
        self.assertIn("状态：风险", row["risk_text"])

    def test_initial_import_uses_source_timestamp_instead_of_current_week(self) -> None:
        result = self.result()
        result.records[0]["updatedAt"] = "2026-07-01T09:00:00+08:00"
        stored = self.collector._store_table(
            self.spec, result, seen_at="2026-08-13T10:00:00+08:00"
        )
        row = self.db.fetch_one("SELECT changed_at FROM source_record WHERE record_id='record-1'")
        self.assertEqual(0, stored["changed"])
        self.assertEqual("2026-07-01T09:00:00+08:00", row["changed_at"])

    def test_resolves_aitable_union_id_to_cached_employee_user_id(self) -> None:
        result = self.result()
        result.records[0]["cells"]["f3"] = [
            {"unionId": "union-product", "name": "别名.产品甲"}
        ]
        self.collector._store_table(
            self.spec, result, seen_at="2026-08-13T10:00:00+08:00"
        )
        row = self.db.fetch_one("SELECT * FROM source_record WHERE record_id='record-1'")
        self.assertEqual(["u-product"], json.loads(row["product_manager_user_ids_json"]))
        self.assertEqual(["产品甲"], json.loads(row["product_manager_names_json"]))


if __name__ == "__main__":
    unittest.main()
