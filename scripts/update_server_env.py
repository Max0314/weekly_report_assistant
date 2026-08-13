from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ALLOWED_KEYS = {
    "DINGTALK_APP_SECRET",
    "DINGTALK_AITABLE_OPERATOR_ID",
    "BI_CENTER_BASE_URL",
    "BI_CENTER_API_TOKEN",
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
    args = parser.parse_args()
    # Windows PowerShell may prefix piped UTF-8 text with a BOM.
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise SystemExit("stdin JSON must be an object")
    unknown = sorted(set(payload) - ALLOWED_KEYS)
    if unknown:
        raise SystemExit("unsupported environment keys: " + ", ".join(unknown))
    values = {str(key): str(value or "").replace("\r", "").replace("\n", "") for key, value in payload.items()}
    update_env(Path(args.env_file), values)
    print(f"updated {len(values)} approved runtime settings; values were not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
