"""Доступ к данным: все изменения истории, анализов и карточек — здесь, а не в обработчиках HTTP.

Обработчики (routes/*) проверяют запрос и права, затем вызывают функции этого модуля.
Карточку целиком собирает assess.build_card — она только читает.
"""
from __future__ import annotations

from datetime import date
from typing import Any
from fastapi import HTTPException

from . import config, db, runtime

# Типы записей истории и подписи по умолчанию.
EVENT_TYPES = {
    "checked": "Проверено, замечаний нет",
    "defect": "Зарегистрировано замечание",
    "maintenance": "Выполнено обслуживание",
    "replacement": "Выполнена замена",
    "resolved": "Замечание устранено",
    "inspection": "Результат осмотра сохранён",
    "approval": "Осмотр подтвержден мастером",
    "operation": "Режим работы изменен",
    "configuration": "Нормативы изменены",
    "review": "Решение по находке ИИ",
}
# Эти записи создаются только своими действиями карточки, не через общий журнал.
SYSTEM_EVENTS = {"approval", "operation", "configuration", "inspection"}
# Решение инженера по находке ИИ.
VERDICTS = {"confirmed": "Находка ИИ подтверждена", "rejected": "Находка ИИ не подтверждена", "deferred": "Решение отложено"}
VISUAL_ORDER = {"ok": 0, "check": 1, "defect": 2}


# ---------------------------------------------------------------- справочник

def equipment_brief(eid: str) -> dict[str, Any] | None:
    return db.query_one("SELECT id, name, model, location FROM equipment WHERE id = ?", (eid,))


def all_equipment() -> list[dict[str, Any]]:
    return db.query("SELECT * FROM equipment ORDER BY id")


def qr_payload(eid: str) -> str | None:
    row = db.query_one("SELECT qr_payload FROM equipment WHERE id = ?", (eid,))
    return row["qr_payload"] if row else None


def component(eid: str, cid: str) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM components WHERE id = ? AND equipment_id = ?", (cid, eid))


# ---------------------------------------------------------------- ИИ-анализы

