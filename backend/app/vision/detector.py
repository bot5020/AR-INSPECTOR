"""Детектор узлов YOLO-World (офлайн-словарь из vocab.py) — в отдельном процессе.

Почему процесс, а не поток: импорт torch и загрузка модели на ROCm занимают Python на десятки секунд,
и сервер в это время не отвечал (вход отваливался по таймауту). А вылет драйвера ROCm на Windows
(access violation в hipMalloc) нельзя перехватить исключением — в общем процессе он ронял всё приложение.
Теперь сервер сразу отвечает «детектор загружается», а падение детектора не трогает вход, карточки и ИИ-осмотр:
процесс перезапускается при следующем запросе.

GPU проверяется ещё одним коротким процессом (_gpu_smoke_ok); если проверка не прошла — CPU с лёгкой моделью.
"""
from __future__ import annotations

import io
import logging
import multiprocessing as mp
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("YOLO_OFFLINE", "1")  # не ходить в сеть за обновлениями/шрифтами

from PIL import Image  # noqa: E402

from .vocab import PROMPT_TO_CLASS  # noqa: E402

log = logging.getLogger("detector")
ROOT = Path(__file__).resolve().parents[3]
MODELS = Path(os.getenv("MODELS_DIR", str(ROOT / "models")))
GPU_WEIGHTS = MODELS / os.getenv("DETECTOR_GPU_WEIGHTS", "equipment-world-l.pt")
CPU_WEIGHTS = MODELS / os.getenv("DETECTOR_CPU_WEIGHTS", "equipment-world-s.pt")
MAX_RESTARTS = 3


def _accelerator() -> tuple[str | int, str] | None:
    """Доступный ускоритель: ROCm/CUDA (устройство 0) или Apple Metal (mps)."""
    try:
        import torch
    except ImportError:
        return None
    if torch.cuda.is_available():
        return 0, "GPU (ROCm)" if getattr(torch.version, "hip", None) else "GPU (CUDA)"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps", "GPU (Apple MPS)"
    return None


def _gpu_smoke_ok(weights: Path, device: str | int, timeout: float = 180) -> bool:
    code = (
        "import os, sys; os.environ['YOLO_OFFLINE']='1'\n"
        "import numpy as np, torch\n"
        "from ultralytics import YOLO\n"
        f"m = YOLO(r'{weights}')\n"
        "img = (np.random.rand(480, 640, 3) * 255).astype('uint8')\n"
        f"for _ in range(3): m.predict(img, device={device!r}, imgsz=640, verbose=False)\n"
        "name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'Apple GPU'\n"
        "print('GPU_OK', name)\n"
    )
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("Проверка GPU не уложилась в %s с", timeout)
        return False
    ok = r.returncode == 0 and "GPU_OK" in r.stdout
    if ok:
        log.info("GPU пригоден: %s", r.stdout.strip().splitlines()[-1])
    else:
        log.warning("GPU недоступен (код %s): %s", r.returncode, (r.stderr or r.stdout)[-400:])
    return ok


# ---------------------------------------------------------------- процесс-исполнитель

def _predict(model, device, image_bytes: bytes, classes: set[str] | None, conf: float, imgsz: int) -> dict[str, Any]:
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = image.size
    t = time.perf_counter()
    res = model.predict(image, device=device, imgsz=imgsz, conf=conf, iou=0.5, verbose=False, max_det=60)[0]
    ms = (time.perf_counter() - t) * 1000
    dets: list[dict[str, Any]] = []
    for box, score, cls_idx in zip(res.boxes.xyxy.tolist(), res.boxes.conf.tolist(), res.boxes.cls.tolist()):
        prompt = res.names[int(cls_idx)]
        cls = PROMPT_TO_CLASS.get(prompt, prompt)
        if classes and cls not in classes:
            continue
        x1, y1, x2, y2 = box
        dets.append({"cls": cls, "prompt": prompt, "conf": round(float(score), 3),
                     "box": [round(x1 / w, 4), round(y1 / h, 4), round(x2 / w, 4), round(y2 / h, 4)]})
    return {"width": w, "height": h, "ms": round(ms, 1), "detections": _merge_same_class(dets)}


