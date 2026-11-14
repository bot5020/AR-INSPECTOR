#!/usr/bin/env python3
"""AR-Инспектор: единый скрипт установки и запуска для Windows, macOS и Linux.

    python start.py setup    установка (один раз, нужен интернет)
    python start.py          запуск стенда (интернет не нужен)
    python start.py check    диагностика: окружение, модели, сеть, брандмауэр

Обёртки: launch.bat (Windows, можно двойным щелчком) и ./launch.sh (macOS/Linux) передают сюда те же аргументы.
Скрипт использует только стандартную библиотеку Python 3.10+.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
VENV = ROOT / ".venv"
VPY = VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")
WEIGHTS = [ROOT / "models" / "equipment-world-l.pt", ROOT / "models" / "equipment-world-s.pt"]
DEFAULT_MODEL = "qwen3.8-27b"
DEFAULT_CONTEXT = 6144
LMS_PORT = 8000
CLIP = "git+https://github.com/ultralytics/CLIP.git"

# Проверенная на стенде сборка PyTorch для Windows + AMD RX 9070/9070 XT (gfx1201).
ROCM_WIN_INDEX = ["--index-url", "https://stable.repo.amd.com/rocm/pytorch/whl-next/",
                  "--extra-index-url", "https://stable.repo.amd.com/rocm/core/whl-next/",
                  "--extra-index-url", "https://pypi.org/simple"]
ROCM_WIN_PKGS = ["torch==2.14.0+rocm10.1.0", "torchvision==0.29.0a0+rocm10.1.0",
                 "amd-torch-device-gfx1201==2.14.0+rocm10.1.0", "amd-torch-device-gfx12-0==2.14.0+rocm10.1.0",
                 "amd-torchvision-device-gfx1201==0.29.0a0+rocm10.1.0", "rocm-sdk-device-gfx1201==10.1.0",
                 "rocm==10.1.0"]
TORCH_INDEX = {
    "cuda-win": "https://download.pytorch.org/whl/cu128",
    "rocm-linux": "https://download.pytorch.org/whl/rocm6.4",
    "cpu": "https://download.pytorch.org/whl/cpu",
}

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


# ---------------------------------------------------------------- вывод и команды

def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    say(f"\n==> {msg}")


def warn(msg: str) -> None:
    say(f"  [!] {msg}")


def die(msg: str) -> None:
    say(f"\n[ошибка] {msg}")
    sys.exit(1)


def run(cmd: list[str], check: bool = True, **kw) -> subprocess.CompletedProcess:
    say("  $ " + " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], **kw)
    if check and r.returncode != 0:
        die(f"команда завершилась с кодом {r.returncode}")
    return r


def capture(cmd: list[str], timeout: float = 30) -> str:
    try:
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


# ---------------------------------------------------------------- окружение

def detect_gpu() -> dict[str, str]:
    """Производитель и модель видеокарты: nvidia / amd / apple / none."""
    names: list[str] = []
    if IS_MAC:
        if platform.machine() == "arm64":
            return {"vendor": "apple", "name": "Apple Silicon"}
        return {"vendor": "none", "name": "Intel Mac"}
    if IS_WIN:
        out = capture(["powershell", "-NoProfile", "-Command",
                       "(Get-CimInstance Win32_VideoController).Name"])
        names = [x.strip() for x in out.splitlines() if x.strip()]
    elif IS_LINUX:
        if shutil.which("nvidia-smi"):
            out = capture(["nvidia-smi", "-L"])
            if "GPU" in out:
                return {"vendor": "nvidia", "name": out.splitlines()[0]}
        out = capture(["lspci"]) if shutil.which("lspci") else ""
        names = [x for x in out.splitlines() if any(k in x for k in ("VGA", "3D", "Display"))]
        if not names:
            for vendor_file in Path("/sys/class/drm").glob("card*/device/vendor"):
                v = vendor_file.read_text().strip()
                names.append({"0x10de": "NVIDIA", "0x1002": "AMD Radeon"}.get(v, v))
    text = " ".join(names)
    low = text.lower()
    if "nvidia" in low:
        return {"vendor": "nvidia", "name": text}
    if "radeon" in low or "amd" in low or "advanced micro devices" in low:
        return {"vendor": "amd", "name": text}
    return {"vendor": "none", "name": text or "не определена"}


def torch_plan(choice: str, index: str | None, gpu: dict[str, str]) -> tuple[str, list[str]]:
    """Какую сборку PyTorch ставить: (описание, аргументы pip install)."""
    if index:
        return f"PyTorch с индекса {index}", ["--index-url", index, "torch", "torchvision"]
    if choice == "skip":
        return "PyTorch: установка пропущена", []
    if choice == "auto":
        name = gpu["name"].lower()
        if IS_MAC:
            choice = "default"
        elif IS_WIN and gpu["vendor"] == "amd" and ("9070" in name or "gfx1201" in name):
            choice = "rocm-win"
        elif gpu["vendor"] == "nvidia":
            choice = "cuda"
        elif IS_LINUX and gpu["vendor"] == "amd":
            choice = "rocm"
        else:
            if gpu["vendor"] == "amd":
                warn(f"Для видеокарты «{gpu['name']}» готовой ROCm-сборки в скрипте нет — ставлю CPU-версию. "
                     "Свою сборку можно указать: --torch-index <URL>.")
            choice = "cpu"
    if choice == "rocm-win":
        return "PyTorch ROCm 10.1 для AMD RX 9070 (Windows)", ROCM_WIN_INDEX + ROCM_WIN_PKGS
    if choice == "cuda":
        if IS_WIN:
            return "PyTorch CUDA 12.8 (Windows)", ["--index-url", TORCH_INDEX["cuda-win"], "torch", "torchvision"]
        return "PyTorch с CUDA (PyPI)", ["torch", "torchvision"]
    if choice == "rocm":
        return "PyTorch ROCm (Linux)", ["--index-url", TORCH_INDEX["rocm-linux"], "torch", "torchvision"]
    if choice == "default":
        return "PyTorch (PyPI; на Mac — с ускорением Metal/MPS)", ["torch", "torchvision"]
    return "PyTorch, только CPU", ["--index-url", TORCH_INDEX["cpu"], "torch", "torchvision"]


def venv_base_python(need_312: bool) -> list[str]:
    """Интерпретатор для создания .venv. Сборки ROCm для Windows выпущены под Python 3.12."""
    if need_312 and sys.version_info[:2] != (3, 12):
        if IS_WIN and shutil.which("py") and "3.12" in capture(["py", "-3.12", "--version"]):
            return ["py", "-3.12"]
        die("Для ROCm-сборки под Windows нужен Python 3.12: установите его (python.org) и повторите.")
    return [sys.executable]


def lms_path() -> Path | None:
    exe = "lms.exe" if IS_WIN else "lms"
    p = Path.home() / ".lmstudio" / "bin" / exe
    if p.exists():
        return p
    found = shutil.which("lms")
    return Path(found) if found else None


def http_json(url: str, timeout: float = 3) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 — только localhost
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001
        return None


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def env_value(name: str, default: str) -> str:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip() or default
    return os.getenv(name, default)


def firewall_hint(port: int) -> str:
    if IS_WIN:
        return ("PowerShell от администратора (один раз):\n"
                f'      New-NetFirewallRule -DisplayName "AR-Инспектор" -Direction Inbound -Protocol TCP '
                f"-LocalPort {port} -Action Allow -Profile Private")
    if IS_MAC:
        return "При первом запуске macOS спросит «Разрешить входящие подключения» для Python — нажмите «Разрешить»."
    if shutil.which("ufw"):
        return f"Если включён ufw: sudo ufw allow {port}/tcp"
    if shutil.which("firewall-cmd"):
        return f"firewalld: sudo firewall-cmd --add-port={port}/tcp --permanent && sudo firewall-cmd --reload"
    return f"Если включён брандмауэр — разрешите входящий TCP-порт {port}."


def windows_rule_exists() -> bool:
    out = capture(["powershell", "-NoProfile", "-Command",
                   "Get-NetFirewallRule -DisplayName 'AR-Инспектор' -ErrorAction SilentlyContinue | "
                   "Select-Object -ExpandProperty Enabled"])
    return "True" in out


# ---------------------------------------------------------------- setup

def cmd_setup(args: argparse.Namespace) -> None:
    say(f"AR-Инспектор — установка ({platform.system()} {platform.machine()}, Python {platform.python_version()})")
    if sys.version_info < (3, 10):
        die("Нужен Python 3.10 или новее.")
    gpu = detect_gpu()
    say(f"Видеокарта: {gpu['name']} ({gpu['vendor']})")
    label, torch_args = torch_plan(args.torch, args.torch_index, gpu)

    step("1/6 Виртуальное окружение .venv")
    if VPY.exists():
        say("  уже есть")
    else:
        run(venv_base_python("rocm==10.1.0" in torch_args) + ["-m", "venv", str(VENV)])
    run([VPY, "-m", "pip", "install", "--upgrade", "pip", "-q"])

    step(f"2/6 {label}")
    has_torch = subprocess.run([str(VPY), "-c", "import torch"], capture_output=True).returncode == 0
    if has_torch and not args.reinstall_torch:
        say("  PyTorch уже установлен (переустановить: --reinstall-torch)")
    elif args.torch == "skip":
        say("  пропущено (--torch skip)")
    else:
        run([VPY, "-m", "pip", "install", *torch_args])

    step("3/6 Зависимости приложения")
    run([VPY, "-m", "pip", "install", "-r", ROOT / "backend" / "requirements.txt", CLIP])

    step("4/6 Ускоритель для детектора")
    out = capture([VPY, "-c", ACCEL_SNIPPET], timeout=120).strip()
    say(f"  {out.splitlines()[-1] if out else 'не удалось проверить'}")

    step("5/6 Веса детектора YOLO-World (models/)")
    if all(w.exists() for w in WEIGHTS) and not args.force_models:
        say("  уже подготовлены (пересобрать: --force-models)")
    else:
        run([VPY, ROOT / "scripts" / "prepare_models.py"])

    step("6/6 Настройки и ИИ-модель")
    env_file = ROOT / ".env"
    if not env_file.exists():
        shutil.copy(ROOT / ".env.example", env_file)
        say("  создан .env из .env.example")
    else:
        say("  .env уже есть")
    lms = lms_path()
    if not lms:
        warn("LM Studio не найден. Установите его с https://lmstudio.ai и запустите один раз.")
    else:
        models = capture([lms, "ls"], timeout=60)
        if args.model.lower() in models.lower():
            say(f"  модель «{args.model}» найдена в LM Studio")
        else:
            warn(f"В LM Studio нет модели «{args.model}». Скачайте vision-модель в LM Studio (поиск: Qwen3.8-27B, "
                 "unsloth, вариант UD-IQ3_XXS) или укажите свою при запуске: start.py run --model <ключ из «lms ls»>.")

    port = int(env_value("HTTPS_PORT", "8080"))
    say("\nУстановка завершена. Дальше интернет не нужен.")
    if IS_WIN and windows_rule_exists():
        say("  Брандмауэр — правило «AR-Инспектор» уже есть")
    else:
        say(f"  Брандмауэр — {firewall_hint(port)}")
    say(f"  Запуск:  {'launch.bat' if IS_WIN else './launch.sh'}   (или python start.py)")


ACCEL_SNIPPET = """
import torch
if torch.cuda.is_available():
    kind = 'ROCm' if getattr(torch.version, 'hip', None) else 'CUDA'
    print(f'GPU ({kind}): {torch.cuda.get_device_name(0)}, torch {torch.__version__}')
