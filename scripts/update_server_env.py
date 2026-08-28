from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path


ALLOWED_KEYS = {
    "APP_ENV",
    "APP_PUBLISH_PORT",
    "APP_BASE_PATH",
    "PUBLIC_BASE_URL",
    "ADMIN_API_TOKEN",
    "ADMIN_SESSION_SECRET",
    "ADMIN_SESSION_DAYS",
    "PUBLIC_LINK_SECRET",
    "SCHEDULER_ENABLED",
    "DINGTALK_APP_ID",
    "DINGTALK_AGENT_ID",
    "DINGTALK_APP_KEY",
    "DINGTALK_APP_SECRET",
    "DINGTALK_CALLBACK_TOKEN",
    "DINGTALK_SSO_ENABLED",
    "AITABLE_BASE_ID",
    "DINGTALK_AITABLE_OPERATOR_ID",
    "BI_CENTER_BASE_URL",
    "BI_CENTER_API_TOKEN",
    "TEAMBITION_SYNC_ENABLED",
    "TEAMBITION_SOURCE",
    "TEAMBITION_OPEN_API_BASE",
    "TEAMBITION_OPEN_APP_ID",
    "TEAMBITION_OPEN_APP_SECRET",
    "TEAMBITION_OPEN_ORGANIZATION_ID",
    "TEAMBITION_DINGTALK_APP_KEY",
    "TEAMBITION_DINGTALK_APP_SECRET",
    "TEAMBITION_OPEN_REQUEST_TIMEOUT",
    "TEAMBITION_REQUEST_TIMEOUT",
    "AI_PROVIDER",
    "AI_BASE_URL",
    "AI_MODEL",
    "AI_API_KEY",
}


def update_env(path: Path, values: dict[str, str]) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(values)
    result: list[str] = []
    for line in existing:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else ""
        if key in pending:
            result.append(f"{key}={pending.pop(key)}")
        else:
            result.append(line)
    for key in sorted(pending):
        result.append(f"{key}={pending[key]}")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text("\n".join(result).rstrip() + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description="Update approved weekly assistant runtime settings from stdin JSON.")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--ensure-random",
        choices=("ADMIN_SESSION_SECRET",),
        help="Create the selected secret only when absent; never print its value.",
    )
    args = parser.parse_args()
    env_path = Path(args.env_file)
    if args.ensure_random:
        existing = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        prefix = f"{args.ensure_random}="
        if any(line.startswith(prefix) and line[len(prefix):].strip() for line in existing):
            print(f"{args.ensure_random} is already configured; value was not printed")
            return 0
        update_env(env_path, {args.ensure_random: secrets.token_urlsafe(48)})
        print(f"created {args.ensure_random}; value was not printed")
        return 0
    # Windows PowerShell may prefix piped UTF-8 text with a BOM.
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise SystemExit("stdin JSON must be an object")
    unknown = sorted(set(payload) - ALLOWED_KEYS)
    if unknown:
        raise SystemExit("unsupported environment keys: " + ", ".join(unknown))
    values = {str(key): str(value or "").replace("\r", "").replace("\n", "") for key, value in payload.items()}
    update_env(env_path, values)
    print(f"updated {len(values)} approved runtime settings; values were not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
