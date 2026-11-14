"""Компьютерное зрение: опознание оборудования, QR, быстрый детектор, ИИ-осмотр кадра, архив анализов."""
from __future__ import annotations

import json
import io
import logging
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response, StreamingResponse
from PIL import Image

from .. import assess, briefing, config, db, inspection, llm, store
from ..deps import AnyUser, Engineer, User, card_or_404
from ..vision import analyze
from ..vision.detector import detector
from ..vision.vocab import LABELS_RU, MAIN_OBJECTS

log = logging.getLogger("app")
router = APIRouter(prefix="/api", tags=["зрение"])


@router.post("/identify")
async def identify(file: UploadFile = File(...), _: User = AnyUser) -> dict[str, Any]:
    briefing.cancel_pending()
    img = analyze.load_image(await file.read())
    try:
        res = await analyze.identify(img)
    except llm.LLMUnavailable as e:
        raise HTTPException(503, str(e)) from e
    if res.get("equipment_id"):
        res["equipment"] = await run_in_threadpool(store.equipment_brief, res["equipment_id"])
    return res


@router.post("/qr")
async def qr_decode(request: Request, _: User = AnyUser) -> dict[str, Any]:
    """Распознавание QR на сервере (OpenCV) — для браузеров без BarcodeDetector (iOS)."""
    img = analyze.load_image(await request.body(), max_side=1280)
    code = await run_in_threadpool(analyze.decode_qr, img)
    hit = await run_in_threadpool(analyze.match_code, code) if code else None
    return {"code": code, "equipment_id": hit["id"] if hit else None}


@router.post("/detect")
async def detect(request: Request, _: User = AnyUser) -> dict[str, Any]:
    """Быстрая детекция YOLO по кадру камеры (тело запроса — JPEG). Детектор работает в своём процессе."""
    body = await request.body()
    if not body:
        raise HTTPException(400, "Пустой кадр")
    try:
        res = await run_in_threadpool(detector.detect, body, None, config.DETECTOR_CONF)
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    for d in res["detections"]:
        d["label"] = LABELS_RU.get(d["cls"], d["cls"])
        d["main"] = d["cls"] in MAIN_OBJECTS
    return res


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/equipment/{eid}/analyze")
async def analyze_frame(eid: str, file: UploadFile = File(...), source: str = Form(...),
                        inspection_id: int | None = Form(None),
                        user: User = Engineer) -> StreamingResponse:
    """ИИ-осмотр кадра. Ответ — поток SSE: start → detector → item… → done (или error)."""
    card = await run_in_threadpool(card_or_404, eid)
    if inspection_id is not None:
        await run_in_threadpool(store.inspection_session, eid, inspection_id, user['id'], True)
    if source not in {"camera", "file"}:
        raise HTTPException(400, "Неизвестный источник снимка.")
    briefing.cancel_pending()
    img = analyze.load_image(await file.read())
    stored = analyze.to_jpeg(img, max_side=1600, quality=85)

    async def gen():
        yield _sse("start", {"width": img.width, "height": img.height})
        if detector.ready:
            try:
                det = await run_in_threadpool(detector.detect, stored, None, config.DETECTOR_CONF)
                for d in det["detections"]:
                    d["label"] = LABELS_RU.get(d["cls"], d["cls"])
                yield _sse("detector", det)
            except Exception as e:  # noqa: BLE001 — детектор не обязателен для анализа
                log.warning("Детектор: %s", e)
        result: dict[str, Any] | None = None
        try:
            async for kind, payload in analyze.inspect_stream(card, img):
                if kind == "item":
                    yield _sse("item", assess.enrich_item(payload, card))
                else:
                    result = payload
        except llm.LLMUnavailable as e:
            yield _sse("error", {"message": str(e)})
            return
        assert result is not None
        trace = result.pop("trace", None)
        result["items"] = [assess.enrich_item(it, card) for it in result["items"]]
        result["measurement"] = card.get("measurement")
        result["image_source"] = source
        result["author"] = user["display_name"]
        result["not_covered"] = assess.not_covered(card, result["items"])
        try:
            aid = await run_in_threadpool(store.save_analysis, eid, stored, result, trace, inspection_id, user['id'])
        except HTTPException as exc:
            yield _sse('error', {'message': exc.detail})
            return
        result["analysis_id"] = aid
        result["card"] = await run_in_threadpool(assess.build_card, eid)
        if inspection_id is not None:
            session = await run_in_threadpool(store.inspection_session, eid, inspection_id, user['id'])
            result['inspection'] = await run_in_threadpool(inspection.summary, session, result['card'])
        result["disclaimer"] = card["disclaimer"]
        yield _sse("done", result)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/analyses/{aid}/image")
def analysis_image(aid: int, _: User = AnyUser) -> FileResponse:
    row = store.analysis(aid)
    if not row or not row["image_path"]:
        raise HTTPException(404, "Снимок не найден")
    return FileResponse(row["image_path"], media_type="image/jpeg")


@router.get('/analyses/{aid}/crop/{key}')
def observation_crop(aid: int, key: str, _: User = AnyUser) -> Response:
    row = store.analysis(aid)
    if not row or not row['image_path']:
        raise HTTPException(404, 'Снимок не найден')
    items = db.loads(row['result_json'], {}).get('items', [])
    item = next((i for i in items if i.get('key') == key), None)
    if not item:
        raise HTTPException(404, 'Наблюдение не найдено на этом снимке')
    box = item.get('box')
    if not box or len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        raise HTTPException(422, 'Нет корректной области узла')
    with Image.open(row['image_path']) as im:
        w, h = im.size
        px, py = (box[2] - box[0]) * .12, (box[3] - box[1]) * .12
        rect = (max(0, int((box[0] - px) * w)), max(0, int((box[1] - py) * h)),
                min(w, max(1, int((box[2] + px) * w))), min(h, max(1, int((box[3] + py) * h))))
        crop = im.crop(rect).convert('RGB')
        crop.thumbnail((1000, 1000))
        output = io.BytesIO()
        crop.save(output, format='JPEG', quality=92)
    return Response(output.getvalue(), media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=86400'})


@router.get("/analyses/{aid}")
def analysis(aid: int, _: User = AnyUser) -> dict[str, Any]:
    row = store.analysis(aid)
    if not row:
        raise HTTPException(404, "Анализ не найден")
    result = db.loads(row["result_json"], {})
    decided = store.verdicts(row["equipment_id"], aid)
    for it in result.get("items", []):
        if it.get("component_id") in decided:
            it["verdict"] = decided[it["component_id"]]["verdict"]
    return {"id": row["id"], "equipment_id": row["equipment_id"], "created_at": row["created_at"],
            "result": result, "image_url": f"/api/analyses/{aid}/image",
            "trace": {"prompt_version": row.get("prompt_version"), "model": row.get("model"),
                      "provider": row.get("provider"), "duration_s": row.get("duration_s"), "tokens": row.get("tokens")}}
