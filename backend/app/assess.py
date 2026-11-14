"""Сборка цифровой карточки: ресурс + ТО + параметры + визуальный осмотр → итоговый статус узла.

Уровни: 0 — норма, 1 — требует внимания, 2 — требует действий, None — нет данных.
Итог визуального анализа хранится отдельно и никогда не трактуется как подтверждение исправности.
"""
from __future__ import annotations

import math
import time
import zlib
from datetime import date
from typing import Any

from . import db, defects, runtime, telemetry
from .resource import assess_maintenance, assess_resource

VISUAL_LABELS = {
    "ok": "Видимых проблем не обнаружено",
    "check": "Рекомендуется проверка",
    "defect": "Обнаружены признаки возможного дефекта",
}
VISUAL_LEVEL = {"ok": 0, "check": 1, "defect": 2}
RESOURCE_LEVEL = {"ok": 0, "approaching": 1, "exhausted": 2}
MAINT_LEVEL = {"ok": 0, "soon": 1, "overdue": 2}
LEVEL_LABELS = {0: "Норма", 1: "Требует внимания", 2: "Требует действий", None: "Нет данных"}
CLEARING_EVENTS = {"checked", "maintenance", "replacement", "resolved"}


def _fmt(v: float | None) -> str:
    if v is None:
        return "—"
    s = f"{v:.2f}".rstrip("0").rstrip(".") if abs(v) < 10 else f"{v:.1f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


# ---------------------------------------------------------------- параметры

def live_value(p: dict[str, Any]) -> float | None:
    """Имитация телеметрии: базовое значение из БД + медленное колебание."""
    v = p.get("value")
    if v is None or not p.get("noise"):
        return v
    phase = (zlib.crc32(p["id"].encode()) % 1000) / 159.0
    t = time.time()
    wobble = math.sin(t / 23.0 + phase) * 0.7 + math.sin(t / 7.3 + phase * 2) * 0.3
    return round(v + wobble * p["noise"], 2)


def assess_parameter(p: dict[str, Any]) -> dict[str, Any]:
    v = live_value(p)
    status, limit, text = ("unknown", None, "нет измерения") if v is None else ("normal", None, "в норме")
    if v is not None:
        if p.get("alarm_high") is not None and v >= p["alarm_high"]:
            status, limit, text = "alarm", p["alarm_high"], f"выше аварийной уставки {_fmt(p['alarm_high'])}"
        elif p.get("alarm_low") is not None and v <= p["alarm_low"]:
            status, limit, text = "alarm", p["alarm_low"], f"ниже аварийной уставки {_fmt(p['alarm_low'])}"
        elif p.get("warn_high") is not None and v >= p["warn_high"]:
            status, limit, text = "warning", p["warn_high"], f"выше уставки предупреждения {_fmt(p['warn_high'])}"
        elif p.get("warn_low") is not None and v <= p["warn_low"]:
            status, limit, text = "warning", p["warn_low"], f"ниже уставки предупреждения {_fmt(p['warn_low'])}"
    return {
        "id": p["id"], "name": p["name"], "unit": p.get("unit"), "value": v, "nominal": p.get("nominal"),
        "warn_low": p.get("warn_low"), "warn_high": p.get("warn_high"),
        "alarm_low": p.get("alarm_low"), "alarm_high": p.get("alarm_high"),
        "component_id": p.get("component_id"), "status": status, "limit": limit, "status_text": text,
        "level": {"normal": 0, "warning": 1, "alarm": 2, "unknown": None}[status],
        "source": "Демонстрационная имитация", "measured_at": p.get("updated_at"),
    }


# ---------------------------------------------------------------- карточка

def _component_events(events: list[dict[str, Any]], comp_id: str) -> list[dict[str, Any]]:
    return [e for e in events if e["component_id"] == comp_id]


