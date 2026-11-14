"""Настройки сервиса из переменных окружения (.env в корне проекта)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


class ConfigError(SystemExit):
    """Неверная настройка в .env — понятное сообщение вместо трассировки Python."""


def _num(name: str, default: str, kind=float):
    raw = os.getenv(name) or default
    try:
        return kind(raw)
    except ValueError:
        raise ConfigError(f"Неверное значение {name}={raw!r} в .env: нужно число.") from None


def _list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


# Провайдер ИИ-анализа: local — локальная vision-модель через OpenAI-совместимый API
# (LM Studio / llama.cpp server / Ollama), работает без интернета; mistral — облако, только для разработки.
VLM_PROVIDER: str = os.getenv("VLM_PROVIDER", "local").lower()
VLM_URL: str = os.getenv("VLM_URL", "http://127.0.0.1:8000/v1").rstrip("/")
VLM_MODEL: str = os.getenv("VLM_MODEL", "vlm")
VLM_TIMEOUT: float = _num("VLM_TIMEOUT", "120")
# Отключить «размышления» у reasoning-моделей (Qwen 3.x): иначе ответ в разы дольше.
VLM_REASONING_EFFORT: str = os.getenv("VLM_REASONING_EFFORT", "none")

MISTRAL_API_KEYS: list[str] = _list("MISTRAL_API_KEYS") or _list("MISTRAL_API_KEY")
MISTRAL_MODEL: str = os.getenv("MISTRAL_MODEL", "mistral-large-latest")
# Минимальный интервал между запросами на один ключ (тариф: 1 запрос/с на ключ).
MISTRAL_MIN_INTERVAL: float = _num("MISTRAL_MIN_INTERVAL", "1.05")
MISTRAL_TIMEOUT: float = _num("MISTRAL_TIMEOUT", "90")

DETECTOR_CONF: float = _num("DETECTOR_CONF", "0.12")
DETECTOR_TIMEOUT: float = _num("DETECTOR_TIMEOUT", "10")

HOST: str = os.getenv("HOST", "0.0.0.0")
# Приложение для телефонов: HTTPS на всех интерфейсах (камера в браузере работает только по HTTPS;
# браузер один раз предупредит о сертификате — «Дополнительно» → «Перейти на сайт»).
PORT: int = _num("HTTPS_PORT", "8080", int)
# HTTP только для самого ноутбука (localhost): без предупреждений о сертификате. 0 — выключить.
HTTP_PORT: int = _num("HTTP_PORT", os.getenv("PORT") or "8081", int)
HTTP_HOST: str = os.getenv("HTTP_HOST", "127.0.0.1")

DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()
DB_PATH = DATA_DIR / "app.db"
IMAGES_DIR = DATA_DIR / "images"
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", str(ROOT / "frontend"))).resolve()

# Доля израсходованного ресурса, начиная с которой узел подсвечивается жёлтым.
RESOURCE_WARN_FRACTION: float = _num("RESOURCE_WARN_FRACTION", "0.85")
# За сколько дней до планового ТО предупреждать.
MAINTENANCE_SOON_DAYS: int = _num("MAINTENANCE_SOON_DAYS", "30", int)

DATA_DIR.mkdir(parents=True, exist_ok=True)
IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def validate() -> None:
    """Проверка настроек при старте: ошибка в .env должна быть видна сразу и понятна."""
    problems = []
    if VLM_PROVIDER not in ("local", "mistral"):
        problems.append(f"VLM_PROVIDER={VLM_PROVIDER!r}: допустимо local или mistral.")
    if VLM_PROVIDER == "local" and not VLM_URL.startswith(("http://", "https://")):
        problems.append(f"VLM_URL={VLM_URL!r}: нужен адрес вида http://127.0.0.1:8000/v1.")
    if VLM_PROVIDER == "mistral" and not MISTRAL_API_KEYS:
        problems.append("VLM_PROVIDER=mistral, но не задан MISTRAL_API_KEYS.")
    for name, port in (("HTTPS_PORT", PORT), ("HTTP_PORT", HTTP_PORT)):
        if port and not 1 <= port <= 65535:
            problems.append(f"{name}={port}: порт от 1 до 65535 (HTTP_PORT=0 выключает HTTP).")
    if HTTP_PORT and HTTP_PORT == PORT:
        problems.append(f"HTTPS_PORT и HTTP_PORT совпадают ({PORT}).")
    if not 0 < RESOURCE_WARN_FRACTION < 1:
        problems.append(f"RESOURCE_WARN_FRACTION={RESOURCE_WARN_FRACTION}: доля от 0 до 1, например 0.85.")
    if not 0 < DETECTOR_CONF < 1:
        problems.append(f"DETECTOR_CONF={DETECTOR_CONF}: порог уверенности от 0 до 1.")
    if VLM_TIMEOUT <= 0 or DETECTOR_TIMEOUT <= 0:
        problems.append("VLM_TIMEOUT и DETECTOR_TIMEOUT должны быть больше нуля.")
    if not FRONTEND_DIR.is_dir():
        problems.append(f"Нет папки клиента {FRONTEND_DIR}.")
    if problems:
        raise ConfigError("Ошибка настроек в .env:\n  - " + "\n  - ".join(problems))
