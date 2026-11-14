"""Отчет строится из сохраненного снимка данных осмотра, а не текущих показаний."""
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import re
from xml.sax.saxutils import escape

from fastapi import HTTPException

from . import db, store


def sections(event: dict) -> tuple[dict, dict]:
    snapshot = db.loads(event["data_json"], {})
    card = snapshot.get("card")
    if not card:
        raise HTTPException(409, "Этот старый осмотр не содержит снимка данных. Сохраните новый осмотр.")
    eq = card["equipment"]
    measurement = card.get("measurement") or {}
    approved = [f"{e['author']}, {e['created_at']}" for e in store.approvals(event["equipment_id"], event["id"])]
    summary = [["Осмотр", snapshot.get("inspection_id", event["id"]) ], ["Дата", event["created_at"]], ["Исполнитель", event["author"]],
               ["Агрегат", eq["name"]], ["Модель", eq["model"]], ["Серийный номер", eq["serial_number"]],
               ["Статус по данным", card["status_label"]], ["Учетные данные", eq.get("data_origin", "Демонстрационные")],
               ["Измерения", measurement.get("source", "Демонстрационная имитация")],
               ["Время записи измерений", measurement.get("timestamp", "не задано")],
               ["Режим", measurement.get("notice", "Демонстрация")],
               ["Кадр ИИ", ", ".join("Анализ №" + str(a) for a in snapshot.get("analysis_ids", [snapshot["analysis_id"]])) if snapshot.get("analysis_id") else "ИИ-осмотр не выполнялся"],
               ["Измерения на момент ИИ-анализа", ((snapshot.get("analysis") or {}).get("measurement") or {}).get("timestamp", "не заданы")],
               ["Подтверждение мастером", "; ".join(approved) or "Не подтвержден"],
               ["Ограничение", card["disclaimer"]]]
    review = snapshot.get("review")
    if review:
        summary += [["Решения по находкам ИИ", f"подтверждено {review['confirmed']}, не подтверждено {review['rejected']}, "
                                                f"без решения {review['open']}"],
                    ["Не попали в кадр", ", ".join(n["name"] for n in review["not_covered"]) or "—"]]
        if review.get("partial"):
            summary.append(["Полнота ответа ИИ", "Ответ модели оборвался — показаны не все узлы"])
    if snapshot.get("shots"):
        summary.append(["Кадров в осмотре", len(snapshot["shots"])])
        if (review or {}).get("conflicts"):
            summary.append(["Расхождения кадров", ", ".join(c["name"] for c in review["conflicts"])])
    verdict_text = {"confirmed": "Подтверждено", "rejected": "Не подтверждено", "deferred": "Отложено", None: "Без решения"}
    category_text = {"ok": "Видимых проблем нет", "check": "Проверить", "defect": "Признаки дефекта"}
    findings = [["Узел", "Оценка ИИ", "Причина", "Решение инженера", "Комментарий", "Исполнитель", "Кадр"]]
    findings += [[f["name"], category_text.get(f["category"], f["category"]), f["reason"],
                  "Не требуется" if f["category"] == "ok" and not f["verdict"] else verdict_text.get(f["verdict"], f["verdict"]),
                  f["note"], f["author"], f.get("shot_number")] for f in (review or {}).get("findings", [])]
    parameters = [["Параметр", "Значение", "Единица", "Состояние", "Источник", "Время записи"]]
    parameters += [[p["name"], p["value"], p.get("unit"), p["status_text"], p.get("source"), p.get("measured_at")]
                   for p in card["parameters"]]
    nodes = [["Узел", "Статус по данным", "Осмотр камерой", "Норматив", "Остаток до норматива", "Источник норматива", "Причины", "Действия"]]
    nodes += [[c["name"], c["status_label"], c.get("visual_coverage"), c["resource"]["normative"],
               c["resource"]["remaining_text"], c.get("normative_source"),
               "; ".join(r["text"] for r in c["rollup_reasons"]), "; ".join(c["recommendations"])]
              for c in card["components"]]
    history = [["Дата", "Узел", "Действие", "Описание", "Исполнитель"]]
    history += [[e["created_at"], e.get("component_name"), e["type"], e["note"], e["author"]] for e in card["history"]]
    tables = {"Осмотр": [["Поле", "Значение"]] + summary, "Измерения": parameters, "Узлы": nodes, "История": history}
    if len(findings) > 1:
        tables["Находки ИИ"] = findings
    return snapshot, tables


def export(event: dict, fmt: str) -> bytes:
    snapshot, tables = sections(event)
    if fmt == "pdf":
        return pdf(tables, snapshot.get("image_paths") or [snapshot.get("image_path")])
    return xlsx(tables, snapshot.get("image_paths") or [snapshot.get("image_path")])


DATE_RE = re.compile(r"^\d{4}-\d\d-\d\d[T ]\d\d:\d\d")
WIDTHS = {"Осмотр": [34, 70], "Узлы": [34, 24, 42, 24, 25, 44, 58, 58], "Находки ИИ": [34, 22, 58, 22, 44, 22, 12]}


