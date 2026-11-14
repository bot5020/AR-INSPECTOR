"""Словарь детектора YOLO-World: грубый класс объекта → текстовые подсказки.

Открытый детектор обучен на бытовых датасетах и не различает отраслевые узлы
(«двигатель» vs «насос» vs «муфта»), зато мгновенно и стабильно находит крупные объекты.
Подсказки подобраны по реакции модели на демо-фото (scripts/detect_demo.py):
манометр модель видит как «clock», задвижку и фланец — как «fire hydrant».
Точную привязку к узлам карточки делает vision-модель (analyze.py).

Подсказки «запекаются» в веса скриптом scripts/prepare_models.py — в работе CLIP и интернет не нужны.
"""
from __future__ import annotations

VOCAB: dict[str, list[str]] = {
    "machinery": ["machinery", "machine", "motor", "engine", "pump"],
    "electrical panel": ["electrical panel", "fuse box", "control panel"],
    "gauge": ["clock", "gauge", "dial", "thermometer"],
    "valve": ["fire hydrant", "valve", "handwheel"],
    "pipe": ["pipe"],
    "transformer": ["transformer", "tank"],
}

LABELS_RU: dict[str, str] = {
    "machinery": "агрегат",
    "electrical panel": "щит/шкаф",
    "gauge": "прибор",
    "valve": "арматура",
    "pipe": "трубопровод",
    "transformer": "трансформатор",
}

# Классы, наличие которых в кадре означает «оборудование в поле зрения» (для автозапуска анализа).
MAIN_OBJECTS = {"machinery", "electrical panel", "transformer"}

PROMPTS: list[str] = [p for prompts in VOCAB.values() for p in prompts]
PROMPT_TO_CLASS: dict[str, str] = {p: cls for cls, prompts in VOCAB.items() for p in prompts}
