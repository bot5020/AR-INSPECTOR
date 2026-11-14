"""ИИ-анализ кадра: идентификация оборудования и визуальный осмотр узлов.

Гибрид: YOLO-World быстро находит крупные объекты, локальная vision-модель
привязывает узлы цифровой карточки к изображению (рамки 0–1000) и оценивает видимые признаки.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from typing import Annotated, Literal
from pydantic import BaseModel, Field, ValidationError
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from .. import db, defects, llm

log = logging.getLogger("analyze")

CATEGORIES = ["ok", "check", "defect"]
BBOX = {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4}

SYSTEM_INSPECT = """Ты — модуль предварительного визуального осмотра промышленного оборудования в мобильном AR-приложении инженера.
Тебе дают снимок с камеры и цифровую карточку оборудования со списком узлов.

Правила:
1. Сначала опиши в scene, какой объект действительно виден. Карточка — справочник возможных узлов, а не доказательство их присутствия. Затем найди только различимые узлы и дай их рамки: bbox = [x1, y1, x2, y2] в координатах 0–1000 относительно всего снимка. Заполнять весь список не нужно, пустой components допустим.
2. Оценивай только то, что видно. Внутренние неисправности по снимку не определяются — не выдумывай их.
3. Категории состояния:
   ok — видимых проблем не обнаружено (это НЕ подтверждение исправности);
   check — рекомендуется проверка: неоднозначные признаки, плохой ракурс, загрязнение мешает осмотру;
   defect — видны признаки возможного дефекта: коррозия, течь или подтёки, нагар, оплавление, потемнение от перегрева, трещины, отсутствующий крепёж или кожух, повреждённая изоляция.
4. Выводы делай только по изображению. Невидимые узлы не добавляй, не рисуй внутренние подшипники, обмотки и рабочее колесо сквозь закрытый корпус. Не объявляй отсутствие кожуха штатной конструкцией и не определяй по фото, работает ли агрегат.
5. Используй component_id из карточки. Видимый важный объект, которого нет в карточке, — component_id "other".
6. defect_bbox — рамка самого признака дефекта (пятно ржавчины, подтёк, нагар), если он виден; иначе пустой массив.
7. confidence — твоя уверенность в оценке состояния от 0 до 1.
8. Пиши по-русски. reason — до 20 слов, recommendation — до 15 слов, конкретное действие для инженера.
9. Приоритет внешних признаков: коррозия, следы утечки, повреждение кабеля/ребер/защитного кожуха. Не утверждай, что крепеж затянут, прибор исправен или температура измерена по цвету корпуса. Время работы и показания стрелок из кадра не выводи.
10. visibility=visible только когда сам внешний узел различим; при перекрытии occluded, при отсутствии not_visible. Отсутствие важного защитного кожуха требует проверки и не объясняется штатной конструкцией без документации.
11. matches_equipment=true, если в кадре это оборудование целиком ИЛИ крупным планом любой его узел из карточки (фланец, муфта, манометр, клеммник, контакты) — инженер специально досматривает узлы вблизи. false — только если в кадре явно другое оборудование или оборудования нет вовсе.
12. signs — только id признаков из справочника ниже (по документу «Типы дефектов промышленного оборудования»), без своих формулировок; если признака нет в списке, опиши его в reason. Для category ok signs пустой.
13. Не подгоняй видимый объект под название из карточки. Крышка с болтами и шток задвижки не являются корпусом насоса, муфтой или уплотнением вала. Муфту отмечай только при различимом соединении валов; уплотнение вала — при видимом месте выхода вала. На крупном плане арматуры могут быть видны только задвижка и фланцы. Не добавляй насос и двигатель за границами кадра.
14. Ржавчина и отслоение краски не доказывают течь; ржавый болт не доказывает ослабление крепежа. Если источник влажного пятна не виден, опиши пятно без утверждения, откуда течёт. Внутренние повреждения и причины отказа по внешнему виду не подтверждай.
15. Не повторяй одинаковые наблюдения. Для одного узла собери видимые признаки в одну запись; reason описывает именно область внутри bbox.

