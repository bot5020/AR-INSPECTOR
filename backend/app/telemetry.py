"""Пороговые правила из описаний организаторов, без использования столбцов «Диагноз»."""
import json
from functools import lru_cache
from pathlib import Path

from . import config, db

FOCUS = "PUMP-001"
RULE_SOURCE = "Описание БД организаторов: вибрация / температура"
CHANNELS = [
    ("rpm", "Частота вращения", "об/мин", None, None, "PUMP-001-MOTOR", None),
    ("rms", "Общая виброскорость", "мм/с", None, None, "PUMP-001-BRG", None),
    ("v1", "Амплитуда 1x", "мм/с", 2.8, 4.5, "PUMP-001-PUMP", "Признак дисбаланса рабочего колеса; проверить вибрацию и состояние ротора"),
    ("v2", "Амплитуда 2x", "мм/с", 1.8, 3.0, "PUMP-001-COUP", "Признак нарушения центровки; проверить соосность и крепления"),
    ("hf", "Высокочастотный шум", "g", 2.5, None, "PUMP-001-BRG", "Возможны износ подшипника или кавитация; нужны дополнительные измерения"),
    ("bearing_temp", "Температура подшипника насоса", "°C", 75, 90, "PUMP-001-BRG", "Превышена температура подшипника; проверить нагрев и смазку"),
    ("winding_temp", "Температура обмоток двигателя", "°C", 90, 110, "PUMP-001-MOTOR-WND", "Превышена температура обмоток; проверить охлаждение и нагрузку"),
]


@lru_cache(maxsize=1)
def dataset() -> dict:
    path = config.ROOT / "demo" / "telemetry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("rows"):
        raise ValueError("В предоставленном наборе нет измерений.")
    return data


def parameters(row: dict) -> list[dict]:
    result = []
    for key, name, unit, warn, alarm, cid, recommendation in CHANNELS:
        value = row.get(key)
        level = None if value is None else 2 if alarm is not None and value > alarm else 1 if warn is not None and value > warn else 0
        threshold = alarm if level == 2 else warn if level == 1 else None
        text = "нет измерения" if level is None else f"выше {'аварийной' if level == 2 else 'предупредительной'} уставки {threshold}" if level else "в пределах заданных уставок" if warn is not None else "справочное значение, уставка не задана"
        result.append(dict(id="ORG-" + key, name=name, unit=unit, value=value, nominal=None,
                           warn_low=None, warn_high=warn, alarm_low=None, alarm_high=alarm,
                           component_id=cid, status={None: "unknown", 0: "normal", 1: "warning", 2: "alarm"}[level],
                           level=level, status_text=text, limit=threshold, source=RULE_SOURCE,
                           measured_at=row["timestamp"], recommendation=recommendation if level else None))
    return result


def current(eid: str) -> tuple[list[dict] | None, dict | None]:
    state = db.query_one("SELECT row_index FROM scenario_state WHERE equipment_id=?", (eid,))
    if not state:
        return None, None
    data = dataset()
    index = min(max(state["row_index"], 0), len(data["rows"]) - 1)
    row = data["rows"][index]
    return parameters(row), dict(mode="replay", index=index, count=len(data["rows"]),
                                timestamp=row["timestamp"], source=data["source"], notice=data["notice"],
                                normative_note="Сроки службы и счетчики в карточке: допущения команды. Измерения: примеры организаторов.")


def select(eid: str, index: int) -> None:
    if eid != FOCUS or not 0 <= index < len(dataset()["rows"]):
        raise ValueError("Выберите существующую запись насосного сценария.")
    with db.tx() as c:
        c.execute("UPDATE scenario_state SET row_index=? WHERE equipment_id=?", (index, eid))


def presets() -> list[dict]:
    rows = dataset()["rows"]
    choices = [("Норма", lambda r: r["v1"] < 1 and r["v2"] < 1 and r["hf"] < 1 and r["bearing_temp"] < 60 and r["winding_temp"] < 80),
               ("Вибрация повышена, температура в норме", lambda r: r["v1"] > 2.8 and r["bearing_temp"] < 75),
               ("Расцентровка", lambda r: r["v2"] > 3),
               ("Нагрев подшипника", lambda r: r["bearing_temp"] > 90),
               ("Нагрев обмоток", lambda r: r["winding_temp"] > 110)]
    # Пресет без подходящей записи в наборе пропускаем, а не роняем запрос (StopIteration → 500).
    found = [(label, next((i for i, row in enumerate(rows) if test(row)), None)) for label, test in choices]
    return [{"label": label, "index": index} for label, index in found if index is not None]
