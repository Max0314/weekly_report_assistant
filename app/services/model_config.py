from __future__ import annotations

import time
from typing import Any

from ..config import Settings, settings
from ..db import Database, db
from ..integrations.http_json import JsonHttpError, request_json


CONFIG_KEY = "ai_model"
PROVIDERS = [
    {
        "value": "openai",
        "label": "OpenAI",
        "description": "OpenAI 官方 API，适合 GPT-5 与 o 系列模型。",
        "defaultApiBase": "https://api.openai.com/v1",
        "defaultModel": "gpt-5.4-mini",
        "defaultModels": ["gpt-5.4-mini", "gpt-5.4", "gpt-5-mini"],
    },
    {
        "value": "openrouter",
        "label": "OpenRouter",
        "description": "统一接入多家模型供应商，模型名称需包含厂商前缀。",
        "defaultApiBase": "https://openrouter.ai/api/v1",
        "defaultModel": "openai/gpt-5.4-mini",
        "defaultModels": ["openai/gpt-5.4-mini", "z-ai/glm-5.2", "openai/gpt-5-mini"],
    },
    {
        "value": "qwen",
        "label": "DashScope / Qwen",
        "description": "阿里云百炼 OpenAI 兼容接口，自动关闭 Qwen thinking。",
        "defaultApiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "defaultModel": "qwen3.6-plus-2026-04-02",
        "defaultModels": ["qwen3.6-plus-2026-04-02", "qwen3.6-plus", "qwen3-coder-plus"],
    },
    {
        "value": "compatible",
        "label": "其他兼容代理",
        "description": "支持 OpenAI Chat Completions 的自建网关或其他代理商。",
        "defaultApiBase": "",
        "defaultModel": "",
        "defaultModels": [],
    },
]


