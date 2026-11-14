"""Карточки агрегатов из документации организаторов — подгружаются сами, если папка с данными есть.

Папка ищется так: ORG_DATA_DIR из .env, иначе yandex/ в проекте, иначе ../materials/yandex.
Паспорта — сканы без текстового слоя, поэтому значения ниже переписаны из них вручную (14.11.2026)
и подтверждаются наличием самого паспорта: карточка добавляется, только если файл её паспорта на месте.

Добавляются только недостающие карточки — записи инженеров, наработка и решения не перезаписываются.
Чего в паспорте нет (дата ввода в эксплуатацию, наработка, место установки), оставлено пустым:
карточка честно пишет «нет данных», а не подставляет допущения.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from . import config, db, seed

log = logging.getLogger("orgdata")

PASSPORTS = Path("Документация") / "Паспорта Насосов"


def find_dir() -> Path | None:
    env = os.getenv("ORG_DATA_DIR", "").strip()
    candidates = [Path(env)] if env else [config.ROOT / "yandex", config.ROOT.parent / "materials" / "yandex"]
    return next((p for p in candidates if (p / PASSPORTS).is_dir()), None)


RELEASED = "2023-06-01"        # дата выпуска насосов по паспорту: 06.2023
MOTOR_MADE = "2023-03-01"      # дата изготовления двигателя по паспорту: 2023.03.01
SOURCE_PUMP = "паспорт агрегата AMS (ТУ3631-001-23430200-2015)"
SOURCE_MOTOR = "паспорт двигателя ДТ.525000.038 ПС (ЯЭМЗ)"
SOURCE_GEAR = "паспорт редуктора «Европромтех»"
FAST_WEAR = "быстроизнашиваемая деталь: 12 месяцев при наработке 3000 ч"


def _unit(n: int) -> dict[str, Any]:
    eid = f"AMS-{n}"
    k = lambda s: f"{eid}-{s}"  # noqa: E731
    return {
        "id": eid,
        "passport": f"1) Насос №{n} ПС.pdf",
        "motor_passport": f"2) Редукт, ЭД, ТПП №{n} ПС.pdf",
        "name": f"Винтовой насосный агрегат №{n}",
        "type": "Агрегат насосный одновинтовой эксцентриковый",
        "model": "AMS PCP60S12/3 01 Ex B1 HNBR DTS",
        "manufacturer": "ООО «Альтернативные механические системы» (АМС), Россия",
        "serial_number": f"21031004-{n}",
        "inventory_number": None,
        "location": "Место установки не указано",
        "commissioned": None,
        "qr_payload": f"KST:EQ:{eid}",
        "hours_per_day": None,
        "data_origin": f"Паспорта организаторов: «{PASSPORTS.as_posix()}/1) Насос №{n} ПС.pdf» и «2) Редукт, ЭД, ТПП №{n} ПС.pdf». "
                       "Дата ввода и наработка в паспорте не заполнены.",
        "description": "Перекачивание обводнённого нефтепродукта. Взрывозащищённое исполнение, климатическое У1. "
                       "Мотор-редуктор вращает винтовой ротор в эластомерном статоре; реверсивная работа, защита от сухого хода.",
        "appearance": "Длинный низкий горизонтальный агрегат на раме (около 2,7 м): с одного края электродвигатель с ребристым "
                      "корпусом и вентилятором, сразу за ним редуктор, затем узкий длинный цилиндрический корпус статора "
                      "и всасывающая камера с фланцами входа и выхода; вал закрыт защитным кожухом.",
        "specs": {
            "Подача": "5–20 м³/ч", "Давление": "до 10 бар", "NPSHr": "2 м", "Температура продукта": "+5…+45 °C",
            "Плотность продукта": "до 1600 кг/м³", "Твёрдая фаза": "до 5 %", "Габариты": "2742×360×605 мм", "Масса": "430 кг",
            "Ресурс до списания": "не менее 11 000 ч", "Срок службы": "20 лет", "Дата выпуска": "06.2023",
        },
        "schema": {"w": 100, "h": 40},
        "components": [
            dict(id=k("MOTOR"), name="Электродвигатель BRA160M4", kind="assembly", detect="electric motor",
                 installed=MOTOR_MADE, life_years=20, norm_hours=60000,
                 specs={"Тип": "BRA160M4FБУ1, 1ExdIIBT4Gb", "Мощность": "11 кВт", "Ток номинальный": "23 А",
                        "Частота вращения": "1460 об/мин", "IP": "IP55", "Допустимая виброскорость": "2,2 мм/с",
                        "Источник": SOURCE_MOTOR},
                 inspect_for=seed.inspect_signs("PUMP-001-MOTOR", ["перегрев/потемнение краски", "загрязнение рёбер охлаждения", "повреждение кожуха вентилятора",
                              "состояние клеммной коробки и кабельного ввода"]),
                 schema=[2, 10, 22, 20]),
            dict(id=k("GEAR"), name="Редуктор WXRF87", kind="assembly", detect="gearbox", installed=RELEASED,
                 specs={"Тип": "WXRF87-AM160-5.30-M1-ф300", "Передаточное отношение": "5,3", "Выходная частота": "260 об/мин",
                        "Крутящий момент": "910 Н·м", "Масло": "синтетическое VG220", "Подшипники": "NSK",
                        "Источник": SOURCE_GEAR},
                 inspect_for=["течь масла по разъёму и уплотнениям валов", "уровень масла в смотровом окне",
                              "посторонний шум, нагрев корпуса"],
                 schema=[24, 12, 12, 16]),
            dict(id=k("PUMP"), name="Насос (корпус статора и всасывающая камера)", kind="assembly", detect="pump",
                 installed=RELEASED, life_years=20, norm_hours=11000,
                 specs={"Корпус": "сталь 20 с защитным покрытием", "Источник": SOURCE_PUMP},
                 inspect_for=["коррозия и повреждение покрытия корпуса", "подтёки продукта", "трещины, вмятины корпуса"],
                 schema=[38, 14, 50, 12]),
            dict(id=k("STATOR"), access="internal", name="Статор 60S1240DTS", kind="part", parent=k("PUMP"),
                 installed=RELEASED, norm_hours=3000,
                 specs={"Материал": "эластомер абразивостойкий HNBR", "Ресурс": FAST_WEAR, "Источник": SOURCE_PUMP}),
            dict(id=k("ROTOR"), access="internal", name="Ротор", kind="part", parent=k("PUMP"),
                 installed=RELEASED, norm_hours=3000,
                 specs={"Материал": "сталь 40Х13 + закалка (50 HRC)", "Ресурс": FAST_WEAR, "Источник": SOURCE_PUMP}),
            dict(id=k("SEAL"), name="Торцевое уплотнение 60S1290", kind="part", parent=k("PUMP"), detect="pump",
                 installed=RELEASED, norm_hours=3000,
                 specs={"Тип": "одинарное торцевое, карбид вольфрама / карбид кремния", "Ресурс": FAST_WEAR,
                        "Источник": SOURCE_PUMP},
                 inspect_for=seed.inspect_signs("PUMP-001-SEAL", ["подтёки и капли продукта у вала", "налёт и наросты у уплотнения"]),
                 schema=[36, 14, 4, 12]),
            dict(id=k("FLANGE"), name="Фланцы входа и выхода", kind="assembly", detect="pipe flange", installed=RELEASED,
                 specs={"Тип": "100-16-01-1-B-ст.20-IV-db-110 ГОСТ 33259-15", "Прокладки": "спирально-навитые СНП-Д",
                        "Источник": SOURCE_PUMP},
                 inspect_for=seed.inspect_signs("PUMP-001-FLANGE", ["подтёки на стыке фланцев", "коррозия и недостающие болты"]),
                 schema=[88, 6, 10, 28]),
            dict(id=k("GUARD"), name="Защитный кожух вала", kind="part", detect="coupling", installed=RELEASED,
                 specs={"Источник": SOURCE_PUMP},
                 inspect_for=["отсутствие или повреждение кожуха", "крепление кожуха"],
                 schema=[34, 12, 6, 16]),
            dict(id=k("DTS"), name="Датчик защиты сухого хода DTS", kind="part", detect="gauge", installed=RELEASED,
                 specs={"Тип": "ПТ 0304 Ex, 1×Pt100", "Реле": "РТЗ-1М АС230В УХЛ4", "Источник": SOURCE_PUMP},
                 inspect_for=["целостность корпуса и кабельного ввода датчика"],
                 schema=[62, 8, 4, 6]),
        ],
    }


UNITS: list[dict[str, Any]] = [_unit(n) for n in range(1, 5)]


def sync() -> int:
    """Добавляет в базу карточки агрегатов, паспорта которых есть в папке организаторов. Возвращает число новых."""
    root = find_dir()
    if not root:
        return 0
    have = {r["id"] for r in db.query("SELECT id FROM equipment")}
    added = []
    with db.tx() as c:
        for unit in UNITS:
            if unit["id"] in have or not (root / PASSPORTS / unit["passport"]).is_file():
                continue
            seed.insert_equipment(c, unit, lambda v: v)
            added.append(unit["id"])
    if added:
        log.info("Данные организаторов (%s): добавлены карточки %s", root, ", ".join(added))
    return len(added)
