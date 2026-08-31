from __future__ import annotations

import json
import hmac
from html import escape
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .config import settings
from .db import db
from .services.collector import source_collector
from .services.archive import archive_service
from .services.admin_auth import (
    AdminAuthError,
    AdminIdentity,
    OAUTH_STATE_COOKIE,
    SESSION_COOKIE,
    admin_auth_service,
)
from .services.delivery import delivery_service
from .services.directory import directory_service
from .services.model_config import ModelConfigError, model_config_service
from .services.rendering import report_html, report_renderer
from .services.reports import REPORT_KINDS, report_service
from .services.robot_commands import robot_command_service
from .services.scheduler import scheduler_service
from .services.teambition import teambition_service
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
    request: Request,
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None),
) -> str:
    session_token = str(request.cookies.get(SESSION_COOKIE) or "").strip()
    if session_token:
        try:
            return admin_auth_service.authenticate(session_token).actor
        except AdminAuthError:
            pass
    expected = settings.admin_api_token.strip()
    if not expected:
        raise HTTPException(status_code=503, detail="ADMIN_API_TOKEN is not configured")
    supplied = str(x_admin_token or "").strip()
    if not supplied and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid admin token")
    return "admin"


def _session_identity(request: Request) -> AdminIdentity:
    if not admin_auth_service.configured:
        raise HTTPException(status_code=503, detail="钉钉登录尚未配置")
    session_token = str(request.cookies.get(SESSION_COOKIE) or "").strip()
    if not session_token:
        raise HTTPException(status_code=401, detail="请先使用钉钉登录")
    try:
        return admin_auth_service.authenticate(session_token)
    except AdminAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _personal_full_scope(identity: AdminIdentity) -> bool:
    return any(
        str(item.get("userId") or "").strip() == identity.user_id
        and item.get("enabled") is not False
        for item in workflow_config_service.get().get("approverTargets") or []
        if isinstance(item, dict)
    )


