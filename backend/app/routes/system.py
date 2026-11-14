"""Состояние стенда, подключение телефона, QR-метки, сертификат, демо-снимки, сервис-воркер."""
from __future__ import annotations

import io
from html import escape as html_escape
from typing import Any

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from .. import auth, certs, config, llm, store
from ..deps import AnyUser, User
from ..vision.detector import detector

router = APIRouter(tags=["стенд"])
DEMO_DIR = config.ROOT / "demo" / "photos"


def _hosts() -> list[str]:
    return certs.SRV_HOSTS.read_text(encoding="utf-8").split(",") if certs.SRV_HOSTS.exists() else []


def qr_svg_text(text: str) -> str:
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


@router.get("/api/status")
async def status() -> dict[str, Any]:
    """Открыт без входа: экран входа показывает, готовы ли ИИ и детектор."""
    return {"camera_only": False, "detector": detector.status(), "vlm": await llm.status(),
            "offline": config.VLM_PROVIDER == "local", "https_hosts": _hosts(), "port": config.PORT}


@router.get("/api/demo-photos")
def demo_photos(_: User = AnyUser) -> list[dict[str, str]]:
    if not DEMO_DIR.exists():
        return []
    titles = {}
    attr = DEMO_DIR / "ATTRIBUTION.md"
    if attr.exists():
        for line in attr.read_text(encoding="utf-8").splitlines():
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) >= 5 and parts[0].endswith(".jpg"):
                titles[parts[0]] = {"title": parts[1], "credit": f"{parts[3]}, {parts[4]}, Wikimedia Commons"}
    return [{"name": p.name, "url": f"/demo-photos/{p.name}", **titles.get(p.name, {"title": p.stem, "credit": ""})}
            for p in sorted(DEMO_DIR.glob("*.jpg"))]


@router.get("/api/equipment/{eid}/qr.svg")
def qr_svg(eid: str, _: User = AnyUser) -> Response:
    payload = store.qr_payload(eid)
    if not payload:
        raise HTTPException(404, "Оборудование не найдено")
    return Response(qr_svg_text(payload), media_type="image/svg+xml")


@router.get("/labels", response_class=HTMLResponse)
def labels() -> str:
    cards = [f"""<div class="l">{qr_svg_text(e['qr_payload'])}<div><b>{html_escape(e['name'])}</b><br>{html_escape(e['model'])}<br>
            Зав. № {html_escape(e['serial_number'])} · Инв. № {html_escape(e['inventory_number'])}<br>
            <small>{html_escape(e['qr_payload'])}</small></div></div>""" for e in store.all_equipment()]
    return f"""<!doctype html><html lang="ru"><meta charset="utf-8"><title>QR-метки оборудования</title>
<style>body{{font:14px system-ui,sans-serif;margin:16px}}.l{{display:flex;gap:16px;align-items:center;border:1px dashed #999;
padding:12px;margin:0 0 12px;break-inside:avoid;max-width:560px}}.l svg{{width:150px;height:150px;flex:none}}
@media print{{button{{display:none}}}}</style>
<h2>QR-метки для демонстрации</h2><button onclick="print()">Печать</button>{''.join(cards)}</html>"""


@router.get("/ca.crt")
def ca_cert() -> Response:
    return Response(certs.ca_der(), media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="ar-inspector-ca.crt"'})


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request) -> str:
    """Страница подключения телефона. На ноутбуке показывает QR, на телефоне — пошаговую инструкцию."""
    ips = [h for h in _hosts() if h[0].isdigit() and h != "127.0.0.1"]
    main_ip = certs.primary_ip(ips) or "IP-адрес-сервера"
    app_url = f"https://{main_ip}:{config.PORT}/"
    others = ", ".join(f"https://{ip}:{config.PORT}/" for ip in ips if ip != main_ip)
    qr_block = f"""<div class="qr"><div>{qr_svg_text(app_url)}</div><div><b>Отсканируйте камерой телефона</b><br>
<code>{app_url}</code><br><span class="m">Телефон и этот компьютер должны быть в одной Wi-Fi-сети.
{f'Если адрес не открывается, попробуйте: {others}' if others else ''}</span></div></div>"""
    # Учётки показываем только на самом сервере (localhost), не по сети.
    if request.client and request.client.host in ("127.0.0.1", "::1"):
        rows = "".join(f"<li><code>{html_escape(line)}</code></li>" for line in auth.accounts_hint())
        qr_block += (f"<div class=\"qr\"><div><b>Вход в приложение</b> <span class=\"m\">(видно только на этом компьютере)</span>"
                     f"<ul>{rows}</ul><span class=\"m\">Свои пароли: DEMO_PASSWORD_ENGINEER, DEMO_PASSWORD_MASTER, "
                     f"DEMO_PASSWORD_ADMIN в .env, затем перезапуск.</span></div></div>")
    return f"""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Подключение телефона</title><style>body{{font:16px system-ui,sans-serif;margin:16px;max-width:680px;line-height:1.5;color:#111}}
a.b{{display:inline-block;padding:12px 16px;background:#0b6bcb;color:#fff;border-radius:8px;text-decoration:none;margin:4px 0}}
.qr{{display:flex;gap:16px;align-items:center;border:1px solid #ccc;border-radius:12px;padding:12px;margin:12px 0;flex-wrap:wrap}}
.qr svg{{width:200px;height:200px}} .m{{color:#555;font-size:14px}} code{{background:#f1f3f5;padding:1px 5px;border-radius:4px}}
li{{margin:6px 0}} details{{margin-top:14px}}</style>
<h2>Подключение телефона к AR-Инспектору</h2>
{qr_block}
<ol>
<li>Подключите телефон к той же Wi-Fi-сети, что и сервер. Интернет не нужен.</li>
<li>Откройте <a class="b" href="{app_url}">{app_url}</a><br>
<span class="m">Android — Chrome, iPhone — Safari.</span></li>
<li>Браузер предупредит, что подключение не защищено (сертификат выпущен самим сервером, а не публичным центром):<br>
<b>Chrome:</b> «Дополнительные» → «Перейти на сайт {main_ip} (небезопасно)».<br>
<b>Safari:</b> «Подробнее» → «посетить этот веб-сайт» → «Посетить веб-сайт».</li>
<li>Разрешите доступ к камере. Для быстрого запуска: Chrome → ⋮ → «Добавить на главный экран»;
Safari → Поделиться → «На экран Домой».</li>
</ol>
<details><summary>Убрать предупреждение и установить как полноценное приложение (необязательно)</summary>
<p>Установите на телефон сертификат локального удостоверяющего центра — тогда предупреждения не будет, а приложение
установится с офлайн-кэшем: <a href="/ca.crt">ar-inspector-ca.crt</a>.</p>
<p><b>Android:</b> Настройки → Безопасность → Шифрование и учётные данные → Установить сертификат → Сертификат ЦС.<br>
<b>iPhone/iPad:</b> открыть файл → Настройки → «Профиль загружен» → Установить; Основные → Об этом устройстве →
Доверие сертификатам → включить «AR-Инспектор локальный УЦ».</p></details>
<details><summary>Не открывается?</summary><ul>
<li>Брандмауэр Windows на сервере должен пропускать порт {config.PORT} в частной сети.</li>
<li>В роутере должна быть выключена изоляция клиентов (гостевые сети её обычно включают).</li>
<li>Сеть Wi-Fi на сервере должна быть помечена как «Частная».</li></ul></details>
</html>"""


@router.get("/sw.js")
def service_worker() -> FileResponse:
    return FileResponse(config.FRONTEND_DIR / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})
