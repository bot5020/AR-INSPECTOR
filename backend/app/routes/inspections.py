"""Сохранение осмотра, подтверждение мастером, отчёты PDF и Excel."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from .. import db, inspection, reports, store
from ..deps import AnyUser, Engineer, Master, User, card_or_404, require_equipment

router = APIRouter(prefix="/api/equipment/{eid}/inspections", tags=["осмотры"])


@router.get('/session')
def active_session(eid: str, user: User = Engineer) -> dict:
    card = card_or_404(eid)
    session = store.active_inspection(eid, user['id'])
    return {'session': inspection.summary(session, card) if session else None}


@router.post('/session')
def start_session(eid: str, user: User = Engineer) -> dict:
    card = card_or_404(eid)
    return {'session': inspection.summary(store.start_inspection(eid, user['id']), card)}


@router.get('/session/{sid}')
def get_session(eid: str, sid: int, user: User = Engineer) -> dict:
    return {'session': inspection.summary(store.inspection_session(eid, sid, user['id']), card_or_404(eid))}


def review_of(eid: str, analysis: dict[str, Any] | None, aid: int | None) -> dict[str, Any] | None:
    """Находки ИИ последнего кадра и решения инженера по ним — часть сохранённого осмотра."""
    if not analysis or not aid:
        return None
    decided = store.verdicts(eid, aid)
    findings = []
    for it in analysis.get("items", []):
        v = decided.get(it.get("component_id") or "")
        findings.append({"component_id": it.get("component_id"), "name": it.get("name"), "category": it.get("category"),
                         "reason": it.get("reason"), "verdict": v["verdict"] if v else None,
                         "note": v["note"] if v else None, "author": v["author"] if v else None})
    attention = [f for f in findings if f["category"] in ("check", "defect")]
    return {"findings": findings, "not_covered": analysis.get("not_covered", []),
            "open": sum(1 for f in attention if not f["verdict"] or f["verdict"] == "deferred"),
            "confirmed": sum(1 for f in findings if f["verdict"] == "confirmed"),
            "rejected": sum(1 for f in findings if f["verdict"] == "rejected"),
            "partial": bool(analysis.get("partial"))}


@router.post("")
def save_inspection(eid: str, payload: dict[str, Any] = Body(...), user: User = Engineer) -> dict[str, Any]:
    """Сохраняет итог осмотра в историю: сводка статусов, кадр ИИ, решения инженера по находкам."""
    card = card_or_404(eid)
    sid = payload.get('inspection_id')
    view = None
    if sid is not None:
        if type(sid) is not int:
            raise HTTPException(400, 'Неверный номер осмотра.')
        session = store.inspection_session(eid, sid, user['id'])
        if session['status'] == 'closed':
            return {**card, 'saved_inspection_id': session['event_id'], 'inspection_number': sid}
        view = inspection.summary(session, card)
        if not view['shots']:
            raise HTTPException(400, 'Сначала добавьте кадр в осмотр.')
    aid = payload.get("analysis_id")
    if view:
        aid = view['shots'][-1]['analysis_id']
    row = store.analysis_of(eid, aid) if aid else None
    if aid and not row:
        raise HTTPException(400, "Снимок анализа не относится к этому агрегату.")
    analysis = db.loads(row["result_json"], {}) if row else None
    summary = {
        "level": card["level"], "status_label": card["status_label"], "counts": card["counts"],
        "by_source": card["by_source"],
        "attention": [{"id": c["id"], "name": c["name"], "level": c["rollup_level"]}
                      for c in card["components"] if not c["parent_id"] and (c["rollup_level"] or 0) > 0],
    }
    note = (payload.get("note") or "").strip() or f"Осмотр завершён: {card['status_label'].lower()}"
    snapshot = {"analysis_id": aid, "summary": summary, "card": card, "user": user, "analysis": analysis,
                "review": review_of(eid, analysis, aid), "image_path": row["image_path"] if row else None}
    if view:
        snapshot.update(inspection_id=sid, shots=view['shots'], review=view['review'],
                        analysis_ids=[s['analysis_id'] for s in view['shots']],
                        image_paths=[s['image_path'] for s in view['shots']])
        note = f'Осмотр №{sid}: ' + note
        event_id = store.close_inspection(eid, sid, user['id'], note, user['display_name'], snapshot)
    else:
        event_id = store.save_inspection(eid, note, user["display_name"], snapshot)
    result = card_or_404(eid)
    result["saved_inspection_id"] = event_id
    result['inspection_number'] = sid or event_id
    return result


@router.post("/{iid}/approve")
def approve(eid: str, iid: int, user: User = Master) -> dict[str, Any]:
    require_equipment(eid)
    if not store.inspection(eid, iid):
        raise HTTPException(404, "Осмотр не найден.")
    if store.approvals(eid, iid):
        raise HTTPException(409, "Этот осмотр уже подтверждён.")
    store.approve(eid, iid, user["display_name"])
    return card_or_404(eid)


@router.get("/{iid}/report.{fmt}")
def export_report(eid: str, iid: int, fmt: str, _: User = AnyUser) -> Response:
    row = store.inspection(eid, iid)
    if not row:
        raise HTTPException(404, "Сохраненный осмотр не найден.")
    if fmt not in {"pdf", "xlsx"}:
        raise HTTPException(400, "Поддерживаются PDF и XLSX.")
    raw = reports.export(row, fmt)
    mime = "application/pdf" if fmt == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(raw, media_type=mime, headers={"Content-Disposition": f'attachment; filename="inspection-{iid}.{fmt}"'})
