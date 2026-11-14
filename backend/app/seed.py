"""Демо-набор цифровых карточек оборудования.

Даты задаются относительно дня первого запуска, чтобы на демонстрации
статусы ресурса (норма / приближается порог / исчерпан) были стабильными.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

from . import db

# Элемент компонента:
# id, name, kind (assembly|part), detect (класс детектора или None), parent,
# installed (дней назад или None), life_years, norm_hours, hours, norm_cycles, cycles,
# mnt_days (интервал ТО), mnt_name, last_service (дней назад), specs, inspect_for, schema [x,y,w,h]

EQUIPMENT: list[dict[str, Any]] = [
    {
        "id": "PUMP-001",
        "name": "Центробежный насос НА-1",
        "data_origin": "Учебная карточка. Нормативы и счетчики приняты командой; измерения из примеров организаторов.",
        "type": "Центробежный насосный агрегат",
        "model": "К 100-65-200 / АИР180M2",
        "manufacturer": "Демо-производитель",
        "serial_number": "2019-0471",
        "inventory_number": "ИНВ-004712",
        "location": "Котельная №2, насосная станция ХВО",
        "commissioned_days_ago": 6 * 365 + 40,
        "qr_payload": "KST:EQ:PUMP-001",
        "hours_per_day": 15.5,
        "description": "Консольный центробежный насос с электродвигателем на общей раме, соединение через упругую муфту.",
        "appearance": "Горизонтальный агрегат на раме: электродвигатель с ребристым корпусом и вентилятором, муфта под защитным кожухом, "
                      "спиральный корпус насоса, всасывающий и напорный патрубки с фланцами, манометр и задвижка на напорной линии.",
        "specs": {
            "Подача": "100 м³/ч", "Напор": "50 м", "Мощность двигателя": "30 кВт",
            "Частота вращения": "2900 об/мин", "Перекачиваемая среда": "вода до 85 °C", "Масса": "420 кг",
        },
        "schema": {"w": 100, "h": 50},
        "components": [
            dict(id="PUMP-001-MOTOR", name="Электродвигатель", kind="assembly", detect="electric motor",
                 installed=6 * 365 + 40, life_years=20, norm_hours=60000, hours=33900, mnt_days=180,
                 mnt_name="Плановое ТО двигателя", last_service=150,
                 specs={"Тип": "АИР180M2", "Мощность": "30 кВт", "Ток номинальный": "56 А", "IP": "IP55"},
                 inspect_for=["перегрев/потемнение краски", "загрязнение рёбер охлаждения", "повреждение кожуха вентилятора",
                              "состояние клеммной коробки и кабельного ввода"],
                 schema=[4, 14, 30, 22]),
            dict(id="PUMP-001-MOTOR-BRG", access="internal", name="Подшипники двигателя 6311-2Z", kind="part", parent="PUMP-001-MOTOR",
                 installed=3 * 365, norm_hours=20000, hours=17400, mnt_days=None, last_service=None,
                 specs={"Тип": "6311-2Z", "Смазка": "закрытые, на весь срок"},
                 inspect_for=["посторонний шум", "нагрев крышек подшипников"]),
            dict(id="PUMP-001-MOTOR-WND", access="internal", name="Обмотка статора", kind="part", parent="PUMP-001-MOTOR",
                 installed=6 * 365 + 40, life_years=20, norm_hours=None, hours=None, mnt_days=365,
                 mnt_name="Измерение сопротивления изоляции", last_service=200,
                 specs={"Класс изоляции": "F"}, inspect_for=[]),
            dict(id="PUMP-001-COUP", name="Соединительная муфта", kind="assembly", detect="coupling",
                 installed=6 * 365 + 40, life_years=10, mnt_days=365, mnt_name="Проверка центровки и упругих элементов",
                 last_service=410,
                 specs={"Тип": "упругая втулочно-пальцевая МУВП-40"},
                 inspect_for=["отсутствие/повреждение защитного кожуха", "следы износа резиновых элементов"],
                 schema=[34, 18, 10, 14]),
            dict(id="PUMP-001-PUMP", name="Насос (корпус и проточная часть)", kind="assembly", detect="pump",
                 installed=6 * 365 + 40, life_years=15, norm_hours=50000, hours=33900, mnt_days=365,
                 mnt_name="Ревизия насоса", last_service=300,
                 specs={"Тип": "К 100-65-200", "Материал корпуса": "чугун СЧ20"},
                 inspect_for=["коррозия корпуса", "течь по корпусу", "трещины", "отслоение покраски"],
                 schema=[44, 12, 26, 26]),
            dict(id="PUMP-001-SEAL", name="Торцевое уплотнение вала", kind="part", parent="PUMP-001-PUMP",
                 installed=520, norm_hours=8000, hours=7930, mnt_days=None, last_service=None,
                 specs={"Тип": "торцевое одинарное, Ø32"},
                 inspect_for=["капельная течь под валом", "солевые отложения", "следы воды на раме"]),
            dict(id="PUMP-001-BRG", access="internal", name="Подшипниковый узел насоса", kind="part", parent="PUMP-001-PUMP",
                 installed=6 * 365 + 40, norm_hours=40000, hours=33900, mnt_days=180, mnt_name="Замена смазки",
                 last_service=170,
                 specs={"Подшипники": "6309 ×2", "Смазка": "Литол-24"},
                 inspect_for=["выдавливание смазки", "потемнение краски от нагрева"]),
            dict(id="PUMP-001-IMP", access="internal", name="Рабочее колесо", kind="part", parent="PUMP-001-PUMP",
                 installed=None, life_years=8, specs={"Диаметр": "200 мм"}, inspect_for=[]),
            dict(id="PUMP-001-GAUGE", name="Манометр на напорном патрубке", kind="assembly", detect="pressure gauge",
                 installed=2 * 365, life_years=8, mnt_days=365, mnt_name="Поверка манометра", last_service=345,
                 specs={"Тип": "ДМ02-100", "Диапазон": "0–1,0 МПа", "Класс точности": "1,5"},
                 inspect_for=["разбитое стекло", "стрелка не на нуле при остановке", "истёк срок поверки (пломба)"],
                 schema=[60, 2, 8, 8]),
            dict(id="PUMP-001-VALVE", name="Задвижка на напорной линии", kind="assembly", detect="valve",
                 installed=6 * 365 + 40, life_years=15, cycles=410, norm_cycles=2000, mnt_days=365,
                 mnt_name="Проверка хода и набивки сальника", last_service=250,
                 specs={"Тип": "30с41нж Ду100 Ру16"},
                 inspect_for=["течь через сальник", "коррозия штока", "повреждение маховика"],
                 schema=[74, 2, 10, 14]),
            dict(id="PUMP-001-FLANGE", name="Фланцевые соединения и прокладки", kind="assembly", detect="pipe flange",
                 installed=4 * 365 + 200, life_years=5, mnt_days=180, mnt_name="Протяжка крепежа", last_service=120,
                 specs={"Прокладки": "паронит ПОН-Б", "Крепёж": "М16"},
                 inspect_for=["течь/подтёки", "коррозия крепежа", "отсутствие болтов"],
                 schema=[70, 20, 12, 12]),
        ],
        "parameters": [
            dict(id="P1-PRESS", name="Давление нагнетания", unit="МПа", value=0.63, nominal=0.50,
                 warn_low=0.35, warn_high=0.60, alarm_low=0.25, alarm_high=0.70, noise=0.01, component="PUMP-001-GAUGE"),
            dict(id="P1-VIB", name="Вибрация подшипникового узла насоса", unit="мм/с", value=5.8, nominal=2.8,
                 warn_high=4.5, alarm_high=7.1, noise=0.15, component="PUMP-001-BRG"),
            dict(id="P1-TBRG", name="Температура подшипника насоса", unit="°C", value=67, nominal=55,
                 warn_high=80, alarm_high=95, noise=0.6, component="PUMP-001-BRG"),
            dict(id="P1-CUR", name="Ток двигателя", unit="А", value=51.2, nominal=56,
                 warn_high=58, alarm_high=62, noise=0.4, component="PUMP-001-MOTOR"),
            dict(id="P1-TMOT", name="Температура корпуса двигателя", unit="°C", value=62, nominal=60,
                 warn_high=80, alarm_high=95, noise=0.5, component="PUMP-001-MOTOR"),
        ],
        "events": [
            ("PUMP-001-SEAL", 520, "replacement", "Замена торцевого уплотнения после течи", "Иванов А.С."),
            ("PUMP-001-MOTOR-BRG", 3 * 365, "replacement", "Замена подшипников двигателя по наработке", "Петров В.И."),
            ("PUMP-001-PUMP", 300, "maintenance", "Ревизия насоса, замер зазоров — в норме", "Иванов А.С."),
            ("PUMP-001-BRG", 170, "maintenance", "Замена смазки подшипникового узла", "Иванов А.С."),
            ("PUMP-001-MOTOR", 150, "maintenance", "ТО двигателя: очистка, проверка крепления", "Петров В.И."),
            ("PUMP-001-FLANGE", 120, "maintenance", "Протяжка фланцевых соединений", "Иванов А.С."),
            ("PUMP-001-BRG", 21, "defect", "Рост вибрации до 5,4 мм/с, назначен контроль", "Сидорова Е.Н."),
        ],
    },
    {
        "id": "CAB-003",
        "name": "Шкаф управления ШУ-3",
        "type": "Низковольтный шкаф управления",
        "model": "ШУ-3 (0,4 кВ, 160 А)",
        "manufacturer": "Демо-производитель",
        "serial_number": "ШУ-2020-118",
        "inventory_number": "ИНВ-005130",
        "location": "Насосная станция ХВО, щитовая",
        "commissioned_days_ago": 5 * 365 + 90,
        "qr_payload": "KST:EQ:CAB-003",
        "hours_per_day": 24,
        "description": "Шкаф управления насосами НА-1/НА-2: вводной автомат, контакторы, реле контроля фаз, клеммники, вентиляция.",
        "appearance": "Металлический шкаф с открытой дверцей: на DIN-рейках модульные автоматические выключатели, контакторы, реле, "
                      "ряды клеммных колодок, жгуты проводов, кабельные вводы снизу, вентилятор с фильтром, сигнальные лампы на двери.",
        "specs": {"Номинальное напряжение": "380 В", "Номинальный ток": "160 А", "Степень защиты": "IP54",
                  "Габариты": "1200×800×300 мм"},
        "schema": {"w": 100, "h": 60},
        "components": [
            dict(id="CAB-003-QF", name="Вводной автоматический выключатель QF1", kind="assembly", detect="circuit breaker",
                 installed=5 * 365 + 90, life_years=15, cycles=2300, norm_cycles=10000, mnt_days=365,
                 mnt_name="Проверка расцепителей", last_service=200,
                 specs={"Тип": "ВА57-35, 160 А", "Отключающая способность": "25 кА"},
                 inspect_for=["потемнение корпуса у клемм", "оплавление", "трещины корпуса"],
                 schema=[6, 6, 20, 14]),
            dict(id="CAB-003-KM", name="Контактор КМ1 (пуск насоса)", kind="assembly", detect="contactor",
                 installed=5 * 365 + 90, life_years=12, cycles=236000, norm_cycles=250000, mnt_days=365,
                 mnt_name="Осмотр контактов", last_service=330,
                 specs={"Тип": "КМИ-46512, 65 А", "Коммутационная износостойкость": "250 000 циклов (AC-3)"},
                 inspect_for=["нагар", "оплавление", "гудение катушки", "потемнение пластика"],
                 schema=[32, 6, 16, 14]),
            dict(id="CAB-003-KM-CONT", name="Главные контакты КМ1", kind="part", parent="CAB-003-KM",
                 installed=5 * 365 + 90, cycles=236000, norm_cycles=250000, specs={}, inspect_for=[]),
            dict(id="CAB-003-KV", name="Реле контроля фаз", kind="assembly", detect="relay",
                 installed=5 * 365 + 90, life_years=12, mnt_days=365, mnt_name="Проверка срабатывания", last_service=180,
                 specs={"Тип": "ЕЛ-11"}, inspect_for=["индикация", "целостность корпуса"],
                 schema=[54, 6, 10, 14]),
            dict(id="CAB-003-XT", name="Клеммные колодки X1", kind="assembly", detect="terminal block",
                 installed=5 * 365 + 90, life_years=20, mnt_days=365, mnt_name="Протяжка клеммных соединений",
                 last_service=425,
                 specs={"Тип": "ЗНИ-16, 40 шт."},
                 inspect_for=["потемнение/оплавление изоляции", "ослабленные провода", "окислы"],
                 schema=[6, 26, 56, 8]),
            dict(id="CAB-003-FAN", name="Вентилятор с фильтром", kind="assembly", detect="fan",
                 installed=2 * 365, life_years=5, norm_hours=40000, hours=17300, mnt_days=180,
                 mnt_name="Замена фильтрующего элемента", last_service=205,
                 specs={"Расход": "55 м³/ч"}, inspect_for=["загрязнение фильтра", "не вращается"],
                 schema=[70, 6, 22, 16]),
            dict(id="CAB-003-CABLE", name="Кабельные вводы", kind="assembly", detect="cable",
                 installed=5 * 365 + 90, life_years=25, mnt_days=None, last_service=None,
                 specs={"Тип": "ВВГнг 4×50, сальники PG"},
                 inspect_for=["повреждение изоляции", "отсутствие уплотнения ввода", "следы нагрева"],
                 schema=[6, 46, 56, 10]),
            dict(id="CAB-003-HL", name="Сигнальная арматура", kind="assembly", detect="indicator light",
                 installed=5 * 365 + 90, life_years=10, specs={"Тип": "AD22DS, 220 В"},
                 inspect_for=["не горит при наличии сигнала", "трещины линз"],
                 schema=[70, 30, 22, 10]),
        ],
        "parameters": [
            dict(id="C3-TIN", name="Температура внутри шкафа", unit="°C", value=41.5, nominal=30,
                 warn_high=40, alarm_high=50, noise=0.4, component="CAB-003-FAN"),
            dict(id="C3-TQF", name="Температура клемм QF1 (термометка)", unit="°C", value=47, nominal=40,
                 warn_high=60, alarm_high=75, noise=0.5, component="CAB-003-QF"),
            dict(id="C3-UL", name="Линейное напряжение", unit="В", value=402, nominal=400,
                 warn_low=370, warn_high=420, alarm_low=360, alarm_high=430, noise=1.5),
            dict(id="C3-IL", name="Ток ввода", unit="А", value=58, nominal=80, warn_high=140, alarm_high=160, noise=1.0),
        ],
        "events": [
            ("CAB-003-FAN", 2 * 365, "replacement", "Замена вентилятора (заклинил)", "Кузнецов Д.А."),
            ("CAB-003-QF", 200, "maintenance", "Проверка расцепителей — в норме", "Кузнецов Д.А."),
            ("CAB-003-KM", 330, "maintenance", "Осмотр контактов КМ1: умеренный нагар, зачистка", "Кузнецов Д.А."),
            ("CAB-003-XT", 425, "maintenance", "Протяжка клеммных соединений", "Кузнецов Д.А."),
        ],
    },
    {
        "id": "TR-001",
        "name": "Силовой трансформатор Т-1",
        "type": "Масляный силовой трансформатор",
        "model": "ТМГ-400/10/0,4",
        "manufacturer": "Демо-производитель",
        "serial_number": "ТМГ-1708-3321",
        "inventory_number": "ИНВ-002208",
        "location": "ТП-12, камера Т-1",
        "commissioned_days_ago": 9 * 365,
        "qr_payload": "KST:EQ:TR-001",
        "hours_per_day": 24,
        "description": "Герметичный масляный трансформатор 10/0,4 кВ, питание насосной станции и котельной.",
        "appearance": "Серый стальной бак с гофрированными стенками-радиаторами, на крышке фарфоровые вводы высокого и низкого "
                      "напряжения, маслоуказатель, термометр, привод переключателя ответвлений, шильдик на баке.",
        "specs": {"Мощность": "400 кВА", "Напряжение": "10/0,4 кВ", "Схема": "Y/Yн-0",
                  "Масса масла": "320 кг", "Охлаждение": "естественное масляное (М)"},
        "schema": {"w": 100, "h": 60},
        "components": [
            dict(id="TR-001-TANK", name="Бак и гофростенки", kind="assembly", detect="transformer",
                 installed=9 * 365, life_years=25, mnt_days=365, mnt_name="Внешний осмотр и очистка", last_service=90,
                 specs={"Покрытие": "эмаль ПФ-115"},
                 inspect_for=["течь масла", "коррозия", "вмятины гофр", "масляные пятна под баком"],
                 schema=[20, 22, 60, 34]),
            dict(id="TR-001-HV", name="Вводы ВН 10 кВ", kind="assembly", detect="insulator",
                 installed=9 * 365, life_years=25, mnt_days=365, mnt_name="Очистка и осмотр изоляторов", last_service=240,
                 specs={"Тип": "фарфоровые проходные"},
                 inspect_for=["сколы фарфора", "загрязнение", "следы перекрытия", "течь масла у основания"],
                 schema=[24, 6, 26, 12]),
            dict(id="TR-001-LV", name="Вводы НН 0,4 кВ", kind="assembly", detect="insulator",
                 installed=9 * 365, life_years=25, mnt_days=365, mnt_name="Протяжка контактов", last_service=240,
                 specs={"Тип": "фарфоровые, шпилька М20"},
                 inspect_for=["потемнение контактов от нагрева", "сколы"],
                 schema=[52, 6, 26, 12]),
            dict(id="TR-001-OIL", name="Трансформаторное масло", kind="part", parent="TR-001-TANK",
                 installed=9 * 365, life_years=None, mnt_days=3 * 365, mnt_name="Отбор пробы масла (ХАРГ)",
                 last_service=2 * 365 + 300, specs={"Марка": "ГК"}, inspect_for=[]),
            dict(id="TR-001-GAUGE", name="Маслоуказатель и термометр", kind="assembly", detect="gauge",
                 installed=9 * 365, life_years=10, mnt_days=365, mnt_name="Поверка термометра", last_service=100,
                 specs={}, inspect_for=["уровень масла вне меток", "разбитое стекло"],
                 schema=[82, 22, 12, 14]),
            dict(id="TR-001-TAP", name="Переключатель ответвлений (ПБВ)", kind="assembly", detect=None,
                 installed=9 * 365, life_years=25, cycles=14, norm_cycles=None, specs={"Положение": "3 из 5"},
                 inspect_for=[], schema=[6, 22, 12, 14]),
        ],
        "parameters": [
            dict(id="T1-TOIL", name="Температура верхних слоёв масла", unit="°C", value=71, nominal=60,
                 warn_high=85, alarm_high=95, noise=0.5, component="TR-001-OIL"),
            dict(id="T1-LOAD", name="Загрузка", unit="%", value=82, nominal=70, warn_high=100, alarm_high=120,
                 noise=1.5, component="TR-001-TANK"),
            dict(id="T1-ULV", name="Напряжение НН", unit="В", value=398, nominal=400,
                 warn_low=380, warn_high=420, alarm_low=360, alarm_high=430, noise=1.5, component="TR-001-LV"),
        ],
        "events": [
            ("TR-001-OIL", 2 * 365 + 300, "maintenance", "Отбор пробы масла: показатели в норме", "Орлов П.К."),
            ("TR-001-TANK", 90, "maintenance", "Очистка бака, следов течи нет", "Орлов П.К."),
            ("TR-001-HV", 240, "maintenance", "Очистка изоляторов ВН и протяжка контактов НН", "Орлов П.К."),
        ],
    },
]


def _d(days_ago: int | None, today: date) -> str | None:
    if days_ago is None:
        return None
    return (today - timedelta(days=int(days_ago))).isoformat()


def _stamp(days_ago: int, today: date) -> str:
    """Запись истории «days_ago дней назад в 10:00 по местному времени» — в UTC, как все отметки времени."""
    local = datetime.combine(today - timedelta(days=days_ago), time(10)).astimezone()
    return local.astimezone(timezone.utc).isoformat(timespec="microseconds")


# Признаки из справочника организаторов «Типы дефектов промышленного оборудования» (yandex/):
# дописываются к «Что осматривать» узла — по ним ИИ ищет дефекты на снимке.
ORG_SIGNS: dict[str, list[str]] = {
    "PUMP-001-MOTOR": ["трещины или оплавление изоляции кабеля, оголённые жилы", "погнутые или сломанные рёбра охлаждения",
                       "побежалость краски у подшипниковых щитов и клеммной коробки",
                       "нет или оборван жёлто-зелёный провод заземления"],
    "PUMP-001-VALVE": ["капли, влажное пятно или солевой налёт под сальником штока",
                       "трещины маховика или ключ вместо маховика", "положение штока не совпадает с указателем"],
    "PUMP-001-FLANGE": ["ржавые подтёки от болтов вниз по корпусу", "вздутие краски на стыке фланцев"],
    "PUMP-001-SEAL": ["белый солевой налёт или наросты у вала"],
    "CAB-003-CABLE": ["трещины или оплавление изоляции кабеля, оголённые жилы"],
    "TR-001-TANK": ["маслянистые дорожки от прокладок крышки, фланцев расширителя и сливных кранов"],
    "TR-001-HV": ["трещины, сколы и следы электрической дуги на изоляторах"],
    "TR-001-LV": ["трещины, сколы и следы электрической дуги на изоляторах"],
    "TR-001-OIL": ["цвет силикагеля в воздухоосушителе: розовый или белый — требует замены"],
}


def inspect_signs(cid: str, base: list[str]) -> list[str]:
    return base + [s for s in ORG_SIGNS.get(cid, []) if s not in base]


# Оборудование с воспроизведением данных организаторов (телеметрия насоса).
SCENARIO_EQUIPMENT = ("PUMP-001",)


def insert_equipment(c: Any, eq: dict[str, Any], when: Callable[[Any], str | None],
                     stamp: Callable[[Any], str] | None = None) -> None:
    """Добавляет карточку агрегата с узлами, параметрами и историей. when — дата из значения карточки
    («дней назад» в демо-наборе, ISO-дата в карточках по паспортам), stamp — отметка времени события."""
    c.execute(
        """INSERT INTO equipment (id, name, type, model, manufacturer, serial_number, inventory_number, location,
               commissioned_at, qr_payload, hours_per_day, description, appearance, specs_json, schema_json,
               data_origin)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (eq["id"], eq["name"], eq["type"], eq["model"], eq["manufacturer"], eq["serial_number"],
         eq["inventory_number"], eq["location"], when(eq.get("commissioned_days_ago", eq.get("commissioned"))), eq["qr_payload"],
         eq["hours_per_day"], eq["description"], eq["appearance"], db.dumps(eq["specs"]), db.dumps(eq["schema"]),
         eq.get("data_origin", "Демонстрационные учетные данные")),
    )
    for i, comp in enumerate(eq["components"]):
        c.execute(
            """INSERT INTO components (id, equipment_id, parent_id, name, kind, detect_class, order_idx, specs_json,
                   inspect_for_json, installed_at, normative_life_years, normative_hours, operating_hours,
                   normative_cycles, cycles, maintenance_interval_days, maintenance_name, last_service_at,
                   last_inspection_at, schema_json, visual_access)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (comp["id"], eq["id"], comp.get("parent"), comp["name"], comp["kind"], comp.get("detect"), i,
             db.dumps(comp.get("specs", {})), db.dumps(inspect_signs(comp["id"], comp.get("inspect_for", []))),
             when(comp.get("installed")), comp.get("life_years"), comp.get("norm_hours"),
             comp.get("hours"), comp.get("norm_cycles"), comp.get("cycles"), comp.get("mnt_days"),
             comp.get("mnt_name"), when(comp.get("last_service")), None,
             db.dumps(comp["schema"]) if comp.get("schema") else None, comp.get("access", "external")),
        )
    for p in eq.get("parameters", []):
        c.execute(
            """INSERT INTO parameters (id, equipment_id, component_id, name, unit, value, nominal,
                   warn_low, warn_high, alarm_low, alarm_high, noise, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p["id"], eq["id"], p.get("component"), p["name"], p.get("unit"), p.get("value"), p.get("nominal"),
             p.get("warn_low"), p.get("warn_high"), p.get("alarm_low"), p.get("alarm_high"), p.get("noise", 0),
             db.now_iso()),
        )
    for comp_id, days_ago, etype, note, author in eq.get("events", []):
        c.execute(
            "INSERT INTO events (equipment_id, component_id, type, note, author, created_at) VALUES (?,?,?,?,?,?)",
            (eq["id"], comp_id, etype, note, author, stamp(days_ago)),
        )


def seed(force: bool = False) -> None:
    if not force and not db.is_empty():
        return
    today = date.today()
    with db.tx() as c:
        for table in ("scenario_state", "visual_results", "analyses", "events", "parameters", "components", "equipment"):
            c.execute(f"DELETE FROM {table}")
        for eq in EQUIPMENT:
            insert_equipment(c, eq, lambda v: _d(v, today), lambda v: _stamp(v, today))
            if eq["id"] in SCENARIO_EQUIPMENT:
                c.execute("INSERT INTO scenario_state (equipment_id, row_index) VALUES (?, 0)", (eq["id"],))
