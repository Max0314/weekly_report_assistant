from __future__ import annotations

import json
import hmac
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .config import settings
from .db import db
from .services.collector import source_collector
from .services.archive import archive_service
from .services.delivery import delivery_service
from .services.directory import directory_service
from .services.model_config import ModelConfigError, model_config_service
from .services.rendering import report_html, report_renderer
from .services.reports import REPORT_KINDS, report_service
from .services.robot_commands import robot_command_service
from .services.scheduler import scheduler_service
from .services.workflow_config import workflow_config_service
from .time_utils import now_local


router = APIRouter()


class GenerateBody(BaseModel):
    periodKey: str = ""
    reportKind: str = "combined"
    useAI: bool = True


class SectionsBody(BaseModel):
    sections: dict[str, Any]


class ReasonBody(BaseModel):
    reason: str = Field(default="", max_length=2000)


class ConfigBody(BaseModel):
    config: dict[str, Any]


class ModelConfigBody(BaseModel):
    provider: str = ""
    apiBase: str = ""
    modelName: str = ""
    apiKey: str = ""


class CoverageBody(BaseModel):
    periodKey: str = ""
    reportKind: str = "combined"


def _admin_token(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
) -> str:
    expected = settings.admin_api_token.strip()
    if not expected:
        raise HTTPException(status_code=503, detail="ADMIN_API_TOKEN is not configured")
    supplied = str(x_admin_token or "").strip()
    if not supplied and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid admin token")
    return "admin"


