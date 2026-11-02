"""Real model acceptance with synthetic sources and a disposable database.

Run from the deployed app environment. Only the effective model configuration
is read from the live database. No report, target, or job state is written there.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

if "__file__" in globals():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Database
from app.integrations.aitable import TableResult
from app.services.ai_summary import AISummaryClient, BOARD_SECTION_KEYS
from app.services.collector import SourceCollector
from app.services.directory import DirectoryService
from app.services.model_config import ModelConfigService, model_config_service
from app.services.reports import ReportService
from app.services.scheduler import SchedulerService
from app.services.team_editing import ReportGenerationDeferred
from app.services.workflow_config import WorkflowConfigService
from app.source_catalog import SOURCE_TABLES
from app.time_utils import now_local, to_db


def check() -> dict:
    model = model_config_service.effective()
    model_url = model["apiBase"].rstrip("/") + "/chat/completions"
    calls = []

    def only_model(event, args):
        if event == "urllib.Request":
            if args[0] != model_url:
                raise RuntimeError("Acceptance script blocked a non-model HTTP request")
            calls.append(args[0])

    sys.addaudithook(only_model)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="weekly-model-acceptance-") as folder:
        database = Database(Path(folder) / "acceptance.db")
        database.initialize()
        workflow = WorkflowConfigService(database)
        workflow.update({"reportTitle": "[合成验收-不发送] 模型生成链路"}, actor="model-acceptance")
        models = ModelConfigService(database)
        models.update(model, actor="model-acceptance")
        assert models.test(model)["ok"] and models.test_status()["ok"]
        ai = AISummaryClient(model_service=models)
        reports = ReportService(database, workflow, ai)
        collector = SourceCollector(database, directory=DirectoryService(database), config_service=workflow)
        stamp = to_db(now_local())
        specs = {item["key"]: item for item in SOURCE_TABLES}
        roster = []
        tenders = []
        for index, department in enumerate(("中国区经营中心", "海外经营中心")):
            person = {"userId": f"synthetic-acceptance-{index}", "name": f"合成验收人员{index}"}
            roster.append({"recordId": f"acceptance-roster-{index}", "fields": {"姓名": [person], "所属部门": department}})
            tenders.append({
                "recordId": f"acceptance-tender-{index}", "updatedAt": stamp,
                "fields": {"招标名称": f"合成验收标案{index}", "状态": "进行中", "产品经理": [person],
                           "本周进展": "已完成技术评审，待补充交付日期。", "下周计划": "确认交付日期后提交方案。"},
            })
        for key, records in (("product_manager_roster", roster), ("tenders", tenders)):
            spec = specs[key]
            collector._store_table(spec, TableResult(spec["tableId"], [], records, 1), seen_at=stamp)
        window, items = reports.source_items()
        assert len(items) == 2 and {item["businessBoard"] for item in items} == {"domestic", "overseas_iot"}

        # Exercise the edit lease -> durable queue -> background generation path.
        initial = reports.generate(period_key=window["periodKey"], use_ai=False, actor="model-acceptance")
        lease = reports.team_editing.acquire(initial["id"], actor="model-acceptance", owner_name="合成验收")
        try:
            reports.generate(period_key=window["periodKey"], use_ai=True, actor="model-acceptance")
        except ReportGenerationDeferred:
            pass
        else:
            raise AssertionError("Generation did not defer while the editor held the lease")
        released = reports.team_editing.release(initial["id"], actor="model-acceptance", lease_token=lease["leaseToken"])
        assert released["generationQueued"]
        scheduler = SchedulerService(database, workflow, reports=reports)
        results = scheduler.process_pending_generations()
        assert len(results) == 1 and results[0]["status"] == "success", "Background generation failed"
        report_id = results[0]["reportId"]
        saved = reports.get(report_id, include_sources=True)
        # Fail on deterministic fallback; HTTP 200 alone is insufficient.
        assert saved["aiStatus"] == "success", "AI fallback: " + saved["aiError"].replace(model["apiKey"], "[redacted]")
        assert len(calls) == 3, "Connection test and both non-empty boards must call the real model"
        assert saved["workflowState"] == "draft_generated" and saved["sourceSnapshot"]
        assert len(saved["sources"]) == 2 and saved["metrics"]["itemCount"] == 2
        assert saved["contentHash"] == reports._content_hash(report_id)
        boards = saved["sections"]["boardSections"]
        for board in boards.values():
            assert set(BOARD_SECTION_KEYS) <= board["sections"].keys()
            assert all(isinstance(board["sections"][key], str) and board["sections"][key].strip() for key in BOARD_SECTION_KEYS)
            assert "技术评审" in "\n".join(board["sections"].values()), "Generated text lost the input fact"
        # A new service instance must read the same saved result and snapshot.
        reopened = ReportService(Database(database.path), workflow, ai).get(report_id, include_sources=True)
        assert reopened == saved
        queue = database.fetch_one("SELECT status, generated_report_id FROM weekly_report_generation_queue")
        assert queue["status"] == "success" and queue["generated_report_id"] == report_id
        assert scheduler.process_pending_generations() == [], "Completed work was replayed"
        assert database.fetch_one("SELECT COUNT(*) AS count FROM dingtalk_robot_send_log")["count"] == 0
        result = {"ok": True, "model": model["model"], "sources": len(items), "boards": len(boards),
                  "aiStatus": saved["aiStatus"], "workflowState": saved["workflowState"], "modelRequests": len(calls),
                  "queueStatus": queue["status"], "persistedReadback": True, "contentHashValid": True,
                  "syntheticBoardSections": {key: board["sections"] for key, board in boards.items()},
                  "elapsedSeconds": round(time.monotonic() - started, 2), "notificationsSent": 0}
    result["temporaryDatabaseRemoved"] = not Path(folder).exists()
    return result


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False))
