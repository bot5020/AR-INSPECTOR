"""SQLite-хранилище цифровых карточек оборудования, параметров и истории."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from . import config

log = logging.getLogger("db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS equipment (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    model TEXT,
    manufacturer TEXT,
    serial_number TEXT,
    inventory_number TEXT,
    location TEXT,
    commissioned_at TEXT,
    qr_payload TEXT UNIQUE,
    hours_per_day REAL,
    description TEXT,
    appearance TEXT,
    specs_json TEXT NOT NULL DEFAULT '{}',
    schema_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS components (
    id TEXT PRIMARY KEY,
    equipment_id TEXT NOT NULL REFERENCES equipment(id),
    parent_id TEXT REFERENCES components(id),
    name TEXT NOT NULL,
    kind TEXT NOT NULL,                -- assembly | part
    detect_class TEXT,                 -- класс детектора для визуально доступных узлов
    order_idx INTEGER NOT NULL DEFAULT 0,
    specs_json TEXT NOT NULL DEFAULT '{}',
    inspect_for_json TEXT NOT NULL DEFAULT '[]',
    installed_at TEXT,
    normative_life_years REAL,
    normative_hours REAL,
    operating_hours REAL,
    normative_cycles REAL,
    cycles REAL,
    maintenance_interval_days INTEGER,
    maintenance_name TEXT,
    last_service_at TEXT,
    last_inspection_at TEXT,
    schema_json TEXT
);

CREATE TABLE IF NOT EXISTS parameters (
    id TEXT PRIMARY KEY,
    equipment_id TEXT NOT NULL REFERENCES equipment(id),
    component_id TEXT REFERENCES components(id),
    name TEXT NOT NULL,
    unit TEXT,
    value REAL,
    nominal REAL,
    warn_low REAL, warn_high REAL,
    alarm_low REAL, alarm_high REAL,
    noise REAL NOT NULL DEFAULT 0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id TEXT NOT NULL REFERENCES equipment(id),
    component_id TEXT REFERENCES components(id),
    type TEXT NOT NULL,                -- checked | defect | maintenance | replacement | inspection | resolved
    note TEXT,
    author TEXT,
    created_at TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id TEXT NOT NULL REFERENCES equipment(id),
    created_at TEXT NOT NULL,
    image_path TEXT,
    model TEXT,
    result_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS visual_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id TEXT NOT NULL REFERENCES equipment(id),
    component_id TEXT REFERENCES components(id),
    analysis_id INTEGER REFERENCES analyses(id),
    category TEXT NOT NULL,            -- ok | check | defect
    signs_json TEXT NOT NULL DEFAULT '[]',
    reason TEXT,
    recommendation TEXT,
    confidence REAL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_components_eq ON components(equipment_id);
CREATE INDEX IF NOT EXISTS ix_events_eq ON events(equipment_id, created_at);
CREATE INDEX IF NOT EXISTS ix_visual_comp ON visual_results(component_id, created_at);
"""

_lock = threading.RLock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _connect()
        migrate(_conn)
    return _conn


# ---------------------------------------------------------------- миграции
# Номер применённой миграции хранится в PRAGMA user_version. Новая миграция — новая функция
# в конце списка; старые не меняются. Каждая идемпотентна: база из любой прежней версии
# (в том числе созданная до нумерации) доводится до текущей.

