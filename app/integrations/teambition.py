from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from typing import Any, Iterable

from ..config import Settings, settings
from .http_json import JsonHttpError, request_json


class TeambitionError(RuntimeError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _base64url(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _chunks(values: Iterable[str], size: int) -> Iterable[list[str]]:
    batch: list[str] = []
    for value in values:
        normalized = _text(value)
        if not normalized:
            continue
        batch.append(normalized)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


class TeambitionClient:
    """Read-only Teambition client compatible with bi_center's two sources."""

    def __init__(self, config: Settings | None = None) -> None:
        self.settings = config or settings
        self._native_token = ""
        self._native_token_app_id = ""
        self._native_token_secret_hash = ""
        self._native_token_expires_at = 0.0
        self._dingtalk_token = ""
        self._dingtalk_token_expires_at = 0.0
        self._lock = threading.Lock()

    @property
    def source(self) -> str:
        value = self.settings.teambition_source.strip().lower() or "native"
        if value not in {"native", "dingtalk"}:
            raise TeambitionError(f"unsupported TEAMBITION_SOURCE: {value}")
        return value

    def configured(self) -> bool:
        return self.settings.teambition_configured

    def _native_access_token(self) -> str:
        app_id = self.settings.teambition_open_app_id.strip()
        app_secret = self.settings.teambition_open_app_secret.strip()
        if not app_id or not app_secret:
            raise TeambitionError("Teambition OpenAPI App ID or App Secret is not configured")
        now = time.time()
        secret_hash = hashlib.sha256(app_secret.encode("utf-8")).hexdigest()
        with self._lock:
            if (
                self._native_token
                and self._native_token_app_id == app_id
                and self._native_token_secret_hash == secret_hash
                and now < self._native_token_expires_at
            ):
                return self._native_token
            issued_at = int(now)
            header = _base64url(
                json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode()
            )
            payload = _base64url(
                json.dumps(
                    {"_appId": app_id, "iat": issued_at, "exp": issued_at + 3600},
                    separators=(",", ":"),
                ).encode()
            )
            signing_input = header + b"." + payload
            signature = _base64url(
                hmac.new(app_secret.encode(), signing_input, hashlib.sha256).digest()
            )
            self._native_token = (signing_input + b"." + signature).decode("ascii")
            self._native_token_app_id = app_id
            self._native_token_secret_hash = secret_hash
            self._native_token_expires_at = issued_at + 3300
            return self._native_token

    def _native_request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        operator_id: str = "",
    ) -> dict[str, Any]:
        organization_id = self.settings.teambition_open_organization_id.strip()
        if not organization_id:
            raise TeambitionError("Teambition OpenAPI Organization ID is not configured")
        headers = {
            "Authorization": f"Bearer {self._native_access_token()}",
            "X-Tenant-Type": "organization",
            "X-Tenant-Id": organization_id,
        }
        if _text(operator_id):
            headers["x-operator-id"] = _text(operator_id)
        try:
            payload = request_json(
                f"{self.settings.teambition_open_api_base.strip().rstrip('/')}{path}",
                headers=headers,
                params=params,
                timeout=self.settings.teambition_request_timeout,
            )
        except JsonHttpError as exc:
            raise TeambitionError(str(exc)) from exc
        if not isinstance(payload, dict):
            raise TeambitionError("invalid Teambition OpenAPI response")
        code = payload.get("code")
        if code not in (None, 0, 200, "0", "200"):
            raise TeambitionError(
                _text(payload.get("errorMessage") or payload.get("message") or payload)
            )
        return payload

    def _dingtalk_access_token(self) -> str:
        app_key = (
            self.settings.teambition_dingtalk_app_key.strip()
            or self.settings.dingtalk_app_key.strip()
        )
        app_secret = (
            self.settings.teambition_dingtalk_app_secret.strip()
            or self.settings.dingtalk_app_secret.strip()
        )
        if not app_key or not app_secret:
            raise TeambitionError("Teambition DingTalk App Key or App Secret is not configured")
        now = time.time()
        with self._lock:
            if self._dingtalk_token and self._dingtalk_token_expires_at - now > 120:
                return self._dingtalk_token
            try:
                payload = request_json(
                    "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                    method="POST",
                    payload={"appKey": app_key, "appSecret": app_secret},
                    timeout=self.settings.teambition_request_timeout,
                )
            except JsonHttpError as exc:
                raise TeambitionError(str(exc)) from exc
            if not isinstance(payload, dict):
                raise TeambitionError("invalid DingTalk access token response")
            token = _text(payload.get("accessToken") or payload.get("access_token"))
            if not token:
                raise TeambitionError(
                    _text(payload.get("message") or payload.get("errmsg"))
                    or "DingTalk response did not contain accessToken"
                )
            expires_in = int(payload.get("expireIn") or payload.get("expires_in") or 7200)
            self._dingtalk_token = token
            self._dingtalk_token_expires_at = now + max(300, expires_in)
            return token

    def _dingtalk_request(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            payload = request_json(
                f"https://api.dingtalk.com{path}",
                method=method,
                headers={"x-acs-dingtalk-access-token": self._dingtalk_access_token()},
                params=params,
                timeout=self.settings.teambition_request_timeout,
            )
        except JsonHttpError as exc:
            raise TeambitionError(str(exc)) from exc
        if not isinstance(payload, dict):
            raise TeambitionError("invalid DingTalk Teambition response")
        code = payload.get("code") or payload.get("errcode")
        if code not in (None, "", 0, "0"):
            raise TeambitionError(_text(payload.get("message") or payload.get("errmsg") or payload))
        return payload

    def map_dingtalk_user_ids(self, user_ids: Iterable[str]) -> dict[str, str]:
        unique = list(dict.fromkeys(_text(value) for value in user_ids if _text(value)))
        if self.source == "dingtalk":
            return {value: value for value in unique}
        mapping: dict[str, str] = {}
        for batch in _chunks(unique, 50):
            payload = self._native_request(
                "/idmap/dingtalk/getTbUserId",
                params={"dingUserIds": ",".join(batch)},
            )
            for item in payload.get("result") or []:
                if not isinstance(item, dict):
                    continue
                ding_id = _text(item.get("dingtalkUserId"))
                tb_id = _text(item.get("tbUserId"))
                if ding_id and tb_id:
                    mapping[ding_id] = tb_id
        return mapping

    def _query_native_tasks(
        self, task_ids: Iterable[str], *, operator_id: str
    ) -> list[dict[str, Any]]:
        unique = list(dict.fromkeys(_text(value) for value in task_ids if _text(value)))
        by_id: dict[str, dict[str, Any]] = {}
        for batch in _chunks(unique, 100):
            payload = self._native_request(
                "/v3/task/query",
                params={"taskId": ",".join(batch)},
                operator_id=operator_id,
            )
            for item in payload.get("result") or []:
                if not isinstance(item, dict):
                    continue
                task_id = _text(item.get("id") or item.get("taskId"))
                if task_id and task_id not in by_id:
                    by_id[task_id] = item
        return [by_id[task_id] for task_id in unique if task_id in by_id]

    def search_executor_tasks(
        self, query_user_id: str, *, max_pages: int = 500
    ) -> list[dict[str, Any]]:
        user_id = _text(query_user_id)
        if not user_id:
            return []
        if self.source == "dingtalk":
            rows: list[dict[str, Any]] = []
            next_token = ""
            for _ in range(max(1, max_pages)):
                payload = self._dingtalk_request(
                    f"/v1.0/project/users/{user_id}/tasks/search",
                    method="POST",
                    params={
                        "roleTypes": "executor",
                        "maxResults": 100,
                        "nextToken": next_token,
                    },
                )
                rows.extend(
                    item for item in (payload.get("result") or []) if isinstance(item, dict)
                )
                next_token = _text(payload.get("nextToken"))
                if not next_token:
                    return rows
            raise TeambitionError("DingTalk Teambition task pagination exceeded limit")

        escaped = user_id.replace("\\", "\\\\").replace("'", "\\'")
        task_ids: list[str] = []
        seen: set[str] = set()
        next_token = ""
        stalled_pages = 0
        for _ in range(max(1, max_pages)):
            payload = self._native_request(
                "/all-task/search",
                params={
                    "tql": f"executorId = '{escaped}'",
                    "pageSize": 1000,
                    "pageToken": next_token,
                },
                operator_id=user_id,
            )
            page_ids = [_text(value) for value in payload.get("result") or [] if _text(value)]
            new_count = 0
            for task_id in page_ids:
                if task_id and task_id not in seen:
                    seen.add(task_id)
                    task_ids.append(task_id)
                    new_count += 1
            stalled_pages = stalled_pages + 1 if page_ids and new_count == 0 else 0
            if stalled_pages >= 3:
                raise TeambitionError("Teambition executor task pagination returned no new task")
            next_token = _text(payload.get("nextPageToken"))
            if not next_token:
                rows = self._query_native_tasks(task_ids, operator_id=user_id)
                return [item for item in rows if _text(item.get("executorId")) == user_id]
        raise TeambitionError("Teambition executor task pagination exceeded limit")

    def query_projects(self, project_ids: Iterable[str]) -> list[dict[str, Any]]:
        if self.source == "dingtalk":
            return []
        rows: list[dict[str, Any]] = []
        unique = list(dict.fromkeys(_text(value) for value in project_ids if _text(value)))
        for batch in _chunks(unique, 100):
            payload = self._native_request(
                "/v3/project/query",
                params={"projectIds": ",".join(batch), "pageSize": len(batch)},
            )
            rows.extend(item for item in (payload.get("result") or []) if isinstance(item, dict))
        return rows

    def search_projects(self, name: str, *, max_pages: int = 20) -> list[dict[str, Any]]:
        """Search projects by name without enumerating unrelated project details."""
        normalized_name = _text(name)
        if not normalized_name or self.source == "dingtalk":
            return []
        rows: list[dict[str, Any]] = []
        next_token = ""
        for _ in range(max(1, max_pages)):
            params: dict[str, Any] = {"name": normalized_name, "pageSize": 100}
            if next_token:
                params["pageToken"] = next_token
            payload = self._native_request("/v3/project/query", params=params)
            rows.extend(item for item in (payload.get("result") or []) if isinstance(item, dict))
            next_token = _text(payload.get("nextPageToken") or payload.get("nextToken"))
            if not next_token:
                return rows
        raise TeambitionError("Teambition project search pagination exceeded limit")

    def query_project_statuses(
        self,
        project_id: str,
        *,
        operator_id: str = "",
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        """Return project-status history, newest first when provided by Teambition."""
        normalized_project_id = _text(project_id)
        if not normalized_project_id or self.source == "dingtalk":
            return []
        rows: list[dict[str, Any]] = []
        next_token = ""
        for _ in range(max(1, max_pages)):
            params: dict[str, Any] = {"pageSize": 100}
            if next_token:
                params["pageToken"] = next_token
            payload = self._native_request(
                f"/v3/project/{normalized_project_id}/status/list",
                params=params,
                operator_id=operator_id,
            )
            rows.extend(item for item in (payload.get("result") or []) if isinstance(item, dict))
            next_token = _text(payload.get("nextPageToken") or payload.get("nextToken"))
            if not next_token:
                return rows
        raise TeambitionError("Teambition project status pagination exceeded limit")

    def query_project_tasks(
        self,
        project_id: str,
        *,
        operator_id: str = "",
        max_pages: int = 500,
    ) -> list[dict[str, Any]]:
        """Read tasks only inside one already-whitelisted project."""
        normalized_project_id = _text(project_id)
        if not normalized_project_id or self.source == "dingtalk":
            return []
        rows: list[dict[str, Any]] = []
        next_token = ""
        for _ in range(max(1, max_pages)):
            # includeArchived defaults to false; omitting it avoids clients that
            # serialize Python booleans as the invalid query value "False".
            params: dict[str, Any] = {"pageSize": 100}
            if next_token:
                params["pageToken"] = next_token
            payload = self._native_request(
                f"/v3/project/{normalized_project_id}/task/query",
                params=params,
                operator_id=operator_id,
            )
            rows.extend(item for item in (payload.get("result") or []) if isinstance(item, dict))
            next_token = _text(payload.get("nextPageToken") or payload.get("nextToken"))
            if not next_token:
                return rows
        raise TeambitionError("Teambition project task pagination exceeded limit")


teambition_client = TeambitionClient()
