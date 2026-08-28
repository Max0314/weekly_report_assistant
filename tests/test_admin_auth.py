from __future__ import annotations

import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app.config import Settings
from app.services.admin_auth import AdminAuthError, AdminAuthService


class FakeDirectory:
    def __init__(self) -> None:
        self.active = True

    def lookup_by_union_id(self):
        return {
            "union-reviewer": {
                "user_id": "user-reviewer",
                "employee_name": "审核人",
            }
        } if self.active else {}

    def lookup_by_user_id(self):
        return {
            "user-reviewer": {
                "user_id": "user-reviewer",
                "employee_name": "审核人",
            }
        } if self.active else {}


class AdminAuthTests(unittest.TestCase):
    def service(self, *, directory: FakeDirectory | None = None) -> AdminAuthService:
        app_settings = Settings(
            _env_file=None,
            app_env="production",
            app_base_path="/weekly-assistant",
            public_base_url="https://example.test/weekly-assistant",
            dingtalk_sso_enabled=True,
            dingtalk_app_key="ding-client-id",
            dingtalk_app_secret="client-secret",
            admin_session_secret="session-secret-that-is-not-the-client-secret",
            admin_session_days=30,
        )
        return AdminAuthService(
            app_settings=app_settings,
            directory=directory or FakeDirectory(),
        )

    def test_login_url_uses_published_callback_without_exposing_secret(self) -> None:
        service = self.service()
        authorize_url, nonce, state = service.begin_login("#/reports")
        parsed = urlparse(authorize_url)
        query = parse_qs(parsed.query)
        self.assertEqual("login.dingtalk.com", parsed.netloc)
        self.assertEqual(["ding-client-id"], query["client_id"])
        self.assertEqual(
            ["https://example.test/weekly-assistant/api/auth/dingtalk/callback"],
            query["redirect_uri"],
        )
        self.assertEqual(["openid"], query["scope"])
        self.assertNotIn("client-secret", authorize_url)
        self.assertEqual("#/reports", service.verify_state(state, nonce))
        with self.assertRaises(AdminAuthError):
            service.verify_state(state, "another-browser")

    def test_exchange_profile_issues_long_session_for_active_employee(self) -> None:
        service = self.service()
        with patch(
            "app.services.admin_auth.request_json",
            side_effect=[
                {"accessToken": "short-lived-user-token"},
                {"unionId": "union-reviewer", "nick": "审核人"},
            ],
        ) as request:
            token, identity = service.complete_login("one-time-auth-code")
        self.assertEqual("user-reviewer", identity.user_id)
        self.assertEqual("审核人", service.authenticate(token).name)
        self.assertEqual("POST", request.call_args_list[0].kwargs["method"])
        self.assertEqual(
            "short-lived-user-token",
            request.call_args_list[1].kwargs["headers"]["x-acs-dingtalk-access-token"],
        )
        with self.assertRaises(AdminAuthError):
            service.authenticate(token[:-1] + ("A" if token[-1] != "A" else "B"))
        with self.assertRaises(AdminAuthError):
            service.authenticate(token, now=int(time.time()) + 31 * 86400)

    def test_active_employee_does_not_need_to_be_an_approver(self) -> None:
        service = self.service()
        with patch(
            "app.services.admin_auth.request_json",
            side_effect=[
                {"accessToken": "short-lived-user-token"},
                {"unionId": "union-reviewer"},
            ],
        ):
            token, identity = service.complete_login("one-time-auth-code")
        self.assertEqual("user-reviewer", identity.user_id)
        self.assertEqual("审核人", service.authenticate(token).name)

    def test_session_is_revoked_when_user_leaves_active_directory(self) -> None:
        directory = FakeDirectory()
        service = self.service(directory=directory)
        with patch(
            "app.services.admin_auth.request_json",
            side_effect=[
                {"accessToken": "short-lived-user-token"},
                {"unionId": "union-reviewer"},
            ],
        ):
            token, _ = service.complete_login("one-time-auth-code")
        directory.active = False
        with self.assertRaisesRegex(AdminAuthError, "权限已失效"):
            service.authenticate(token)

    def test_unknown_directory_user_cannot_receive_admin_session(self) -> None:
        directory = FakeDirectory()
        directory.active = False
        service = self.service(directory=directory)
        with patch(
            "app.services.admin_auth.request_json",
            side_effect=[
                {"accessToken": "short-lived-user-token"},
                {"unionId": "union-reviewer"},
            ],
        ):
            with self.assertRaisesRegex(AdminAuthError, "不在有效人员目录"):
                service.complete_login("one-time-auth-code")


if __name__ == "__main__":
    unittest.main()
