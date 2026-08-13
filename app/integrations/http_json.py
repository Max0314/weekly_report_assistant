from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class JsonHttpError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body[:2000]


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | list[Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any] | list[Any]:
    final_url = url
    if params:
        query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
        if query:
            final_url = f"{url}{'&' if '?' in url else '?'}{query}"
    body = None
    final_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        final_headers.setdefault("Content-Type", "application/json")
    request = Request(final_url, data=body, headers=final_headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    except HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_body = ""
        raise JsonHttpError(
            f"HTTP {exc.code}: {error_body or exc.reason}",
            status=int(exc.code or 0),
            body=error_body,
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise JsonHttpError(f"request failed: {exc}") from exc
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise JsonHttpError(f"invalid JSON response: {raw[:500]}") from exc
    if not isinstance(parsed, (dict, list)):
        raise JsonHttpError("invalid JSON response type")
    return parsed

