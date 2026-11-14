"""Справочник видимых дефектов и комбинированные диагнозы «камера + датчики».

Классы — по документу организаторов «Типы дефектов промышленного оборудования» (yandex/) и признакам, которые
модель уже искала. Модель обязана выбирать признаки только из этого списка (enum в схеме ответа), приложение
показывает русские названия и отбрасывает всё, чего в справочнике нет.

Комбинированные правила — таблица соответствий, а не нейросеть: диагноз появляется, только когда сработало
правило по измерениям (уставки из описания организаторов) И модель отметила на снимке соответствующий признак
(или когда совпали два правила по измерениям). Каждое правило показывает, какие источники совпали.
"""
from __future__ import annotations

from typing import Any

# id: (название, что видно на снимке)
CLASSES: dict[str, tuple[str, str]] = {
    "corrosion": ("коррозия", "ржавчина, рыжие пятна, отслоение металла"),
    "paint_damage": ("отслоение краски", "краска шелушится или отслаивается"),
    "leak": ("течь, подтёки", "капли, подтёки, влажные или масляные пятна, лужа под агрегатом"),
    "seal_leak": ("течь уплотнения вала", "капли или подтёки у вала насоса, пятно под уплотнением"),
    "stem_packing_leak": ("течь сальника штока", "капли, белый налёт или влажное пятно под штоком арматуры"),
    "flange_leak": ("подтёки у фланца", "ржавые подтёки от болтов вниз, вздутие краски на стыке фланцев"),
    "salt_deposits": ("солевые отложения", "белый налёт или наросты"),
    "overheat_discoloration": ("потемнение от перегрева", "побежалость, бурый или синеватый цвет краски у щитов и клеммной коробки"),
    "burn_marks": ("нагар, оплавление", "копоть, обугливание, оплавленный пластик, следы дуги"),
    "cable_damage": ("повреждение кабеля", "трещины или оплавление изоляции, оголённые жилы"),
    "cooling_fins_damage": ("повреждение рёбер охлаждения", "погнутые, сломанные, забитые грязью рёбра"),
    "guard_missing": ("нет защитного кожуха", "кожух муфты или вентилятора отсутствует или повреждён"),
    "loose_fasteners": ("нет или ослаблен крепёж", "отсутствующие или ржавые ослабленные болты, гайки"),
    "grounding_missing": ("нет заземления", "нет или оборван жёлто-зелёный провод заземления"),
    "crack": ("трещина", "видимая трещина корпуса, трубы или изолятора"),
    "handwheel_damage": ("повреждение маховика", "трещины маховика, маховика нет"),
    "indicator_mismatch": ("указатель не совпадает", "положение штока не совпадает с указателем"),
    "dirt": ("загрязнение мешает осмотру", "грязь, пыль, наросты скрывают узел"),
}

SIGN_NAMES = {k: v[0] for k, v in CLASSES.items()}
_BY_NAME = {v[0]: k for k, v in CLASSES.items()}


def prompt_list() -> str:
    return "\n".join(f"   {k} — {name}: {look}" for k, (name, look) in CLASSES.items())


def normalize_signs(raw: list[Any]) -> list[str]:
    """Только классы справочника (по id или русскому названию), без повторов, в виде id."""
    out: list[str] = []
    for s in raw or []:
        key = str(s).strip().lower()
        key = key if key in CLASSES else _BY_NAME.get(key)
        if key and key not in out:
            out.append(key)
    return out


def sign_ids(signs: list[Any]) -> set[str]:
    """id классов из сохранённых признаков (новые анализы хранят русские названия, старые — что угодно)."""
    return set(normalize_signs(signs))


