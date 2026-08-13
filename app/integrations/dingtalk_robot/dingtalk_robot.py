from __future__ import annotations

import json
import threading
import time
from typing import Any, Iterable

from ...config import Settings, settings
from ..http_json import JsonHttpError, request_json


class DingTalkRobotError(RuntimeError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _response_error(payload: dict[str, Any]) -> str:
    code = payload.get("code") or payload.get("errcode")
    success = payload.get("success")
    if success is False or code not in (None, "", 0, "0"):
        return _text(payload.get("message") or payload.get("errmsg") or payload)
    return ""


class DingTalkRobotClient:
    def __init__(self, config: Settings | None = None) -> None:
        self.settings = config or settings
        self._token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def access_token(self) -> str:
        app_key = self.settings.dingtalk_app_key.strip()
        app_secret = self.settings.dingtalk_app_secret.strip()
        if not app_key or not app_secret:
            raise DingTalkRobotError("DingTalk Client ID or Client Secret is not configured")
        now = time.time()
        with self._lock:
            if self._token and self._expires_at - now > 120:
                return self._token
            try:
                response = request_json(
                    "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                    method="POST",
                    payload={"appKey": app_key, "appSecret": app_secret},
                    timeout=min(self.settings.http_timeout_seconds, 30),
                )
            except JsonHttpError as exc:
                raise DingTalkRobotError(str(exc)) from exc
            if not isinstance(response, dict):
                raise DingTalkRobotError("invalid DingTalk access token response")
            error = _response_error(response)
            token = _text(response.get("accessToken") or response.get("access_token"))
            if error or not token:
                raise DingTalkRobotError(error or "DingTalk response did not contain accessToken")
            expires_in = int(response.get("expireIn") or response.get("expires_in") or 7200)
            self._token = token
            self._expires_at = now + max(300, expires_in)
            return token

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = request_json(
                f"https://api.dingtalk.com{path}",
                method="POST",
                headers={"x-acs-dingtalk-access-token": self.access_token()},
                payload=payload,
                timeout=self.settings.http_timeout_seconds,
            )
        except JsonHttpError as exc:
            raise DingTalkRobotError(str(exc)) from exc
        if not isinstance(response, dict):
            raise DingTalkRobotError("invalid DingTalk robot response")
        return response

    def send_group(
        self,
        *,
        open_conversation_id: str,
        robot_code: str,
        msg_key: str,
        msg_param: dict[str, Any],
    ) -> dict[str, Any]:
        conversation_id = _text(open_conversation_id)
        code = _text(robot_code)
        if not conversation_id or not code:
            return {"sent": False, "error": "robot conversation or robot code missing", "processQueryKey": ""}
        try:
            response = self._post(
                "/v1.0/robot/groupMessages/send",
                {
                    "robotCode": code,
                    "openConversationId": conversation_id,
                    "msgKey": _text(msg_key),
                    "msgParam": json.dumps(msg_param or {}, ensure_ascii=False),
                },
            )
        except DingTalkRobotError as exc:
            return {"sent": False, "error": str(exc), "processQueryKey": ""}
        error = _response_error(response)
        return {
            "sent": not bool(error),
            "error": error,
            "processQueryKey": _text(
                response.get("processQueryKey") or response.get("taskId") or response.get("task_id")
            ),
            "raw": response,
        }

    def send_private(
        self,
        user_ids: Iterable[str] | str,
        *,
        robot_code: str,
        msg_key: str,
        msg_param: dict[str, Any],
    ) -> dict[str, Any]:
        users = [user_ids] if isinstance(user_ids, str) else list(user_ids)
        normalized = list(dict.fromkeys(_text(item) for item in users if _text(item)))
        code = _text(robot_code)
        if not normalized:
            return {"sent": False, "error": "recipient user ID missing", "processQueryKey": ""}
        if not code:
            return {"sent": False, "error": "robot code missing", "processQueryKey": ""}
        try:
            response = self._post(
                "/v1.0/robot/oToMessages/batchSend",
                {
                    "robotCode": code,
                    "userIds": normalized,
                    "msgKey": _text(msg_key),
                    "msgParam": json.dumps(msg_param or {}, ensure_ascii=False),
                },
            )
        except DingTalkRobotError as exc:
            return {"sent": False, "error": str(exc), "processQueryKey": ""}
        error = _response_error(response)
        return {
            "sent": not bool(error),
            "error": error,
            "processQueryKey": _text(
                response.get("processQueryKey") or response.get("taskId") or response.get("task_id")
            ),
            "raw": response,
        }

    def recall_group(
        self,
        *,
        open_conversation_id: str,
        robot_code: str,
        process_query_keys: Iterable[str] | str,
    ) -> dict[str, Any]:
        keys = [process_query_keys] if isinstance(process_query_keys, str) else list(process_query_keys)
        normalized = list(dict.fromkeys(_text(item) for item in keys if _text(item)))
        conversation_id = _text(open_conversation_id)
        code = _text(robot_code)
        if not conversation_id or not code:
            return {"recalled": False, "error": "robot conversation or robot code missing"}
        if not normalized:
            return {"recalled": False, "error": "processQueryKey missing"}
        try:
            response = self._post(
                "/v1.0/robot/groupMessages/recall",
                {
                    "robotCode": code,
                    "openConversationId": conversation_id,
                    "processQueryKeys": normalized,
                },
            )
        except DingTalkRobotError as exc:
            return {"recalled": False, "error": str(exc)}
        error = _response_error(response)
        return {"recalled": not bool(error), "error": error, "raw": response}

    def recall_private(
        self,
        *,
        robot_code: str,
        process_query_keys: Iterable[str] | str,
    ) -> dict[str, Any]:
        keys = [process_query_keys] if isinstance(process_query_keys, str) else list(process_query_keys)
        normalized = list(dict.fromkeys(_text(item) for item in keys if _text(item)))
        code = _text(robot_code)
        if not code:
            return {"recalled": False, "error": "robot code missing"}
        if not normalized:
            return {"recalled": False, "error": "processQueryKey missing"}
        try:
            response = self._post(
                "/v1.0/robot/oToMessages/recall",
                {"robotCode": code, "processQueryKeys": normalized},
            )
        except DingTalkRobotError as exc:
            return {"recalled": False, "error": str(exc)}
        error = _response_error(response)
        return {"recalled": not bool(error), "error": error, "raw": response}


def sender_id_from_payload(payload: dict[str, Any]) -> str:
    for key in ("senderStaffId", "senderId", "senderUserId", "senderUnionId", "userId"):
        value = _text(payload.get(key))
        if value:
            return value
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    for key in ("staffId", "userId", "unionId", "id"):
        value = _text(sender.get(key))
        if value:
            return value
    return ""


def parse_robot_callback_event(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    text = data.get("text") if isinstance(data.get("text"), dict) else {}
    return {
        "openConversationId": _text(data.get("conversationId") or data.get("openConversationId")),
        "conversationTitle": _text(data.get("conversationTitle")),
        "conversationType": _text(data.get("conversationType")),
        "senderNick": _text(data.get("senderNick")),
        "senderId": sender_id_from_payload(data),
        "robotCode": _text(data.get("robotCode")),
        "messageText": _text(text.get("content") or data.get("content")),
        "raw": data,
    }


def markdown_param(title: str, text: str) -> dict[str, str]:
    return {"title": _text(title), "text": str(text or "")}


robot_client = DingTalkRobotClient()