def _add_columns(c: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    present = {row[1] for row in c.execute(f"PRAGMA table_info({table})")}
    for name, declaration in columns.items():
        if name not in present:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def _m1_base(c: sqlite3.Connection) -> None:
    c.executescript(SCHEMA)


def _m2_roles_runtime(c: sqlite3.Connection) -> None:
    _add_columns(c, "equipment", {"run_state": "TEXT NOT NULL DEFAULT 'stopped'", "run_started_at": "TEXT",
                                  "data_origin": "TEXT NOT NULL DEFAULT 'Демонстрационные учетные данные'"})
    _add_columns(c, "components", {"visual_access": "TEXT NOT NULL DEFAULT 'external'",
                                   "normative_source": "TEXT NOT NULL DEFAULT 'Демонстрационное допущение команды'",
                                   "maintenance_interval_hours": "REAL", "last_service_hours": "REAL",
                                   "hours_updated_at": "TEXT"})
    c.executescript("""
        CREATE TABLE IF NOT EXISTS scenario_state (
            equipment_id TEXT PRIMARY KEY REFERENCES equipment(id),
            row_index INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL, role TEXT NOT NULL,
            password_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            expires_at REAL NOT NULL
        );
    """)


def to_utc(value: str | None) -> str | None:
    """Отметка времени → UTC ISO с микросекундами. Без смещения — считается местным временем сервера.
    Даты без времени (YYYY-MM-DD) не трогаем."""
    if not value or len(value) <= 10:
        return value
    try:
        d = datetime.fromisoformat(value.replace(" ", "T"))
    except ValueError:
        return value
    if d.tzinfo is None:
        d = d.astimezone()
    return d.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _m3_utc(c: sqlite3.Connection) -> None:
    """Раньше часть записей хранилась в местном времени со смещением, часть — в UTC:
    сортировка строк по времени путала порядок. Приводим всё к одному формату."""
    for table, key, columns in [("events", "id", ["created_at"]), ("analyses", "id", ["created_at"]),
                                ("visual_results", "id", ["created_at"]), ("parameters", "id", ["updated_at"]),
                                ("equipment", "id", ["run_started_at"]), ("components", "id", ["hours_updated_at"])]:
        for col in columns:
            for row in c.execute(f"SELECT {key}, {col} FROM {table} WHERE {col} IS NOT NULL").fetchall():
                new = to_utc(row[1])
                if new != row[1]:
                    c.execute(f"UPDATE {table} SET {col} = ? WHERE {key} = ?", (new, row[0]))


def _m4_pump_scenario(c: sqlite3.Connection) -> None:
    """Учебный сценарий насоса раньше дописывался в базу при каждом старте (telemetry.configure).
    Теперь он в seed.py; для уже созданных баз — один раз здесь."""
    if not c.execute("SELECT 1 FROM equipment WHERE id = 'PUMP-001'").fetchone():
        return
    c.execute("INSERT OR IGNORE INTO scenario_state (equipment_id, row_index) VALUES ('PUMP-001', 0)")
    c.execute("UPDATE equipment SET name = ?, data_origin = ? WHERE id = 'PUMP-001'",
              ("Центробежный насос НА-1",
               "Учебная карточка. Нормативы и счетчики приняты командой; измерения из примеров организаторов."))
    c.execute("UPDATE components SET visual_access = 'internal' WHERE id IN "
              "('PUMP-001-MOTOR-BRG', 'PUMP-001-MOTOR-WND', 'PUMP-001-BRG', 'PUMP-001-IMP')")


def _m5_analysis_trace(c: sqlite3.Connection) -> None:
    """След запроса к модели: по нему можно воспроизвести и сравнить анализы разных моделей и промптов."""
    _add_columns(c, "analyses", {"prompt_version": "TEXT", "provider": "TEXT", "duration_s": "REAL",
                                 "tokens": "INTEGER", "raw_response": "TEXT"})


def _m6_org_defect_signs(c: sqlite3.Connection) -> None:
    """Признаки из справочника дефектов организаторов — в «Что осматривать» узлов уже созданной базы."""
    from .seed import inspect_signs
    for cid, raw in c.execute("SELECT id, inspect_for_json FROM components").fetchall():
        base = loads(raw, [])
        merged = inspect_signs(cid, base)
        if merged != base:
            c.execute("UPDATE components SET inspect_for_json = ? WHERE id = ?", (dumps(merged), cid))


def _m7_inspection_sessions(c: sqlite3.Connection) -> None:
    """Осмотр из нескольких кадров. В ветке, где он появился, это была 6-я миграция, а 6-й в main стали признаки
    дефектов, — поэтому обе идемпотентны и здесь признаки применяются ещё раз: база с любой ветки придёт к одной схеме."""
    _m6_org_defect_signs(c)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS inspection_sessions (
            id INTEGER PRIMARY KEY, equipment_id TEXT NOT NULL REFERENCES equipment(id),
            user_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','closed')),
            event_id INTEGER REFERENCES events(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ix_open_inspection
            ON inspection_sessions(equipment_id,user_id) WHERE status='draft';
        CREATE TABLE IF NOT EXISTS inspection_shots (
            session_id INTEGER NOT NULL REFERENCES inspection_sessions(id),
            analysis_id INTEGER NOT NULL UNIQUE REFERENCES analyses(id),
            PRIMARY KEY(session_id,analysis_id)
        );
    """)


MIGRATIONS: list[tuple[str, Callable[[sqlite3.Connection], None]]] = [
    ("базовая схема", _m1_base),
    ("пользователи, режим работы, источники нормативов", _m2_roles_runtime),
    ("время записей в UTC", _m3_utc),
    ("учебный сценарий насоса", _m4_pump_scenario),
    ("след ИИ-запроса в анализах", _m5_analysis_trace),
    ("признаки дефектов из справочника организаторов", _m6_org_defect_signs),
    ("осмотр с несколькими кадрами", _m7_inspection_sessions),
]


def migrate(c: sqlite3.Connection) -> None:
    version = c.execute("PRAGMA user_version").fetchone()[0]
    for number, (title, step) in enumerate(MIGRATIONS, start=1):
        if number <= version:
            continue
        with _lock:
            step(c)
            c.execute(f"PRAGMA user_version = {number}")
            c.commit()
        log.info("База данных: миграция %d — %s", number, title)


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    with _lock:
        c = conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def query(sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def is_empty() -> bool:
    row = query_one("SELECT COUNT(*) AS n FROM equipment")
    return not row or row["n"] == 0