def _raise_api_error(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _auth_error_page(message: str, *, status_code: int = 400) -> HTMLResponse:
    app_url = admin_auth_service.app_url if settings.public_base_url.strip() else "./"
    safe_message = escape(str(message or "登录失败"))
    safe_url = escape(app_url, quote=True)
    return HTMLResponse(
        f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>登录未完成</title>
<style>body{{font:16px/1.6 system-ui;margin:0;background:#f5f7fb;color:#16365c}}main{{max-width:520px;margin:12vh auto;padding:32px;background:#fff;border-radius:18px;box-shadow:0 18px 50px #17375e18}}a{{display:inline-block;margin-top:12px;padding:10px 18px;border-radius:10px;background:#2563d9;color:#fff;text-decoration:none}}</style></head>
<body><main><h1>登录未完成</h1><p>{safe_message}</p><a href=\"{safe_url}\">返回周报助手</a></main></body></html>""",
        status_code=status_code,
    )


@router.get("/api/auth/session")
def auth_session(request: Request) -> dict[str, Any]:
    return admin_auth_service.session_status(str(request.cookies.get(SESSION_COOKIE) or ""))


@router.get("/api/auth/dingtalk/login")
def dingtalk_login(next_path: str = Query(default="", alias="next")) -> RedirectResponse:
    try:
        authorize_url, nonce, _ = admin_auth_service.begin_login(next_path)
    except AdminAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response = RedirectResponse(authorize_url, status_code=302)
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        nonce,
        max_age=600,
        httponly=True,
        secure=admin_auth_service.app_url.startswith("https://"),
        samesite="lax",
        path=admin_auth_service.cookie_path,
    )
    return response


@router.get("/api/auth/dingtalk/callback", response_model=None)
def dingtalk_callback(request: Request):
    query = request.query_params
    auth_code = str(query.get("authCode") or query.get("code") or "")
    state = str(query.get("state") or "")
    nonce = str(request.cookies.get(OAUTH_STATE_COOKIE) or "")
    try:
        next_path = admin_auth_service.verify_state(state, nonce)
        session_token, _ = admin_auth_service.complete_login(auth_code)
    except AdminAuthError as exc:
        return _auth_error_page(str(exc))
    except Exception:
        return _auth_error_page("钉钉登录暂时不可用，请稍后重试", status_code=502)
    response = RedirectResponse(f"{admin_auth_service.app_url}{next_path}", status_code=302)
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=settings.admin_session_days * 86400,
        httponly=True,
        secure=admin_auth_service.app_url.startswith("https://"),
        samesite="lax",
        path=admin_auth_service.cookie_path,
    )
    response.delete_cookie(OAUTH_STATE_COOKIE, path=admin_auth_service.cookie_path)
    return response


@router.post("/api/auth/logout")
def auth_logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path=admin_auth_service.cookie_path)
    return response


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
    teambition = teambition_service.status()
    teambition_required = bool(config.get("teambitionIncludeInReports"))
    teambition_snapshot_ready, teambition_reason = scheduler_service._teambition_snapshot_ready(
        now_local(), freshness_hours=int(config.get("sourceFreshnessHours") or 26)
    )
    teambition_ready = bool(teambition.get("configured") and teambition_snapshot_ready)
    return {
        "ready": bool(
            settings.aitable_configured
            and settings.bi_center_configured
            and source_ready
            and callback_auth
            and delivery_ready
            and archive_ready
            and (teambition_ready or not teambition_required)
            and (public_links or not config.get("sendGroupImages"))
        ),
        "checks": {
            "dingtalkApp": settings.dingtalk_configured,
            "adminSso": settings.dingtalk_sso_configured,
            "aiTable": settings.aitable_configured,
            "biCenter": settings.bi_center_configured,
            "biCenterDetail": {
                "configured": settings.bi_center_configured,
                "baseUrl": settings.bi_center_base_url.strip(),
                "tokenConfigured": bool(settings.bi_center_api_token.strip()),
                "accessMode": "read_only",
            },
            "teambition": {
                **teambition,
                "required": teambition_required,
                "ready": teambition_ready,
                "reason": teambition_reason,
            },
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
            "scheduler": {
                "processEnabled": settings.scheduler_enabled,
                "workflowEnabled": bool(config.get("enabled")),
                "autoGenerateEnabled": bool(config.get("autoGenerateEnabled")),
                "autoPreviewEnabled": bool(config.get("autoPreviewEnabled")),
                "teambitionSyncEnabled": bool(
                    settings.teambition_sync_enabled and config.get("teambitionSyncEnabled")
                ),
                "formalSendAlwaysManual": True,
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


@router.get("/api/teambition/status")
def teambition_status(_: str = Depends(_admin_token)) -> dict[str, Any]:
    return teambition_service.status()


@router.post("/api/sync/teambition")
def sync_teambition(actor: str = Depends(_admin_token)) -> dict[str, Any]:
    try:
        return teambition_service.sync(actor=actor)
    except Exception as exc:
        _raise_api_error(exc)


@router.get("/api/teambition/dashboard")
def teambition_dashboard(
    month: str = Query(default="", max_length=7),
    query: str = Query(default="", max_length=200),
    department: str = Query(default="", max_length=200),
    status: str = Query(default="all", max_length=30),
    limit: int = Query(default=500, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    _: str = Depends(_admin_token),
) -> dict[str, Any]:
    try:
        return teambition_service.dashboard(
            month=month,
            query=query,
            department=department,
            status=status,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        _raise_api_error(exc)


@router.get("/api/directory")
def directory_search(
    query: str = Query(default="", max_length=100),
    organization_key: str = Query(default="", max_length=300),
    leaders_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    _: str = Depends(_admin_token),
) -> dict[str, Any]:
    return {
        "items": directory_service.search(
            query=query,
            organization_key=organization_key,
            leaders_only=leaders_only,
            limit=limit,
        )
    }


@router.get("/api/directory/organizations")
def organization_search(
    query: str = Query(default="", max_length=100),
    organization_type: str = Query(default="", pattern="^(|department|biz_group)$"),
    limit: int = Query(default=100, ge=1, le=500),
    _: str = Depends(_admin_token),
) -> dict[str, Any]:
    return {
        "items": directory_service.search_organizations(
            query=query, organization_type=organization_type, limit=limit
        )
    }


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


@router.get("/api/personal-reports/context")
def personal_report_context(
    report_id: int = Query(default=0, ge=0),
    identity: AdminIdentity = Depends(_session_identity),
) -> dict[str, Any]:
    reports = report_service.personal_report_options(limit=52)
    selected_report_id = int(report_id or (reports[0]["id"] if reports else 0))
    if report_id and selected_report_id not in {int(item["id"]) for item in reports}:
        raise HTTPException(status_code=404, detail="综合周报不存在")
    full_scope = _personal_full_scope(identity)
    allowed = {
        str(item.get("userId") or ""): item
        for item in directory_service.accessible_people(identity.user_id, full_scope=full_scope)
    }
    report_members = report_service.personal_members(selected_report_id) if selected_report_id else []
    report_members_by_id = {str(item.get("userId") or ""): item for item in report_members}
    visible_ids = set(allowed).intersection(report_members_by_id)
    visible_ids.add(identity.user_id)
    members: list[dict[str, Any]] = []
    for user_id in visible_ids:
        directory_person = allowed.get(user_id, {})
        report_person = report_members_by_id.get(user_id, {})
        members.append(
            {
                "userId": user_id,
                "name": str(directory_person.get("name") or report_person.get("name") or user_id),
                "department": str(directory_person.get("department") or ""),
                "title": str(directory_person.get("title") or ""),
                "itemCount": int(report_person.get("itemCount") or 0),
                "roles": report_person.get("roles") or [],
                "isSelf": user_id == identity.user_id,
            }
        )
    members.sort(key=lambda item: (not item["isSelf"], str(item.get("name") or "")))
    return {
        "viewer": {"userId": identity.user_id, "name": identity.name},
        "selectedReportId": selected_report_id,
        "reports": reports,
        "members": members,
        "canViewMembers": len(members) > 1,
    }


@router.get("/api/personal-reports/{report_id}")
def get_personal_report(
    report_id: int,
    user_id: str = Query(default="", max_length=200),
    identity: AdminIdentity = Depends(_session_identity),
) -> dict[str, Any]:
    target_user_id = str(user_id or identity.user_id).strip()
    full_scope = _personal_full_scope(identity)
    if not directory_service.can_view_person(identity.user_id, target_user_id, full_scope=full_scope):
        raise HTTPException(status_code=403, detail="无权查看该成员的个人周报")
    person = directory_service.lookup_by_user_id().get(target_user_id, {})
    try:
        return report_service.personal(
            report_id,
            user_id=target_user_id,
            name=str(person.get("employee_name") or (identity.name if target_user_id == identity.user_id else "")),
        )
    except Exception as exc:
        _raise_api_error(exc)


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


@router.get("/api/reports/{report_id}/public-urls")
def get_report_public_urls(report_id: int, _: str = Depends(_admin_token)) -> dict[str, str]:
    try:
        report_service.get(report_id)
        return report_renderer.public_urls(report_id)
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
    return HTMLResponse(
        report_html(
            report,
            interactive=True,
            personal_report_url=report_renderer.personal_report_url(report_id),
        )
    )


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
