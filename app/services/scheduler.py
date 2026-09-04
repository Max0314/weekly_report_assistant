from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any, Callable

from ..config import settings
from ..db import Database, db
from ..source_catalog import TEAMBITION_TABLE_ID
from ..time_utils import SHANGHAI, from_db, now_local, to_db
from .collector import SourceCollector, source_collector
from .delivery import DeliveryService, delivery_service
from .directory import DirectoryService, directory_service
from .rendering import ReportRenderer, report_renderer
from .reports import ReportService, report_service
from .teambition import TeambitionService, teambition_service
from .workflow_config import WorkflowConfigService, workflow_config_service


class SchedulerService:
    def __init__(
        self,
        database: Database | None = None,
        config_service: WorkflowConfigService | None = None,
        collector: SourceCollector | None = None,
        directory: DirectoryService | None = None,
        reports: ReportService | None = None,
        renderer: ReportRenderer | None = None,
        delivery: DeliveryService | None = None,
        teambition: TeambitionService | None = None,
    ) -> None:
        self.db = database or db
        self.config_service = config_service or workflow_config_service
        self.collector = collector or source_collector
        self.directory = directory or directory_service
        self.reports = reports or report_service
        self.renderer = renderer or report_renderer
        self.delivery = delivery or delivery_service
        self.teambition = teambition or teambition_service
        self._lock = threading.Lock()

    def _should_run(self, job_key: str, period_key: str, now: datetime) -> bool:
        row = self.db.fetch_one(
            "SELECT status, next_retry_at FROM job_status WHERE job_key=? AND period_key=?", (job_key, period_key)
        )
        if not row:
            return True
        if row.get("status") in {"success", "skipped"}:
            return False
        next_retry = from_db(row.get("next_retry_at"))
        return not next_retry or now >= next_retry

    def _run(self, job_key: str, period_key: str, callback: Callable[[], Any]) -> dict[str, Any]:
        timestamp = to_db(now_local())
        try:
            result = callback()
        except Exception as exc:
            previous = self.db.fetch_one(
                "SELECT retry_count FROM job_status WHERE job_key=? AND period_key=?", (job_key, period_key)
            ) or {}
            retry_count = int(previous.get("retry_count") or 0) + 1
            retry_minutes = min(60, 5 * (2 ** min(3, retry_count - 1)))
            next_retry = to_db(now_local().replace(microsecond=0) + timedelta(minutes=retry_minutes))
            self.db.execute(
                """
                INSERT INTO job_status(job_key, period_key, status, retry_count, next_retry_at, error_text, ran_at, updated_at)
                VALUES (?, ?, 'error', ?, ?, ?, ?, ?)
                ON CONFLICT(job_key, period_key) DO UPDATE SET status='error',
                    retry_count=excluded.retry_count, next_retry_at=excluded.next_retry_at, error_text=excluded.error_text,
                    ran_at=excluded.ran_at, updated_at=excluded.updated_at
                """,
                (job_key, period_key, retry_count, next_retry, str(exc)[:2000], timestamp, timestamp),
            )
            return {"job": job_key, "status": "error", "error": str(exc)}
        self.db.execute(
            """
            INSERT INTO job_status(job_key, period_key, status, retry_count, next_retry_at, error_text, ran_at, updated_at)
            VALUES (?, ?, 'success', 0, '', '', ?, ?)
            ON CONFLICT(job_key, period_key) DO UPDATE SET status='success', retry_count=0,
                next_retry_at='', error_text='', ran_at=excluded.ran_at, updated_at=excluded.updated_at
            """,
            (job_key, period_key, timestamp, timestamp),
        )
        return {"job": job_key, "status": "success", "result": result}

    def _skip(self, job_key: str, period_key: str, reason: str) -> dict[str, Any]:
        """Persist a non-retryable safety decision for weekend delivery."""
        timestamp = to_db(now_local())
        self.db.execute(
            """
            INSERT INTO job_status(job_key,period_key,status,retry_count,next_retry_at,error_text,ran_at,updated_at)
            VALUES (?,?,'skipped',0,'',?,?,?)
            ON CONFLICT(job_key,period_key) DO UPDATE SET status='skipped',retry_count=0,
                next_retry_at='',error_text=excluded.error_text,ran_at=excluded.ran_at,
                updated_at=excluded.updated_at
            """,
            (job_key, period_key, str(reason or "skipped")[:2000], timestamp, timestamp),
        )
        return {"job": job_key, "status": "skipped", "reason": str(reason or "skipped")}

    @staticmethod
    def _weekend_periods_due(
        now: datetime, *, weekday: int, hour: int, minute: int = 0
    ) -> list[str]:
        """Return current/recoverable-weekend periods in Asia/Shanghai.

        The bounded recovery window allows a process restart to make up a
        missed Saturday/Sunday execution without replaying an arbitrarily old
        report after an outage.  Job rows provide the durable idempotency key.
        """
        local = now.astimezone(SHANGHAI) if now.tzinfo else now.replace(tzinfo=SHANGHAI)
        monday = (local - timedelta(days=local.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        result: list[str] = []
        for candidate in (monday - timedelta(days=7), monday):
            scheduled_at = candidate + timedelta(days=weekday, hours=hour, minutes=minute)
            if scheduled_at <= local <= scheduled_at + timedelta(hours=36):
                result.append(f"week:{candidate.strftime('%Y%m%d')}")
        return result

    @staticmethod
    def _delivery_succeeded(result: dict[str, Any]) -> bool:
        return bool(result.get("sent") or result.get("skipped")) and not int(result.get("failed") or 0)

    def _source_snapshot_ready(self, now: datetime, *, freshness_hours: int) -> tuple[bool, str]:
        latest = self.db.fetch_one(
            "SELECT status, finished_at, error_text FROM sync_run ORDER BY id DESC LIMIT 1"
        )
        if not latest:
            return False, "AI Table has not completed a successful sync"
        if latest.get("status") != "success":
            return False, str(latest.get("error_text") or "latest AI Table sync failed")
        finished_at = from_db(latest.get("finished_at"))
        if not finished_at:
            return False, "latest AI Table sync has no completion timestamp"
        if now - finished_at > timedelta(hours=max(1, int(freshness_hours))):
            return False, "latest AI Table snapshot is stale"
        source_count = int(
            (
                self.db.fetch_one(
                    "SELECT COUNT(*) AS count FROM source_record WHERE is_deleted=0 AND table_id<>?",
                    (TEAMBITION_TABLE_ID,),
                )
                or {}
            ).get("count")
            or 0
        )
        if source_count <= 0:
            return False, "latest AI Table snapshot is empty"
        return True, ""

    def _teambition_snapshot_ready(
        self, now: datetime, *, freshness_hours: int
    ) -> tuple[bool, str]:
        latest = self.db.fetch_one(
            """
            SELECT status,finished_at,project_count,error_text
            FROM teambition_sync_run ORDER BY id DESC LIMIT 1
            """
        )
        if not latest:
            return False, "Teambition has not completed a usable sync"
        if latest.get("status") not in {"success", "partial"}:
            return False, str(latest.get("error_text") or "latest Teambition sync failed")
        finished_at = from_db(latest.get("finished_at"))
        if not finished_at:
            return False, "latest Teambition sync has no completion timestamp"
        if now - finished_at > timedelta(hours=max(1, int(freshness_hours))):
            return False, "latest Teambition snapshot is stale"
        if int(latest.get("project_count") or 0) <= 0:
            return False, "latest Teambition snapshot has no matched key projects"
        return True, ""

    def tick(self, reference: datetime | None = None) -> list[dict[str, Any]]:
        if not self._lock.acquire(blocking=False):
            return []
        try:
            now = reference or now_local()
            config = self.config_service.get()
            if not config.get("enabled"):
                return []
            results: list[dict[str, Any]] = []
            interval = int(config["sourceSyncIntervalMinutes"])
            minute_index = (now.hour * 60 + now.minute) // interval * interval
            source_bucket = f"{now.date().isoformat()}:{minute_index:04d}"
            teambition_interval = int(config["teambitionSyncIntervalMinutes"])
            teambition_minute_index = (
                (now.hour * 60 + now.minute) // teambition_interval * teambition_interval
            )
            teambition_bucket = f"{now.date().isoformat()}:{teambition_minute_index:04d}"
            directory_bucket = f"{now.date().isoformat()}:{now.hour // 6}"
            if config.get("directorySyncEnabled") and settings.bi_center_configured and self._should_run("directory_sync", directory_bucket, now):
                results.append(self._run("directory_sync", directory_bucket, lambda: self.directory.refresh(actor="scheduler")))
            if config.get("sourceSyncEnabled") and settings.aitable_configured and self._should_run("source_sync", source_bucket, now):
                results.append(self._run("source_sync", source_bucket, lambda: self.collector.sync_all(actor="scheduler")))
            if (
                config.get("teambitionSyncEnabled")
                and settings.teambition_sync_enabled
                and settings.teambition_configured
                and self._should_run("teambition_sync", teambition_bucket, now)
            ):
                results.append(
                    self._run(
                        "teambition_sync",
                        teambition_bucket,
                        lambda: self.teambition.sync(actor="scheduler"),
                    )
                )
            source_ready, source_error = self._source_snapshot_ready(
                now, freshness_hours=int(config["sourceFreshnessHours"])
            )
            teambition_required = bool(config.get("teambitionIncludeInReports"))
            teambition_ready, teambition_error = self._teambition_snapshot_ready(
                now, freshness_hours=int(config["sourceFreshnessHours"])
            )
            data_ready = bool(source_ready and (teambition_ready or not teambition_required))
            data_error = source_error if not source_ready else teambition_error
            # Fixed Shanghai weekend cadence.  The generic configurable
            # auto-preview path is intentionally not used for these jobs: the
            # Saturday 09:00 delivery is *only* the configured test group;
            # Sunday formal delivery can occur only after a hash-bound human
            # approval of the current combined version.
            for period_key in self._weekend_periods_due(now, weekday=5, hour=9):
                if not self._should_run("weekend_sat09_test", period_key, now):
                    continue
                if not data_ready:
                    results.append(
                        self._run(
                            "weekend_sat09_test", period_key,
                            lambda error=data_error: (_ for _ in ()).throw(RuntimeError(error)),
                        )
                    )
                    continue

                def saturday_morning() -> Any:
                    prior = self.db.fetch_one(
                        "SELECT status FROM job_status WHERE job_key=? AND period_key=?",
                        ("weekend_sat09_test", period_key),
                    )
                    report = self.reports.latest(period_key=period_key, report_kind="combined")
                    # First execution always regenerates. A later retry uses
                    # the newest saved revision, so an edit made after a failed
                    # attempt is never replaced by stale generated content.
                    if not prior or not report:
                        report = self.reports.generate(
                            period_key=period_key, report_kind="combined", actor="scheduler"
                        )
                    if not report.get("imageReady"):
                        self.renderer.render(int(report["id"]))
                    outcome = self.delivery.test_push(
                        int(report["id"]), release_key=f"{period_key}-sat09"
                    )
                    if not self._delivery_succeeded(outcome):
                        raise RuntimeError("Saturday test-group delivery failed")
                    return outcome

                results.append(self._run("weekend_sat09_test", period_key, saturday_morning))

            for period_key in self._weekend_periods_due(now, weekday=5, hour=17):
                if not self._should_run("weekend_sat17_final", period_key, now):
                    continue

                def saturday_final() -> Any:
                    report = self.reports.latest(period_key=period_key, report_kind="combined")
                    if not report:
                        raise RuntimeError("latest combined report is unavailable for Saturday final delivery")
                    if not report.get("imageReady"):
                        self.renderer.render(int(report["id"]))
                    outcome = self.delivery.saturday_final(
                        int(report["id"]), schedule_key=f"{period_key}-sat17"
                    )
                    if not self._delivery_succeeded(outcome):
                        raise RuntimeError("Saturday private final delivery failed")
                    return outcome

                results.append(self._run("weekend_sat17_final", period_key, saturday_final))

            for period_key in self._weekend_periods_due(now, weekday=6, hour=20):
                if not self._should_run("weekend_sun20_formal", period_key, now):
                    continue
                report = self.reports.latest(period_key=period_key, report_kind="combined")
                if not report:
                    results.append(self._skip("weekend_sun20_formal", period_key, "latest combined report is unavailable"))
                    continue
                current, reason = self.reports.formal_version_is_current(int(report["id"]))
                if (
                    report.get("workflowState") != "approved"
                    or report.get("confirmStatus") != "confirmed"
                    or not current
                ):
                    results.append(
                        self._skip(
                            "weekend_sun20_formal", period_key,
                            reason or "current report has not been human-approved",
                        )
                    )
                    continue

                def sunday_formal() -> Any:
                    current_report = self.reports.latest(period_key=period_key, report_kind="combined")
                    if not current_report or int(current_report["id"]) != int(report["id"]):
                        raise RuntimeError("latest combined report changed before formal delivery")
                    if not current_report.get("imageReady"):
                        self.renderer.render(int(current_report["id"]))
                    outcome = self.delivery.formal(int(current_report["id"]))
                    if not self._delivery_succeeded(outcome):
                        raise RuntimeError("Sunday formal delivery failed")
                    return outcome

                results.append(self._run("weekend_sun20_formal", period_key, sunday_formal))
            return results
        finally:
            self._lock.release()


scheduler_service = SchedulerService()
