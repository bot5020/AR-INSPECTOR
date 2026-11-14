"""Общие зависимости обработчиков FastAPI."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from . import assess, auth

# Роли: инженер записывает работы, мастер ещё и подтверждает осмотры, администратор ведёт нормативы.
anyone = auth.role()
engineer = auth.role("engineer", "master", "admin")
master = auth.role("master", "admin")
admin = auth.role("admin")

User = dict[str, Any]
AnyUser = Depends(anyone)
Engineer = Depends(engineer)
Master = Depends(master)
Admin = Depends(admin)


def card_or_404(eid: str) -> dict[str, Any]:
    card = assess.build_card(eid)
    if not card:
        raise HTTPException(404, "Оборудование не найдено")
    return card


def require_equipment(eid: str) -> str:
    """Проверка существования агрегата без сборки всей карточки."""
    from . import store
    if not store.equipment_brief(eid):
        raise HTTPException(404, "Оборудование не найдено")
    return eid
