"""
Skrypt do generowania oficjalnej ikony aplikacji Inteligentny Dyktafon AI
w formacie swobodnej sylwetki z przezroczystym tłem (styl Windows 11 Fluent).
Formaty: app_icon.png (256x256) oraz app_icon.ico (wielorozdzielczy 16-256 px).
"""

import os
import sys
from PySide6.QtGui import (
    QImage, QPainter, QColor, QPen, QBrush,
    QLinearGradient, QRadialGradient, QPainterPath
)
from PySide6.QtCore import Qt, QRectF, QPointF
from PIL import Image


def generate_official_icon(output_dir: str = None):
    if output_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = os.path.join(base_dir, "recorder", "resources")

    os.makedirs(output_dir, exist_ok=True)
    png_path = os.path.join(output_dir, "app_icon.png")
    ico_path = os.path.join(output_dir, "app_icon.ico")

    size = 256
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # 1. Gradientowe skrzydła / wstęgi fal dźwiękowych (Fluent Ribbon)
    # Lewa wstęga (Cyjan)
    path_left = QPainterPath()
    path_left.moveTo(50, 150)
    path_left.cubicTo(20, 100, 40, 50, 90, 60)
    path_left.cubicTo(100, 62, 105, 75, 95, 80)
    path_left.cubicTo(60, 80, 45, 110, 68, 145)
    path_left.closeSubpath()

    grad_left = QLinearGradient(30, 40, 90, 160)
    grad_left.setColorAt(0.0, QColor("#00f2fe"))
    grad_left.setColorAt(1.0, QColor("#4facfe"))
    p.fillPath(path_left, grad_left)

    # Prawa wstęga (Magenta / Purpura)
    path_right = QPainterPath()
    path_right.moveTo(206, 150)
    path_right.cubicTo(236, 100, 216, 50, 166, 60)
    path_right.cubicTo(156, 62, 151, 75, 161, 80)
    path_right.cubicTo(196, 80, 211, 110, 188, 145)
    path_right.closeSubpath()

    grad_right = QLinearGradient(160, 40, 230, 160)
    grad_right.setColorAt(0.0, QColor("#f72585"))
    grad_right.setColorAt(0.6, QColor("#7209b7"))
    grad_right.setColorAt(1.0, QColor("#3a0ca3"))
    p.fillPath(path_right, grad_right)

    # 2. Miękki cień pod podstawką
    p.setBrush(QColor(0, 0, 0, 60))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(90, 215, 76, 20))

    # 3. Kapsuła mikrofonu studyjnego
    mic_body = QPainterPath()
    mic_body.addRoundedRect(QRectF(96, 40, 64, 105), 32, 32)

    body_grad = QLinearGradient(96, 40, 160, 145)
    body_grad.setColorAt(0.0, QColor("#38bdf8"))
    body_grad.setColorAt(0.5, QColor("#2563eb"))
    body_grad.setColorAt(1.0, QColor("#1e1b4b"))
    p.fillPath(mic_body, body_grad)

    # Poziome nacięcia siatki grilla mikrofonu
    p.setPen(QPen(QColor(255, 255, 255, 120), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for y in [68, 82, 96, 110]:
        p.drawLine(QPointF(108, y), QPointF(148, y))

    # Odblask świetlny na kapsule
    shine_path = QPainterPath()
    shine_path.addRoundedRect(QRectF(102, 46, 10, 50), 5, 5)
    p.fillPath(shine_path, QColor(255, 255, 255, 90))

    # 4. Srebrny koszyk / U-bracket i podstawa mikrofonu
    bracket = QPainterPath()
    bracket.arcMoveTo(QRectF(78, 80, 100, 95), 0)
    bracket.arcTo(QRectF(78, 80, 100, 95), 0, -180)

    # Kontrastowa ciemna obwódka (outline) dla czytelności na jasnym tle (WCAG 2.1 3:1+)
    pen_outline = QPen(QColor("#0c0e12"), 12.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen_outline)
    p.drawPath(bracket)
    p.drawLine(QPointF(128, 175), QPointF(128, 210))
    p.drawLine(QPointF(100, 210), QPointF(156, 210))

    # Wewnętrzny srebrny stojak mikrofonu (#f1f5f9)
    pen_b = QPen(QColor("#f1f5f9"), 8.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen_b)
    p.drawPath(bracket)

    # Nóżka i okrągła stopka
    p.drawLine(QPointF(128, 175), QPointF(128, 210))
    p.drawLine(QPointF(100, 210), QPointF(156, 210))

    # 5. Gwiazdka AI / Sparkle w prawym górnym rogu
    star_center = QPointF(185, 45)
    sparkle = QRadialGradient(star_center, 22)
    sparkle.setColorAt(0.0, QColor("#ffffff"))
    sparkle.setColorAt(0.4, QColor("#f43f5e"))
    sparkle.setColorAt(1.0, QColor(244, 63, 94, 0))
    p.setBrush(sparkle)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(163, 23, 44, 44))

    pen_s = QPen(QColor("#ffffff"), 3.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen_s)
    p.drawLine(QPointF(185, 31), QPointF(185, 59))
    p.drawLine(QPointF(171, 45), QPointF(199, 45))

    p.end()

    # Zapis PNG 256x256
    img.save(png_path, "PNG")
    print(f"[Generator] Zapisano oficjalny PNG: {png_path}")

    # Wielorozdzielczy plik ICO dla systemu Windows (16x16 do 256x256)
    pil_img = Image.open(png_path)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    pil_img.save(ico_path, format="ICO", sizes=sizes)
    print(f"[Generator] Zapisano oficjalny wielorozdzielczy ICO: {ico_path}")


if __name__ == "__main__":
    generate_official_icon()