def _worker(conn, want: str) -> None:
    """Точка входа процесса: загрузить модель, сообщить статус, отвечать на запросы до закрытия канала."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s detector-worker: %(message)s")
    try:
        from ultralytics import YOLO
        import numpy as np

        acc = _accelerator() if want in ("auto", "gpu", "cuda", "0", "mps") else None
        if acc and GPU_WEIGHTS.exists() and _gpu_smoke_ok(GPU_WEIGHTS, acc[0]):
            (device, label), weights = acc, GPU_WEIGHTS
        else:
            device, label = "cpu", "CPU"
            weights = CPU_WEIGHTS if CPU_WEIGHTS.exists() else GPU_WEIGHTS
        if not weights.exists():
            raise FileNotFoundError(f"Нет весов {weights}. Запустите scripts/prepare_models.py")
        model = YOLO(str(weights))
        model.predict(np.zeros((320, 320, 3), dtype="uint8"), device=device, verbose=False)
    except Exception as e:  # noqa: BLE001
        conn.send({"status": "error", "error": f"{type(e).__name__}: {e}"})
        return
    conn.send({"status": "ready", "device": label, "weights": weights.name})
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return
        try:
            conn.send({"ok": _predict(model, device, *msg)})
        except Exception as e:  # noqa: BLE001 — один плохой кадр не роняет процесс
            conn.send({"error": f"{type(e).__name__}: {e}"})


# ---------------------------------------------------------------- клиент в процессе сервера

class Detector:
    """Тот же интерфейс, что был у детектора в потоке: start(), status(), detect(), ready."""

    def __init__(self) -> None:
        self.ready = False
        self.loading = False
        self.error: str | None = None
        self.device_label: str | None = None
        self.weights: str | None = None
        self.restarts = 0
        self._proc: mp.process.BaseProcess | None = None
        self._conn = None
        self._lock = threading.Lock()   # один запрос в канале одновременно

    def start(self) -> None:
        if self.loading or self.ready:
            return
        ctx = mp.get_context("spawn")   # spawn: одинаково на Windows, macOS и Linux, без копии состояния сервера
        parent, child = ctx.Pipe()
        want = os.getenv("DETECTOR_DEVICE", "auto").lower()
        self._proc = ctx.Process(target=_worker, args=(child, want), name="detector", daemon=True)
        self._conn, self.loading, self.error = parent, True, None
        self._proc.start()
        child.close()
        threading.Thread(target=self._await_ready, daemon=True, name="detector-wait").start()

    def _await_ready(self) -> None:
        try:
            msg = self._conn.recv()
        except (EOFError, OSError):
            msg = {"status": "error", "error": "Процесс детектора завершился при загрузке"}
        if msg.get("status") == "ready":
            self.device_label, self.weights, self.ready = msg["device"], msg["weights"], True
            log.info("Детектор готов: %s, %s", self.weights, self.device_label)
        else:
            self.error = msg.get("error")
            log.error("Детектор не загрузился: %s", self.error)
        self.loading = False

    def _fail(self, reason: str) -> None:
        log.warning("Детектор: %s — процесс будет перезапущен", reason)
        self.ready, self.error = False, reason
        if self._proc and self._proc.is_alive():
            self._proc.kill()
        if self.restarts < MAX_RESTARTS:
            self.restarts += 1
            self.start()

    def status(self) -> dict[str, Any]:
        return {"ready": self.ready, "loading": self.loading, "error": self.error,
                "device": self.device_label if self.ready else None, "weights": self.weights,
                "process": "отдельный процесс", "restarts": self.restarts}

    def detect(self, image: Image.Image | bytes, classes: set[str] | None = None, conf: float = 0.12,
               imgsz: int = 640, timeout: float | None = None) -> dict[str, Any]:
        from .. import config
        if not self.ready:
            raise RuntimeError(self.error or "Детектор ещё загружается")
        if not isinstance(image, (bytes, bytearray)):
            buf = io.BytesIO()
            image.convert("RGB").save(buf, format="JPEG", quality=85)
            image = buf.getvalue()
        with self._lock:
            try:
                self._conn.send((bytes(image), classes, conf, imgsz))
                if not self._conn.poll(timeout or config.DETECTOR_TIMEOUT):
                    self._fail("нет ответа за отведённое время")
                    raise RuntimeError("Детектор не ответил вовремя")
                msg = self._conn.recv()
            except (EOFError, OSError, BrokenPipeError):
                self._fail("процесс завершился")
                raise RuntimeError("Детектор перезапускается") from None
        if "error" in msg:
            raise RuntimeError(msg["error"])
        return msg["ok"]


def _iou(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _merge_same_class(dets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Несколько подсказок одного класса дают дубли рамок — оставляем самую уверенную."""
    dets = sorted(dets, key=lambda d: -d["conf"])
    kept: list[dict[str, Any]] = []
    for d in dets:
        if any(k["cls"] == d["cls"] and _iou(k["box"], d["box"]) > 0.5 for k in kept):
            continue
        kept.append(d)
    return kept


detector = Detector()