elif getattr(torch.backends, 'mps', None) is not None and torch.backends.mps.is_available():
    print(f'GPU (Apple MPS), torch {torch.__version__}')
else:
    print(f'CPU (ускоритель не найден), torch {torch.__version__}')
"""


# ---------------------------------------------------------------- run

def ensure_lms_model(model: str, context: int) -> None:
    lms = lms_path()
    if not lms:
        warn("LM Studio (lms) не найден: ИИ-анализ будет недоступен, остальные функции работают.")
        return
    step(f"LM Studio: сервер моделей на порту {LMS_PORT}")
    run([lms, "server", "start", "--port", str(LMS_PORT)], check=False, capture_output=True)
    if "vlm" in capture([lms, "ps"]).split():
        say("  модель vlm уже загружена")
    else:
        # Один слот и контекст 6144: 27B-модель целиком помещается в 16 ГБ видеопамяти (иначе скорость падает втрое).
        step(f"LM Studio: загружаю модель «{model}» как vlm (около 1,5 минуты)")
        r = run([lms, "load", model, "--gpu", "max", "--context-length", str(context), "--parallel", "1",
                 "--identifier", "vlm", "-y"], check=False, capture_output=True)
        if r.returncode != 0:
            warn(f"Модель не загрузилась. Проверьте ключ в «lms ls» и запустите: start.py run --model <ключ>.")
    say(capture([lms, "ps"]).strip())


def cmd_run(args: argparse.Namespace) -> None:
    if not VPY.exists():
        die(f"Нет окружения .venv — сначала выполните установку: {'launch.bat' if IS_WIN else './launch.sh'} setup")
    if not all(w.exists() for w in WEIGHTS):
        warn("Нет весов детектора в models/ — выполните установку (setup). Детектор будет недоступен.")
    port = int(env_value("HTTPS_PORT", "8080"))
    if port_open(port):
        die(f"Порт {port} уже занят — возможно, сервер уже запущен. Остановите его или смените HTTPS_PORT в .env.")
    if not args.no_lms and env_value("VLM_MANAGED_EXTERNALLY", "0") != "1":
        ensure_lms_model(args.model, args.context)
    if IS_WIN and not windows_rule_exists():
        warn("Нет правила брандмауэра для телефона — он не сможет подключиться. " + firewall_hint(port))
    step("Сервер приложения (остановка — Ctrl+C)")
    try:
        code = subprocess.call([str(VPY), str(ROOT / "backend" / "run.py")], cwd=str(ROOT))
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


# ---------------------------------------------------------------- check

def cmd_check(args: argparse.Namespace) -> None:
    ok, bad = "OK ", "!! "

    def line(good: bool, what: str, detail: str = "") -> None:
        say(f"  {ok if good else bad} {what}" + (f" — {detail}" if detail else ""))

    say(f"AR-Инспектор — диагностика ({platform.system()} {platform.release()}, {platform.machine()})")
    step("Окружение")
    line(sys.version_info >= (3, 10), "Python", platform.python_version())
    line(VPY.exists(), ".venv", str(VPY) if VPY.exists() else "нет — выполните setup")
    if VPY.exists():
        out = capture([VPY, "-c", ACCEL_SNIPPET], timeout=120).strip()
        line(bool(out) and "Error" not in out, "PyTorch", out.splitlines()[-1] if out else "не установлен")
    line(all(w.exists() for w in WEIGHTS), "Веса детектора", "models/equipment-world-*.pt")
    line((ROOT / ".env").exists(), ".env")

    step("ИИ-модель (LM Studio)")
    lms = lms_path()
    line(lms is not None, "lms", str(lms) if lms else "LM Studio не установлен или не запускался")
    data = http_json(f"http://127.0.0.1:{LMS_PORT}/v1/models")
    line(data is not None, f"Сервер моделей :{LMS_PORT}", "работает" if data else "не запущен")
    if data is not None:
        ids = [m.get("id") for m in data.get("data", [])]
        loaded = "vlm" in capture([lms, "ps"]).split() if lms else False
        line(loaded, "Модель vlm загружена", "" if loaded else f"нет — запустите start (модель: {args.model})")
        if args.model not in ids and lms:
            line(False, f"Модель «{args.model}» скачана", "не найдена в LM Studio")

    step("Сервер приложения и сеть")
    port = int(env_value("HTTPS_PORT", "8080"))
    running = port_open(port)
    line(running, f"Приложение :{port}", "запущено" if running else "не запущено")
    if VPY.exists():
        out = capture([VPY, "-c", "import sys; sys.path.insert(0, 'backend'); from app.certs import local_ips, "
                       "primary_ip; ips = local_ips(); print(primary_ip(ips) or ''); print(', '.join(ips))"],
                      timeout=60)
        rows = [x.strip() for x in out.strip().splitlines()]
        if rows and rows[0]:
            say(f"       Адрес для телефона: https://{rows[0]}:{port}/")
            if len(rows) > 1:
                say(f"       Все адреса сервера: {rows[1]}")
    if IS_WIN:
        exists = windows_rule_exists()
        line(exists, "Правило брандмауэра «AR-Инспектор»", "" if exists else firewall_hint(port))
    else:
        say(f"       Брандмауэр: {firewall_hint(port)}")


# ---------------------------------------------------------------- main

def main() -> None:
    default_model = env_value("LMSTUDIO_MODEL", DEFAULT_MODEL)
    p = argparse.ArgumentParser(description="AR-Инспектор: установка, запуск и диагностика (Windows/macOS/Linux)")
    sub = p.add_subparsers(dest="cmd")

    ps = sub.add_parser("setup", help="установка (один раз, нужен интернет)")
    ps.add_argument("--torch", default="auto", choices=["auto", "rocm-win", "cuda", "rocm", "default", "cpu", "skip"],
                    help="сборка PyTorch (по умолчанию — по ОС и видеокарте)")
    ps.add_argument("--torch-index", help="свой индекс pip для torch/torchvision")
    ps.add_argument("--reinstall-torch", action="store_true", help="переустановить PyTorch")
    ps.add_argument("--force-models", action="store_true", help="пересобрать веса детектора")
    ps.add_argument("--model", default=default_model, help="ключ ИИ-модели в LM Studio для проверки")

    for name, helptext in (("run", "запуск стенда (по умолчанию)"), ("check", "диагностика")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--model", default=default_model, help=f"ключ модели в LM Studio (по умолчанию {default_model})")
        sp.add_argument("--context", type=int, default=DEFAULT_CONTEXT, help="размер контекста модели")
        sp.add_argument("--no-lms", action="store_true", help="не трогать LM Studio (модель запущена отдельно)")

    args = p.parse_args(sys.argv[1:] or ["run"])
    {"setup": cmd_setup, "run": cmd_run, "check": cmd_check}[args.cmd or "run"](args)


if __name__ == "__main__":
    main()
