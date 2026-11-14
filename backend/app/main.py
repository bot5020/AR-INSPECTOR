"""Сборка приложения: запуск, проверки доступа, разделы API (routes/*), раздача PWA.

Всё работает в локальной сети без доступа в интернет.
"""
from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth, config, db, llm, orgdata, seed  # noqa: F401 — llm и detector нужны тестам через main
from .routes import ROUTERS
from .routes.system import DEMO_DIR
from .vision.detector import detector

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for _noisy in ("httpx", "httpcore"):  # опрос LM Studio каждые 10 с не должен засорять консоль
    logging.getLogger(_noisy).setLevel(logging.WARNING)
log = logging.getLogger("app")
# Реестр Windows иногда отдаёт .js как text/plain — тогда браузер не загрузит модули клиента.
for _ext in (".js", ".mjs"):
    mimetypes.add_type("text/javascript", _ext)

_started = False


def _startup() -> None:
    global _started
    if _started:  # HTTP и HTTPS делят одно приложение — инициализация один раз
        return
    _started = True
    config.validate()
    db.conn()          # миграции
    seed.seed()        # демо-данные в пустую базу
    orgdata.sync()     # карточки по паспортам организаторов, если папка с данными есть
    auth.bootstrap()   # демо-учётки
    detector.start()   # отдельный процесс: сервер отвечает, пока модель загружается
    log.info("ИИ-анализ: %s (%s)", config.VLM_PROVIDER, config.VLM_URL if config.VLM_PROVIDER == "local" else "облако")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _startup()
    yield


app = FastAPI(title="AR-Инспектор", version="0.2.0", lifespan=lifespan)
for router in ROUTERS:
    app.include_router(router)

# Без входа доступны только эти адреса API. Роли проверяются в каждом обработчике (deps.*),
# а это — запрет по умолчанию: новый обработчик без проверки не окажется открытым.
PUBLIC_API = {"/api/auth/login", "/api/auth/me", "/api/status"}


@app.middleware("http")
async def session_access(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_API and not await run_in_threadpool(auth.current, request):
        return JSONResponse({"detail": "Войдите в приложение."}, status_code=401)
    return await call_next(request)


_seen_clients: set[str] = set()


@app.middleware("http")
async def no_stale_shell(request: Request, call_next):
    """Оболочка PWA всегда перепроверяется у сервера, иначе после обновления телефон держит старую версию.

    Заодно пишет в журнал первое обращение каждого внешнего устройства — помогает понять, доходит ли телефон.
    """
    host = request.client.host if request.client else ""
    if host and host not in ("127.0.0.1", "::1", "testclient") and host not in _seen_clients:
        _seen_clients.add(host)
        log.info("Подключилось устройство %s: %s", host, request.headers.get("user-agent", "")[:90])
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".js", ".mjs", ".css", ".html", ".webmanifest")):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


if DEMO_DIR.exists():
    app.mount("/demo-photos", StaticFiles(directory=DEMO_DIR), name="demo-photos")
app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="static")
