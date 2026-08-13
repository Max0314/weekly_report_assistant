from __future__ import annotations

import json
from typing import Any

from ..config import Settings, settings
from ..integrations.http_json import JsonHttpError, request_json


SECTION_KEYS = (
    "executiveSummary",
    "productHighlights",
    "projectHighlights",
    "risks",
    "nextPlans",
    "supportNeeds",
)


class AISummaryError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any]:
    normalized = text.strip()
    if normalized.startswith("```"):
        normalized = normalized.strip("`").removeprefix("json").strip()
    start = normalized.find("{")
    end = normalized.rfind("}")
    if start < 0 or end <= start:
        raise AISummaryError("AI response did not contain a JSON object")
    try:
        value = json.loads(normalized[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AISummaryError(f"invalid AI JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AISummaryError("AI JSON must be an object")
    result = {key: str(value.get(key) or "").strip() for key in SECTION_KEYS}
    if not result["executiveSummary"]:
        raise AISummaryError("AI summary is missing executiveSummary")
    return result


class AISummaryClient:
    def __init__(self, config: Settings | None = None) -> None:
        self.settings = config or settings

    def summarize(
        self,
        *,
        window: dict[str, Any],
        metrics: dict[str, Any],
        items: list[dict[str, Any]],
        fallback: dict[str, str],
    ) -> dict[str, str]:
        if not self.settings.ai_configured:
            raise AISummaryError("AI model is not configured")
        limit = self.settings.ai_max_items
        text_limit = self.settings.ai_max_text_chars
        prioritized = sorted(
            items,
            key=lambda item: (
                not bool(item.get("riskText") or item.get("overdue")),
                not (item.get("priority") in {"高", "紧急"}),
                str(item.get("dueAt") or "9999"),
                int(item.get("id") or 0),
            ),
        )[:limit]

        def clipped(value: Any) -> str:
            return str(value or "").strip()[:text_limit]

        facts = [
            {
                "id": item.get("id"),
                "category": item.get("category"),
                "title": clipped(item.get("title")),
                "status": clipped(item.get("status")),
                "priority": clipped(item.get("priority")),
                "progress": clipped(item.get("progressText")),
                "plan": clipped(item.get("planText")),
                "risk": clipped(item.get("riskText")),
                "dueAt": item.get("dueAt"),
                **(
                    {
                        "productManagers": item.get("productManagerNames"),
                        "projectManagers": item.get("projectManagerNames"),
                    }
                    if self.settings.ai_include_person_names else {}
                ),
            }
            for item in prioritized
        ]
        prompt = {
            "task": "基于事实生成产品经理、项目经理可用的团队周报，只能归纳，不得新增事实或数字。",
            "window": window,
            "metrics": metrics,
            "facts": facts,
            "factSelection": {"total": len(items), "sentToAI": len(facts), "prioritizedRisksFirst": True},
            "fallbackDraft": fallback,
            "output": {key: "string" for key in SECTION_KEYS},
            "rules": [
                "输出单个 JSON 对象，不使用 Markdown 代码块",
                "产品亮点与项目亮点分开",
                "风险必须保留事项名称、状态或期限依据",
                "没有事实时写暂无，不得猜测",
                "不评价个人绩效，不生成排名",
            ],
        }
        url = self.settings.ai_base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        try:
            response = request_json(
                url,
                method="POST",
                headers={"Authorization": f"Bearer {self.settings.ai_api_key}"},
                payload={
                    "model": self.settings.ai_model,
                    "temperature": 0.2,
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是企业周报编辑器。严格依据输入事实，返回约定 JSON。",
                        },
                        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                    ],
                },
                timeout=max(self.settings.http_timeout_seconds, 60),
            )
        except JsonHttpError as exc:
            raise AISummaryError(str(exc)) from exc
        if not isinstance(response, dict):
            raise AISummaryError("invalid AI response")
        choices = response.get("choices") if isinstance(response.get("choices"), list) else []
        message = choices[0].get("message") if choices and isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else ""
        if isinstance(content, list):
            content = "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
        return _extract_json(str(content or ""))


ai_summary_client = AISummaryClient()
