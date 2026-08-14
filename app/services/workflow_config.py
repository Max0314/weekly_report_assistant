from __future__ import annotations

from typing import Any

from ..db import Database, db
from ..source_catalog import DEFAULT_WORKFLOW_CONFIG


CONFIG_KEY = "weekly_workflow"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(low, min(high, result))


def _groups(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        conversation_id = _text(item.get("openConversationId"))
        robot_code = _text(item.get("robotCode"))
        if not conversation_id or not robot_code:
            continue
        key = (conversation_id, robot_code)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "name": _text(item.get("name")) or "钉钉群",
                "openConversationId": conversation_id,
                "robotCode": robot_code,
                "enabled": item.get("enabled") is not False,
            }
        )
    return result


def _people(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        user_id = _text(item.get("userId") or item.get("senderId"))
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        result.append(
            {
                "name": _text(item.get("name")) or user_id,
                "userId": user_id,
                "robotCode": _text(item.get("robotCode")),
                "enabled": item.get("enabled") is not False,
            }
        )
    return result


def _keywords(value: Any) -> list[str]:
    candidates = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in candidates:
        keyword = _text(item)
        if keyword and keyword not in result:
            result.append(keyword)
    return result


def _projects(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    allowed_statuses = {"active", "waiting", "paused", "done"}
    for index, item in enumerate(value[:100]):
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))[:120]
        normalized_name = name.casefold()
        if not name or normalized_name in seen:
            continue
        seen.add(normalized_name)
        status = _text(item.get("status"))
        result.append(
            {
                "seq": _int(item.get("seq"), index + 1, 1, 9999),
                "direction": _text(item.get("direction"))[:80],
                "name": name,
                "owner": _text(item.get("owner"))[:80],
                "status": status if status in allowed_statuses else "active",
                "description": _text(item.get("description") or item.get("note"))[:1000],
                "visible": item.get("visible") is not False,
            }
        )
    return sorted(result, key=lambda item: (int(item["seq"]), str(item["name"])))


def normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    config = {**DEFAULT_WORKFLOW_CONFIG, **(raw or {})}
    for key in (
        "enabled",
        "sourceSyncEnabled",
        "directorySyncEnabled",
        "autoGenerateEnabled",
        "autoPreviewEnabled",
        "requireApproval",
        "requirePreviewBeforeFormal",
        "enforceDirectoryForFormalSend",
        "sendGroupImages",
        "archiveWriteEnabled",
    ):
        config[key] = bool(config.get(key))
    # Formal delivery is deliberately confirmation-driven in v1. It remains a
    # visible config field so future migrations do not need a schema change.
    config["autoFormalSendEnabled"] = False
    config["sourceSyncIntervalMinutes"] = _int(config.get("sourceSyncIntervalMinutes"), 60, 5, 1440)
    config["sourceFreshnessHours"] = _int(config.get("sourceFreshnessHours"), 26, 1, 168)
    config["generateWeekday"] = _int(config.get("generateWeekday"), 4, 0, 6)
    config["generateHour"] = _int(config.get("generateHour"), 18, 0, 23)
    config["generateMinute"] = _int(config.get("generateMinute"), 10, 0, 59)
    config["periodEndWeekday"] = _int(config.get("periodEndWeekday"), 4, 0, 6)
    config["periodEndHour"] = _int(config.get("periodEndHour"), 18, 0, 23)
    config["quietStartHour"] = _int(config.get("quietStartHour"), 21, 0, 23)
    config["quietEndHour"] = _int(config.get("quietEndHour"), 8, 0, 23)
    config["dueSoonDays"] = _int(config.get("dueSoonDays"), 14, 1, 90)
    config["reportTitle"] = _text(config.get("reportTitle")) or "产品与项目管理周报"
    config["defaultRobotCode"] = _text(config.get("defaultRobotCode"))
    config["previewGroupTargets"] = _groups(config.get("previewGroupTargets"))
    config["formalGroupTargets"] = _groups(config.get("formalGroupTargets"))
    config["previewPersonalTargets"] = _people(config.get("previewPersonalTargets"))
    config["formalPersonalTargets"] = _people(config.get("formalPersonalTargets"))
    config["approverTargets"] = _people(config.get("approverTargets"))
    config["projectManagerRoster"] = _people(config.get("projectManagerRoster"))
    config["projectManagerTitleKeywords"] = _keywords(config.get("projectManagerTitleKeywords"))
    config["projectBaseline"] = _projects(config.get("projectBaseline"))
    config["approvalCommandScope"] = (
        "formal_only" if config.get("approvalCommandScope") == "formal_only" else "any_configured_group"
    )
    config["confirmSendTarget"] = "formal"
    overrides = config.get("projectManagerFieldOverrides")
    config["projectManagerFieldOverrides"] = overrides if isinstance(overrides, dict) else {}
    config["archiveTableId"] = _text(config.get("archiveTableId"))
    archive_fields = config.get("archiveFieldMap")
    config["archiveFieldMap"] = {
        _text(key): _text(value)
        for key, value in (archive_fields.items() if isinstance(archive_fields, dict) else [])
        if _text(key) and _text(value)
    }
    return config


class WorkflowConfigService:
    def __init__(self, database: Database | None = None) -> None:
        self.db = database or db

    def get(self) -> dict[str, Any]:
        return normalize_config(self.db.load_config(CONFIG_KEY, DEFAULT_WORKFLOW_CONFIG))

    def update(self, patch: dict[str, Any], *, actor: str = "admin") -> dict[str, Any]:
        current = self.get()
        allowed = set(DEFAULT_WORKFLOW_CONFIG)
        merged = {**current, **{key: value for key, value in patch.items() if key in allowed}}
        normalized = normalize_config(merged)
        preview_ids = {
            item["openConversationId"] for item in normalized["previewGroupTargets"]
            if item.get("enabled") is not False
        }
        formal_ids = {
            item["openConversationId"] for item in normalized["formalGroupTargets"]
            if item.get("enabled") is not False
        }
        if preview_ids & formal_ids:
            raise ValueError("preview groups and formal groups must use different openConversationId values")
        for key in ("previewPersonalTargets", "formalPersonalTargets"):
            for target in normalized[key]:
                if target.get("enabled") is not False and not (
                    target.get("robotCode") or normalized.get("defaultRobotCode")
                ):
                    raise ValueError(f"{key} requires defaultRobotCode or a target robotCode")
        if normalized.get("archiveWriteEnabled"):
            if not normalized.get("archiveTableId"):
                raise ValueError("archiveTableId is required when archiveWriteEnabled is true")
            required_archive_fields = {"archiveKey", "title", "periodKey"}
            missing = sorted(required_archive_fields - set(normalized.get("archiveFieldMap") or {}))
            if missing:
                raise ValueError(
                    "archiveFieldMap is missing required semantic keys: " + ", ".join(missing)
                )
        self.db.save_config(CONFIG_KEY, normalized, actor=actor)
        return normalized


workflow_config_service = WorkflowConfigService()
