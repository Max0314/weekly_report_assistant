from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import Settings, settings
from .http_json import JsonHttpError, request_json


class BiCenterError(RuntimeError):
    pass


@dataclass(frozen=True)
class DirectorySnapshot:
    directory_version: str
    policy_version: str
    items: list[dict[str, Any]]


class BiCenterClient:
    def __init__(self, config: Settings | None = None) -> None:
        self.settings = config or settings

    def _url(self, path: str) -> str:
        base = self.settings.bi_center_base_url.strip().rstrip("/")
        if not base:
            raise BiCenterError("BI_CENTER_BASE_URL is not configured")
        return f"{base}{path}"

    def _headers(self) -> dict[str, str]:
        token = self.settings.bi_center_api_token.strip()
        if not token:
            raise BiCenterError("BI_CENTER_API_TOKEN is not configured")
        return {"Authorization": f"Bearer {token}"}

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            payload = request_json(
                self._url(path),
                headers=self._headers(),
                params=params,
                timeout=self.settings.http_timeout_seconds,
            )
        except JsonHttpError as exc:
            raise BiCenterError(str(exc)) from exc
        if not isinstance(payload, dict):
            raise BiCenterError("invalid bi_center response")
        if payload.get("code") not in (None, 0, "0"):
            raise BiCenterError(str(payload.get("message") or payload))
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise BiCenterError("invalid bi_center data response")
        return data

    def status(self) -> dict[str, Any]:
        return self._get("/api/internal/v1/employee-master-data/status")

    def current_directory(self) -> DirectorySnapshot:
        items: list[dict[str, Any]] = []
        offset = 0
        directory_version = ""
        policy_version = ""
        while True:
            page = self._get(
                "/api/internal/v1/employee-directory/current",
                params={"limit": 500, "offset": offset, "includeInactive": "false"},
            )
            batch = page.get("items") if isinstance(page.get("items"), list) else []
            items.extend(item for item in batch if isinstance(item, dict))
            directory_version = str(page.get("directoryVersion") or directory_version)
            policy_version = str(page.get("policyVersion") or policy_version)
            total = int(page.get("total") or len(items))
            offset += len(batch)
            if not batch or offset >= total:
                break
        return DirectorySnapshot(
            directory_version=directory_version,
            policy_version=policy_version,
            items=items,
        )

    def current_leaders(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._get(
                "/api/internal/v1/employee-directory/leaders/current",
                params={"limit": 500, "offset": offset, "includeInactive": "false"},
            )
            batch = page.get("items") if isinstance(page.get("items"), list) else []
            items.extend(item for item in batch if isinstance(item, dict))
            total = int(page.get("total") or len(items))
            offset += len(batch)
            if not batch or offset >= total:
                break
        return items


bi_center_client = BiCenterClient()