def _open_defects(comp_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Замечания, после которых не было обслуживания/замены/закрытия."""
    open_: dict[str, dict[str, Any]] = {}
    for e in sorted(comp_events, key=lambda e: (e["created_at"], e["id"])):
        if e["type"] == "defect":
            # Повторное подтверждение не превращает одно замечание в несколько предупреждений.
            open_[" ".join(e["note"].split()).casefold()] = e
        elif e["type"] in ("maintenance", "replacement", "resolved"):
            open_.clear()
    return list(open_.values())


def _last_clearing(comp_events: list[dict[str, Any]]) -> str | None:
    times = [e["created_at"] for e in comp_events if e["type"] in CLEARING_EVENTS]
    return max(times) if times else None


def _assess_component(c: dict[str, Any], eq: dict[str, Any], params: list[dict[str, Any]],
                      events: list[dict[str, Any]], visual: dict[str, Any] | None, today: date) -> dict[str, Any]:
    if c.get("visual_access") == "internal":
        visual = None
    res = assess_resource(c, eq.get("hours_per_day"), today)
    mnt = assess_maintenance(c, today)
    comp_events = _component_events(events, c["id"])
    defects = _open_defects(comp_events)
    reasons: list[dict[str, Any]] = []
    recs: list[str] = []
    name = c["name"]

    # Ресурс
    if res["status"] == "exhausted":
        reasons.append({"source": "resource", "level": 2,
                        "text": f"Нормативный ресурс исчерпан ({res['limiting_factor'].lower()}: "
                                f"{int(round(res['used_fraction'] * 100))}%)"})
        recs.append(f"Запланировать замену: нормативный ресурс исчерпан ({res['limiting_factor'].lower()}).")
        reasons[-1]['action'] = recs[-1]
    elif res["status"] == "approaching":
        reasons.append({"source": "resource", "level": 1,
                        "text": f"Израсходовано {int(round(res['used_fraction'] * 100))}% ресурса "
                                f"({res['limiting_factor'].lower()}), осталось {res['remaining_text']}"})
        recs.append(f"Включить в ближайший план замены: осталось {res['remaining_text']}.")
        reasons[-1]['action'] = recs[-1]
    elif res["status"] == "unknown" and (c.get("normative_life_years") or c.get("normative_hours")
                                         or c.get("normative_cycles")):
        recs.append("Внести в карточку дату установки и наработку — без них остаточный ресурс не оценить.")

    # Обслуживание
    if mnt:
        if mnt["status"] == "overdue":
            detail = f"на {-mnt['days_left']} дн." if mnt['days_left'] is not None and mnt['days_left'] <= 0 else f"на {-(mnt.get('hours_left') or 0):.1f} ч"
            reasons.append({"source": "maintenance", "level": 2,
                            "text": f"Просрочено: {mnt['name'].lower()} ({detail})"})
            recs.append(f"Выполнить: {mnt['name'].lower()}; достигнут интервал обслуживания.")
            reasons[-1]['action'] = recs[-1]
        elif mnt["status"] == "soon":
            detail = f"через {mnt['days_left']} дн." if mnt['days_left'] is not None and mnt['days_left'] <= 30 else f"через {mnt.get('hours_left', 0):.1f} ч"
            reasons.append({"source": "maintenance", "level": 1,
                            "text": f"{mnt['name']}: {detail}"})
            recs.append(f"Запланировать: {mnt['name'].lower()} ({detail}).")
            reasons[-1]['action'] = recs[-1]

    # Параметры
    comp_params = [p for p in params if p.get("component_id") == c["id"]]
    for p in comp_params:
        if (p["level"] or 0) > 0:
            reasons.append({"source": "parameter", "level": p["level"],
                            "text": f"{p['name']}: {_fmt(p['value'])} {p['unit'] or ''} — {p['status_text']}"})
            if p.get("recommendation"):
                recs.append(p["recommendation"])
            elif p["level"] == 2:
                recs.append(f"Проверить «{p['name'].lower()}» по месту; при подтверждении — вывести в ремонт.")
            else:
                recs.append(f"Усилить контроль параметра «{p['name'].lower()}», выяснить причину отклонения.")
            reasons[-1]['action'] = recs[-1]

    # Зарегистрированные замечания
    for d in defects:
        reasons.append({"source": "defect", "level": 2, "text": f"Открытое замечание: {d['note']}",
                        "action": "Проверить зарегистрированное замечание и зафиксировать результат."})
    if defects:
        recs.append("Устранить зарегистрированное замечание и отметить выполнение в системе.")

    # Визуальный анализ (только если после него узел не проверяли/не обслуживали)
    vis_out = None
    if visual:
        cleared = _last_clearing(comp_events)
        superseded = bool(cleared and cleared > visual["created_at"])
        vis_out = {
            "category": visual["category"], "label": VISUAL_LABELS.get(visual["category"], visual["category"]),
            "signs": db.loads(visual.get("signs_json"), []), "reason": visual.get("reason"),
            "recommendation": visual.get("recommendation"), "confidence": visual.get("confidence"),
            "created_at": visual["created_at"], "analysis_id": visual.get("analysis_id"),
            "superseded": superseded,
            "latest_frame": visual.get("latest_frame", False),
        }
        if not superseded and visual["category"] in ("check", "defect"):
            reasons.append({"source": "visual", "level": VISUAL_LEVEL[visual["category"]],
                            "text": f"Визуально: {visual.get('reason') or vis_out['label']}",
                            "action": visual.get('recommendation') or 'Проверить находку на снимке и зафиксировать решение.'})
            if visual.get("recommendation"):
                recs.insert(0, visual["recommendation"])

    own_level: int | None
    if reasons:
        own_level = max(r["level"] for r in reasons)
    elif res["status"] != "unknown" or (mnt and mnt["status"] != "unknown") or any(p["level"] is not None for p in comp_params) or (vis_out and not vis_out["superseded"]):
        own_level = 0
    else:
        own_level = None

    reasons.sort(key=lambda r: -r["level"])
    return {
        "id": c["id"], "name": name, "kind": c["kind"], "parent_id": c.get("parent_id"),
        "detect_class": c.get("detect_class"), "order": c["order_idx"],
        "specs": db.loads(c.get("specs_json"), {}), "inspect_for": db.loads(c.get("inspect_for_json"), []),
        "visual_access": c.get("visual_access", "external"),
        "normative_source": c.get("normative_source"),
        "normative_hours": c.get("normative_hours"),
        "normative_life_years": c.get("normative_life_years"),
        "visual_coverage": "Внутренний узел: камерой не осматривается" if c.get("visual_access") == "internal" else
                           "Осмотрен в последнем кадре" if vis_out and not vis_out["superseded"] and vis_out["latest_frame"] else "Не осмотрен в последнем кадре",
        "schema": db.loads(c.get("schema_json"), None),
        "installed_at": c.get("installed_at"), "last_service_at": c.get("last_service_at"),
        "last_inspection_at": c.get("last_inspection_at"),
        "operating_hours": c.get("operating_hours"), "cycles": c.get("cycles"),
        "resource": res, "maintenance": mnt, "parameters": comp_params, "open_defects": defects,
        "visual": vis_out, "reasons": reasons, "recommendations": list(dict.fromkeys(recs)),
        "level": own_level, "history": sorted(comp_events, key=lambda e: e["created_at"], reverse=True)[:20],
    }


def _max_level(levels: list[int | None]) -> int | None:
    known = [lv for lv in levels if lv is not None]
    return max(known) if known else None


def build_card(equipment_id: str, today: date | None = None) -> dict[str, Any] | None:
    today = today or date.today()
    eq = db.query_one("SELECT * FROM equipment WHERE id = ?", (equipment_id,))
    if not eq:
        return None
    comps = db.query("SELECT * FROM components WHERE equipment_id = ? ORDER BY order_idx", (equipment_id,))
    params, measurement = telemetry.current(equipment_id)
    if params is None:
        params = [assess_parameter(p) for p in
                  db.query("SELECT * FROM parameters WHERE equipment_id = ? ORDER BY rowid", (equipment_id,))]
    for c in comps:
        if c.get("operating_hours") is not None:
            c["operating_hours"] += runtime.running_hours(eq, c)
    events = db.query(
        """SELECT e.*, c.name AS component_name FROM events e LEFT JOIN components c ON c.id = e.component_id
           WHERE e.equipment_id = ? ORDER BY e.created_at DESC, e.id DESC""", (equipment_id,))
    for event in events:
        event.pop("data_json", None)
    last_analysis = db.query_one("SELECT id,created_at,model FROM analyses WHERE equipment_id=? ORDER BY id DESC LIMIT 1", (equipment_id,))
    visual_rows = db.query(
        """SELECT v.* FROM visual_results v
           JOIN (SELECT component_id, MAX(id) AS mid FROM visual_results WHERE equipment_id = ? GROUP BY component_id) m
             ON m.mid = v.id""", (equipment_id,))
    visual_by_comp = {v["component_id"]: {**v, "latest_frame": bool(last_analysis and v["analysis_id"] == last_analysis["id"])} for v in visual_rows if v["component_id"]}

    assessed = {c["id"]: _assess_component(c, eq, params, events, visual_by_comp.get(c["id"]), today) for c in comps}

    # Комбинированные диагнозы «камера + датчики»: совпали правило по измерениям и признак на снимке.
    live_visual = {cid: a["visual"] for cid, a in assessed.items() if a["visual"] and not a["visual"]["superseded"]}
    combined = defects.combined(params, live_visual, {cid: a["name"] for cid, a in assessed.items()})
    for d in combined:
        a = assessed.get(d["component_id"])
        if not a:
            continue
        basis = "; ".join(d["sensors"] + d["visual"])
        a["reasons"].insert(0, {"source": "combined", "level": d["level"],
                                "text": f"{d['diagnosis']}: {basis}", "action": d['action']})
        a["recommendations"] = list(dict.fromkeys([d["action"], *a["recommendations"]]))
        a["level"] = _max_level([a["level"], d["level"]])

    # Свести статусы комплектующих в узлы.
    for a in assessed.values():
        a["children"] = []
    for a in assessed.values():
        if a["parent_id"] and a["parent_id"] in assessed:
            assessed[a["parent_id"]]["children"].append(a["id"])
    for a in assessed.values():
        kids = [assessed[k] for k in a["children"]]
        a["rollup_level"] = _max_level([a["level"]] + [k["level"] for k in kids])
        a["rollup_reasons"] = a["reasons"] + [
            {**r, "text": f"{k['name']}: {r['text']}", "component_id": k["id"]} for k in kids for r in k["reasons"]
        ]
        a["rollup_reasons"].sort(key=lambda r: -r["level"])
        a["status_label"] = LEVEL_LABELS[a["rollup_level"]]

    components = sorted(assessed.values(), key=lambda a: a["order"])
    top = [a for a in components if not a["parent_id"]]
    overall = _max_level([a["rollup_level"] for a in top])
    counts = {str(lv): sum(1 for a in components if a["level"] == lv) for lv in (0, 1, 2)}
    counts["unknown"] = sum(1 for a in components if a["level"] is None)

    return {
        "equipment": {
            "id": eq["id"], "name": eq["name"], "type": eq["type"], "model": eq["model"],
            "manufacturer": eq["manufacturer"], "serial_number": eq["serial_number"],
            "inventory_number": eq["inventory_number"], "location": eq["location"],
            "commissioned_at": eq["commissioned_at"], "qr_payload": eq["qr_payload"],
            "hours_per_day": eq["hours_per_day"], "description": eq["description"],
            "specs": db.loads(eq["specs_json"], {}), "schema": db.loads(eq["schema_json"], {}),
            "data_origin": eq.get("data_origin"), "run_state": eq.get("run_state", "stopped"),
        },
        "level": overall, "status_label": LEVEL_LABELS[overall], "counts": counts,
        "by_source": by_source(components, params, measurement, last_analysis),
        "combined": combined,
        "components": components, "parameters": params,
        "measurement": measurement,
        "coverage": {"external": sum(c.get("visual_access") != "internal" for c in components),
                     "inspected": sum(c.get("visual_access") != "internal" and bool(c.get("visual") and not c["visual"]["superseded"] and c["visual"]["latest_frame"]) for c in components)},
        "guidance": guidance(components),
        "history": events[:60], "last_analysis": last_analysis,
        "disclaimer": "ИИ-анализ — инструмент предварительного осмотра. Отсутствие видимых дефектов не подтверждает "
                      "исправность: внутренние неисправности по изображению не определяются.",
    }


def _source(key: str, label: str, level: int | None, reasons: list[str], note: str) -> dict[str, Any]:
    return {"key": key, "label": label, "level": level, "status_label": LEVEL_LABELS[level],
            "count": len(reasons), "top": reasons[:3], "note": note}


def by_source(components: list[dict[str, Any]], params: list[dict[str, Any]],
              measurement: dict[str, Any] | None, last_analysis: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Общий статус по отдельности для каждого источника данных.

    Общий уровень — максимум по всем источникам, поэтому один исчерпанный ресурс красит весь агрегат
    и изменение измерений сверху не видно. Здесь у каждого источника свой цвет и свои причины."""
    def reasons_of(*sources: str) -> list[dict[str, Any]]:
        found = [(r, c) for c in components for r in c["reasons"] if r["source"] in sources]
        found.sort(key=lambda rc: -rc[0]["level"])
        return [{**r, "text": f"{c['name']}: {r['text']}"} for r, c in found]

    # Ресурс и ТО — по учётным данным.
    res = reasons_of("resource", "maintenance")
    known = any(c["resource"]["status"] != "unknown" or (c["maintenance"] and c["maintenance"]["status"] != "unknown")
                for c in components)
    res_level = max((r["level"] for r in res), default=0 if known else None)

    # Измерения — по всем параметрам, в том числе не привязанным к узлу.
    levels = [p["level"] for p in params if p["level"] is not None]
    bad = sorted((p for p in params if (p["level"] or 0) > 0), key=lambda p: -p["level"])
    meas_level = max(levels) if levels else None
    meas_note = (f"Запись {measurement['index'] + 1} из {measurement['count']} · {measurement['timestamp']}"
                 if measurement else "Демонстрационная имитация показаний" if params else "Датчики не подключены")

    # Визуальный осмотр — последние оценки ИИ, которые инженер ещё не перекрыл своей отметкой.
    visuals = [c["visual"] for c in components if c["visual"] and not c["visual"]["superseded"]]
    vis = reasons_of("visual")
    vis_level = max((VISUAL_LEVEL[v["category"]] for v in visuals), default=None)
    vis_note = (f"Последний кадр ИИ: {last_analysis['created_at'][:10]}" if last_analysis
                else "ИИ-осмотр ещё не выполнялся")

    defects = reasons_of("defect")
    both = reasons_of("combined")
    return ([_source("combined", "Камера + датчики", max(r["level"] for r in both), [r["text"] for r in both],
                     "Совпали правило по измерениям и признак на снимке")] if both else []) + [
        _source("resource", "Ресурс и ТО", res_level, [r["text"] for r in res], "По учётным данным карточки"),
        _source("measurements", "Измерения", meas_level,
                [f"{p['name']}: {_fmt(p['value'])} {p['unit'] or ''} — {p['status_text']}" for p in bad], meas_note),
        _source("visual", "Визуальный осмотр", vis_level, [r["text"] for r in vis], vis_note),
        _source("defects", "Замечания", 2 if defects else 0, [r["text"] for r in defects], "Открытые замечания инженеров"),
    ]


def _nonvisual_level(comp: dict[str, Any]) -> int | None:
    levels = [r["level"] for r in comp["rollup_reasons"] if r["source"] != "visual"]
    if levels:
        return max(levels)
    return 0 if comp["rollup_level"] is not None else None


def enrich_item(item: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    """Совмещает визуальный результат ИИ с учётными данными карточки (ресурс, ТО, параметры)."""
    comp = next((c for c in card["components"] if c["id"] == item["component_id"]), None)
    vis_level = VISUAL_LEVEL[item["category"]]
    item["visual_label"] = VISUAL_LABELS[item["category"]]
    if comp:
        nv = _nonvisual_level(comp)
        item["card_level"] = nv
        item["level"] = max(vis_level, nv or 0)
        item["card_reasons"] = [r["text"] for r in comp["rollup_reasons"] if r["source"] != "visual"][:3]
        res = comp["resource"]
        item["resource"] = {"status": res["status"], "used_fraction": res["used_fraction"],
                            "remaining_text": res["remaining_text"], "normative": res["normative"]}
        item["recommendations"] = comp["recommendations"][:3]
    else:
        item["card_level"], item["level"], item["card_reasons"], item["resource"] = None, vis_level, [], None
        item["recommendations"] = [item["recommendation"]] if item.get("recommendation") else []
    item["status_label"] = LEVEL_LABELS[item["level"]]
    return item


def not_covered(card: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Внешние узлы карточки, которых нет на этом кадре: их надо снять следующим снимком."""
    seen = {it.get("component_id") for it in items}
    rest = [c for c in card["components"] if c.get("visual_access") != "internal" and c["id"] not in seen]
    rest.sort(key=lambda c: -(c["rollup_level"] or 0))
    return [{"id": c["id"], "name": c["name"], "level": c["rollup_level"],
             "look_for": c["inspect_for"][:2]} for c in rest]


def guidance(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Куда навести камеру в первую очередь: узлы с наибольшим риском по данным карточки."""
    by_id = {c["id"]: c for c in components}
    items = []
    for c in components:
        if c["parent_id"]:
            continue
        lvl = c["rollup_level"]
        if not lvl:
            continue
        used = max([c["resource"].get("used_fraction") or 0] +
                   [by_id[k]["resource"].get("used_fraction") or 0 for k in c["children"]])
        score = lvl * 100 + len([r for r in c["rollup_reasons"] if r["level"] == lvl]) * 5 + used * 10
        top_reason = c["rollup_reasons"][0]["text"] if c["rollup_reasons"] else ""
        hidden = [by_id[k] for k in c["children"] if by_id[k]["level"] and by_id[k]["level"] > 0]
        look_for: list[str] = list(c["inspect_for"])
        for h in hidden:
            look_for = h["inspect_for"] + look_for
        visible = bool(c["detect_class"])
        hint = (f"Наведите камеру на «{c['name']}»" if visible else f"Осмотрите «{c['name']}» (не распознаётся камерой)")
        items.append({
            "component_id": c["id"], "name": c["name"], "level": lvl, "score": round(score, 1),
            "hint": hint, "reason": top_reason, "look_for": look_for[:4], "visible": visible,
        })
    items.sort(key=lambda x: -x["score"])
    return items[:5]


def equipment_list() -> list[dict[str, Any]]:
    out = []
    for eq in db.query("SELECT id FROM equipment ORDER BY id"):
        card = build_card(eq["id"])
        if card:
            e = card["equipment"]
            out.append({"id": e["id"], "name": e["name"], "type": e["type"], "model": e["model"],
                        "location": e["location"], "serial_number": e["serial_number"],
                        "level": card["level"], "status_label": card["status_label"], "counts": card["counts"]})
    return out
