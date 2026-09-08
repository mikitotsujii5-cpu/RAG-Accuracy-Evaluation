#!/usr/bin/env python3
"""Generate a synthetic PDF corpus for the Databricks RAG evaluation demo.

All vehicle specifications and procedures in these documents are fictional.
The PDFs are intentionally designed with overlapping terms, tables, diagrams,
year-specific values, and cross-document references so that retrieval changes
are measurable across the five RAG phases.
"""

from __future__ import annotations

import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageEnhance
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "output" / "pdf"
TMP_DIR = ROOT / "tmp" / "pdfs"

FONT_REGULAR_PATH = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
FONT_BOLD_PATH = Path(
    "/System/Library/AssetsV2/com_apple_MobileAsset_Font8/"
    "0ebfdb7e5a2a1db668fa6209779e0725d6f6baba.asset/"
    "AssetData/BIZ_UDMincho-regular.ttf"
)
FONT_REGULAR = "DemoGothic"
FONT_BOLD = "DemoGothicBold"

NAVY = HexColor("#17324D")
TEAL = HexColor("#1F7A8C")
PALE_TEAL = HexColor("#E8F4F6")
INK = HexColor("#1F2933")
MUTED = HexColor("#52606D")
LINE = HexColor("#CBD5E1")
PALE = HexColor("#F5F7FA")
ORANGE = HexColor("#D97706")
PALE_ORANGE = HexColor("#FFF3E0")
RED = HexColor("#B42318")
PALE_RED = HexColor("#FDECEC")
WHITE = colors.white

DISCLAIMER = (
    "非公式・架空のデモ資料 - 実車の操作・整備・救助には使用しないでください"
)


@dataclass(frozen=True)
class DocMeta:
    doc_id: str
    filename: str
    title: str
    model: str
    model_year: str
    document_type: str
    vehicle_category: str
    revision: str = "DEMO-1.0"
    landscape_mode: bool = False

    @property
    def page_size(self):
        return landscape(A4) if self.landscape_mode else A4


def register_fonts() -> None:
    if not FONT_REGULAR_PATH.exists() or not FONT_BOLD_PATH.exists():
        raise FileNotFoundError("Japanese TrueType fonts were not found")
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(FONT_REGULAR_PATH)))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONT_BOLD_PATH)))


