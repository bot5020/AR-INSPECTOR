"""Проверочный набор ИИ-осмотра: сравнение моделей и версий промпта на одних и тех же фото.

Малый набор с ручным эталоном (demo/eval_cases.json), а не промышленная оценка точности.
Модель берётся из .env (VLM_URL, VLM_MODEL) — та, что сейчас загружена в LM Studio.

    .venv\\Scripts\\python scripts\\evaluate_vision.py                      # demo/eval_cases.json, 1 прогон
    .venv\\Scripts\\python scripts\\evaluate_vision.py --runs 3 --label qwen
    .venv\\Scripts\\python scripts\\evaluate_vision.py --cases свой_набор.json

Отчёт: data/eval/vision-<метка>-<версия промпта>-<время>.json и сводная таблица в консоли.
Пути к фото в наборе — от корня проекта или абсолютные.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="eval-"))   # карточки — из чистого демо-набора

from PIL import Image  # noqa: E402

from app import assess, config, llm, seed  # noqa: E402
from app.vision import analyze  # noqa: E402

ALERT = ("check", "defect")


def _hit(expected: str, ids: set[str]) -> bool:
    return any(alt in ids for alt in expected.split("|"))


def score(case: dict, data: dict | None, refused: bool) -> dict:
    if case.get("expect_refused"):
        return {"passed": refused}
    if refused or data is None:
        return {"passed": False, "found": 0, "alerts": 0, "false_alerts": 0}
    found = {i["component_id"] for i in data["items"]}
    alerted = {i["component_id"] for i in data["items"] if i["category"] in ALERT}
    exp_found, exp_alert, exp_ok = case.get("expect_found", []), case.get("expect_alert", []), case.get("expect_ok", [])
    out = {
        "found": sum(_hit(e, found) for e in exp_found), "found_of": len(exp_found),
        "alerts": sum(_hit(e, alerted) for e in exp_alert), "alerts_of": len(exp_alert),
        "false_alerts": sum(_hit(e, alerted) for e in exp_ok),
    }
    out["passed"] = out["found"] == out["found_of"] and out["alerts"] == out["alerts_of"] and not out["false_alerts"]
    return out


async def run_case(case: dict) -> dict:
    card = assess.build_card(case["equipment"])
    photo = case.get("photo")
    if photo:
        path = Path(photo) if Path(photo).is_absolute() else ROOT / photo
        image = analyze.load_image(path.read_bytes())
    else:
        image = Image.new("RGB", (640, 480), "white")
    t0 = time.monotonic()
    data, refused, reason = None, False, None
    try:
        data = await analyze.inspect(card, image)
    except llm.LLMUnavailable as e:
        refused, reason = True, str(e)
    seconds = time.monotonic() - t0
    meta = (data or {}).get("model") or {}
    tokens = meta.get("tokens")
    return {"case": case["case"], "seconds": round(seconds, 1), "tokens": tokens,
            "tok_s": round(tokens / seconds, 1) if tokens and seconds else None,
            "refused": refused, "reason": reason, **score(case, data, refused),
            "items": [{k: i.get(k) for k in ("component_id", "category", "visibility", "reason")} for i in (data or {}).get("items", [])],
            "partial": (data or {}).get("partial", False)}


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases", default=str(ROOT / "demo" / "eval_cases.json"))
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--label", default=config.VLM_MODEL, help="метка модели в имени отчёта (например qwen или bonsai)")
    args = ap.parse_args()
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))["cases"]
    seed.seed()
    status = await llm.status()
    print(f"Модель: {status.get('model')} · {config.VLM_URL} · промпт {analyze.PROMPT_VERSION}\n")
    results = []
    for run in range(1, args.runs + 1):
        for case in cases:
            r = await run_case(case)
            r["run"] = run
            results.append(r)
            mark = "✓" if r["passed"] else "✗"
            extra = "отказ: " + r["reason"] if r["refused"] else (
                f"узлы {r.get('found', 0)}/{r.get('found_of', 0)}, тревоги {r.get('alerts', 0)}/{r.get('alerts_of', 0)}, "
                f"ложные {r.get('false_alerts', 0)}")
            print(f"{mark} {r['case']:<18} {r['seconds']:>5} с  {extra}", flush=True)
    ok = [r for r in results if not r["refused"]]
    summary = {
        "passed": f"{sum(r['passed'] for r in results)} из {len(results)}",
        "avg_seconds": round(statistics.mean(r["seconds"] for r in ok), 1) if ok else None,
        "avg_tok_s": round(statistics.mean(r["tok_s"] for r in ok if r["tok_s"]), 1) if any(r["tok_s"] for r in ok) else None,
    }
    print(f"\nИтого: {summary['passed']} · среднее время {summary['avg_seconds']} с · {summary['avg_tok_s']} ток/с")
    out_dir = ROOT / "data" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"vision-{args.label}-{analyze.PROMPT_VERSION}-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"model": status.get("model"), "label": args.label, "url": config.VLM_URL,
                               "prompt_version": analyze.PROMPT_VERSION, "cases_file": args.cases,
                               "summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Отчёт: {out}")


if __name__ == "__main__":
    asyncio.run(main())
