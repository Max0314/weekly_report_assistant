from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path


def _secret() -> str:
    return secrets.token_urlsafe(48)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a permission-restricted production .env file")
    parser.add_argument("--output", default=".env")
    parser.add_argument("--publish-port", default="39022")
    parser.add_argument("--public-base-url", required=True)
    parser.add_argument("--app-base-path", default="/weekly-assistant")
    parser.add_argument("--dingtalk-app-id", default="")
    parser.add_argument("--dingtalk-agent-id", default="")
    parser.add_argument("--dingtalk-app-key", default="")
    parser.add_argument("--aitable-base-id", default="")
    args = parser.parse_args()

    output = Path(args.output).resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing {output}")

    values = {
        "COMPOSE_PROJECT_NAME": "weekly_report_assistant",
        "APP_ENV": "production",
        "APP_HOST": "0.0.0.0",
        "APP_PORT": "39057",
        "APP_PUBLISH_PORT": args.publish_port,
        "APP_BASE_PATH": args.app_base_path,
        "PUBLIC_BASE_URL": args.public_base_url.rstrip("/"),
        "ADMIN_API_TOKEN": _secret(),
        "PUBLIC_LINK_SECRET": _secret(),
        "PUBLIC_LINK_LIFETIME_DAYS": "30",
        "DINGTALK_APP_ID": args.dingtalk_app_id,
        "DINGTALK_AGENT_ID": args.dingtalk_agent_id,
        "DINGTALK_APP_KEY": args.dingtalk_app_key,
        "DINGTALK_APP_SECRET": "",
        "DINGTALK_AITABLE_OPERATOR_ID": "",
        "DINGTALK_CALLBACK_TOKEN": _secret(),
        "AITABLE_BASE_ID": args.aitable_base_id,
        "BI_CENTER_BASE_URL": "",
        "BI_CENTER_API_TOKEN": "",
        "AI_BASE_URL": "",
        "AI_API_KEY": "",
        "AI_MODEL": "",
        "AI_INCLUDE_PERSON_NAMES": "false",
        "AI_MAX_ITEMS": "120",
        "AI_MAX_TEXT_CHARS": "600",
        "DATABASE_PATH": "runtime/weekly_report_assistant.db",
        "ARTIFACT_DIR": "runtime/reports",
        # Keep automatic jobs disabled until all upstream credentials have
        # passed readiness and manual sync verification.
        "SCHEDULER_ENABLED": "false",
        "SCHEDULER_POLL_SECONDS": "30",
        "HTTP_TIMEOUT_SECONDS": "30",
    }
    content = "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    output.write_text(content, encoding="utf-8", newline="\n")
    os.chmod(output, 0o600)
    print(f"created {output} with mode 600")
    print(
        "missing runtime values: DINGTALK_APP_SECRET, DINGTALK_AITABLE_OPERATOR_ID, "
        "BI_CENTER_BASE_URL, BI_CENTER_API_TOKEN"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
