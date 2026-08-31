from __future__ import annotations

import json
from typing import Any

from ..config import Settings, settings
from ..integrations.http_json import JsonHttpError, request_json
from .model_config import ModelConfigService, build_chat_payload, model_config_service


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
    raw_digests = value.get("categoryDigests")
    result["categoryDigests"] = {
        str(key): "\n".join(str(item or "") for item in digest).strip()
        if isinstance(digest, list)
        else str(digest or "").strip()
        for key, digest in (raw_digests.items() if isinstance(raw_digests, dict) else [])
        if str(key).strip() and (isinstance(digest, list) or str(digest or "").strip())
    }
    if not result["executiveSummary"]:
        raise AISummaryError("AI summary is missing executiveSummary")
    return result


class AISummaryClient:
    def __init__(
        self,
        config: Settings | None = None,
        model_service: ModelConfigService | None = None,
    ) -> None:
        self.settings = config or settings
        self.model_service = model_service if model_service is not None else (
            None if config is not None else model_config_service
        )

    def _model_config(self) -> dict[str, str]:
        if self.model_service is not None:
            return self.model_service.effective()
        return {
            "provider": self.settings.ai_provider.strip(),
            "apiBase": self.settings.ai_base_url.strip().rstrip("/"),
            "apiKey": self.settings.ai_api_key.strip(),
            "model": self.settings.ai_model.strip(),
        }

    def summarize(
        self,
        *,
        window: dict[str, Any],
        metrics: dict[str, Any],
        items: list[dict[str, Any]],
        fallback: dict[str, str],
        project_baseline: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        model_config = self._model_config()
        if not all(model_config.get(key) for key in ("apiBase", "apiKey", "model")):
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
                "categoryKey": item.get("categoryKey"),
                "category": item.get("category"),
                "subcategory": item.get("subcategory"),
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
            "task": "基于按多维表业务分类的事实生成团队周报管理摘要，只能归纳，不得新增事实或数字。",
            "window": window,
            "metrics": metrics,
            "facts": facts,
            "projectBackground": [
                {
                    "direction": clipped(item.get("direction")),
                    "name": clipped(item.get("name")),
                    "status": clipped(item.get("status")),
                    "description": clipped(item.get("description")),
                    **(
                        {"owner": clipped(item.get("owner"))}
                        if self.settings.ai_include_person_names else {}
                    ),
                }
                for item in (project_baseline or [])
                if isinstance(item, dict) and item.get("visible") is not False
            ][:100],
            "factSelection": {"total": len(items), "sentToAI": len(facts), "prioritizedRisksFirst": True},
            "output": {
                **{key: "string" for key in SECTION_KEYS},
                "categoryDigests": {
                    "<categoryKey>": "2-5 条换行分隔的管理摘要，不要输出逐条原文"
                },
            },
            "rules": [
                "输出单个 JSON 对象，不使用 Markdown 代码块",
                "产品亮点与项目亮点分开",
                "categoryDigests 必须以 facts 中的 categoryKey 为键，每类只写 2-5 条",
                "合并同客户、同项目或同主题的重复填报，去掉过程性冗语和无关背景",
                "分类摘要优先表达已取得成果、关键变化、下一步和阻塞，每条尽量不超过 80 字",
                "重点项目不逐条罗列正常项目；应归纳阶段成果，再单列风险、延期、暂停和缺少进展的情况",
                "不要在本周进展摘要中重复罗列分类数量，应给出业务成果、关键变化和管理关注点",
                "风险必须保留事项名称、状态或期限依据",
                "没有事实时写暂无，不得猜测",
                "不评价个人绩效，不生成排名",
                "项目背景只用于项目归类和叙述背景，不得据此编造本周进展、风险或计划",
            ],
        }
        url = model_config["apiBase"].rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        try:
            response = request_json(
                url,
                method="POST",
                headers={"Authorization": f"Bearer {model_config['apiKey']}"},
                payload=build_chat_payload(
                    model_config,
                    messages=[
                        {
                            "role": "system",
                            "content": "你是企业周报编辑器。严格依据输入事实，返回约定 JSON。",
                        },
                        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                    ],
                    max_tokens=4200,
                    temperature=0.2,
                ),
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
