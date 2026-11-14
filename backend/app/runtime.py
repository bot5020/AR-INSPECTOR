"""Наработка по фактическому времени нахождения в состоянии «в работе»."""
from datetime import datetime, timezone

from . import db


def running_hours(eq: dict, component: dict, now: datetime | None = None) -> float:
    if eq.get("run_state") != "running" or not eq.get("run_started_at"):
        return 0.0
    now = now or datetime.now(timezone.utc)
    def stamp(value):
        d = datetime.fromisoformat(value)
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    since = stamp(eq["run_started_at"])
    if component.get("hours_updated_at"):
        since = max(since, stamp(component["hours_updated_at"]))
    return max(0, (now - since).total_seconds() / 3600)


def set_state(eid: str, state: str, author: str) -> None:
    if state not in {"running", "stopped"}:
        raise ValueError("Выберите «в работе» или «остановлен».")
    with db.tx() as c:
        eq = dict(c.execute("SELECT * FROM equipment WHERE id=?", (eid,)).fetchone())
        if eq["run_state"] == state:
            return
        now = db.now_iso()
        if eq["run_state"] == "running":
            for row in c.execute("SELECT * FROM components WHERE equipment_id=?", (eid,)).fetchall():
                comp = dict(row)
                if comp["operating_hours"] is not None:
                    c.execute("UPDATE components SET operating_hours=?, hours_updated_at=? WHERE id=?",
                              (comp["operating_hours"] + running_hours(eq, comp), now, comp["id"]))
        c.execute("UPDATE equipment SET run_state=?, run_started_at=? WHERE id=?",
                  (state, now if state == "running" else None, eid))
        c.execute("INSERT INTO events(equipment_id,type,note,author,created_at) VALUES(?,?,?,?,?)",
                  (eid, "operation", "В работе" if state == "running" else "Остановлен", author, now))