def make_styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    return {
        "cover_kicker": ParagraphStyle(
            "cover_kicker",
            parent=sample["Normal"],
            fontName=FONT_BOLD,
            fontSize=10,
            leading=14,
            textColor=TEAL,
            spaceAfter=8,
            wordWrap="CJK",
        ),
        "cover_title": ParagraphStyle(
            "cover_title",
            parent=sample["Title"],
            fontName=FONT_BOLD,
            fontSize=25,
            leading=34,
            textColor=NAVY,
            alignment=TA_LEFT,
            spaceAfter=12,
            wordWrap="CJK",
        ),
        "cover_subtitle": ParagraphStyle(
            "cover_subtitle",
            parent=sample["Normal"],
            fontName=FONT_REGULAR,
            fontSize=11,
            leading=18,
            textColor=MUTED,
            spaceAfter=16,
            wordWrap="CJK",
        ),
        "page_title": ParagraphStyle(
            "page_title",
            parent=sample["Heading1"],
            fontName=FONT_BOLD,
            fontSize=17,
            leading=23,
            textColor=NAVY,
            spaceBefore=0,
            spaceAfter=10,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=sample["Heading2"],
            fontName=FONT_BOLD,
            fontSize=12,
            leading=18,
            textColor=TEAL,
            spaceBefore=8,
            spaceAfter=5,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "body": ParagraphStyle(
            "body",
            parent=sample["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=9.6,
            leading=15,
            textColor=INK,
            spaceAfter=7,
            wordWrap="CJK",
        ),
        "body_dense": ParagraphStyle(
            "body_dense",
            parent=sample["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=8.8,
            leading=13,
            textColor=INK,
            spaceAfter=5,
            wordWrap="CJK",
        ),
        "small": ParagraphStyle(
            "small",
            parent=sample["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=7.6,
            leading=11,
            textColor=MUTED,
            spaceAfter=4,
            wordWrap="CJK",
        ),
        "table": ParagraphStyle(
            "table",
            parent=sample["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=8.1,
            leading=11,
            textColor=INK,
            wordWrap="CJK",
        ),
        "table_header": ParagraphStyle(
            "table_header",
            parent=sample["BodyText"],
            fontName=FONT_BOLD,
            fontSize=8.2,
            leading=11,
            textColor=WHITE,
            alignment=TA_CENTER,
            wordWrap="CJK",
        ),
        "table_center": ParagraphStyle(
            "table_center",
            parent=sample["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=8.4,
            leading=11,
            textColor=INK,
            alignment=TA_CENTER,
            wordWrap="CJK",
        ),
        "callout": ParagraphStyle(
            "callout",
            parent=sample["BodyText"],
            fontName=FONT_REGULAR,
            fontSize=9,
            leading=14,
            textColor=INK,
            wordWrap="CJK",
        ),
        "callout_title": ParagraphStyle(
            "callout_title",
            parent=sample["BodyText"],
            fontName=FONT_BOLD,
            fontSize=9.5,
            leading=14,
            textColor=NAVY,
            wordWrap="CJK",
        ),
        "column_term": ParagraphStyle(
            "column_term",
            parent=sample["Heading2"],
            fontName=FONT_BOLD,
            fontSize=11.2,
            leading=16,
            textColor=TEAL,
            spaceBefore=5,
            spaceAfter=4,
            wordWrap="CJK",
        ),
    }


STYLES: dict[str, ParagraphStyle] = {}


def P(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, STYLES[style])


def bullet_list(items: list[str], dense: bool = False) -> list[Flowable]:
    style = "body_dense" if dense else "body"
    return [P(f"・{item}", style) for item in items]


def callout(title: str, text: str, kind: str = "info") -> Table:
    if kind == "warning":
        bg, accent = PALE_ORANGE, ORANGE
    elif kind == "danger":
        bg, accent = PALE_RED, RED
    else:
        bg, accent = PALE_TEAL, TEAL
    data = [[P(title, "callout_title")], [P(text, "callout")]]
    table = Table(data, colWidths=[None], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("BOX", (0, 0), (-1, -1), 0.7, accent),
                ("LINEBEFORE", (0, 0), (0, -1), 4, accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, 0), 7),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
                ("TOPPADDING", (0, 1), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
            ]
        )
    )
    return table


def cell(value, header: bool = False, center: bool = False):
    if isinstance(value, Flowable):
        return value
    style = "table_header" if header else ("table_center" if center else "table")
    return P(str(value).replace("\n", "<br/>"), style)


def styled_table(
    rows: list[list],
    col_widths: list[float] | None = None,
    *,
    header_rows: int = 1,
    center_columns: set[int] | None = None,
    row_backgrounds: dict[int, colors.Color] | None = None,
) -> Table:
    center_columns = center_columns or set()
    converted = []
    for row_index, row in enumerate(rows):
        converted.append(
            [
                cell(
                    value,
                    header=row_index < header_rows,
                    center=(column_index in center_columns),
                )
                for column_index, value in enumerate(row)
            ]
        )
    table = Table(
        converted,
        colWidths=col_widths,
        repeatRows=header_rows,
        hAlign="LEFT",
        splitByRow=1,
    )
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    if header_rows:
        commands.append(("BACKGROUND", (0, 0), (-1, header_rows - 1), NAVY))
    for row_index in range(header_rows, len(rows)):
        if row_index % 2 == 0:
            commands.append(("BACKGROUND", (0, row_index), (-1, row_index), PALE))
    for row_index, color in (row_backgrounds or {}).items():
        commands.append(("BACKGROUND", (0, row_index), (-1, row_index), color))
    table.setStyle(TableStyle(commands))
    return table


class SpeedBand(Flowable):
    def __init__(self, lower: int, upper: int, label: str, max_speed: int = 80):
        super().__init__()
        self.width = 470
        self.height = 82
        self.lower = lower
        self.upper = upper
        self.label = label
        self.max_speed = max_speed

    def draw(self):
        c = self.canv
        c.saveState()
        x0, y0, w = 28, 29, self.width - 56
        c.setStrokeColor(LINE)
        c.setLineWidth(1.2)
        c.line(x0, y0, x0 + w, y0)
        for value in range(0, self.max_speed + 1, 10):
            x = x0 + w * value / self.max_speed
            c.line(x, y0 - 4, x, y0 + 4)
            c.setFont(FONT_REGULAR, 7.5)
            c.setFillColor(MUTED)
            c.drawCentredString(x, y0 - 15, str(value))
        start = x0 + w * self.lower / self.max_speed
        end = x0 + w * self.upper / self.max_speed
        c.setFillColor(TEAL)
        c.roundRect(start, y0 + 8, end - start, 17, 5, stroke=0, fill=1)
        c.setFillColor(NAVY)
        c.setFont(FONT_BOLD, 9.5)
        c.drawCentredString((start + end) / 2, y0 + 31, self.label)
        c.setFont(FONT_REGULAR, 8)
        c.setFillColor(MUTED)
        c.drawRightString(x0 + w, y0 - 15, "km/h")
        c.restoreState()


class SensorDiagram(Flowable):
    def __init__(self):
        super().__init__()
        self.width = 470
        self.height = 230

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(PALE)
        c.setStrokeColor(NAVY)
        c.setLineWidth(1.2)
        c.roundRect(145, 43, 180, 130, 24, fill=1, stroke=1)
        c.setFillColor(WHITE)
        c.roundRect(177, 75, 116, 66, 12, fill=1, stroke=1)
        c.setFont(FONT_BOLD, 10)
        c.setFillColor(NAVY)
        c.drawCentredString(235, 106, "車両上面図")

        markers = [
            (235, 172, "①", "単眼カメラ", 45, 195),
            (235, 44, "②", "ミリ波レーダー", 45, 57),
            (293, 108, "③", "ドライバーモニター", 337, 123),
        ]
        for x, y, number, label, lx, ly in markers:
            c.setFillColor(TEAL)
            c.circle(x, y, 11, fill=1, stroke=0)
            c.setFillColor(WHITE)
            c.setFont(FONT_BOLD, 9)
            c.drawCentredString(x, y - 3, number)
            c.setStrokeColor(TEAL)
            c.line(x, y, lx + (90 if lx < x else 0), ly - 2)
            c.setFillColor(INK)
            c.setFont(FONT_BOLD, 9)
            c.drawString(lx, ly, f"{number} {label}")

        c.setFillColor(MUTED)
        c.setFont(FONT_REGULAR, 8)
        c.drawString(28, 18, "図1 安全支援センサーの配置（模式図・実車を示すものではありません）")
        c.restoreState()


class EmergencyLayoutDiagram(Flowable):
    def __init__(self):
        super().__init__()
        self.width = 470
        self.height = 240

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(PALE)
        c.setStrokeColor(NAVY)
        c.setLineWidth(1.3)
        c.roundRect(132, 42, 205, 148, 30, fill=1, stroke=1)
        c.setFillColor(PALE_TEAL)
        c.roundRect(169, 76, 131, 82, 12, fill=1, stroke=1)
        c.setFillColor(NAVY)
        c.setFont(FONT_BOLD, 10)
        c.drawCentredString(234, 115, "ZVW60 模式図")

        markers = [
            (205, 159, "①", "サービスプラグ", "後席座面下", 20, 190),
            (313, 69, "②", "12Vバッテリー", "荷室左側", 350, 80),
            (235, 108, "③", "HVバッテリー", "床下中央", 350, 145),
        ]
        for x, y, number, label, location, lx, ly in markers:
            c.setFillColor(ORANGE if number == "①" else TEAL)
            c.circle(x, y, 11, fill=1, stroke=0)
            c.setFillColor(WHITE)
            c.setFont(FONT_BOLD, 9)
            c.drawCentredString(x, y - 3, number)
            c.setStrokeColor(ORANGE if number == "①" else TEAL)
            c.line(x, y, lx + (100 if lx < x else 0), ly - 4)
            c.setFillColor(INK)
            c.setFont(FONT_BOLD, 8.8)
            c.drawString(lx, ly, f"{number} {label}")
            c.setFont(FONT_REGULAR, 8)
            c.drawString(lx, ly - 13, location)

        c.setFillColor(MUTED)
        c.setFont(FONT_REGULAR, 8)
        c.drawString(20, 18, "図2 高電圧関連部品の配置（評価デモ用の架空図）")
        c.restoreState()


class ProcedureFlow(Flowable):
    def __init__(self, steps: list[str]):
        super().__init__()
        self.steps = steps
        self.width = 470
        self.height = 236

    def draw(self):
        c = self.canv
        c.saveState()
        box_h = 34
        x = 40
        w = 390
        y = self.height - 40
        for index, step in enumerate(self.steps, start=1):
            c.setFillColor(PALE_ORANGE if index >= 4 else PALE_TEAL)
            c.setStrokeColor(ORANGE if index >= 4 else TEAL)
            c.roundRect(x, y, w, box_h, 6, fill=1, stroke=1)
            c.setFillColor(ORANGE if index >= 4 else TEAL)
            c.circle(x + 19, y + box_h / 2, 11, fill=1, stroke=0)
            c.setFillColor(WHITE)
            c.setFont(FONT_BOLD, 9)
            c.drawCentredString(x + 19, y + box_h / 2 - 3, str(index))
            c.setFillColor(INK)
            c.setFont(FONT_REGULAR, 8.6)
            c.drawString(x + 39, y + box_h / 2 - 3, step)
            if index < len(self.steps):
                c.setStrokeColor(MUTED)
                c.line(x + w / 2, y, x + w / 2, y - 9)
                c.line(x + w / 2, y - 9, x + w / 2 - 4, y - 4)
                c.line(x + w / 2, y - 9, x + w / 2 + 4, y - 4)
            y -= 43
        c.restoreState()


class ChangeTimeline(Flowable):
    def __init__(self):
        super().__init__()
        self.width = 470
        self.height = 155

    def draw(self):
        c = self.canv
        c.saveState()
        c.setStrokeColor(LINE)
        c.setLineWidth(3)
        c.line(65, 80, 405, 80)
        for x, year, label in [
            (95, "2023", "旧条件"),
            (375, "2024", "支援範囲を拡大"),
        ]:
            c.setFillColor(TEAL)
            c.circle(x, 80, 13, fill=1, stroke=0)
            c.setFillColor(NAVY)
            c.setFont(FONT_BOLD, 13)
            c.drawCentredString(x, 112, year)
            c.setFont(FONT_REGULAR, 9)
            c.drawCentredString(x, 50, label)
        c.setFillColor(MUTED)
        c.setFont(FONT_REGULAR, 9)
        c.drawCentredString(235, 16, "具体的な速度値は各年式の取扱ガイドを参照")
        c.restoreState()


class DemoDocTemplate(BaseDocTemplate):
    def __init__(self, path: Path, meta: DocMeta):
        page_size = meta.page_size
        left = right = 18 * mm
        top = 27 * mm
        bottom = 22 * mm
        super().__init__(
            str(path),
            pagesize=page_size,
            leftMargin=left,
            rightMargin=right,
            topMargin=top,
            bottomMargin=bottom,
            title=meta.title,
            author="Databricks RAG Accuracy Evaluation Demo",
            subject="Synthetic and unofficial RAG evaluation corpus",
            keywords=f"{meta.doc_id}, {meta.model}, {meta.model_year}, {meta.document_type}, RAG demo",
        )
        self.meta = meta
        frame = Frame(
            left,
            bottom,
            page_size[0] - left - right,
            page_size[1] - top - bottom,
            id="normal",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        self.addPageTemplates(
            [PageTemplate(id="main", frames=[frame], onPage=self._draw_header_footer)]
        )

    def _draw_header_footer(self, c: canvas.Canvas, doc) -> None:
        width, height = self.meta.page_size
        c.saveState()
        c.setFillColor(NAVY)
        c.rect(0, height - 17 * mm, width, 17 * mm, fill=1, stroke=0)
        c.setFillColor(WHITE)
        c.setFont(FONT_BOLD, 9)
        c.drawString(18 * mm, height - 10.5 * mm, self.meta.doc_id)
        c.setFont(FONT_REGULAR, 7.4)
        header_right = (
            f"{self.meta.model} | {self.meta.model_year} | "
            f"{self.meta.document_type} | {self.meta.revision}"
        )
        c.drawRightString(width - 18 * mm, height - 10.5 * mm, header_right)

        c.setStrokeColor(LINE)
        c.setLineWidth(0.5)
        c.line(18 * mm, 15 * mm, width - 18 * mm, 15 * mm)
        c.setFillColor(MUTED)
        c.setFont(FONT_REGULAR, 6.8)
        c.drawString(18 * mm, 9.5 * mm, DISCLAIMER)
        c.drawRightString(width - 18 * mm, 9.5 * mm, f"PDF物理ページ {doc.page} / 5")
        c.restoreState()


def cover(meta: DocMeta, summary: str, keywords: list[str]) -> list[Flowable]:
    metadata_rows = [
        ["Project想定", "トヨタ車種関連RAG評価デモ"],
        ["車種", meta.model],
        ["年式", meta.model_year],
        ["文書種別", meta.document_type],
        ["車両カテゴリ", meta.vehicle_category],
        ["文書ID / 改訂", f"{meta.doc_id} / {meta.revision}"],
    ]
    return [
        Spacer(1, 18 * mm),
        P("RAG ACCURACY EVALUATION CORPUS", "cover_kicker"),
        P(meta.title, "cover_title"),
        P(summary, "cover_subtitle"),
        callout(
            "重要: 評価専用の架空資料",
            "本文中の型式、速度、装備、操作、救助手順は検索評価のために作った架空情報です。実在車両の判断には使用できません。トヨタ自動車株式会社の公式資料ではありません。",
            "danger",
        ),
        Spacer(1, 8 * mm),
        styled_table(metadata_rows, [42 * mm, 92 * mm], header_rows=0),
        Spacer(1, 7 * mm),
        P("検索キーワード", "h2"),
        P(" / ".join(keywords), "body"),
    ]


def page(title: str, *items: Flowable) -> list[Flowable]:
    return [P(title, "page_title"), *items]


def build_pdf(meta: DocMeta, pages: list[list[Flowable]]) -> Path:
    if len(pages) != 5:
        raise ValueError(f"{meta.doc_id} must contain exactly five page definitions")
    output_path = OUTPUT_DIR / meta.filename
    story: list[Flowable] = []
    for index, page_items in enumerate(pages):
        story.extend(page_items)
        if index < len(pages) - 1:
            story.append(PageBreak())
    doc = DemoDocTemplate(output_path, meta)
    doc.build(story)
    actual_pages = len(PdfReader(str(output_path)).pages)
    if actual_pages != 5:
        raise RuntimeError(f"{meta.doc_id}: expected 5 pages, got {actual_pages}")
    return output_path


def build_d01() -> Path:
    meta = DocMeta(
        "D01",
        "01_prius_2024_owners_guide_demo.pdf",
        "Prius 2024 取扱クイックガイド",
        "Prius",
        "2024",
        "owners_guide",
        "passenger_car",
    )
    pages = [
        cover(
            meta,
            "2024年式Priusの識別、PDA、AHS、SEAの基本操作をまとめたクイックガイドです。",
            ["ZVW60", "ZVW65", "PDA", "AHS", "SEA", "2024"],
        ),
        page(
            "2. 車両の識別と型式",
            P("型式は車両識別ラベルと登録情報で確認します。駆動方式によって評価用の型式コードが異なります。"),
            styled_table(
                [
                    ["駆動方式", "評価用型式", "呼称", "確認ポイント"],
                    ["2WD", "ZVW60", "Prius 2WD", "車両識別ラベルのMODEL欄"],
                    ["E-Four", "ZVW65", "Prius E-Four", "車両識別ラベルのMODEL欄"],
                ],
                [28 * mm, 32 * mm, 39 * mm, 64 * mm],
                center_columns={0, 1},
            ),
            Spacer(1, 5 * mm),
            callout(
                "検索上の注意",
                "ZVW60とZVW65は近い表記ですが、E-Fourの評価用型式はZVW65です。Crown SportのAZSH36Wとは異なります。",
            ),
            P("グレード", "h2"),
            P("このデモではGとZの2グレードを扱います。装備差はD03の装備一覧を参照してください。"),
        ),
        page(
            "3. PDA（先読み運転支援）",
            P("PDAはProactive Driving Assistの略称です。周辺状況に応じ、穏やかな減速や操舵支援を行う評価用機能として記載しています。"),
            SpeedBand(10, 60, "PDA 作動速度 10-60 km/h"),
            callout(
                "2024年式の作動条件",
                "車速10-60 km/h、前方センサーが遮られていないこと、システム警告が表示されていないことが条件です。",
            ),
            P("基本設定", "h2"),
            P("センターディスプレイで「設定 > 運転支援 > PDA」を開き、有効を選択します。解除操作の詳細はD04 p4を参照してください。"),
            P("雪、泥、強い逆光でセンサー性能が低下する場合、PDAは使用できません。運転者の安全確認を代替しません。", "small"),
        ),
        page(
            "4. AHS（配光支援）",
            P("AHSはAdaptive High-beam Systemの略称です。前方状況に応じて照射範囲を変える評価用機能です。"),
            SpeedBand(30, 80, "AHS 自動配光開始 30 km/h以上"),
            P("事前操作", "h2"),
            *bullet_list(
                [
                    "ライトスイッチをAUTOにする。",
                    "ヘッドランプレバーを前方へ操作する。",
                    "AHS表示灯を確認する。",
                ]
            ),
            callout(
                "2023年式との違い",
                "2024年式の自動配光開始は30 km/h以上です。2023年式の35 km/h以上、Crown Sport 2024の25 km/h以上と混同しないでください。",
                "warning",
            ),
        ),
        page(
            "5. SEA（降車時警報）",
            P("SEAはSafe Exit Assistの略称です。停車後、後方から接近する対象を検知したとき、表示と警報で降車時の確認を支援します。"),
            styled_table(
                [
                    ["グレード", "2024年式の設定", "確認先"],
                    ["G", "設定なし", "D03 p2 / p4"],
                    ["Z", "標準装備", "D03 p2"],
                ],
                [36 * mm, 48 * mm, 68 * mm],
                center_columns={0, 1},
            ),
            P("作動の考え方", "h2"),
            P("車両電源をOFFにした直後も短時間監視します。警報がない場合でも、ドアを開く前に目視確認してください。"),
            callout(
                "関連資料",
                "略称の意味はD08、グレード別の正確な装備差はD03、新旧比較はD06を参照します。",
            ),
        ),
    ]
    return build_pdf(meta, pages)


def build_d02() -> Path:
    meta = DocMeta(
        "D02",
        "02_prius_2023_owners_guide_demo.pdf",
        "Prius 2023 取扱クイックガイド",
        "Prius",
        "2023",
        "owners_guide",
        "passenger_car",
    )
    pages = [
        cover(
            meta,
            "2023年式Priusの操作条件をまとめた旧年式ガイドです。2024年式との近似表現を意図的に含みます。",
            ["Prius", "2023", "PDA", "AHS", "SEA", "DRIVE ASSIST"],
        ),
        page(
            "2. 車両の識別と対象範囲",
            P("このガイドは2023年式の評価データです。2024年式ガイドと機能名は同じでも、速度条件と操作方法が異なります。"),
            styled_table(
                [
                    ["駆動方式", "評価用型式", "年式", "対象"],
                    ["2WD", "ZVW60", "2023", "G / Z"],
                    ["E-Four", "ZVW65", "2023", "G / Z"],
                ],
                [34 * mm, 38 * mm, 35 * mm, 43 * mm],
                center_columns={0, 1, 2, 3},
            ),
            callout(
                "Metadata Filtering用の負例",
                "質問に2024年式とある場合、この文書の速度値や旧操作を回答へ使ってはいけません。",
                "warning",
            ),
        ),
        page(
            "3. PDA（2023年式）",
            P("2023年式のPDAも先読み運転支援を意味しますが、作動上限と設定操作は2024年式と異なります。"),
            SpeedBand(10, 40, "PDA 作動速度 10-40 km/h"),
            P("有効化操作", "h2"),
            P("ステアリングのDRIVE ASSISTスイッチを2秒以上長押しします。2024年式のディスプレイ設定とは異なります。"),
            callout(
                "年式固有値",
                "2023年式は10-40 km/hです。2024年式Priusの10-60 km/h、Crown Sport 2024の10-50 km/hと区別します。",
            ),
        ),
        page(
            "4. AHS（2023年式）",
            P("AHSを利用する前に、ライトをAUTOにし、ヘッドランプレバーを前方へ操作します。この操作条件は2024年式と同じです。"),
            SpeedBand(35, 80, "AHS 自動配光開始 35 km/h以上"),
            callout(
                "開始速度",
                "2023年式は35 km/h以上です。2024年式Priusでは30 km/h以上に変更されたという評価シナリオです。",
                "warning",
            ),
            P("周囲が明るい場合やセンサーが汚れている場合は、自動配光を行わないことがあります。"),
        ),
        page(
            "5. SEA（2023年式）",
            P("2023年式のSEAはZグレードだけに標準設定されます。Gグレードには設定されません。"),
            styled_table(
                [
                    ["グレード", "SEA", "注記"],
                    ["G", "設定なし", "追加オプションなし"],
                    ["Z", "標準装備", "後方接近時に警報"],
                ],
                [36 * mm, 43 * mm, 73 * mm],
                center_columns={0, 1},
            ),
            callout(
                "検索時の注意",
                "同じ『Zのみ標準』という文が2024年式の一部説明と似ているため、年式と文書種別を必ず確認します。",
            ),
        ),
    ]
    return build_pdf(meta, pages)


def build_d03() -> Path:
    meta = DocMeta(
        "D03",
        "03_prius_2024_grade_equipment_demo.pdf",
        "Prius 2024 グレード別装備一覧",
        "Prius",
        "2024",
        "equipment_spec",
        "passenger_car",
        landscape_mode=True,
    )
    pages = [
        cover(
            meta,
            "GとZの装備差を、記号、表の行列、脚注で表現した表解析用の資料です。",
            ["G", "Z", "PDA", "AHS", "SEA", "Safety Plus package"],
        ),
        page(
            "2. 安全支援装備マトリクス",
            callout(
                "凡例",
                "● = 標準装備 / ○ = メーカーオプション / - = 設定なし。列は左からG、Zの順です。",
            ),
            Spacer(1, 4 * mm),
            styled_table(
                [
                    ["分類", "機能", "略称", "G", "Z", "注記"],
                    ["運転支援", "先読み運転支援", "PDA", "●", "●", "両グレード標準"],
                    ["照明支援", "アダプティブハイビーム", "AHS", "○", "●", "Gは脚注Aを参照"],
                    ["降車支援", "セーフイグジットアシスト", "SEA", "-", "●", "Gには設定なし"],
                ],
                [38 * mm, 67 * mm, 30 * mm, 24 * mm, 24 * mm, 71 * mm],
                center_columns={2, 3, 4},
            ),
            Spacer(1, 6 * mm),
            P("読み取り例", "h2"),
            P("Z列はPDA、AHS、SEAの3機能がすべて●です。G列はPDAが●、AHSが○、SEAが-です。"),
        ),
        page(
            "3. 快適・視界装備マトリクス",
            styled_table(
                [
                    ["分類", "装備", "コード", "G", "Z", "備考"],
                    ["視界", "パノラミックビューモニター", "PVM-24", "○", "●", "GはVision package"],
                    ["快適", "運転席メモリー", "MEM-2", "-", "●", "Zのみ"],
                    ["空調", "前席シートヒーター", "HTR-F", "○", "●", "地域設定なし"],
                    ["安全", "Safety Plus package", "SP-24A", "○", "-", "G専用package"],
                ],
                [38 * mm, 72 * mm, 34 * mm, 24 * mm, 24 * mm, 68 * mm],
                center_columns={2, 3, 4},
            ),
            Spacer(1, 8 * mm),
            callout(
                "完全一致検索用コード",
                "GグレードのAHS条件を調べる場合は、packageコードSP-24Aも検索語に含めると脚注へ到達しやすくなります。",
            ),
        ),
        page(
            "4. 脚注と選択条件",
            P("脚注A: GグレードのAHS", "h2"),
            callout(
                "Safety Plus package選択時のみ",
                "GグレードのAHSはメーカーオプションです。Safety Plus package（SP-24A）を選択した場合だけ装着できます。AHSだけを単独選択することはできません。",
                "warning",
            ),
            P("脚注B: Zグレード", "h2"),
            P("ZグレードではPDA、AHS、SEAが標準装備です。SP-24AはG専用のため、Zでは選択しません。"),
            P("記号の誤読防止", "h2"),
            *bullet_list(
                [
                    "○は標準ではなくオプションです。",
                    "-は設定なしであり、オプション追加もできません。",
                    "隣接するZ列の●をG列の値として読まないでください。",
                ]
            ),
        ),
        page(
            "5. 構成例と参照ルール",
            styled_table(
                [
                    ["質問例", "G", "Z", "参照"],
                    ["PDAは利用できるか", "標準", "標準", "p2"],
                    ["AHSは利用できるか", "SP-24Aで選択", "標準", "p2 / p4"],
                    ["SEAは利用できるか", "設定なし", "標準", "p2"],
                ],
                [76 * mm, 62 * mm, 48 * mm, 44 * mm],
                center_columns={1, 2, 3},
            ),
            Spacer(1, 7 * mm),
            callout(
                "RAG評価ポイント",
                "p2の行列とp4の脚注を同時に取得できるかを確認します。小さすぎるチャンクでは脚注との関係が失われ、大きすぎるチャンクでは隣接列を混同しやすくなります。",
            ),
        ),
    ]
    return build_pdf(meta, pages)


def build_d04() -> Path:
    meta = DocMeta(
        "D04",
        "04_prius_2024_safety_operation_demo.pdf",
        "Prius 2024 安全支援 操作ガイド",
        "Prius",
        "2024",
        "safety_operation_guide",
        "passenger_car",
    )
    pages = [
        cover(
            meta,
            "PDAとAHSの作動条件、センサー、設定・解除手順を詳しく示す操作資料です。",
            ["PDA", "cancel switch", "単眼カメラ", "ミリ波レーダー", "AHS"],
        ),
        page(
            "2. 安全支援センサー図",
            P("次の番号はこの文書内だけで有効です。D05のレスキュー配置図では同じ番号が別の部品を表します。"),
            SensorDiagram(),
            styled_table(
                [
                    ["図番号", "名称", "主な役割"],
                    ["①", "単眼カメラ", "車線・対象物の画像認識"],
                    ["②", "ミリ波レーダー", "対象物までの距離と相対速度"],
                    ["③", "ドライバーモニター", "運転者の顔向き確認"],
                ],
                [26 * mm, 51 * mm, 78 * mm],
                center_columns={0},
            ),
        ),
        page(
            "3. PDAの作動条件と制限",
            SpeedBand(10, 60, "2024 Prius PDA 10-60 km/h"),
            P("すべて満たす条件", "h2"),
            *bullet_list(
                [
                    "車速が10-60 km/hの範囲内である。",
                    "①単眼カメラと②ミリ波レーダーが遮られていない。",
                    "システム警告が表示されていない。",
                    "運転者がステアリングを保持し、周囲を確認している。",
                ]
            ),
            callout(
                "センサーが遮られている場合",
                "雪、泥、ステッカーなどでセンサーが遮られているときはPDAを使用できません。清掃後も警告が続く場合は機能をOFFにします。",
                "warning",
            ),
        ),
        page(
            "4. PDAの有効化・解除手順",
            P("有効化", "h2"),
            styled_table(
                [
                    ["手順", "操作", "確認"],
                    ["1", "車両を安全な場所で停止", "READY表示を確認"],
                    ["2", "センターディスプレイで設定を開く", "設定メニュー"],
                    ["3", "運転支援を選択", "支援機能一覧"],
                    ["4", "PDAを選択して有効にする", "PDA表示がON"],
                ],
                [21 * mm, 83 * mm, 50 * mm],
                center_columns={0},
            ),
            P("メニュー表記は「設定 > 運転支援 > PDA」です。", "body"),
            callout(
                "一時解除",
                "走行中に一時解除する場合は、ステアリングのcancel switchを1回押します。2023年式のDRIVE ASSISTスイッチ長押しとは異なります。",
                "warning",
            ),
            P("再開するときは周囲を確認してからPDAを再度有効にします。"),
        ),
        page(
            "5. AHSの作動条件",
            SpeedBand(30, 80, "2024 Prius AHS 30 km/h以上"),
            P("事前操作", "h2"),
            *bullet_list(
                [
                    "ライトスイッチをAUTOにする。",
                    "ヘッドランプレバーを前方へ操作する。",
                    "AHS表示灯が点灯していることを確認する。",
                ]
            ),
            callout(
                "作動しない主な条件",
                "明るい市街地、強い降雪、フロントガラスの汚れ、対向車を正しく検知できない環境では自動配光しない場合があります。",
            ),
            P("2023年式Priusは35 km/h以上、Crown Sport 2024は25 km/h以上です。車種と年式を確認してください。", "small"),
        ),
    ]
    return build_pdf(meta, pages)


def build_d05() -> Path:
    meta = DocMeta(
        "D05",
        "05_prius_2024_emergency_response_demo.pdf",
        "Prius 2024 緊急時対応・レスキューガイド",
        "Prius",
        "2024",
        "emergency_response_guide",
        "passenger_car",
    )
    pages = [
        cover(
            meta,
            "ZVW60の高電圧部品配置と遮断手順を題材にした図・長手順評価用資料です。",
            ["ZVW60", "サービスプラグ", "12Vバッテリー", "HVバッテリー", "10分"],
        ),
        page(
            "2. 対象車両と高電圧識別",
            callout(
                "警告: 架空の救助手順",
                "この文書は検索評価専用です。実際の救助、整備、事故対応には絶対に使用しないでください。",
                "danger",
            ),
            P("評価対象", "h2"),
            styled_table(
                [
                    ["車種", "年式", "評価用型式", "駆動"],
                    ["Prius", "2024", "ZVW60", "2WD"],
                ],
                [42 * mm, 34 * mm, 42 * mm, 35 * mm],
                center_columns={0, 1, 2, 3},
            ),
            P("識別ルール", "h2"),
            *bullet_list(
                [
                    "高電圧配線はオレンジ色という架空設定です。",
                    "SERVICE PLUG表示のあるカバーを識別します。",
                    "型式ZVW60を確認し、ZVW65やAZSH36Wの資料と混同しません。",
                ]
            ),
        ),
        page(
            "3. 高電圧関連部品の配置",
            EmergencyLayoutDiagram(),
            styled_table(
                [
                    ["図番号", "部品", "評価用の位置"],
                    ["①", "サービスプラグ", "後席座面下"],
                    ["②", "12Vバッテリー", "荷室左側"],
                    ["③", "HVバッテリー", "床下中央"],
                ],
                [28 * mm, 58 * mm, 68 * mm],
                center_columns={0},
            ),
        ),
        page(
            "4. 高電圧遮断の5手順",
            callout(
                "評価用手順",
                "順序と待機時間を一つの回答で保持できるかを確認します。以下は実車向け手順ではありません。",
                "danger",
            ),
            Spacer(1, 3 * mm),
            ProcedureFlow(
                [
                    "車両のREADYをOFFにする",
                    "電子キーを車両から5 m以上離す",
                    "②12Vバッテリーのマイナス端子を切り離す",
                    "①サービスプラグを取り外す",
                    "取り外し後、10分間待機する",
                ]
            ),
            P("回答では『10分』だけでなく、READY OFFから始まる順序も引用してください。", "small"),
        ),
        page(
            "5. 禁止領域と終了確認",
            styled_table(
                [
                    ["領域", "禁止事項", "理由（評価用）"],
                    ["床下中央", "切断・穴あけ禁止", "③HVバッテリー配置領域"],
                    ["オレンジ色配線周辺", "接触・切断禁止", "高電圧配線の識別色"],
                    ["後席座面下", "手順前に開放しない", "①サービスプラグ配置領域"],
                ],
                [42 * mm, 55 * mm, 57 * mm],
            ),
            callout(
                "終了確認",
                "遮断後10分の待機が完了しても、この架空資料だけで安全を判断してはいけません。正式評価では『資料に記載された手順』としてのみ回答します。",
                "warning",
            ),
        ),
    ]
    return build_pdf(meta, pages)


def build_d06() -> Path:
    meta = DocMeta(
        "D06",
        "06_prius_2023_2024_change_report_demo.pdf",
        "Prius 2023-2024 変更点レポート",
        "Prius",
        "2024",
        "model_change_report",
        "passenger_car",
    )
    pages = [
        cover(
            meta,
            "旧年式と新年式を横断し、複数文書参照が必要な変更点をまとめたQuery Optimization評価用資料です。",
            ["旧型", "新型", "PDA", "AHS", "SEA", "変更点"],
        ),
        page(
            "2. PDA支援範囲の変更概要",
            P("2024年式では、2023年式と比べてPDAの支援速度範囲を拡大したという評価シナリオです。"),
            ChangeTimeline(),
            callout(
                "数値は参照文書で確認",
                "本ページは変更の方向だけを示します。正確な作動速度はD01 p3（2024年式）とD02 p3（2023年式）を参照してください。",
            ),
            P("『先読み運転支援』という自然言語はD08でPDAへ展開します。"),
        ),
        page(
            "3. グレード別装備の変更結果",
            styled_table(
                [
                    ["2024グレード", "PDA", "AHS", "SEA", "詳細参照"],
                    ["G", "標準", "オプション", "設定なし", "D03 p2 / p4"],
                    ["Z", "標準", "標準", "標準", "D03 p2"],
                ],
                [38 * mm, 29 * mm, 34 * mm, 34 * mm, 38 * mm],
                center_columns={0, 1, 2, 3, 4},
            ),
            callout(
                "GのAHS",
                "GではSafety Plus package選択時だけAHSを装着できます。『オプション』だけで回答せず、D03 p4の条件を確認します。",
                "warning",
            ),
            P("ZではPDA、AHS、SEAの3機能がすべて標準です。"),
        ),
        page(
            "4. 参照文書マップ",
            styled_table(
                [
                    ["調べたい内容", "2023", "2024", "補足"],
                    ["PDA速度と操作", "D02 p3", "D01 p3 / D04 p3-p4", "条件と解除"],
                    ["AHS速度と操作", "D02 p4", "D01 p4 / D04 p5", "配光支援"],
                    ["グレード装備", "D02 p5", "D03 p2 / p4", "表と脚注"],
                    ["略称・別名", "D08", "D08", "Query展開"],
                ],
                [55 * mm, 35 * mm, 53 * mm, 38 * mm],
                center_columns={1, 2},
            ),
            Spacer(1, 6 * mm),
            callout(
                "複数文書を検索する質問",
                "『新型で先読み支援と配光支援が旧型からどう変わったか』は、PDAとAHSへ言い換え、D01、D02、D06を統合します。",
            ),
        ),
        page(
            "5. 用語と変更方向の索引",
            styled_table(
                [
                    ["利用者の表現", "正規化語", "変更方向", "数値の参照先"],
                    ["先読み運転支援", "PDA", "上限速度を拡大", "D01 p3 / D02 p3"],
                    ["配光支援", "AHS", "開始速度を引き下げ", "D01 p4 / D02 p4"],
                    ["降車時警報", "SEA", "グレード差あり", "D03 p2"],
                ],
                [46 * mm, 32 * mm, 49 * mm, 47 * mm],
                center_columns={1},
            ),
            P("変更値の計算例", "h2"),
            P("PDAは下限を維持し、上限を20 km/h拡大します。AHSは開始速度を5 km/h引き下げます。数値根拠は必ず各年式ガイドへ引用します。"),
            callout(
                "Reranking用decoy",
                "このレポートは関連語を多く含みますが、操作手順は完結しません。操作質問ではD04を上位に並べる必要があります。",
            ),
        ),
    ]
    return build_pdf(meta, pages)


def build_d07() -> Path:
    meta = DocMeta(
        "D07",
        "07_crown_sport_2024_owners_guide_demo.pdf",
        "Crown Sport 2024 取扱クイックガイド",
        "Crown Sport",
        "2024",
        "owners_guide",
        "suv",
    )
    pages = [
        cover(
            meta,
            "Priusと同じ機能名で異なる速度値を持つ、車種Metadata Filtering評価用の文書です。",
            ["Crown Sport", "AZSH36W", "PDA", "AHS", "SEA"],
        ),
        page(
            "2. 車両の識別と型式",
            styled_table(
                [
                    ["車種", "年式", "評価用型式", "カテゴリ", "駆動"],
                    ["Crown Sport", "2024", "AZSH36W", "SUV", "AWD"],
                ],
                [45 * mm, 28 * mm, 42 * mm, 28 * mm, 28 * mm],
                center_columns={0, 1, 2, 3, 4},
            ),
            Spacer(1, 6 * mm),
            callout(
                "型式の完全一致",
                "AZSH36WはCrown Sport 2024の評価用型式です。PriusのZVW60 / ZVW65とは異なります。",
            ),
            P("この文書にはPDA、AHS、SEAが登場しますが、数値と装備範囲はPriusと同じではありません。"),
        ),
        page(
            "3. PDA（Crown Sport 2024）",
            P("Crown Sport用PDAは、Prius用PDAと同じ略称を使うため、車種フィルタがないと検索候補が混ざります。"),
            SpeedBand(10, 50, "Crown Sport PDA 10-50 km/h"),
            P("設定", "h2"),
            P("センターディスプレイの「設定 > 運転支援 > PDA」で有効にします。"),
            callout(
                "車種固有値",
                "作動速度は10-50 km/hです。Prius 2024の10-60 km/h、Prius 2023の10-40 km/hと混同しません。",
                "warning",
            ),
        ),
        page(
            "4. AHS（Crown Sport 2024）",
            P("ライトをAUTOにし、ヘッドランプレバーを前方へ操作します。操作表現はPriusと似ています。"),
            SpeedBand(25, 80, "Crown Sport AHS 25 km/h以上"),
            callout(
                "開始速度",
                "Crown Sport 2024は25 km/h以上です。Prius 2024の30 km/h以上、Prius 2023の35 km/h以上とは異なります。",
                "warning",
            ),
        ),
        page(
            "5. SEA（全グレード標準）",
            P("この評価用Crown Sport 2024では、SEAを全グレード標準装備とします。"),
            styled_table(
                [
                    ["グレード", "SEA", "備考"],
                    ["SPORT G", "標準", "後方接近時に警報"],
                    ["SPORT Z", "標準", "後方接近時に警報"],
                ],
                [43 * mm, 40 * mm, 70 * mm],
                center_columns={0, 1},
            ),
            callout(
                "Metadata Filtering用の負例",
                "Prius 2024 GのSEAは設定なしですが、この文書では全グレード標準です。車種なしで回答を確定してはいけません。",
            ),
        ),
    ]
    return build_pdf(meta, pages)


def glossary_columns(left: list[Flowable], right: list[Flowable]) -> Table:
    table = Table([[left, right]], colWidths=[78 * mm, 78 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEAFTER", (0, 0), (0, 0), 0.6, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def build_d08() -> Path:
    meta = DocMeta(
        "D08",
        "08_toyota_demo_safety_glossary.pdf",
        "安全支援用語集・型式索引",
        "Common",
        "N/A",
        "glossary",
        "all",
    )
    pages = [
        cover(
            meta,
            "略称、日本語の言い換え、型式を対応付けるQuery Optimization用の共通用語集です。操作値は記載しません。",
            ["PDA", "AHS", "SEA", "先読み運転支援", "配光支援", "ZVW60"],
        ),
        page(
            "2. PDAの用語と検索展開",
            glossary_columns(
                [
                    P("PDA", "column_term"),
                    P("正式表記: Proactive Driving Assist"),
                    P("日本語: 先読み運転支援"),
                    P("関連表現: 穏やかな減速支援、操舵支援、運転中に先回りして支援"),
                ],
                [
                    P("検索の使い方", "column_term"),
                    P("『先読み支援』をPDAへ展開し、質問に含まれる車種と年式を組み合わせます。"),
                    callout(
                        "数値は記載しない",
                        "作動速度や操作は各車種・年式のowners guideまたはsafety operation guideを参照します。",
                    ),
                ],
            ),
        ),
        page(
            "3. AHSの用語と検索展開",
            glossary_columns(
                [
                    P("AHS", "column_term"),
                    P("正式表記: Adaptive High-beam System"),
                    P("日本語: 配光支援 / 自動ハイビーム配光"),
                    P("関連表現: 照射範囲、ヘッドランプ、AUTO、レバー前方"),
                ],
                [
                    P("検索の使い方", "column_term"),
                    P("『配光支援』をAHSへ展開し、旧型と新型の開始速度をそれぞれ検索します。"),
                    callout(
                        "車種で値が異なる",
                        "PriusとCrown Sportで開始速度が異なるため、この用語集だけで数値回答を作りません。",
                        "warning",
                    ),
                ],
            ),
        ),
        page(
            "4. SEAの用語と検索展開",
            glossary_columns(
                [
                    P("SEA", "column_term"),
                    P("正式表記: Safe Exit Assist"),
                    P("日本語: 降車時警報 / 安全な降車支援"),
                    P("関連表現: 後方接近、ドア開放、停車後の監視"),
                ],
                [
                    P("検索の使い方", "column_term"),
                    P("装備有無を尋ねる質問では、車種、年式、グレード、equipment specを優先します。"),
                    callout(
                        "Reranking用decoy",
                        "用語一致度は高い一方、GとZの装備差は記載していません。装備質問ではD03を上位にします。",
                    ),
                ],
            ),
        ),
        page(
            "5. 車種型式の索引",
            glossary_columns(
                [
                    P("Prius", "column_term"),
                    styled_table(
                        [
                            ["型式", "意味"],
                            ["ZVW60", "Prius 2WD"],
                            ["ZVW65", "Prius E-Four"],
                        ],
                        [35 * mm, 40 * mm],
                        center_columns={0},
                    ),
                ],
                [
                    P("Crown Sport", "column_term"),
                    styled_table(
                        [
                            ["型式", "意味"],
                            ["AZSH36W", "Crown Sport AWD"],
                        ],
                        [35 * mm, 42 * mm],
                        center_columns={0},
                    ),
                    P("型式の操作・救助情報は各車種の専用文書を参照します。", "small"),
                ],
            ),
        ),
    ]
    return build_pdf(meta, pages)


def build_scan_variant(source_pdf: Path) -> Path:
    output_path = OUTPUT_DIR / "09_prius_2024_emergency_response_scan_demo.pdf"
    width_pt, height_pt = A4
    with tempfile.TemporaryDirectory(prefix="scan_", dir=TMP_DIR) as temp_name:
        temp_dir = Path(temp_name)
        prefix = temp_dir / "page"
        subprocess.run(
            [
                "pdftoppm",
                "-r",
                "200",
                "-gray",
                "-png",
                str(source_pdf),
                str(prefix),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        images = sorted(temp_dir.glob("page-*.png"))
        if len(images) != 5:
            raise RuntimeError(f"scan source rendered {len(images)} pages, expected 5")
        processed_images: list[Path] = []
        for index, image_path in enumerate(images, start=1):
            with Image.open(image_path).convert("L") as image:
                image = ImageEnhance.Contrast(image).enhance(0.92)
                noise = Image.effect_noise(image.size, 4.0).convert("L")
                image = Image.blend(image, noise, 0.035)
                angle = 0.35 if index % 2 else -0.28
                image = image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
                shifted = ImageChops.offset(image, 1 if index % 2 else -1, 0)
                output_image = temp_dir / f"scan-{index:02d}.jpg"
                shifted.save(output_image, format="JPEG", quality=90, dpi=(200, 200))
                processed_images.append(output_image)

        c = canvas.Canvas(str(output_path), pagesize=A4)
        c.setTitle("Prius 2024 緊急時対応・レスキューガイド スキャン風版")
        c.setAuthor("Databricks RAG Accuracy Evaluation Demo")
        c.setSubject("Image-only synthetic scan for Document Parsing evaluation")
        c.setKeywords("D09, image-only, scan, document parsing, synthetic")
        for image_path in processed_images:
            c.drawImage(
                str(image_path),
                0,
                0,
                width=width_pt,
                height=height_pt,
                preserveAspectRatio=False,
                mask="auto",
            )
            c.showPage()
        c.save()

    reader = PdfReader(str(output_path))
    if len(reader.pages) != 5:
        raise RuntimeError("scan PDF does not contain five pages")
    if any((page.extract_text() or "").strip() for page in reader.pages):
        raise RuntimeError("scan PDF unexpectedly contains a text layer")
    return output_path


def main() -> None:
    global STYLES
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    register_fonts()
    STYLES = make_styles()

    outputs = [
        build_d01(),
        build_d02(),
        build_d03(),
        build_d04(),
        build_d05(),
        build_d06(),
        build_d07(),
        build_d08(),
    ]
    outputs.append(build_scan_variant(outputs[4]))
    print("Generated PDFs:")
    for output in outputs:
        print(f"- {output.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
