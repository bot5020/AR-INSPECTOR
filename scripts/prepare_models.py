"""Подготовка весов детектора (выполняется один раз при развёртывании стенда, нужен интернет).

Скачивает YOLO-World, задаёт словарь узлов из backend/app/vision/vocab.py и сохраняет
модель с «запечёнными» классами в models/. После этого сервис работает полностью офлайн.

    .venv\\Scripts\\python scripts\\prepare_models.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"
MODELS.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(MODELS)  # ultralytics скачивает веса в текущую папку

from ultralytics import YOLOWorld  # noqa: E402

from app.vision.vocab import PROMPTS  # noqa: E402

for size in sys.argv[1:] or ["l", "s"]:
    base = f"yolov8{size}-worldv2.pt"
    out = MODELS / f"equipment-world-{size}.pt"
    model = YOLOWorld(base)
    model.set_classes(PROMPTS)
    model.save(str(out))
    print(f"OK {out.name}: {len(PROMPTS)} подсказок")