class ModelConfigError(RuntimeError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_api_base(value: Any) -> str:
    normalized = _text(value).rstrip("/")
    if normalized.endswith("/chat/completions"):
        normalized = normalized[: -len("/chat/completions")].rstrip("/")
    return normalized


def _model_identity(model_name: Any) -> str:
    return _text(model_name).lower().rsplit("/", 1)[-1]


def _infer_provider(provider: Any, api_base: Any, model_name: Any) -> str:
    explicit = _text(provider).lower()
    if explicit in {str(item["value"]) for item in PROVIDERS}:
        return explicit
    normalized_base = _text(api_base).lower()
    identity = _model_identity(model_name)
    if "openrouter.ai" in normalized_base:
        return "openrouter"
    if "dashscope" in normalized_base or identity.startswith("qwen"):
        return "qwen"
    if "api.openai.com" in normalized_base:
        return "openai"
    return "compatible"


def _mask_api_key(value: Any) -> str:
    secret = _text(value)
    if not secret:
        return ""
    if len(secret) <= 8:
        return f"{secret[:2]}***{secret[-2:]}"
    return f"{secret[:4]}***{secret[-4:]}"


def build_chat_payload(
    config: dict[str, Any],
    *,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float = 0.2,
) -> dict[str, Any]:
    model_name = _text(config.get("model"))
    identity = _model_identity(model_name)
    token_limit = max(1, int(max_tokens or 1))
    payload: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    if "/" not in model_name and identity.startswith(("gpt-5", "o1", "o3", "o4")):
        payload["max_completion_tokens"] = token_limit
    else:
        payload["max_tokens"] = token_limit
    if identity.startswith("qwen"):
        payload["enable_thinking"] = False
    if not identity.startswith(("gpt-5", "o1", "o3", "o4")):
        payload["temperature"] = temperature
    provider = _text(config.get("provider")).lower()
    normalized_model = model_name.lower()
    if provider == "openrouter" and normalized_model == "z-ai/glm-5.2":
        payload["reasoning"] = {"enabled": False, "exclude": True}
    elif provider == "openrouter" and identity.startswith("gpt-5.4"):
        payload["reasoning"] = {"effort": "none", "exclude": True}
    return payload


class ModelConfigService:
    def __init__(
        self,
        database: Database | None = None,
        config: Settings | None = None,
    ) -> None:
        self.db = database or db
        self.settings = config or settings

    def inherited(self) -> dict[str, str]:
        api_base = _normalize_api_base(self.settings.ai_base_url)
        model_name = _text(self.settings.ai_model)
        return {
            "provider": _infer_provider(self.settings.ai_provider, api_base, model_name),
            "apiBase": api_base,
            "model": model_name,
            "apiKey": _text(self.settings.ai_api_key),
        }

    def override(self) -> dict[str, str]:
        row = self.db.fetch_one("SELECT config_json FROM app_config WHERE config_key=?", (CONFIG_KEY,))
        if not row:
            return {}
        payload = self.db.load_config(CONFIG_KEY, {})
        api_base = _normalize_api_base(payload.get("apiBase"))
        model_name = _text(payload.get("model") or payload.get("modelName"))
        api_key = _text(payload.get("apiKey"))
        if not (api_base and model_name and api_key):
            return {}
        return {
            "provider": _infer_provider(payload.get("provider"), api_base, model_name),
            "apiBase": api_base,
            "model": model_name,
            "apiKey": api_key,
        }

    def effective(self) -> dict[str, str]:
        return self.override() or self.inherited()

    def configured(self) -> bool:
        config = self.effective()
        return bool(config.get("apiBase") and config.get("model") and config.get("apiKey"))

    @staticmethod
    def _public(config: dict[str, Any], *, source: str) -> dict[str, Any]:
        return {
            "source": source,
            "provider": _text(config.get("provider")) or "compatible",
            "apiBase": _normalize_api_base(config.get("apiBase")),
            "modelName": _text(config.get("model") or config.get("modelName")),
            "hasApiKey": bool(_text(config.get("apiKey"))),
            "apiKeyMasked": _mask_api_key(config.get("apiKey")),
        }

    def get(self) -> dict[str, Any]:
        inherited = self.inherited()
        override = self.override()
        effective = override or inherited
        return {
            "providers": PROVIDERS,
            "effective": self._public(
                effective,
                source="weekly_assistant" if override else "bi_center_deployment",
            ),
            "inherited": self._public(inherited, source="bi_center_deployment"),
            "override": self._public(override, source="weekly_assistant") if override else None,
            "compatibility": {
                "qwen": "Qwen 使用 max_tokens，并自动关闭 enable_thinking。",
                "openrouter": "OpenRouter GPT-5.4/GLM 结构化任务关闭额外 reasoning。",
                "secret": "API Key 只保存在服务端，读取接口始终脱敏。",
            },
        }

    def resolve(self, payload: dict[str, Any]) -> dict[str, str]:
        api_base = _normalize_api_base(payload.get("apiBase") or payload.get("api_base"))
        model_name = _text(payload.get("modelName") or payload.get("model"))
        if not api_base:
            raise ModelConfigError("API Base URL 不能为空")
        if not model_name:
            raise ModelConfigError("模型名称不能为空")
        api_key = _text(payload.get("apiKey") or payload.get("api_key"))
        if not api_key:
            api_key = _text(self.effective().get("apiKey"))
        if not api_key:
            raise ModelConfigError("API Key 不能为空")
        return {
            "provider": _infer_provider(payload.get("provider"), api_base, model_name),
            "apiBase": api_base,
            "model": model_name,
            "apiKey": api_key,
        }

    def update(self, payload: dict[str, Any], *, actor: str = "admin") -> dict[str, Any]:
        resolved = self.resolve(payload)
        self.db.save_config(CONFIG_KEY, resolved, actor=actor)
        return self.get()

    def reset(self) -> dict[str, Any]:
        self.db.execute("DELETE FROM app_config WHERE config_key=?", (CONFIG_KEY,))
        return self.get()

    def test(self, payload: dict[str, Any]) -> dict[str, Any]:
        config = self.resolve(payload)
        url = f"{config['apiBase']}/chat/completions"
        request_payload = build_chat_payload(
            config,
            messages=[
                {"role": "system", "content": "你是模型连通性测试助手，只返回 JSON。"},
                {"role": "user", "content": '返回 {"status":"ok"}。'},
            ],
            max_tokens=80,
            temperature=0,
        )
        started = time.monotonic()
        try:
            response = request_json(
                url,
                method="POST",
                headers={"Authorization": f"Bearer {config['apiKey']}"},
                payload=request_payload,
                timeout=max(self.settings.http_timeout_seconds, 60),
            )
        except JsonHttpError as exc:
            raise ModelConfigError(str(exc)) from exc
        if not isinstance(response, dict) or not isinstance(response.get("choices"), list):
            raise ModelConfigError("模型返回格式不正确")
        return {
            "ok": True,
            "provider": config["provider"],
            "modelName": config["model"],
            "latencyMs": round((time.monotonic() - started) * 1000),
            "message": "模型连接正常",
        }


model_config_service = ModelConfigService()
