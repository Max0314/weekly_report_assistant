from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import Settings, settings
from .dingtalk_robot.dingtalk_robot import DingTalkRobotClient, robot_client
from .http_json import JsonHttpError, request_json


class AITableError(RuntimeError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list_from(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    candidates: list[Any] = [payload, payload.get("data"), payload.get("result")]
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
        if not isinstance(candidate, dict):
            continue
        for key in keys:
            items = candidate.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    return []


def _next_token(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for candidate in (payload, payload.get("data"), payload.get("result")):
        if isinstance(candidate, dict):
            value = _text(candidate.get("nextToken") or candidate.get("nextCursor") or candidate.get("cursor"))
            if value:
                return value
    return ""


def _has_more(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    for candidate in (payload, payload.get("data"), payload.get("result")):
        if isinstance(candidate, dict) and "hasMore" in candidate:
            return bool(candidate.get("hasMore"))
    return bool(_next_token(payload))


@dataclass(frozen=True)
class TableResult:
    table_id: str
    fields: list[dict[str, Any]]
    records: list[dict[str, Any]]
    pages: int


class AITableClient:
    def __init__(
        self,
        config: Settings | None = None,
        dingtalk: DingTalkRobotClient | None = None,
    ) -> None:
        self.settings = config or settings
        self.dingtalk = dingtalk or robot_client

    def _headers(self) -> dict[str, str]:
        return {"x-acs-dingtalk-access-token": self.dingtalk.access_token()}

    def _table_url(self, table_id: str) -> tuple[str, str]:
        base_id = self.settings.aitable_base_id.strip()
        table_id = _text(table_id)
        if not base_id or not table_id:
            raise AITableError("AI Table baseId/tableId is not configured")
        operator_id = self.settings.dingtalk_aitable_operator_id.strip()
        if not operator_id:
            raise AITableError("DINGTALK_AITABLE_OPERATOR_ID is required for this Base")
        return f"https://api.dingtalk.com/v1.0/notable/bases/{base_id}/sheets/{table_id}", operator_id

    def fetch_table(self, table_id: str, *, page_limit: int = 100) -> TableResult:
        table_id = _text(table_id)
        base_url, operator_id = self._table_url(table_id)
        try:
            field_payload = request_json(
                f"{base_url}/fields",
                headers=self._headers(),
                params={"operatorId": operator_id},
                timeout=self.settings.http_timeout_seconds,
            )
        except JsonHttpError as exc:
            raise AITableError(f"failed to read fields for table {table_id}: {exc}") from exc

        fields = _list_from(field_payload, ("fields", "items", "list", "value"))
        records: list[dict[str, Any]] = []
        token = ""
        seen_tokens: set[str] = set()
        pages = 0
        while pages < max(1, page_limit):
            body: dict[str, Any] = {"maxResults": 100}
            if token:
                body["nextToken"] = token
            try:
                record_payload = request_json(
                    f"{base_url}/records/list",
                    method="POST",
                    headers=self._headers(),
                    params={"operatorId": operator_id},
                    payload=body,
                    timeout=max(self.settings.http_timeout_seconds, 30),
                )
            except JsonHttpError as exc:
                raise AITableError(f"failed to read records for table {table_id}, page {pages + 1}: {exc}") from exc
            pages += 1
            records.extend(_list_from(record_payload, ("records", "items", "list", "value")))
            if not _has_more(record_payload):
                break
            token = _next_token(record_payload)
            if not token or token in seen_tokens:
                raise AITableError(f"invalid pagination token returned for table {table_id}")
            seen_tokens.add(token)
        else:
            raise AITableError(f"AI Table page limit reached for table {table_id}")
        return TableResult(table_id=table_id, fields=fields, records=records, pages=pages)

    def create_record(self, table_id: str, cells: dict[str, Any]) -> dict[str, Any]:
        """Create one record using field IDs as required by DingTalk Notable."""
        table_id = _text(table_id)
        if not cells or any(not _text(field_id) for field_id in cells):
            raise AITableError("AI Table record cells must use non-empty fieldId keys")
        base_url, operator_id = self._table_url(table_id)
        try:
            payload = request_json(
                f"{base_url}/records",
                method="POST",
                headers=self._headers(),
                params={"operatorId": operator_id},
                payload={"records": [{"cells": cells}]},
                timeout=max(self.settings.http_timeout_seconds, 30),
            )
        except JsonHttpError as exc:
            raise AITableError(f"failed to create record for table {table_id}: {exc}") from exc
        records = _list_from(payload, ("records", "items", "list", "value"))
        record = records[0] if records else payload if isinstance(payload, dict) else {}
        nested = record.get("data") if isinstance(record.get("data"), dict) else {}
        record_id = _text(
            record.get("recordId")
            or record.get("record_id")
            or record.get("id")
            or nested.get("recordId")
            or nested.get("id")
        )
        return {"recordId": record_id, "response": payload}


aitable_client = AITableClient()