def _cell_value(value):
    """Даты — датами Excel; текст, похожий на формулу, экранируется (защита от формула-инъекций)."""
    if isinstance(value, str) and DATE_RE.match(value):
        try:
            d = datetime.fromisoformat(value.replace(" ", "T"))
            return d if d.tzinfo is None else d.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            return value
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def xlsx(tables: dict, image_paths: list[str | None]) -> bytes:
    """Excel средствами Python (openpyxl): без Node.js и внешних пакетов, работает офлайн на любом стенде."""
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    book = Workbook()
    book.remove(book.active)
    head_fill, head_font = PatternFill("solid", fgColor="243B53"), Font(name="Arial", bold=True, color="F4F7FA")
    body_font, wrap = Font(name="Arial", size=11, color="18222B"), Alignment(wrap_text=True, vertical="top")
    for name, rows in tables.items():
        sheet = book.create_sheet(name)
        sheet.sheet_view.showGridLines = False
        cols = len(rows[0])
        widths = WIDTHS.get(name, [32] * cols)
        for r, row in enumerate(rows, start=1):
            for c, value in enumerate(row, start=1):
                cell = sheet.cell(row=r, column=c, value=_cell_value(value))
                cell.font, cell.alignment = (head_font if r == 1 else body_font), wrap
                if r == 1:
                    cell.fill = head_fill
                elif isinstance(cell.value, datetime):
                    cell.number_format = "yyyy-mm-dd hh:mm"
            lines = max(-(-len(str(v if v is not None else "")) // max(12, widths[i] - 3)) for i, v in enumerate(row))
            sheet.row_dimensions[r].height = 24 if r == 1 else max(21, lines * 12 + 9)
        for c in range(cols):
            sheet.column_dimensions[get_column_letter(c + 1)].width = widths[c]
        sheet.freeze_panes = "A2"
    for number, image_path in enumerate(image_paths, 1):
        if not image_path or not Path(image_path).is_file():
            continue
        from PIL import Image
        sheet = book.create_sheet("Кадр" if len(image_paths) == 1 else f"Кадр {number}")
        sheet["A1"] = "Кадр ИИ-осмотра. Выводы предварительные."
        sheet["A1"].font = Font(name="Arial", bold=True)
        buf = io.BytesIO()
        with Image.open(image_path) as im:
            im.thumbnail((960, 720))
            im.convert("RGB").save(buf, format="PNG")
        buf.seek(0)
        sheet.add_image(XLImage(buf), "A3")
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def pdf(tables: dict, image_paths: list[str | None]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image
    candidates = [Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                  Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"]
    font = next((p for p in candidates if p.exists()), None)
    if not font:
        raise HTTPException(503, "Для PDF нужен шрифт Arial или DejaVu Sans с кириллицей.")
    pdfmetrics.registerFont(TTFont("Inspector", str(font)))
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = "Inspector"
    body = styles["BodyText"]
    body.fontSize, body.leading = 9, 12
    story = [Paragraph("Отчет об осмотре оборудования", styles["Title"])]
    def p(value):
        return Paragraph(escape(str(value if value is not None else "Нет данных")).replace("\n", "<br/>"), body)
    for name in ("Осмотр", "Измерения"):
        story += [Spacer(1, 12), Paragraph(name, styles["Heading2"])]
        rows = tables[name]
        if name == "Измерения":
            rows = [["Параметр", "Значение", "Состояние"]] + [[r[0], f"{r[1]} {r[2] or ''}", r[3]] for r in rows[1:]]
        widths = [150, 365] if name == "Осмотр" else [195, 100, 220]
        table = Table([[p(v) for v in row] for row in rows], colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8edf2")),
                                   ("LINEBELOW", (0, 0), (-1, 0), .5, colors.grey),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
        story.append(table)
    if "Находки ИИ" in tables:
        story += [Spacer(1, 12), Paragraph("Находки ИИ и решения инженера", styles["Heading2"])]
        rows = [[r[6], r[0], r[1], r[3]] for r in tables["Находки ИИ"]]
        table = Table([[p(v) for v in row] for row in rows], colWidths=[40, 170, 145, 160], repeatRows=1)
        table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8edf2")),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
        story.append(table)
    story += [PageBreak(), Paragraph("Узлы и действия", styles["Heading1"])]
    for row in tables["Узлы"][1:]:
        story.append(Paragraph(escape(row[0]), styles["Heading2"]))
        for title, value in zip(tables["Узлы"][0][1:], row[1:]):
            story.append(p(f"{title}: {value if value else 'Нет данных'}"))
        story.append(Spacer(1, 8))
    for number, image_path in enumerate(image_paths, 1):
        if not image_path or not Path(image_path).is_file():
            continue
        from PIL import Image as PILImage
        with PILImage.open(image_path) as im:
            scale = min(515 / im.width, 500 / im.height)
            width, height = im.width * scale, im.height * scale
        story += [PageBreak(), Paragraph(f"Кадр {number} предварительного ИИ-осмотра", styles["Heading1"]),
                  Image(image_path, width=width, height=height)]
    story += [PageBreak(), Paragraph("История на момент сохранения", styles["Heading1"])]
    for row in tables["История"][1:]:
        story += [p(" · ".join(str(v) for v in row if v)), Spacer(1, 8)]
    output = io.BytesIO()
    def page_number(canvas, doc):
        canvas.setFont("Inspector", 8)
        canvas.drawRightString(555, 24, str(doc.page))
    SimpleDocTemplate(output, rightMargin=40, leftMargin=40, topMargin=35, bottomMargin=40,
                      title="Осмотр оборудования").build(story, onFirstPage=page_number, onLaterPages=page_number)
    return output.getvalue()
