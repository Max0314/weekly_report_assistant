from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import router
from app.config import Settings, settings
from app.db import Database
from app.services.delivery import DeliveryService
from app.services.scheduler import SchedulerService
from app.time_utils import SHANGHAI, to_db


class SchedulerSecurityAndWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_source_snapshot_requires_latest_success_and_fresh_data(self) -> None:
        scheduler = SchedulerService(database=self.db)
        now = datetime(2026, 8, 13, 12, 0, tzinfo=SHANGHAI)
        ready, reason = scheduler._source_snapshot_ready(now, freshness_hours=26)
        self.assertFalse(ready)
        self.assertIn("has not completed", reason)

        self.db.execute(
            """
            INSERT INTO sync_run(run_type,status,started_at,finished_at)
            VALUES ('scheduler','success',?,?)
            """,
            (to_db(now), to_db(now)),
        )
        self.db.execute(
            """
            INSERT INTO source_record(
              base_id,table_id,table_name,record_id,first_seen_at,last_seen_at,
              changed_at,record_hash,raw_json
            ) VALUES ('b','t','table','r',?,?,?,?, '{}')
            """,
            (to_db(now), to_db(now), to_db(now), "hash"),
        )
        ready, reason = scheduler._source_snapshot_ready(now, freshness_hours=26)
        self.assertTrue(ready, reason)

        self.db.execute(
            """
            INSERT INTO sync_run(run_type,status,started_at,finished_at,error_text)
            VALUES ('scheduler','error',?,?, 'upstream failed')
            """,
            (to_db(now), to_db(now)),
        )
        ready, reason = scheduler._source_snapshot_ready(now, freshness_hours=26)
        self.assertFalse(ready)
        self.assertEqual("upstream failed", reason)

    def test_send_claim_is_atomic_and_blocks_an_inflight_duplicate(self) -> None:
        delivery = DeliveryService(database=self.db)
        self.assertEqual("claimed", delivery._claim_send("same-key"))
        self.assertEqual("pending", delivery._claim_send("same-key"))

    def test_additive_migration_upgrades_an_existing_weekly_report_table(self) -> None:
        path = Path(self.temp_dir.name) / "old.db"
        connection = sqlite3.connect(path)
        connection.execute(
            """
            CREATE TABLE weekly_report (
              id INTEGER PRIMARY KEY, period_key TEXT, report_kind TEXT, version INTEGER,
              workflow_state TEXT, updated_at TEXT
            )
            """
        )
        connection.commit()
        connection.close()
        database = Database(path)
        database.initialize()
        with database.connect() as upgraded:
            columns = {str(row["name"]) for row in upgraded.execute("PRAGMA table_info(weekly_report)")}
        self.assertIn("source_snapshot_json", columns)
        self.assertIn("coverage_json", columns)
        self.assertIn("archive_status", columns)
        self.assertIn("archive_record_id", columns)
        self.assertIn("archive_payload_json", columns)

    def test_additive_migration_upgrades_an_existing_employee_cache(self) -> None:
        path = Path(self.temp_dir.name) / "old-directory.db"
        connection = sqlite3.connect(path)
        connection.execute(
            """
            CREATE TABLE employee_cache (
              employee_key TEXT PRIMARY KEY, corp_id TEXT, user_id TEXT, union_id TEXT,
              employee_name TEXT, title TEXT, department_name TEXT, biz_group_name TEXT,
              is_active INTEGER, directory_version TEXT, refreshed_at TEXT
            )
            """
        )
        connection.commit()
        connection.close()
        database = Database(path)
        database.initialize()
        with database.connect() as upgraded:
            columns = {
                str(row["name"])
                for row in upgraded.execute("PRAGMA table_info(employee_cache)")
            }
            organization_table = upgraded.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='organization_cache'"
            ).fetchone()
        self.assertIn("primary_dept_id", columns)
        self.assertIn("leader_roles_json", columns)
        self.assertIsNotNone(organization_table)

    def test_production_callback_fails_closed_without_a_token(self) -> None:
        test_app = FastAPI()
        test_app.include_router(router)
        with patch.object(settings, "app_env", "production"), patch.object(
            settings, "dingtalk_callback_token", ""
        ):
            response = TestClient(test_app).post("/api/dingtalk/robot/callback", json={})
        self.assertEqual(503, response.status_code)

    def test_base_path_is_normalized_and_frontend_uses_relative_assets(self) -> None:
        self.assertEqual("/weekly-assistant", Settings(app_base_path="weekly-assistant/").normalized_base_path)
        root = Path(__file__).resolve().parents[1]
        html = (root / "static" / "index.html").read_text(encoding="utf-8")
        script = (root / "static" / "app.js").read_text(encoding="utf-8")
        nginx = (root / "deploy" / "nginx-weekly-assistant.conf").read_text(encoding="utf-8")
        self.assertNotIn('href="/static/', html)
        self.assertNotIn('src="/static/', html)
        self.assertIn("resolveUrl(path)", script)
        self.assertIn('id="modelProvider"', html)
        self.assertIn('data-route="reports"', html)
        self.assertIn('data-route="report-config"', html)
        self.assertIn('data-route="model-config"', html)
        self.assertIn('data-route="delivery"', html)
        self.assertIn('data-page="reports"', html)
        self.assertIn('id="projectRows"', html)
        self.assertIn('id="previewGroupTargets"', html)
        self.assertIn('id="section-executiveSummary"', html)
        self.assertIn("setRoute(routeFromHash())", script)
        self.assertIn('api("/api/config"', script)
        self.assertIn('api("/api/model-config"', script)
        self.assertIn('api("/api/model-config/test"', script)
        self.assertIn("styles.css?v=20260814c", html)
        self.assertIn("app.js?v=20260814c", html)
        self.assertIn("proxy_pass http://127.0.0.1:39022;", nginx)
        self.assertNotIn("proxy_pass http://127.0.0.1:39022/;", nginx)
        self.assertIn(
            "proxy_pass http://127.0.0.1:39022/weekly-assistant/static/;", nginx
        )


if __name__ == "__main__":
    unittest.main()