# Комбинированные правила. sensors — каналы telemetry (сработала хотя бы предупредительная уставка, все из списка);
# visual — классы на снимке (любой из списка) на узлах components; пустой visual — правило только по измерениям.
COMBINED: list[dict[str, Any]] = [
    dict(id="C1", sensors=["hf", "bearing_temp"], visual=[], components=[], target="PUMP-001-BRG", level=2,
         confidence="высокая", diagnosis="Подозрение на дефект подшипника насоса",
         action="Проверить виброспектр, нагрев и смазку; решение об останове и замене принять по регламенту."),
    dict(id="C2", sensors=["hf"], visual=["seal_leak", "leak"], components=["PUMP-001-SEAL", "PUMP-001-PUMP"], target="PUMP-001-SEAL",
         level=2, confidence="высокая", diagnosis="Возможный износ подшипника и уплотнения вала",
         action="Проверить источник течи и биение вала; необходимость замены подтвердить осмотром."),
    dict(id="C3", sensors=["bearing_temp"], visual=["overheat_discoloration", "burn_marks"], components=["PUMP-001-PUMP", "PUMP-001-BRG"],
         target="PUMP-001-BRG", level=2, confidence="высокая", diagnosis="Нагрев подшипникового узла и изменение цвета корпуса",
         action="Проверить смазку и посадку подшипника, снизить нагрузку до выяснения причины."),
    dict(id="C4", sensors=["v2"], visual=["loose_fasteners", "guard_missing"], components=["PUMP-001-COUP", "PUMP-001-MOTOR", "PUMP-001-PUMP"],
         target="PUMP-001-COUP", level=2, confidence="высокая", diagnosis="Возможная расцентровка или нарушение крепления",
         action="Проверить крепление, «мягкую лапу» и центровку валов; уточнить причину вибрации."),
    dict(id="C5", sensors=["winding_temp"], visual=["cooling_fins_damage", "dirt"], components=["PUMP-001-MOTOR"],
         target="PUMP-001-MOTOR", level=2, confidence="высокая", diagnosis="Нагрев двигателя: требуется проверка охлаждения",
         action="Очистить и восстановить рёбра и кожух вентилятора, контролировать температуру обмоток."),
    dict(id="C6", sensors=["winding_temp"], visual=["cable_damage", "burn_marks"], components=["PUMP-001-MOTOR"],
         target="PUMP-001-MOTOR", level=2, confidence="высокая", diagnosis="Нагрев двигателя и признаки повреждения кабеля или клемм",
         action="Обесточить, осмотреть клеммную коробку, заменить повреждённый кабель, измерить изоляцию."),
    dict(id="C7", sensors=["v1"], visual=["corrosion", "salt_deposits"], components=["PUMP-001-PUMP"], target="PUMP-001-PUMP",
         level=1, confidence="средняя", diagnosis="Повышенная вибрация и коррозия: причина не установлена",
         action="Проверить вибрацию и состояние проточной части при останове; уточнить причину отклонения."),
    dict(id="C8", sensors=["winding_temp", "v2"], visual=[], components=[], target="PUMP-001-MOTOR", level=1,
         confidence="средняя", diagnosis="Нагрев и вибрация двигателя: причина требует проверки",
         action="Проверить центровку и ток двигателя."),
]


def combined(params: list[dict[str, Any]], visuals: dict[str, dict[str, Any]], names: dict[str, str]) -> list[dict[str, Any]]:
    """Сработавшие комбинированные правила. params — измерения с id «ORG-<канал>» и level;
    visuals — последний несброшенный вывод модели по узлу (component_id → {category, signs})."""
    fired = {p["id"].removeprefix("ORG-"): p for p in params if (p.get("level") or 0) > 0}
    out = []
    for r in COMBINED:
        if not all(s in fired for s in r["sensors"]):
            continue
        matched = []
        for cid in r["components"]:
            v = visuals.get(cid)
            if not v or v.get("category") == "ok":
                continue
            hits = sign_ids(v.get("signs") or []) & set(r["visual"])
            if hits:
                matched.append(f"{names.get(cid, cid)}: {', '.join(SIGN_NAMES[h] for h in sorted(hits))}")
        if r["visual"] and not matched:
            continue
        sensors = [f"{fired[s]['name']} {fired[s]['value']:g} {fired[s].get('unit') or ''}".replace(".", ",").replace("  ", " ")
                   + f" — {fired[s]['status_text']}".replace(".", ",") for s in r["sensors"]]
        out.append({"id": r["id"], "component_id": r["target"], "diagnosis": r["diagnosis"], "level": r["level"],
                    "confidence": r["confidence"], "action": r["action"], "sensors": sensors, "visual": matched,
                    "sources": "камера + датчики" if r["visual"] else "датчики"})
    return out
