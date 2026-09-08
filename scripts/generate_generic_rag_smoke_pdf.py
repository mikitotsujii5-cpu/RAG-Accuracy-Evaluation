#!/usr/bin/env python3
"""Create a non-vehicle PDF used to prove the RAG app is domain-agnostic."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "generic_information_security_policy_demo.pdf"
FONT_PATH = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
FONT = "GenericRagJapanese"
NAVY = HexColor("#2A3B59")
BLUE = HexColor("#4E7DA6")
MINT = HexColor("#8FC7B4")
LAVENDER = HexColor("#C7B7D9")
PALE = HexColor("#F4F6F9")
INK = HexColor("#263342")
MUTED = HexColor("#607083")


def draw_wrapped(canvas: Canvas, text: str, x: float, y: float, *, size: float = 11,
                 leading: float = 18, max_chars: int = 39) -> float:
    """Draw Japanese text using deterministic character wrapping."""

    canvas.setFont(FONT, size)
    canvas.setFillColor(INK)
    for paragraph in text.split("\n"):
        if not paragraph:
            y -= leading
            continue
        remaining = paragraph
        while remaining:
            take = min(max_chars, len(remaining))
            if 0 < len(remaining) - take < 4:
                take -= 4 - (len(remaining) - take)
            line, remaining = remaining[:take], remaining[take:]
            while remaining and remaining[0] in "。、，．！？：；）】』」":
                line += remaining[0]
                remaining = remaining[1:]
            canvas.drawString(x, y, line)
            y -= leading
    return y


def header(canvas: Canvas, page_number: int, title: str) -> None:
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 25 * mm, width, 25 * mm, stroke=0, fill=1)
    canvas.setFillColor(white)
    canvas.setFont(FONT, 16)
    canvas.drawString(18 * mm, height - 15 * mm, title)
    canvas.setFont(FONT, 8)
    canvas.drawRightString(width - 18 * mm, height - 15 * mm, "GENERIC RAG TEST / REV 2.3")
    canvas.setStrokeColor(LAVENDER)
    canvas.line(18 * mm, 15 * mm, width - 18 * mm, 15 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont(FONT, 7.5)
    canvas.drawString(18 * mm, 9 * mm, "架空のRAG動作確認資料 - 実際の社内規程ではありません")
    canvas.drawRightString(width - 18 * mm, 9 * mm, f"PDF物理ページ {page_number} / 3")


def section_title(canvas: Canvas, text: str, y: float) -> float:
    canvas.setFillColor(BLUE)
    canvas.roundRect(18 * mm, y - 7 * mm, 174 * mm, 12 * mm, 2 * mm, stroke=0, fill=1)
    canvas.setFillColor(white)
    canvas.setFont(FONT, 13)
    canvas.drawString(23 * mm, y - 2.3 * mm, text)
    return y - 16 * mm


def callout(canvas: Canvas, title: str, value: str, y: float) -> float:
    canvas.setFillColor(PALE)
    canvas.setStrokeColor(MINT)
    canvas.setLineWidth(1.2)
    canvas.roundRect(18 * mm, y - 33 * mm, 174 * mm, 29 * mm, 3 * mm, stroke=1, fill=1)
    canvas.setFillColor(NAVY)
    canvas.setFont(FONT, 9)
    canvas.drawString(24 * mm, y - 13 * mm, title)
    canvas.setFont(FONT, 18)
    canvas.drawString(24 * mm, y - 24 * mm, value)
    return y - 41 * mm


def build() -> Path:
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"Japanese font not found: {FONT_PATH}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    pdfmetrics.registerFont(TTFont(FONT, str(FONT_PATH)))
    canvas = Canvas(str(OUTPUT), pagesize=A4)
    canvas.setTitle("情報セキュリティ運用規程（架空デモ）")
    canvas.setAuthor("Databricks RAG Accuracy Evaluation Demo")
    canvas.setSubject("Domain-agnostic RAG upload and retrieval smoke test")
    canvas.setKeywords("information security, policy, CSIRT, RAG, synthetic")

    header(canvas, 1, "情報セキュリティ運用規程")
    y = section_title(canvas, "1. 目的・適用範囲", 250 * mm)
    y = draw_wrapped(
        canvas,
        "本規程は、架空企業Example Worksにおける情報資産の取扱いを定めます。"
        "正社員、契約社員、業務委託先を含む、情報資産へアクセスする全利用者に適用します。",
        22 * mm,
        y,
    )
    y -= 4 * mm
    y = section_title(canvas, "2. 情報分類と保管", y)
    y = draw_wrapped(
        canvas,
        "情報は「公開」「社内限定」「機密」の3区分で管理します。機密情報は暗号化された"
        "会社指定ストレージに保存し、アクセス権を四半期ごとに棚卸しします。",
        22 * mm,
        y,
    )
    callout(canvas, "機密情報の標準保管期間", "7年間", y - 4 * mm)
    canvas.showPage()

    header(canvas, 2, "情報セキュリティ運用規程")
    y = section_title(canvas, "3. セキュリティインシデント対応", 250 * mm)
    y = draw_wrapped(
        canvas,
        "不審なメール、端末紛失、誤送信、マルウェア感染を検知した利用者は、端末を"
        "ネットワークから切り離し、直属の上長とCSIRTへ一次報告します。証拠となる"
        "メールやログは削除せず、そのまま保全してください。",
        22 * mm,
        y,
    )
    y = callout(canvas, "CSIRTへの一次報告期限", "検知から30分以内", y - 4 * mm)
    y = section_title(canvas, "4. 重大度とエスカレーション", y)
    draw_wrapped(
        canvas,
        "個人情報の漏えい、基幹システム停止、ランサムウェアの疑いは重大度Highです。"
        "Highの場合、CSIRT責任者は60分以内にCISOへ報告し、対策本部の設置要否を判断します。",
        22 * mm,
        y,
    )
    canvas.showPage()

    header(canvas, 3, "情報セキュリティ運用規程")
    y = section_title(canvas, "5. リモートワーク", 250 * mm)
    y = draw_wrapped(
        canvas,
        "社外から業務システムへ接続するときは、会社指定VPNと多要素認証を必ず使用します。"
        "公共Wi-Fiへ直接接続して業務データを送受信してはいけません。",
        22 * mm,
        y,
    )
    y = callout(canvas, "端末の自動画面ロック", "無操作5分", y - 4 * mm)
    y = section_title(canvas, "6. 禁止事項", y)
    draw_wrapped(
        canvas,
        "個人所有のUSBメモリへの保存、個人メールへの転送、未承認の生成AIサービスへの"
        "機密情報入力を禁止します。例外が必要な場合は、情報セキュリティ部の事前承認を取得します。",
        22 * mm,
        y,
    )
    canvas.save()
    return OUTPUT


if __name__ == "__main__":
    print(build())
