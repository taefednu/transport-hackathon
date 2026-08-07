"""Клиент к YandexGPT (Yandex AI Studio, Foundation Models v1).

Контракт модуля: **никогда не бросает исключение и никогда не висит дольше
таймаута**. Любая беда — нет ключа, нет сети, нет квоты, мусор в ответе —
возвращается как `Answer(text=None)`, а вызывающая сторона обязана иметь
детерминированный путь. Инструмент показывают вживую, связь в зале ненадёжна.

Формат запроса сверен с официальными proto `yandex-cloud/cloudapi`
(`yandex/cloud/ai/foundation_models/v1/`): POST на `/foundationModels/v1/completion`,
поля `modelUri`, `completionOptions`, `messages[].text`, строгий JSON — `jsonSchema`
или `jsonObject`. Сайт документации переехал на aistudio.yandex.ru и отдаёт CAPTCHA
на машинный запрос, поэтому источником взята схема API, а не страница руководства.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from app import config

USER_AGENT = "qatnov/1.0"


@dataclass(frozen=True)
class Answer:
    """Ответ модели. `text is None` — путь через модель не сработал."""

    text: str | None
    source: str  # model | cache | unavailable
    error: str | None = None


_CACHE: dict[str, str] = {}


def clear_cache() -> None:
    """Сбросить кэш ответов. Нужен приёмке: одинаковый вход даёт кэш, а не сеть."""
    _CACHE.clear()


def model_uri() -> str:
    return f"gpt://{config.LLM_FOLDER_ID}/{config.LLM_MODEL}"


def available() -> bool:
    """Есть ли смысл вообще идти в сеть."""
    return bool(config.LLM_API_KEY and config.LLM_FOLDER_ID) and not config.LLM_DISABLED


def status() -> dict:
    """Почему модель недоступна — видно и в ответе эндпоинта, и в гейтах."""
    if config.LLM_DISABLED:
        reason = "выключена переменной QATNOV_LLM_DISABLED"
    elif not config.LLM_API_KEY:
        reason = "нет QATNOV_YC_API_KEY"
    elif not config.LLM_FOLDER_ID:
        reason = "нет QATNOV_YC_FOLDER_ID"
    else:
        reason = None
    return {
        "available": available(),
        "reason": reason,
        "model": config.LLM_MODEL if available() else None,
        "timeout_sec": config.LLM_TIMEOUT_SEC,
        "cached_answers": len(_CACHE),
    }


def _payload(
    system: str,
    user: str,
    temperature: float,
    max_tokens: int,
    json_schema: dict | None,
) -> dict:
    body: dict = {
        "modelUri": model_uri(),
        "completionOptions": {
            "stream": False,
            "temperature": temperature,
            "maxTokens": max_tokens,
            "reasoningOptions": {"mode": "DISABLED"},
        },
        "messages": [
            {"role": "system", "text": system},
            {"role": "user", "text": user},
        ],
    }
    if json_schema is not None:
        # оба режима описаны в proto как oneof ResponseFormat
        if config.LLM_JSON_MODE == "object":
            body["jsonObject"] = True
        else:
            body["jsonSchema"] = {"schema": json_schema}
    return body


def _extract(raw: bytes) -> str | None:
    data = json.loads(raw)
    # синхронный REST заворачивает CompletionResponse в result
    root = data.get("result", data) if isinstance(data, dict) else {}
    alternatives = root.get("alternatives") or []
    if not alternatives:
        return None
    text = (alternatives[0].get("message") or {}).get("text")
    return text.strip() if isinstance(text, str) and text.strip() else None


def complete(
    system: str,
    user: str,
    *,
    temperature: float,
    max_tokens: int,
    json_schema: dict | None = None,
) -> Answer:
    if not available():
        return Answer(None, "unavailable", status()["reason"])

    body = _payload(system, user, temperature, max_tokens, json_schema)
    key = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    if key in _CACHE:
        return Answer(_CACHE[key], "cache")

    request = urllib.request.Request(
        config.LLM_ENDPOINT,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {config.LLM_API_KEY}",
            "x-folder-id": config.LLM_FOLDER_ID,
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=config.LLM_TIMEOUT_SEC) as response:
            text = _extract(response.read())
    except urllib.error.HTTPError as exc:  # нет квоты, неверный ключ, кривая схема
        return Answer(None, "unavailable", f"HTTP {exc.code}: {exc.reason}")
    except Exception as exc:  # таймаут, DNS, обрыв, мусор вместо JSON
        return Answer(None, "unavailable", f"{type(exc).__name__}: {exc}")

    if text is None:
        return Answer(None, "unavailable", "модель вернула пустой ответ")

    if len(_CACHE) >= config.LLM_CACHE_SIZE:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = text
    return Answer(text, "model")
