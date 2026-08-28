from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from ..config import Settings, settings
from ..integrations.http_json import request_json
from .directory import DirectoryService, directory_service


SESSION_COOKIE = "weekly_report_admin_session"
OAUTH_STATE_COOKIE = "weekly_report_oauth_nonce"
AUTHORIZE_URL = "https://login.dingtalk.com/oauth2/auth"
TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/userAccessToken"
PROFILE_URL = "https://api.dingtalk.com/v1.0/contact/users/me"


class AdminAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class AdminIdentity:
    user_id: str
    name: str

    @property
    def actor(self) -> str:
        return f"dingtalk:{self.user_id}"


class AdminAuthService:
    def __init__(
        self,
        *,
        app_settings: Settings | None = None,
        directory: DirectoryService | None = None,
    ) -> None:
        self.settings = app_settings or settings
        self.directory = directory or directory_service

    @property
    def configured(self) -> bool:
        return self.settings.dingtalk_sso_configured

    @property
    def cookie_path(self) -> str:
        return self.settings.normalized_base_path or "/"

    @property
    def callback_url(self) -> str:
        return f"{self.settings.public_base_url.strip().rstrip('/')}/api/auth/dingtalk/callback"

    @property
    def app_url(self) -> str:
        return f"{self.settings.public_base_url.strip().rstrip('/')}/"

    @staticmethod
    def safe_next(value: str) -> str:
        candidate = str(value or "").strip()
        return candidate if candidate.startswith("#/") else "#/overview"

    def _secret(self) -> bytes:
        secret = self.settings.admin_session_secret.strip()
        if not secret:
            raise AdminAuthError("管理会话尚未配置")
        return secret.encode("utf-8")

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    def _sign(self, payload: dict[str, Any]) -> str:
        body = self._b64encode(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = self._b64encode(hmac.new(self._secret(), body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{signature}"

    def _verify(self, token: str, *, kind: str, now: int | None = None) -> dict[str, Any]:
        try:
            body, supplied_signature = str(token or "").split(".", 1)
            expected_signature = self._b64encode(
                hmac.new(self._secret(), body.encode("ascii"), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise AdminAuthError("登录会话无效")
            payload = json.loads(self._b64decode(body).decode("utf-8"))
        except AdminAuthError:
            raise
        except Exception as exc:
            raise AdminAuthError("登录会话无效") from exc
        current = int(time.time() if now is None else now)
        if not isinstance(payload, dict) or payload.get("kind") != kind:
            raise AdminAuthError("登录会话无效")
        if int(payload.get("exp") or 0) <= current:
            raise AdminAuthError("登录会话已过期")
        return payload

    def begin_login(self, next_path: str = "") -> tuple[str, str, str]:
        if not self.configured:
            raise AdminAuthError("钉钉登录尚未配置")
        nonce = secrets.token_urlsafe(24)
        state = self._sign(
            {
                "kind": "oauth_state",
                "nonce": nonce,
                "next": self.safe_next(next_path),
                "exp": int(time.time()) + 600,
            }
        )
        query = urlencode(
            {
                "client_id": self.settings.dingtalk_app_key.strip(),
                "redirect_uri": self.callback_url,
                "response_type": "code",
                "scope": "openid",
                "prompt": "consent",
                "state": state,
            }
        )
        return f"{AUTHORIZE_URL}?{query}", nonce, state

    def verify_state(self, state: str, nonce: str) -> str:
        payload = self._verify(state, kind="oauth_state")
        if not nonce or not hmac.compare_digest(str(payload.get("nonce") or ""), nonce):
            raise AdminAuthError("登录请求校验失败，请重新发起登录")
        return self.safe_next(str(payload.get("next") or ""))

    @staticmethod
    def _api_result(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise AdminAuthError("钉钉返回了无效响应")
        result = value.get("result")
        return result if isinstance(result, dict) else value

    def _exchange_profile(self, auth_code: str) -> dict[str, Any]:
        token_response = self._api_result(
            request_json(
                TOKEN_URL,
                method="POST",
                payload={
                    "clientId": self.settings.dingtalk_app_key.strip(),
                    "clientSecret": self.settings.dingtalk_app_secret.strip(),
                    "code": auth_code,
                    "grantType": "authorization_code",
                },
                timeout=self.settings.http_timeout_seconds,
            )
        )
        access_token = str(token_response.get("accessToken") or "").strip()
        if not access_token:
            raise AdminAuthError("钉钉授权码兑换失败")
        return self._api_result(
            request_json(
                PROFILE_URL,
                headers={"x-acs-dingtalk-access-token": access_token},
                timeout=self.settings.http_timeout_seconds,
            )
        )

    def _identity_from_profile(self, profile: dict[str, Any]) -> AdminIdentity:
        union_id = str(profile.get("unionId") or "").strip()
        user_id = str(profile.get("userId") or profile.get("userid") or "").strip()
        employee = None
        if union_id:
            employee = self.directory.lookup_by_union_id().get(union_id)
        if employee is None and user_id:
            employee = self.directory.lookup_by_user_id().get(user_id)
        if not employee:
            raise AdminAuthError("当前钉钉账号不在有效人员目录中，请先同步人员目录")
        employee_user_id = str(employee.get("user_id") or "").strip()
        if not employee_user_id:
            raise AdminAuthError("当前钉钉账号缺少有效的人员标识")
        return AdminIdentity(
            user_id=employee_user_id,
            name=str(employee.get("employee_name") or profile.get("nick") or employee_user_id).strip(),
        )

    def complete_login(self, auth_code: str) -> tuple[str, AdminIdentity]:
        code = str(auth_code or "").strip()
        if not code:
            raise AdminAuthError("钉钉未返回授权码")
        identity = self._identity_from_profile(self._exchange_profile(code))
        now = int(time.time())
        token = self._sign(
            {
                "kind": "admin_session",
                "userId": identity.user_id,
                "name": identity.name,
                "iat": now,
                "exp": now + self.settings.admin_session_days * 86400,
            }
        )
        return token, identity

    def authenticate(self, token: str, *, now: int | None = None) -> AdminIdentity:
        payload = self._verify(token, kind="admin_session", now=now)
        user_id = str(payload.get("userId") or "").strip()
        employee = self.directory.lookup_by_user_id().get(user_id)
        if not employee:
            raise AdminAuthError("管理权限已失效")
        return AdminIdentity(
            user_id=user_id,
            name=str(employee.get("employee_name") or payload.get("name") or user_id).strip(),
        )

    def session_status(self, token: str) -> dict[str, Any]:
        if not self.configured:
            return {"authenticated": False, "ssoConfigured": False}
        try:
            identity = self.authenticate(token)
        except AdminAuthError:
            return {"authenticated": False, "ssoConfigured": True}
        return {
            "authenticated": True,
            "ssoConfigured": True,
            "user": {"name": identity.name},
        }


admin_auth_service = AdminAuthService()