Справочник признаков (id — название: что видно):
""" + defects.prompt_list()


class Observation(BaseModel):
    component_id: str
    bbox: Annotated[list[Annotated[int, Field(strict=True, ge=0, le=1000)]], Field(min_length=4, max_length=4)]
    visibility: Literal["visible", "occluded", "not_visible"]
    category: Literal["ok", "check", "defect"]
    signs: list[str]
    reason: str
    recommendation: str
    defect_bbox: list[Annotated[int, Field(strict=True, ge=0, le=1000)]]
    confidence: Annotated[float, Field(ge=0, le=1)]


class Inspection(BaseModel):
    scene: str
    matches_equipment: Annotated[bool, Field(strict=True)]
    image_quality: Literal["good", "fair", "poor"]
    components: list[Observation]
    next_view: str

SCHEMA_INSPECT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scene": {"type": "string"},
        "matches_equipment": {"type": "boolean"},
        "image_quality": {"type": "string", "enum": ["good", "fair", "poor"]},
        "components": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "component_id": {"type": "string"},
                    "visibility": {"type": "string", "enum": ["visible", "occluded", "not_visible"]},
                    "bbox": BBOX,
                    "category": {"type": "string", "enum": CATEGORIES},
                    "signs": {"type": "array", "items": {"type": "string", "enum": list(defects.CLASSES)}},
                    "reason": {"type": "string"},
                    "recommendation": {"type": "string"},
                    "defect_bbox": {"type": "array", "items": {"type": "integer"}},
                    "confidence": {"type": "number"},
                },
                "required": ["component_id", "visibility", "bbox", "category", "signs", "reason", "recommendation",
                             "defect_bbox", "confidence"],
            },
        },
        "next_view": {"type": "string"},
    },
    "required": ["scene", "matches_equipment", "image_quality", "components", "next_view"],
}

# Версия промпта осмотра = хеш текста промпта и схемы ответа. Сохраняется в каждом анализе:
# по ней видно, какие результаты получены одним и тем же запросом, а какие — после правки промпта.
PROMPT_VERSION = "inspect-" + hashlib.sha1((SYSTEM_INSPECT + json.dumps(SCHEMA_INSPECT, sort_keys=True))
                                            .encode()).hexdigest()[:10]

SYSTEM_IDENTIFY = """Ты — модуль выбора предполагаемой карточки промышленного оборудования по снимку.
Используй читаемую маркировку, шильдики, серийные и инвентарные номера; без них сравни внешний вид и состав узлов справочника.
Предложи наиболее подходящий equipment_id из справочника, даже при низкой уверенности. Инженер подтвердит выбор.
Крупный план отдельного узла тоже подходит: например, двигатель может относиться к карточке насосного агрегата. Не требуй общий вид и шильдик для предположения.
Если подходят несколько карточек, выбери наиболее похожую, снизь confidence и объясни, почему выбор неоднозначен. Не выдавай тип оборудования за точное установление экземпляра.
В what_is_it назови видимый объект, в evidence объясни связь с выбранной карточкой и чего не хватает для точного определения (до 40 слов).
Не выдумывай маркировку: в nameplate_text только действительно читаемый текст, иначе пустая строка.
unknown нужен, только если оборудования не видно или ни у одной карточки нет даже слабого визуального соответствия. Низкая уверенность сама по себе не причина для unknown.
Пиши по-русски, confidence от 0 до 1 — твоя самооценка."""

SCHEMA_IDENTIFY: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "equipment_id": {"type": "string"},
        "confidence": {"type": "number"},
        "nameplate_text": {"type": "string"},
        "evidence": {"type": "string"},
        "what_is_it": {"type": "string"},
    },
    "required": ["equipment_id", "confidence", "nameplate_text", "evidence", "what_is_it"],
}


# ---------------------------------------------------------------- изображения

def load_image(data: bytes, max_side: int = 1600) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((max_side, max_side))
    return img


def to_jpeg(img: Image.Image, max_side: int = 1280, quality: int = 85) -> bytes:
    im = img.copy()
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def decode_qr(img: Image.Image) -> str | None:
    """QR на сервере (OpenCV) — запасной путь, если браузер не умеет BarcodeDetector."""
    try:
        import cv2
    except ImportError:
        return None
    arr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    det = cv2.QRCodeDetector()
    for scale in (1.0, 0.5):
        a = arr if scale == 1.0 else cv2.resize(arr, None, fx=scale, fy=scale)
        text, _pts, _ = det.detectAndDecode(a)
        if text:
            return text.strip()
    return None


def _norm_box(b: list[int] | None) -> list[float] | None:
    if not b or len(b) != 4:
        return None
    x1, y1, x2, y2 = (max(0, min(1000, int(v))) / 1000 for v in b)
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    if (x2 - x1) < 0.01 or (y2 - y1) < 0.01:
        return None
    return [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]


# ---------------------------------------------------------------- идентификация

def _norm_code(s: str) -> str:
    return re.sub(r"[^0-9a-zа-я]", "", s.lower())


def match_code(text: str) -> dict[str, Any] | None:
    """QR-код, серийный или инвентарный номер → оборудование."""
    if not text:
        return None
    t = text.strip()
    row = db.query_one("SELECT id FROM equipment WHERE qr_payload = ? OR id = ?", (t, t))
    if row:
        return row
    nt = _norm_code(t)
    for eq in db.query("SELECT id, serial_number, inventory_number, qr_payload FROM equipment"):
        for field in ("serial_number", "inventory_number", "id"):
            code = _norm_code(eq[field] or "")
            if len(code) >= 5 and code in nt:
                return {"id": eq["id"], "matched_field": field}
    return None


async def identify(img: Image.Image) -> dict[str, Any]:
    qr = decode_qr(img)
    if qr:
        hit = match_code(qr)
        if hit:
            return {"equipment_id": hit["id"], "method": "qr", "confidence": 1.0, "evidence": f"QR-код: {qr}"}

    catalog = db.query("SELECT id, name, type, model, serial_number, inventory_number, appearance FROM equipment")
    lines = [f"- {e['id']}: {e['name']} ({e['type']}, {e['model']}); зав. № {e['serial_number']}, "
             f"инв. № {e['inventory_number']}. Внешний вид: {e['appearance']}" for e in catalog]
    content = [
        {"type": "text", "text": "Справочник оборудования:\n" + "\n".join(lines) +
         "\n\nКакой образец на снимке? Перепиши весь читаемый текст шильдиков в nameplate_text."},
        llm.image_part(to_jpeg(img)),
    ]
    schema = {**SCHEMA_IDENTIFY, "properties": {**SCHEMA_IDENTIFY["properties"],
              "equipment_id": {"type": "string", "enum": [e["id"] for e in catalog] + ["unknown"]}}}
    data, meta = await llm.ask(SYSTEM_IDENTIFY, content, schema=schema, max_tokens=500)
    result = {
        "equipment_id": data.get("equipment_id") if data.get("equipment_id") in {e["id"] for e in catalog} else None,
        "method": "visual", "confidence": max(0.0, min(1.0, float(data.get("confidence") or 0))),
        "evidence": data.get("evidence"), "nameplate_text": data.get("nameplate_text"),
        "what_is_it": data.get("what_is_it"), "model": meta,
    }
    hit = match_code(data.get("nameplate_text") or "")
    if hit:  # номер с шильдика однозначнее внешнего вида
        result.update(equipment_id=hit["id"], method="nameplate", confidence=max(result["confidence"], 0.95))
    return result


# ---------------------------------------------------------------- осмотр

def _component_lines(card: dict[str, Any]) -> str:
    by_id = {c["id"]: c for c in card["components"]}
    out = []
    for c in card["components"]:
        if c.get("visual_access") == "internal":
            continue
        parent = f" (входит в {by_id[c['parent_id']]['name']})" if c["parent_id"] in by_id else ""
        look = "; ".join(c["inspect_for"]) or "общее состояние"
        out.append(f"- {c['id']}: {c['name']}{parent}. Что осматривать: {look}."
                   + " Только внешние видимые признаки.")
    return "\n".join(out)


def _inspect_content(card: dict[str, Any], img: Image.Image) -> list[dict[str, Any]]:
    eq = card["equipment"]
    return [
        {"type": "text", "text": (
            f"Выбранная карточка (может не соответствовать снимку): {eq['name']} — {eq['type']}, {eq['model']}.\n"
            f"Узлы и комплектующие по карточке:\n{_component_lines(card)}\n\n"
            "Найди видимые узлы, дай их рамки и оцени видимое состояние. В next_view подскажи, "
            "куда навести камеру для уточнения (одна фраза).")},
        llm.image_part(to_jpeg(img)),
    ]


def normalize_item(it: dict[str, Any], idx: int, known: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    box = _norm_box(it.get("bbox"))
    if not box:
        return None
    comp = known.get(it.get("component_id") or "")
    visibility = it.get("visibility") or "visible"
    if not comp or comp.get("visual_access") == "internal" or visibility == "not_visible":
        return None
    cat = it.get("category") if it.get("category") in CATEGORIES else "check"
    reason = (it.get("reason") or "").strip()
    if visibility == "occluded":
        # Частично перекрытый узел не выбрасываем: инженер должен знать, что его надо досмотреть.
        # «Норма» по неполному виду не ставится — минимум «проверить».
        if cat == "ok":
            cat = "check"
        reason = ("Узел частично перекрыт. " + reason).strip()
    try:
        conf = max(0.0, min(1.0, float(it.get("confidence") or 0)))
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "key": f"vlm-{idx}",
        "component_id": comp["id"] if comp else None,
        "name": comp["name"] if comp else "Узел вне карточки",
        "box": box,
        "defect_box": _norm_box(it.get("defect_bbox")),
        "category": cat,
        # только классы справочника, показываются русскими названиями; выдуманные модели признаки отбрасываются
        "signs": [defects.SIGN_NAMES[k] for k in defects.normalize_signs(it.get("signs") or [])][:5],
        "visibility": visibility,
        "reason": reason,
        "recommendation": (it.get("recommendation") or "").strip(),
        "confidence": conf,
    }


def completed_array_objects(text: str, key: str = '"components"') -> list[str]:
    """Законченные JSON-объекты внутри массива key — из ещё не дописанного ответа модели."""
    start = text.find(key)
    if start < 0:
        return []
    start = text.find("[", start)
    if start < 0:
        return []
    out, depth, in_str, esc, obj_start = [], 0, False, False, None
    for i in range(start + 1, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            if depth == 0 and ch == "{":
                obj_start = i
            depth += 1
        elif ch in "}]":
            if depth == 0 and ch == "]":
                break
            depth -= 1
            if depth == 0 and ch == "}" and obj_start is not None:
                out.append(text[obj_start:i + 1])
                obj_start = None
    return out


MSG_INCOMPLETE = "Ответ ИИ неполный или неверного формата. Осмотр не выполнен, снимите кадр повторно."
MSG_OTHER = "На кадре другое оборудование. Подтвердите карточку и переснимите объект."
MSG_POOR = "Кадр не позволяет провести осмотр. Подойдите ближе, уберите блики и переснимите."
MSG_NONE = "Видимые узлы карточки не найдены. Осмотр не выполнен, нужен другой ракурс."
_RE_MATCH = re.compile(r'"matches_equipment"\s*:\s*(true|false)')
_RE_QUALITY = re.compile(r'"image_quality"\s*:\s*"(good|fair|poor)"')


def _frame_gate(text: str) -> str | None:
    """Проверка кадра по флагам, которые схема ставит раньше списка узлов.

    "ok" — кадр принят, метки можно показывать; None — флаги ещё не дописаны; иначе текст ошибки."""
    match, quality = _RE_MATCH.search(text), _RE_QUALITY.search(text)
    if match and match.group(1) == "false":
        return MSG_OTHER
    if quality and quality.group(1) == "poor":
        return MSG_POOR
    return "ok" if match and quality else None


def _observation(raw: Any) -> dict[str, Any] | None:
    try:
        return Observation.model_validate(raw).model_dump()
    except ValidationError:
        return None


async def inspect_stream(card: dict[str, Any], img: Image.Image):
    """Генератор событий: ("item", метка) по мере генерации, затем ("done", полный результат).

    Метки уходят клиенту, как только модель дописала очередной узел, — но только после того,
    как в ответе появились флаги matches_equipment и image_quality и кадр по ним принят."""
    import json

    from ..mistral_client import parse_json

    known = {c["id"]: c for c in card["components"]}
    text, items, meta, emitted, gate = "", [], {}, 0, None
    seen: set[str] = set()

    def unique(item):
        if not item:
            return False
        # Убираем только точные повторы, сохраняя другие признаки и более серьёзные оценки.
        key = json.dumps({k: item[k] for k in ('component_id', 'box', 'category', 'signs', 'reason')}, sort_keys=True)
        if key in seen:
            return False
        seen.add(key)
        return True
    stream = llm.ask_stream(SYSTEM_INSPECT, _inspect_content(card, img), schema=SCHEMA_INSPECT, max_tokens=2000)
    try:
        async for delta, m in stream:
            if m:
                meta = m
            text += delta
            gate = _frame_gate(text)
            if gate not in (None, "ok"):
                break  # кадр отклонён по флагам — дальше не ждём
            if gate != "ok":
                continue
            objs = completed_array_objects(text)
            while emitted < len(objs):
                try:
                    raw = _observation(json.loads(objs[emitted]))
                except ValueError:
                    raw = None
                item = normalize_item(raw, emitted, known) if raw else None
                emitted += 1
                if unique(item):
                    items.append(item)
                    yield "item", item
    finally:
        await stream.aclose()
    if gate not in (None, "ok"):
        raise llm.LLMUnavailable(gate)
    partial = False
    try:
        data = Inspection.model_validate(parse_json(text)).model_dump()
    except (ValueError, ValidationError) as e:
        if not items:
            raise llm.LLMUnavailable(MSG_INCOMPLETE) from e
        # Ответ оборвался, но часть узлов уже разобрана и проверена — отдаём их, а не теряем весь осмотр.
        log.warning("Неполный ответ модели, показано узлов: %d", len(items))
        data, partial = {"scene": None, "matches_equipment": True, "image_quality": None, "components": [],
                         "next_view": "Ответ модели оборвался — для полного осмотра переснимите кадр."}, True
    if not data["matches_equipment"]:
        raise llm.LLMUnavailable(MSG_OTHER)
    if data["image_quality"] == "poor":
        raise llm.LLMUnavailable(MSG_POOR)
    for idx, raw in enumerate(data["components"]):
        if idx < emitted:
            continue
        item = normalize_item(raw, idx, known)
        if unique(item):
            items.append(item)
            yield "item", item
    if not items:
        raise llm.LLMUnavailable(MSG_NONE)
    yield "done", {
        "scene": data.get("scene"), "matches_equipment": bool(data.get("matches_equipment", True)),
        "image_quality": data.get("image_quality"), "next_view": data.get("next_view"),
        "items": items, "partial": partial, "model": meta,
        "trace": {"prompt_version": PROMPT_VERSION, "raw": text},
    }


async def inspect(card: dict[str, Any], img: Image.Image) -> dict[str, Any]:
    result: dict[str, Any] = {}
    async for kind, payload in inspect_stream(card, img):
        if kind == "done":
            result = payload
    return result
