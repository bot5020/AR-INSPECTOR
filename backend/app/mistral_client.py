"""Клиент Mistral с ротацией ключей и разбором JSON-ответа.

На тарифе ~1 запрос/с на ключ, поэтому ключи выдаются по кругу с учётом времени
последнего запроса; при 429 запрос повторяется на следующем ключе.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from typing import Any

from . import config

try:  # mistralai 3.x
    from mistralai.client import Mistral
except ImportError:  # pragma: no cover - mistralai 1.x
    from mistralai import Mistral

log = logging.getLogger("mistral")


class MistralUnavailable(RuntimeError):
    pass


class _KeyPool:
    def __init__(self, keys: list[str]):
        self.clients = [Mistral(api_key=k, timeout_ms=int(config.MISTRAL_TIMEOUT * 1000)) for k in keys]
        self.last_used = [0.0] * len(keys)
        self.lock = asyncio.Lock()

    async def acquire(self, exclude: set[int]) -> int:
        async with self.lock:
            candidates = [i for i in range(len(self.clients)) if i not in exclude] or list(range(len(self.clients)))
            idx = min(candidates, key=lambda i: self.last_used[i])
            wait = self.last_used[idx] + config.MISTRAL_MIN_INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self.last_used[idx] = time.monotonic()
            return idx


_pool: _KeyPool | None = _KeyPool(config.MISTRAL_API_KEYS) if config.MISTRAL_API_KEYS else None


def available() -> bool:
    return _pool is not None


def image_part(jpeg: bytes) -> dict[str, Any]:
    return {"type": "image_url", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}


def _text_of(content: Any) -> str:
    """Ответ mistral-large бывает списком чанков (thinking + text) — берём только текст."""
    if isinstance(content, str):
        return content
    parts = []
    for chunk in content or []:
        ctype = getattr(chunk, "type", None) or (chunk.get("type") if isinstance(chunk, dict) else None)
        if ctype == "text":
            parts.append(getattr(chunk, "text", None) or chunk.get("text", ""))
    return "".join(parts)


def parse_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


async def chat(messages: list[dict[str, Any]], *, json_mode: bool = True, max_tokens: int = 2000,
               temperature: float = 0.2) -> tuple[Any, str]:
    """Вызов модели; возвращает (разобранный JSON или текст, имя модели)."""
    if _pool is None:
        raise MistralUnavailable("Ключи Mistral не настроены (MISTRAL_API_KEYS в .env)")
    tried: set[int] = set()
    last_err: Exception | None = None
    for _attempt in range(max(3, len(_pool.clients) + 1)):
        idx = await _pool.acquire(tried)
        client = _pool.clients[idx]
        try:
            kwargs: dict[str, Any] = dict(model=config.MISTRAL_MODEL, messages=messages, max_tokens=max_tokens,
                                          temperature=temperature)
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = await client.chat.complete_async(**kwargs)
            text = _text_of(resp.choices[0].message.content)
            if not json_mode:
                return text.strip(), resp.model
            try:
                return parse_json(text), resp.model
            except ValueError as e:
                log.warning("Невалидный JSON от модели (ключ #%d): %s", idx + 1, text[:300])
                last_err = e
                continue
        except Exception as e:  # noqa: BLE001 — SDK бросает разные типы ошибок
            msg = str(e)
            last_err = e
            tried.add(idx)
            if "429" in msg or "rate" in msg.lower():
                log.info("Ключ #%d упёрся в лимит, пробую следующий", idx + 1)
                continue
            log.warning("Ошибка Mistral (ключ #%d): %s", idx + 1, msg[:300])
            await asyncio.sleep(0.5)
    raise MistralUnavailable(f"Mistral не ответил: {last_err}")