def save_analysis(eid: str, img_bytes: bytes, result: dict[str, Any], trace: dict[str, Any] | None = None,
                  session_id: int | None = None, user_id: int | None = None) -> int:
    """Снимок, результат и след запроса к модели (версия промпта, сырой ответ) — для воспроизводимости."""
    trace = trace or {}
    meta = result.get("model") or {}
    with db.tx() as c:
        if session_id is not None:
            inspection_session(eid, session_id, user_id, writable=True)
        cur = c.execute(
            """INSERT INTO analyses (equipment_id, created_at, model, result_json, prompt_version, provider,
                   duration_s, tokens, raw_response) VALUES (?,?,?,?,?,?,?,?,?)""",
            (eid, db.now_iso(), meta.get("model"), db.dumps(result), trace.get("prompt_version"),
             meta.get("provider"), meta.get("seconds"), meta.get("tokens"), trace.get("raw")))
        aid = int(cur.lastrowid)
        path = config.IMAGES_DIR / f"analysis-{aid}.jpg"
        path.write_bytes(img_bytes)
        c.execute("UPDATE analyses SET image_path = ? WHERE id = ?", (str(path), aid))
        if session_id is not None:
            c.execute("INSERT INTO inspection_shots(session_id,analysis_id) VALUES (?,?)", (session_id, aid))
        worst: dict[str, dict[str, Any]] = {}
        for it in result.get("items", []):
            cid = it.get("component_id")
            if cid and (cid not in worst or VISUAL_ORDER[it["category"]] > VISUAL_ORDER[worst[cid]["category"]]):
                worst[cid] = it
        for cid, it in worst.items():
            c.execute(
                """INSERT INTO visual_results (equipment_id, component_id, analysis_id, category, signs_json, reason,
                       recommendation, confidence, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (eid, cid, aid, it["category"], db.dumps(it.get("signs", [])), it.get("reason"),
                 it.get("recommendation"), it.get("confidence"), db.now_iso()))
    return aid


def analysis(aid: int) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM analyses WHERE id = ?", (aid,))


def analysis_of(eid: str, aid: int) -> dict[str, Any] | None:
    return db.query_one("SELECT id, result_json, image_path FROM analyses WHERE id = ? AND equipment_id = ?", (aid, eid))


def verdicts(eid: str, aid: int) -> dict[str, dict[str, Any]]:
    """Последнее решение инженера по каждому узлу из находок анализа aid."""
    out: dict[str, dict[str, Any]] = {}
    for e in db.query("SELECT * FROM events WHERE equipment_id = ? AND component_id IS NOT NULL ORDER BY id", (eid,)):
        data = db.loads(e["data_json"], {})
        if data.get("analysis_id") == aid and data.get("verdict") in VERDICTS:
            out[e["component_id"]] = {"verdict": data["verdict"], "type": e["type"], "note": e["note"],
                                      "author": e["author"], "created_at": e["created_at"]}
    return out


# ---------------------------------------------------------------- журнал работ

def add_event(eid: str, comp: dict[str, Any] | None, etype: str, note: str, author: str,
              data: dict[str, Any] | None = None) -> int:
    """Запись в историю; для работ по узлу — сразу обновляет даты и счётчики узла (ресурс пересчитается)."""
    today = date.today().isoformat()
    cid = comp["id"] if comp else None
    with db.tx() as c:
        cur = c.execute("INSERT INTO events (equipment_id, component_id, type, note, author, created_at, data_json) "
                        "VALUES (?,?,?,?,?,?,?)", (eid, cid, etype, note, author, db.now_iso(), db.dumps(data or {})))
        if comp:
            if etype in ("checked", "defect"):
                c.execute("UPDATE components SET last_inspection_at = ? WHERE id = ?", (today, cid))
            if etype == "maintenance":
                eq = dict(c.execute("SELECT * FROM equipment WHERE id = ?", (eid,)).fetchone())
                hours = comp["operating_hours"]
                if hours is not None:
                    hours += runtime.running_hours(eq, comp)
                c.execute("UPDATE components SET last_service_at = ?, last_inspection_at = ?, last_service_hours = ? "
                          "WHERE id = ?", (today, today, hours, cid))
            if etype == "replacement":
                c.execute(
                    """UPDATE components SET installed_at = ?, last_service_at = ?, last_inspection_at = ?,
                           operating_hours = CASE WHEN operating_hours IS NULL THEN NULL ELSE 0 END,
                           cycles = CASE WHEN cycles IS NULL THEN NULL ELSE 0 END,
                           last_service_hours = 0, hours_updated_at = ? WHERE id = ?""",
                    (today, today, today, db.now_iso(), cid))
        return int(cur.lastrowid)


def set_normative(eid: str, cid: str, values: tuple[Any, Any, Any], source: str, author: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE components SET normative_life_years = ?, normative_hours = ?, maintenance_interval_hours = ?, "
                  "normative_source = ? WHERE id = ?", (*values, source, cid))
        c.execute("INSERT INTO events (equipment_id, component_id, type, note, author, created_at) VALUES (?,?,?,?,?,?)",
                  (eid, cid, "configuration", "Нормативы: " + source, author, db.now_iso()))


# ---------------------------------------------------------------- осмотры

def inspection_session(eid: str, sid: int, uid: int, writable: bool = False) -> dict:
    row = db.query_one("SELECT * FROM inspection_sessions WHERE id=? AND equipment_id=?", (sid, eid))
    if not row:
        raise HTTPException(404, "Осмотр не найден.")
    if row["user_id"] != uid:
        raise HTTPException(403, "Этот черновик принадлежит другому инженеру.")
    if writable and row["status"] != "draft":
        raise HTTPException(409, "Осмотр уже завершен. Начните новый осмотр.")
    return row


def active_inspection(eid: str, uid: int) -> dict | None:
    return db.query_one("SELECT * FROM inspection_sessions WHERE equipment_id=? AND user_id=? AND status='draft'", (eid, uid))


def start_inspection(eid: str, uid: int) -> dict:
    with db.tx() as c:
        row = active_inspection(eid, uid)
        if row:
            return row
        sid = c.execute("INSERT INTO inspection_sessions(equipment_id,user_id,created_at) VALUES (?,?,?)",
                        (eid, uid, db.now_iso())).lastrowid
        return inspection_session(eid, sid, uid)


def session_shots(sid: int) -> list[dict]:
    return db.query("SELECT a.* FROM analyses a JOIN inspection_shots s ON s.analysis_id=a.id "
                    "WHERE s.session_id=? ORDER BY a.id", (sid,))


def close_inspection(eid: str, sid: int, uid: int, note: str, author: str, snapshot: dict) -> int:
    with db.tx() as c:
        row = inspection_session(eid, sid, uid)
        if row["status"] == "closed":
            return row["event_id"]
        if [s['id'] for s in session_shots(sid)] != snapshot.get('analysis_ids'):
            raise HTTPException(409, 'Добавлен новый кадр. Обновите осмотр перед завершением.')
        event_id = c.execute("INSERT INTO events (equipment_id,type,note,author,created_at,data_json) VALUES (?,?,?,?,?,?)",
                             (eid, 'inspection', note, author, db.now_iso(), db.dumps(snapshot))).lastrowid
        c.execute("UPDATE inspection_sessions SET status='closed',event_id=? WHERE id=?", (event_id, sid))
        return event_id


def assert_reviewable(eid: str, aid: int, cid: str, uid: int) -> None:
    row = analysis_of(eid, aid)
    if not row or not any(i.get("component_id") == cid for i in db.loads(row["result_json"], {}).get("items", [])):
        raise HTTPException(400, "Находка не относится к этому узлу и анализу.")
    session = db.query_one("SELECT s.status,s.user_id FROM inspection_sessions s JOIN inspection_shots p "
                           "ON p.session_id=s.id WHERE p.analysis_id=?", (aid,))
    if session and session["status"] == "closed":
        raise HTTPException(409, "Осмотр уже завершен. Его решения сохранены в отчете.")
    if session and session['user_id'] != uid:
        raise HTTPException(403, 'Решения этого черновика сохраняет его исполнитель.')

def save_inspection(eid: str, note: str, author: str, snapshot: dict[str, Any]) -> int:
    with db.tx() as c:
        cur = c.execute("INSERT INTO events (equipment_id, type, note, author, created_at, data_json) VALUES (?,?,?,?,?,?)",
                        (eid, "inspection", note, author, db.now_iso(), db.dumps(snapshot)))
        return int(cur.lastrowid)


def inspection(eid: str, iid: int) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM events WHERE id = ? AND equipment_id = ? AND type = 'inspection'", (iid, eid))


def approvals(eid: str, iid: int) -> list[dict[str, Any]]:
    rows = db.query("SELECT author, created_at, data_json FROM events WHERE equipment_id = ? AND type = 'approval' "
                    "ORDER BY id", (eid,))
    return [r for r in rows if db.loads(r["data_json"], {}).get("inspection_id") == iid]


def approve(eid: str, iid: int, author: str) -> None:
    with db.tx() as c:
        c.execute("INSERT INTO events (equipment_id, type, note, author, created_at, data_json) VALUES (?,?,?,?,?,?)",
                  (eid, "approval", f"Подтвержден осмотр №{iid}", author, db.now_iso(), db.dumps({"inspection_id": iid})))
