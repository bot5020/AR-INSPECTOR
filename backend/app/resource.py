"""Расчёт эксплуатационного ресурса и сроков обслуживания.

Разделяются три разные вещи (п. 4.3 задания):
  * нормативный срок службы — из документации (лет / моточасов / циклов);
  * расчётный остаточный ресурс — оценка по учётным данным (дата установки, наработка, циклы);
  * фактическое состояние — результат осмотра/ИИ-анализа (здесь не считается, см. assess.py).
Если данных не хватает, это явно возвращается в limitations.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from . import config


def _parse(d: str | None) -> date | None:
    if not d:
        return None
    try:
        return date.fromisoformat(d[:10])
    except ValueError:
        return None


def _fmt_num(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def _fmt_years(days: float) -> str:
    years = days / 365.25
    if abs(years) >= 1:
        return f"{years:.1f}".replace(".", ",") + " г."
    return f"{days / 30.4:.0f} мес."


def _level_for_used(used: float) -> str:
    if used >= 1.0:
        return "exhausted"
    if used >= config.RESOURCE_WARN_FRACTION:
        return "approaching"
    return "ok"


def assess_resource(c: dict[str, Any], hours_per_day: float | None, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    installed = _parse(c.get("installed_at"))
    factors: list[dict[str, Any]] = []
    limitations: list[str] = []
    normative_parts: list[str] = []

    age_days = (today - installed).days if installed else None

    life_years = c.get("normative_life_years")
    if life_years:
        normative_parts.append(f"{life_years:g} лет")
        if installed:
            life_days = life_years * 365.25
            factors.append({
                "kind": "calendar", "label": "Календарный срок",
                "used": age_days / life_days,
                "actual": _fmt_years(age_days), "normative": f"{life_years:g} лет",
                "remaining_days": life_days - age_days,
            })
        else:
            limitations.append("Не указана дата установки — календарный ресурс не рассчитан.")

    norm_hours = c.get("normative_hours")
    if norm_hours:
        normative_parts.append(f"{_fmt_num(norm_hours)} ч")
        hours = c.get("operating_hours")
        if hours is not None:
            remaining_h = norm_hours - hours
            rate = hours_per_day
            if not rate and age_days:
                rate = hours / max(age_days, 1)
            factors.append({
                "kind": "hours", "label": "Наработка",
                "used": hours / norm_hours,
                "actual": f"{_fmt_num(hours)} ч", "normative": f"{_fmt_num(norm_hours)} ч",
                "remaining_days": remaining_h / rate if rate else None,
                "remaining_text": f"{_fmt_num(max(remaining_h, 0))} ч",
            })
            if not rate:
                limitations.append("Нет данных о среднесуточной наработке — дата исчерпания по моточасам не рассчитана.")
        else:
            limitations.append("Нет данных о наработке — оценка по моточасам невозможна.")

    norm_cycles = c.get("normative_cycles")
    if norm_cycles:
        normative_parts.append(f"{_fmt_num(norm_cycles)} циклов")
        cycles = c.get("cycles")
        if cycles is not None:
            per_day = cycles / age_days if age_days else None
            factors.append({
                "kind": "cycles", "label": "Рабочие циклы",
                "used": cycles / norm_cycles,
                "actual": f"{_fmt_num(cycles)}", "normative": f"{_fmt_num(norm_cycles)}",
                "remaining_days": (norm_cycles - cycles) / per_day if per_day else None,
                "remaining_text": f"{_fmt_num(max(norm_cycles - cycles, 0))} циклов",
            })
        else:
            limitations.append("Нет данных о количестве циклов.")

    if not (life_years or norm_hours or norm_cycles):
        limitations.append("Нормативный срок службы в карточке не задан.")

    result: dict[str, Any] = {
        "normative": " / ".join(normative_parts) or None,
        "factors": factors,
        "installed_at": c.get("installed_at"),
        "age_text": _fmt_years(age_days) if age_days is not None else None,
    }

    if factors:
        limiting = max(factors, key=lambda f: f["used"])
        remaining = [f["remaining_days"] for f in factors if f.get("remaining_days") is not None]
        rem_days = min(remaining) if remaining else None
        used = limiting["used"]
        result.update({
            "status": _level_for_used(used),
            "used_fraction": round(used, 3),
            "remaining_fraction": round(max(0.0, 1 - used), 3),
            "limiting_factor": limiting["label"],
            "remaining_days": round(rem_days) if rem_days is not None else None,
            "estimated_end": (today + timedelta(days=rem_days)).isoformat() if rem_days is not None and rem_days > 0 else None,
            "remaining_text": (
                "ресурс исчерпан" if used >= 1 else
                limiting.get("remaining_text") or (_fmt_years(rem_days) if rem_days is not None else None)
            ),
        })
        if len(factors) == 1 and (norm_hours or norm_cycles) and limiting["kind"] == "calendar":
            limitations.append("Оценка только по календарю, без учёта фактической наработки.")
    else:
        result.update({
            "status": "unknown", "used_fraction": None, "remaining_fraction": None,
            "limiting_factor": None, "remaining_days": None, "estimated_end": None,
            "remaining_text": None,
        })
        limitations.append("Недостаточно данных для оценки остаточного ресурса.")

    result["limitations"] = limitations
    return result


def assess_maintenance(c: dict[str, Any], today: date | None = None) -> dict[str, Any] | None:
    today = today or date.today()
    interval = c.get("maintenance_interval_days")
    hour_interval = c.get("maintenance_interval_hours")
    if not interval and not hour_interval:
        return None
    last = _parse(c.get("last_service_at"))
    name = c.get("maintenance_name") or "Плановое обслуживание"
    due = last + timedelta(days=interval) if last and interval else None
    days_left = (due - today).days if due else None
    hours_left = None
    if hour_interval and c.get("operating_hours") is not None and c.get("last_service_hours") is not None:
        hours_left = hour_interval - (c["operating_hours"] - c["last_service_hours"])
    states = []
    if days_left is not None:
        states.append("overdue" if days_left <= 0 else "soon" if days_left <= config.MAINTENANCE_SOON_DAYS else "ok")
    if hours_left is not None:
        states.append("overdue" if hours_left <= 0 else "soon" if hours_left <= hour_interval * .15 else "ok")
    rank = {"ok": 0, "soon": 1, "overdue": 2}
    status = max(states, key=rank.get) if states else "unknown"
    return {"name": name, "interval_days": interval, "interval_hours": hour_interval,
            "last_at": last.isoformat() if last else None, "due_at": due.isoformat() if due else None,
            "days_left": days_left, "hours_left": hours_left, "status": status,
            "note": "Нет данных о последнем ТО или счетчике на момент ТО." if not states else None}
