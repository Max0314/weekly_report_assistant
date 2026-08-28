from __future__ import annotations

import json
import sys
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(opener, url: str) -> dict:
    with opener.open(url, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    base_url = (sys.argv[1] if len(sys.argv) > 1 else "").rstrip("/")
    if not base_url.startswith("https://"):
        raise SystemExit("usage: check_admin_sso.py https://public.example/base-path")
    jar = CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar), NoRedirect())
    health = read_json(opener, f"{base_url}/api/health")
    session = read_json(opener, f"{base_url}/api/auth/session")
    try:
        opener.open(f"{base_url}/api/auth/dingtalk/login?next=%23%2Freports", timeout=15)
        raise RuntimeError("login endpoint did not redirect")
    except HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise
        location = str(exc.headers.get("Location") or "")
        cookie_header = str(exc.headers.get("Set-Cookie") or "")
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    expected_callback = f"{base_url}/api/auth/dingtalk/callback"
    result = {
        "health": health.get("status"),
        "service": health.get("service"),
        "authenticated": bool(session.get("authenticated")),
        "ssoConfigured": bool(session.get("ssoConfigured")),
        "loginStatus": 302,
        "authorizeHost": parsed.hostname,
        "callbackMatches": query.get("redirect_uri", [""])[0] == expected_callback,
        "scope": query.get("scope", [""])[0],
        "stateCookieSecure": all(
            marker.lower() in cookie_header.lower()
            for marker in ("HttpOnly", "Secure", "SameSite=lax")
        ),
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if all(
        (
            result["health"] == "ok",
            result["ssoConfigured"],
            result["authorizeHost"] == "login.dingtalk.com",
            result["callbackMatches"],
            result["scope"] == "openid",
            result["stateCookieSecure"],
        )
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
