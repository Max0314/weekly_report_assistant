from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.integrations.aitable import TableResult
from app.services.archive import ArchiveService
from app.services.reports import ReportService
from app.services.workflow_config import WorkflowConfigService


class FakeAI:
    def summarize(self, **kwargs):
        raise AssertionError("AI must not be called")


class FakeAITable:
    def __init__(self, records=None):
        self.fetch_calls = 0
        self.create_calls = []
        self.result = TableResult(
            table_id="archive-table",
            fields=[
                {"fieldId": "fldKey", "fieldName": "归档键", "type": "text"},
                {"fieldId": "fldTitle", "fieldName": "周报标题", "type": "text"},
                {"fieldId": "fldPeriod", "fieldName": "周期", "type": "text"},
                {"fieldId": "fldSummary", "fieldName": "摘要", "type": "richText"},
                {"fieldId": "fldUrl", "fieldName": "周报链接", "type": "url"},
            ],
            records=list(records or []),
            pages=1,
        )

    def fetch_table(self, table_id: str, *, page_limit: int = 100):
        self.fetch_calls += 1
        return self.result

    def create_record(self, table_id: str, cells):
        self.create_calls.append((table_id, cells))
        return {"recordId": "rec-created"}


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        self.db.initialize()
        self.config = WorkflowConfigService(self.db)
        self.reports = ReportService(self.db, self.config, FakeAI())
        self.config.update(
            {
                "archiveWriteEnabled": True,
                "archiveTableId": "archive-table",
                "archiveFieldMap": {
                    "archiveKey": "归档键",
                    "title": "周报标题",
                    "periodKey": "周期",
                    "summary": "摘要",
                    "reportUrl": "周报链接",
                },
            }
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _formal_report(self):
        report = self.reports.generate(period_key="week:20260810", use_ai=False)
        self.db.execute(
            """
            UPDATE weekly_report SET workflow_state='formal_sent', send_status='sent',
                sent_at='2026-08-14T18:00:00+08:00' WHERE id=?
            """,
            (report["id"],),
        )
        return self.reports.get(report["id"])

    def test_archive_resolves_names_to_field_ids_and_is_locally_idempotent(self) -> None:
        report = self._formal_report()
        client = FakeAITable()
        service = ArchiveService(self.db, self.reports, self.config, client)
        first = service.write(report["id"], report_url="https://example.test/report")
        second = service.write(report["id"], report_url="https://example.test/report")

        self.assertEqual("sent", first["status"])
        self.assertTrue(second["skipped"])
        self.assertEqual(1, client.fetch_calls)
        self.assertEqual(1, len(client.create_calls))
        cells = client.create_calls[0][1]
        self.assertEqual("week:20260810", cells["fldPeriod"])
        self.assertIn("markdown", cells["fldSummary"])
        self.assertEqual("https://example.test/report", cells["fldUrl"]["link"])
        stored = self.reports.get(report["id"])
        self.assertEqual("rec-created", stored["archive"]["recordId"])

    def test_archive_recovers_an_existing_record_by_archive_key(self) -> None:
        report = self._formal_report()
        archive_key = "weekly-report:week:20260810:combined:v1"
        client = FakeAITable(records=[{"recordId": "rec-existing", "cells": {"fldKey": archive_key}}])
        service = ArchiveService(self.db, self.reports, self.config, client)
        result = service.write(report["id"])
        self.assertTrue(result["recovered"])
        self.assertEqual("rec-existing", result["recordId"])
        self.assertEqual([], client.create_calls)


if __name__ == "__main__":
    unittest.main()
