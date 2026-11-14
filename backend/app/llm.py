"""Единый вызов vision-модели.

По умолчанию — локальная модель через OpenAI-совместимый API (LM Studio, llama.cpp server, Ollama),
работает без интернета. Облачный Mistral оставлен как опция для разработки (VLM_PROVIDER=mistral).
"""
from __future__ import annotations

import base64
import logging
import re
import time
from typing import Any

import httpx

from . import config
from .mistral_client import parse_json

log = logging.getLogger("llm")


class LLMUnavailable(RuntimeError):
    pass


def final_content(text: str) -> str:
    """reasoning_content сервера не используется; из content извлекается только законченный финал."""
    text = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', text, flags=re.I | re.S)
    if re.search(r'</?think\b', text, re.I):
        raise LLMUnavailable('Модель не завершила формирование итогового ответа')
    return text.strip()


def image_part(jpeg: bytes) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}


_client = httpx.AsyncClient(timeout=config.VLM_TIMEOUT)


async def status() -> dict[str, Any]:
    if config.VLM_PROVIDER == "mistral":
        from . import mistral_client
        return {"provider": "mistral", "ready": mistral_client.available(), "model": config.MISTRAL_MODEL,
                "offline": False}
    try:
        r = await _client.get(f"{config.VLM_URL}/models", timeout=3)
        ids = [m.get("id") for m in r.json().get("data", [])]
        return {"provider": "local", "ready": r.status_code == 200, "model": config.VLM_MODEL,
                "loaded": config.VLM_MODEL in ids, "url": config.VLM_URL, "offline": True}
    except Exception as e:  # noqa: BLE001
        return {"provider": "local", "ready": False, "model": config.VLM_MODEL, "error": str(e)[:200],
                "url": config.VLM_URL, "offline": True}


ATTEMPTS = 2   # один повтор при сбое движка до начала ответа


def _error_text(err: Any) -> str:
    return (err.get("message") if isinstance(err, dict) else str(err))[:300] or "ошибка движка"


async def ask_stream(system: str, content: list[dict[str, Any]], *, schema: dict[str, Any],
                     max_tokens: int = 1500, temperature: float = 0.1):
    """Потоковый вариант: отдаёт фрагменты текста по мере генерации (для поэтапного показа меток)."""
    if config.VLM_PROVIDER == "mistral":  # облако — без потока, одним куском
        import json as _json
        data, meta = await ask(system, content, schema=schema, max_tokens=max_tokens, temperature=temperature)
        yield _json.dumps(data, ensure_ascii=False), meta
        return
    body: dict[str, Any] = {
        "model": config.VLM_MODEL, "stream": True, "stream_options": {"include_usage": True},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "max_tokens": max_tokens, "temperature": temperature,
        "response_format": {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}},
    }
    if config.VLM_REASONING_EFFORT:
        body["reasoning_effort"] = config.VLM_REASONING_EFFORT
    if config.VLM_REASONING_EFFORT == "none":
        body["enable_thinking"] = False
        body["chat_template_kwargs"] = {"enable_thinking": False}
    import json as _json
    t0 = time.perf_counter()
    meta: dict[str, Any] = {"model": config.VLM_MODEL, "provider": "local", "attempts": 1}
    for attempt in range(1, ATTEMPTS + 1):
        got, failed = False, None
        try:
            async with _client.stream("POST", f"{config.VLM_URL}/chat/completions", json=body) as r:
                if r.status_code != 200:
                    failed = f"ответ {r.status_code}: {(await r.aread())[:300].decode('utf-8', 'replace')}"
                else:
                    async for line in r.aiter_lines():
                        if not line.startswith("data: ") or line.strip() == "data: [DONE]":
                            continue
                        chunk = _json.loads(line[6:])
                        if chunk.get("error"):   # LM Studio сообщает о сбое движка внутри потока
                            failed = _error_text(chunk["error"])
                            break
                        if chunk.get("usage"):
                            meta["tokens"] = chunk["usage"].get("completion_tokens")
                        if chunk.get("model"):
                            meta["model"] = chunk["model"]
                        choices = chunk.get("choices") or []
                        delta = (choices[0].get("delta") or {}).get("content") if choices else None
                        if delta:
                            got = True
                            yield delta, None
        except httpx.HTTPError as e:
            failed = f"недоступна ({config.VLM_URL}): {e}"
        if failed is None and not got:
            failed = "пустой ответ"
        if failed is None:
            break
        # Сбой до первого токена (например, «Engine protocol predict request failed» в LM Studio) — повторяем:
        # снимок тот же, инженеру не нужно переснимать. После начала ответа повтор дал бы дубли меток.
        if got or attempt == ATTEMPTS:
            raise LLMUnavailable(f"Локальная модель: {failed}")
        log.warning("Сбой модели до начала ответа (%s) — повтор %d", failed, attempt + 1)
        meta["attempts"] = attempt + 1
    meta["seconds"] = round(time.perf_counter() - t0, 1)
    yield "", meta


async def ask(system: str, content: list[dict[str, Any]], *, schema: dict[str, Any] | None = None,
              max_tokens: int = 1500, temperature: float = 0.1) -> tuple[Any, dict[str, Any]]:
    """Возвращает (JSON-ответ или текст, метаданные вызова)."""
    t0 = time.perf_counter()
    if config.VLM_PROVIDER == "mistral":
        from . import mistral_client
        mcontent = [
            {"type": "image_url", "image_url": c["image_url"]["url"]} if c["type"] == "image_url" else c
            for c in content
        ]
        try:
            data, model = await mistral_client.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": mcontent}],
                json_mode=schema is not None, max_tokens=max_tokens, temperature=temperature)
        except mistral_client.MistralUnavailable as e:
            raise LLMUnavailable(str(e)) from e
        return data, {"model": model, "provider": "mistral", "seconds": round(time.perf_counter() - t0, 1)}

    body: dict[str, Any] = {
        "model": config.VLM_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "max_tokens": max_tokens, "temperature": temperature,
    }
    if config.VLM_REASONING_EFFORT:
        body["reasoning_effort"] = config.VLM_REASONING_EFFORT
    if config.VLM_REASONING_EFFORT == "none":
        body["enable_thinking"] = False
        body["chat_template_kwargs"] = {"enable_thinking": False}
    if schema is not None:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "result", "strict": True, "schema": schema}}
    for attempt in range(1, ATTEMPTS + 1):
        try:
            r = await _client.post(f"{config.VLM_URL}/chat/completions", json=body)
        except httpx.HTTPError as e:
            failed = f"недоступна ({config.VLM_URL}): {e}"
        else:
            j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            choices = j.get("choices") or []
            text = final_content(choices[0]["message"].get("content") or "") if choices else ""
            failed = (f"ответ {r.status_code}: {r.text[:300]}" if r.status_code != 200
                      else _error_text(j["error"]) if j.get("error") else None if text else "пустой ответ")
        if failed is None:
            break
        if attempt == ATTEMPTS:
            raise LLMUnavailable(f"Локальная модель: {failed}")
        log.warning("Сбой модели (%s) — повтор %d", failed, attempt + 1)
    meta = {"model": j.get("model", config.VLM_MODEL), "provider": "local",
            "seconds": round(time.perf_counter() - t0, 1), "tokens": (j.get("usage") or {}).get("completion_tokens")}
    if schema is None:
        return text, meta
    try:
        return parse_json(text), meta
    except ValueError as e:
        log.warning("Модель вернула не JSON: %s", text[:300])
        raise LLMUnavailable("Модель вернула некорректный ответ") from e
