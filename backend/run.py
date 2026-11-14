"""Запуск сервера: HTTPS для планшетов (камера и PWA требуют HTTPS) и HTTP для первичной настройки.

    .venv\\Scripts\\python backend\\run.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

import uvicorn  # noqa: E402

from app import certs, config  # noqa: E402

HTTP_PORT = config.HTTP_PORT


async def main() -> None:
    cert, key, hosts = certs.ensure_server_cert()
    ips = [h for h in hosts if h[0].isdigit() and h != "127.0.0.1"]
    main_ip = certs.primary_ip(ips)
    print(f"\n  AR-Инспектор — приложение на https://0.0.0.0:{config.PORT}/")
    if main_ip:
        print(f"  Телефон:            https://{main_ip}:{config.PORT}/")
        print("                      (браузер предупредит о сертификате: «Дополнительно» → «Перейти на сайт»)")
    others = [ip for ip in ips if ip != main_ip]
    if others:
        print(f"  Другие адреса:      {', '.join(f'https://{ip}:{config.PORT}/' for ip in others)}")
    if HTTP_PORT:
        print(f"  Этот компьютер:     http://localhost:{HTTP_PORT}/   (QR для телефона: /setup)")
    print()
    from app.main import app

    servers = [uvicorn.Server(uvicorn.Config(app, host=config.HOST, port=config.PORT, ssl_certfile=str(cert),
                                             ssl_keyfile=str(key), log_level="warning"))]
    if HTTP_PORT:
        http = uvicorn.Server(uvicorn.Config(app, host=config.HTTP_HOST, port=HTTP_PORT, log_level="warning"))
        http.install_signal_handlers = lambda: None  # сигналы обрабатывает один сервер
        servers.append(http)
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    asyncio.run(main())