def _raise_api_error(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/health")
def health() -> dict[str, Any]:
    try:
        db.fetch_one("SELECT 1 AS ok")
        database = "ok"
    except Exception:
        database = "error"
    return {"status": "ok" if database == "ok" else "error", "service": "weekly-report-assistant", "database": database}


@router.get("/api/readiness")
def readiness(_: str = Depends(_admin_token)) -> dict[str, Any]:
    directory = directory_service.cache_status()
    config = workflow_config_service.get()
    callback_auth = bool(settings.dingtalk_callback_token.strip()) or not settings.production_like
    preview_targets = len(config["previewGroupTargets"]) + len(config["previewPersonalTargets"])
    formal_targets = len(config["formalGroupTargets"]) + len(config["formalPersonalTargets"])
    public_links = bool(settings.public_base_url.strip() and settings.public_link_secret.strip())
    delivery_ready = bool(preview_targets and formal_targets and config["approverTargets"])
    archive_enabled = bool(config.get("archiveWriteEnabled"))
    archive_ready = bool(
        not archive_enabled
        or (
            config.get("archiveTableId")
            and {"archiveKey", "title", "periodKey"}.issubset(config.get("archiveFieldMap") or {})
        )
    )
    source_ready, source_reason = scheduler_service._source_snapshot_ready(
        now_local(), freshness_hours=int(config.get("sourceFreshnessHours") or 26)
    )
    model_test = model_config_service.test_status()
    ai_summary_ready = bool(model_config_service.configured() and model_test.get("ok"))
    return {
        "ready": bool(
            settings.aitable_configured
            and settings.bi_center_configured
            and source_ready
            and callback_auth
            and delivery_ready
            and archive_ready
            and (public_links or not config.get("sendGroupImages"))
        ),
        "checks": {
            "dingtalkApp": settings.dingtalk_configured,
            "aiTable": settings.aitable_configured,
            "biCenter": settings.bi_center_configured,
            "aiSummary": ai_summary_ready,
            "aiSummaryDetail": {
                "configured": model_config_service.configured(),
                **model_test,
            },
            "sourceData": {"ready": source_ready, "reason": source_reason},
            "publicLinks": public_links,
            "callbackAuth": callback_auth,
            "deliveryTargets": {
                "ready": delivery_ready,
                "preview": preview_targets,
                "formal": formal_targets,
                "approvers": len(config["approverTargets"]),
            },
            "archive": {
                "enabled": archive_enabled,
                "ready": archive_ready,
                "tableId": str(config.get("archiveTableId") or ""),
                "mappedFields": len(config.get("archiveFieldMap") or {}),
            },
            "basePath": settings.normalized_base_path,
            "directoryCache": directory,
        },
    }


@router.post("/api/dingtalk/robot/callback")
async def robot_callback(request: Request, background: BackgroundTasks) -> dict[str, bool]:
    expected = settings.dingtalk_callback_token.strip()
    if not expected and settings.production_like:
        raise HTTPException(status_code=503, detail="DINGTALK_CALLBACK_TOKEN is required in production")
    supplied = str(request.headers.get("x-callback-token") or request.query_params.get("token") or "").strip()
    if expected and not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid callback token")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if isinstance(payload, dict):
        event_id = robot_command_service.record(payload)
        background.add_task(robot_command_service.handle, event_id)
    return {"success": True}


@router.get("/api/dingtalk/robot/events")
def robot_events(limit: int = Query(default=20, ge=1, le=100), _: str = Depends(_admin_token)) -> dict[str, Any]:
    return {"items": robot_command_service.recent(limit=limit)}


@router.get("/api/config")
def get_config(_: str = Depends(_admin_token)) -> dict[str, Any]:
    return {"config": workflow_config_service.get()}


@router.put("/api/config")
def update_config(body: ConfigBody, actor: str = Depends(_admin_token)) -> dict[str, Any]:
    return {"config": workflow_config_service.update(body.config, actor=actor)}


@router.get("/api/model-config")
def get_model_config(_: str = Depends(_admin_token)) -> dict[str, Any]:
    return model_config_service.get()


@router.put("/api/model-config")
def update_model_config(body: ModelConfigBody, actor: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        return model_config_service.update(body.model_dump(), actor=actor)
    except ModelConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/model-config")
def reset_model_config(_: str = Depends(_admin_token)) -> dict[str, Any]:
    return model_config_service.reset()


@router.post("/api/model-config/test")
def test_model_config(body: ModelConfigBody, _: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        return model_config_service.test(body.model_dump())
    except ModelConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/sync/source")
def sync_source(actor: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        return source_collector.sync_all(actor=actor)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/api/sync/directory")
def sync_directory(actor: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        return directory_service.refresh(actor=actor)
    except Exception as exc:
        _raise_api_error(exc)


@router.get("/api/directory")
def directory_search(
    query: str = Query(default="", max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    _: str = Depends(_admin_token),
) -> dict[str, Any]:
    return {"items": directory_service.search(query=query, limit=limit)}


@router.post("/api/scheduler/tick")
def scheduler_tick(_: str = Depends(_admin_token)) -> dict[str, Any]:
    return {"jobs": scheduler_service.tick()}


@router.get("/api/sync/runs")
def sync_runs(limit: int = Query(default=20, ge=1, le=100), _: str = Depends(_admin_token)) -> dict[str, Any]:
    rows = db.fetch_all("SELECT * FROM sync_run ORDER BY id DESC LIMIT ?", (limit,))
    for row in rows:
        try:
            row["detail"] = json.loads(str(row.pop("detail_json") or "[]"))
        except (TypeError, ValueError):
            row["detail"] = []
    return {"items": rows}


@router.get("/api/source-records")
def source_records(
    limit: int = Query(default=50, ge=1, le=500),
    table_id: str = Query(default=""),
    _: str = Depends(_admin_token),
) -> dict[str, Any]:
    if table_id:
        rows = db.fetch_all(
            "SELECT * FROM source_record WHERE is_deleted=0 AND table_id=? ORDER BY changed_at DESC LIMIT ?",
            (table_id, limit),
        )
    else:
        rows = db.fetch_all(
            "SELECT * FROM source_record WHERE is_deleted=0 ORDER BY changed_at DESC LIMIT ?", (limit,)
        )
    for row in rows:
        row.pop("raw_json", None)
    return {"items": rows}


@router.get("/api/coverage")
def manager_coverage(
    period_key: str = Query(default=""),
    report_kind: str = Query(default="combined"),
    _: str = Depends(_admin_token),
) -> dict[str, Any]:
    if report_kind not in REPORT_KINDS:
        raise HTTPException(status_code=422, detail="reportKind must be combined, product or project")
    try:
        return report_service.manager_coverage(period_key=period_key, report_kind=report_kind)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/api/coverage/remind")
def remind_missing_managers(body: CoverageBody, _: str = Depends(_admin_token)) -> dict[str, Any]:
    if body.reportKind not in REPORT_KINDS:
        raise HTTPException(status_code=422, detail="reportKind must be combined, product or project")
    try:
        return delivery_service.send_coverage_reminders(
            period_key=body.periodKey, report_kind=body.reportKind
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.get("/api/reports")
def list_reports(limit: int = Query(default=20, ge=1, le=100), _: str = Depends(_admin_token)) -> dict[str, Any]:
    return {"items": report_service.list(limit=limit)}


@router.post("/api/reports/generate")
def generate_report(body: GenerateBody, actor: str = Depends(_admin_token)) -> dict[str, Any]:
    if body.reportKind not in REPORT_KINDS:
        raise HTTPException(status_code=422, detail="reportKind must be combined, product or project")
    try:
        return report_service.generate(
            period_key=body.periodKey, report_kind=body.reportKind, actor=actor, use_ai=body.useAI
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.get("/api/reports/{report_id}")
def get_report(report_id: int, _: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        return report_service.get(report_id, include_sources=True)
    except Exception as exc:
        _raise_api_error(exc)


@router.put("/api/reports/{report_id}/sections")
def update_report(report_id: int, body: SectionsBody, actor: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        return report_service.update_sections(report_id, body.sections, actor=actor)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/api/reports/{report_id}/render")
def render_report(report_id: int, _: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        return report_renderer.render(report_id)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/api/reports/{report_id}/preview")
def preview_report(report_id: int, _: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        report = report_service.get(report_id)
        if not report.get("imageReady"):
            report_renderer.render(report_id)
        return delivery_service.preview(report_id)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/api/reports/{report_id}/approve")
def approve_report(report_id: int, actor: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        return report_service.approve(report_id, actor=actor)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/api/reports/{report_id}/formal-send")
def formal_send(report_id: int, _: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        report = report_service.get(report_id)
        if not report.get("imageReady"):
            report_renderer.render(report_id)
        return delivery_service.formal(report_id)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/api/reports/{report_id}/archive")
def archive_report(report_id: int, _: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        config = workflow_config_service.get()
        report_url = ""
        if config.get("archiveWriteEnabled") and "reportUrl" in (config.get("archiveFieldMap") or {}):
            try:
                report_url = str(report_renderer.public_urls(report_id).get("reportUrl") or "")
            except Exception:
                report_url = ""
        return archive_service.write(report_id, report_url=report_url)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/api/reports/{report_id}/request-changes")
def request_changes(report_id: int, body: ReasonBody, actor: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        return report_service.request_changes(report_id, actor=actor, reason=body.reason)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/api/reports/{report_id}/cancel")
def cancel_report(report_id: int, actor: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        return report_service.cancel(report_id, actor=actor)
    except Exception as exc:
        _raise_api_error(exc)


@router.post("/api/reports/{report_id}/recall")
def recall_report(report_id: int, _: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        return delivery_service.recall(report_id)
    except Exception as exc:
        _raise_api_error(exc)


def _verify_public(report_id: int, expires: int, token: str) -> None:
    if not report_renderer.verify_public_token(report_id, expires, token):
        raise HTTPException(status_code=403, detail="public link is invalid or expired")


@router.get("/public/reports/{report_id}", response_class=HTMLResponse)
def public_report(report_id: int, expires: int, token: str) -> HTMLResponse:
    _verify_public(report_id, expires, token)
    try:
        report = report_service.get(report_id, include_sources=True)
    except Exception as exc:
        _raise_api_error(exc)
    if report["workflowState"] in {"recalled", "cancelled"}:
        raise HTTPException(status_code=410, detail="report is no longer available")
    return HTMLResponse(report_html(report))


@router.get("/api/public/reports/{report_id}/image")
def public_report_image(report_id: int, expires: int, token: str) -> FileResponse:
    _verify_public(report_id, expires, token)
    try:
        report = report_service.get(report_id)
        if report["workflowState"] in {"recalled", "cancelled"}:
            raise HTTPException(status_code=410, detail="report is no longer available")
        path = report_renderer.image_path(report_id)
    except Exception as exc:
        _raise_api_error(exc)
    return FileResponse(path, media_type="image/png", filename=path.name)
