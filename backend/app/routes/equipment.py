"""Справочник оборудования, цифровая карточка, журнал работ, режим работы, нормативы, измерения."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool

from .. import assess, briefing as plans, inspection, llm, runtime, store, telemetry
from ..deps import Admin, AnyUser, Engineer, User, card_or_404, require_equipment
from ..vision import analyze

router = APIRouter(prefix="/api", tags=["оборудование"])


@router.get("/equipment")
def equipment_list(_: User = AnyUser) -> list[dict[str, Any]]:
    return assess.equipment_list()


@router.get("/equipment/{eid}")
def equipment_card(eid: str, _: User = AnyUser) -> dict[str, Any]:
    return card_or_404(eid)


@router.post("/resolve")
def resolve(payload: dict[str, Any] = Body(...), _: User = AnyUser) -> dict[str, Any]:
    """QR-код, серийный или инвентарный номер → карточка."""
    hit = analyze.match_code(str(payload.get("code", "")))
    if not hit:
        raise HTTPException(404, "Код не найден в базе оборудования")
    return {"equipment_id": hit["id"], "method": "qr" if payload.get("source") == "qr" else "code"}


# ---------------------------------------------------------------- ИИ-план осмотра

@router.get("/equipment/{eid}/briefing")
async def briefing(eid: str, user: User = AnyUser) -> dict[str, Any]:
    card = await run_in_threadpool(card_or_404, eid)
    active = await run_in_threadpool(store.active_inspection, eid, user['id'])
    session = await run_in_threadpool(inspection.summary, active, card) if active else None
    return await plans.get(card, session)


# ---------------------------------------------------------------- журнал работ

@router.post("/equipment/{eid}/events")
def add_event(eid: str, payload: dict[str, Any] = Body(...), user: User = Engineer) -> dict[str, Any]:
    require_equipment(eid)
    etype = payload.get("type")
    if etype not in store.EVENT_TYPES:
        raise HTTPException(400, f"Неизвестный тип события: {etype}")
    if etype in store.SYSTEM_EVENTS:
        raise HTTPException(400, "Используйте соответствующее действие карточки.")
    cid = payload.get("component_id")
    comp = store.component(eid, cid) if cid else None
    if cid and not comp:
        raise HTTPException(404, "Узел не найден")
    data = {k: payload[k] for k in ("analysis_id", "category", "summary") if k in payload}
    verdict = payload.get("verdict")
    if verdict is not None:
        # Решение инженера по находке ИИ: подтверждено / не подтверждено / отложено — попадёт в осмотр и отчёт.
        if verdict not in store.VERDICTS or not comp or not isinstance(payload.get("analysis_id"), int):
            raise HTTPException(400, "Решение по находке ИИ относится к узлу и конкретному анализу.")
        store.assert_reviewable(eid, payload['analysis_id'], comp['id'], user['id'])
        data["verdict"] = verdict
    note = (payload.get("note") or "").strip() or store.EVENT_TYPES[etype]
    store.add_event(eid, comp, etype, note, user["display_name"], data)
    return card_or_404(eid)


@router.post("/equipment/{eid}/operation")
def operation(eid: str, payload: dict[str, Any] = Body(...), user: User = Engineer) -> dict[str, Any]:
    require_equipment(eid)
    try:
        runtime.set_state(eid, payload.get("state"), user["display_name"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return card_or_404(eid)


@router.post("/equipment/{eid}/components/{cid}/normative")
def normative(eid: str, cid: str, payload: dict[str, Any] = Body(...), user: User = Admin) -> dict[str, Any]:
    require_equipment(eid)
    if not store.component(eid, cid):
        raise HTTPException(404, "Узел не найден.")
    source = payload.get("source")
    if not isinstance(source, str) or not 5 <= len(source.strip()) <= 500:
        raise HTTPException(400, "Укажите источник норматива или допущение команды.")
    values = []
    for field in ("normative_life_years", "normative_hours", "maintenance_interval_hours"):
        value = payload.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1e8):
            raise HTTPException(400, "Норматив должен быть положительным числом или пустым значением.")
        values.append(value)
    store.set_normative(eid, cid, tuple(values), source.strip(), user["display_name"])
    return card_or_404(eid)


# ---------------------------------------------------------------- воспроизведение измерений

@router.get("/equipment/{eid}/scenario")
def scenario(eid: str, _: User = AnyUser) -> dict[str, Any]:
    require_equipment(eid)
    if eid != telemetry.FOCUS:
        raise HTTPException(400, "Измерения подготовлены для насосного сценария.")
    _, state = telemetry.current(eid)
    return {"state": state, "presets": telemetry.presets()}


@router.post("/equipment/{eid}/scenario")
def select_scenario(eid: str, payload: dict[str, Any] = Body(...), _: User = Engineer) -> dict[str, Any]:
    require_equipment(eid)
    index = payload.get("index")
    if not isinstance(index, int) or isinstance(index, bool):
        raise HTTPException(400, "Выберите номер записи.")
    try:
        telemetry.select(eid, index)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return card_or_404(eid)
