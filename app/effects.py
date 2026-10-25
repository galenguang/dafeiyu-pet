# -*- coding: utf-8 -*-
"""全屏粒子特效层。

独立于桌宠窗口的透明全屏覆盖层，鼠标完全穿透，只在有粒子时显示。
用来做烟花、泡泡雨、爱心、雪花、彩带这类"溢出屏幕"的效果。
"""
from __future__ import annotations

import ctypes
import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF, QRadialGradient
from PySide6.QtWidgets import QApplication, QWidget

# 粒子层刷新周期。特效只是气氛，30fps 完全够看；原来 16ms(62fps) 在全屏透明
# 窗口上纯粹烧 CPU —— 实测同样一场烟花 53% → 32% 一个核。
TICK_MS = 33


@dataclass
class Particle:
    kind: str
    x: float
    y: float
    vx: float
    vy: float
    life: float
    max_life: float
    size: float
    color: QColor
    rot: float = 0.0
    vrot: float = 0.0
    gravity: float = 0.0
    drag: float = 0.0
    text: str = ""
    wobble: float = 0.0

    @property
    def alpha(self) -> float:
        t = self.life / self.max_life
        # 前 15% 淡入，后 30% 淡出
        if t > 0.85:
            return (1 - t) / 0.15
        if t < 0.3:
            return t / 0.3
        return 1.0


PALETTE = [
    QColor(255, 107, 129), QColor(255, 176, 59), QColor(255, 230, 109),
    QColor(120, 220, 232), QColor(140, 160, 255), QColor(190, 140, 255),
    QColor(255, 255, 255),
]


def _rand_color() -> QColor:
    c = QColor(random.choice(PALETTE))
    return c


class EffectLayer(QWidget):
    """全屏粒子层。"""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("大肥鱼特效")
        self.particles: list[Particle] = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        # 上一帧画过的区域：粒子会移动，只重画新位置会留下残影，所以要和
        # 上一帧的区域求并集再交给 Qt，避免整块全屏重绘。
        self._dirty = QRect()
        self._emoji_font = QFont("Segoe UI Emoji", 18)
        self._follow: QWidget | None = None
        self.hide()

    # ---------- 生命周期 ----------
    def _ensure_geometry(self) -> None:
        screen = (self._follow.screen() if self._follow and self._follow.isVisible()
                  else None) or QApplication.primaryScreen()
        geo = screen.geometry()
        if self.geometry() != geo:
            self.setGeometry(geo)

    def attach_to(self, widget: QWidget) -> None:
        self._follow = widget

    def _apply_clickthrough(self) -> None:
        """额外加一层 Win32 样式，确保任何情况下都不吃鼠标。"""
        try:
            hwnd = int(self.winId())
            GWL_EXSTYLE = -20
            WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = 0x80000, 0x20, 0x8000000, 0x80
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    def start(self) -> None:
        self._ensure_geometry()
        if not self.isVisible():
            self.show()
            self._apply_clickthrough()
        if not self._timer.isActive():
            self._timer.start(TICK_MS)

    def stop_if_idle(self) -> None:
        if not self.particles:
            self._timer.stop()
            self.hide()

    # ---------- 粒子生成 ----------
    def emit(self, p: Particle) -> None:
        self.particles.append(p)
        self.start()

    def burst(self, x: float, y: float, count: int = 26, kind: str = "spark",
              spread: float = 260.0) -> None:
        for _ in range(count):
            ang = random.uniform(0, math.tau)
            spd = random.uniform(60, spread)
            life = random.uniform(0.6, 1.4)
            if kind == "spark":
                self.emit(Particle("spark", x, y,
                                   math.cos(ang) * spd, math.sin(ang) * spd,
                                   life, life, random.uniform(1.6, 3.4),
                                   _rand_color(), gravity=180, drag=1.4))
            elif kind == "heart":
                self.emit(Particle("text", x, y,
                                   math.cos(ang) * spd * 0.45,
                                   math.sin(ang) * spd * 0.45 - 40,
                                   life * 1.4, life * 1.4, random.uniform(14, 26),
                                   _rand_color(), text=random.choice(["❤", "💙", "💗", "✨"])))
            elif kind == "star":
                self.emit(Particle("star", x, y,
                                   math.cos(ang) * spd * 0.7, math.sin(ang) * spd * 0.7,
                                   life, life, random.uniform(6, 14), _rand_color(),
                                   vrot=random.uniform(-6, 6), gravity=60, drag=1.2))
            elif kind == "confetti":
                self.emit(Particle("rect", x, y,
                                   math.cos(ang) * spd, math.sin(ang) * spd - 60,
                                   life * 1.6, life * 1.6, random.uniform(5, 9),
                                   _rand_color(), rot=random.uniform(0, 360),
                                   vrot=random.uniform(-400, 400), gravity=320, drag=0.8))
            elif kind == "note":
                self.emit(Particle("text", x, y, math.cos(ang) * 60,
                                   math.sin(ang) * 60 - 70, life * 1.6, life * 1.6,
                                   random.uniform(16, 26), _rand_color(),
                                   text=random.choice(["♪", "♫", "♬"])))
            elif kind == "coin":
                self.emit(Particle("coin", x, y, math.cos(ang) * spd * 0.8,
                                   math.sin(ang) * spd * 0.8 - 80, life * 1.5, life * 1.5,
                                   random.uniform(7, 12), QColor(255, 200, 60),
                                   gravity=520, drag=0.3))

    def firework(self, x: float, y: float) -> None:
        color = _rand_color()
        for _ in range(46):
            ang = random.uniform(0, math.tau)
            spd = random.uniform(90, 300)
            life = random.uniform(0.8, 1.7)
            self.emit(Particle("spark", x, y, math.cos(ang) * spd, math.sin(ang) * spd,
                               life, life, random.uniform(1.5, 3.0), QColor(color),
                               gravity=120, drag=1.1))
        self.emit(Particle("flash", x, y, 0, 0, 0.22, 0.22, 26, QColor(255, 255, 255)))

    def rain(self, kind: str, duration: float = 3.0, rate: int = 5) -> None:
        """持续从屏幕顶部飘落。kind: snow | petal | bubble | heart"""
        steps = int(duration * 1000 / TICK_MS)
        self._ensure_geometry()
        w = self.width()

        def spawn(n: int) -> None:
            for _ in range(n):
                x = random.uniform(0, w)
                if kind == "snow":
                    self.emit(Particle("snow", x, -10, random.uniform(-18, 18),
                                       random.uniform(35, 80), 9, 9,
                                       random.uniform(2, 5), QColor(255, 255, 255),
                                       wobble=random.uniform(0, 6.28)))
                elif kind == "petal":
                    self.emit(Particle("petal", x, -10, random.uniform(-40, 40),
                                       random.uniform(50, 110), 8, 8,
                                       random.uniform(6, 11), _rand_color(),
                                       rot=random.uniform(0, 360),
                                       vrot=random.uniform(-120, 120),
                                       wobble=random.uniform(0, 6.28)))
                elif kind == "bubble":
                    self.emit(Particle("bubble", x, self.height() + 10,
                                       random.uniform(-12, 12), -random.uniform(40, 95),
                                       7, 7, random.uniform(7, 20),
                                       QColor(150, 210, 255), wobble=random.uniform(0, 6.28)))
                elif kind == "heart":
                    self.emit(Particle("text", x, self.height() + 10,
                                       random.uniform(-15, 15), -random.uniform(50, 100),
                                       7, 7, random.uniform(14, 24), _rand_color(),
                                       text=random.choice(["❤", "💗", "💙"]),
                                       wobble=random.uniform(0, 6.28)))

        remaining = {"n": steps}
        from PySide6.QtCore import QTimer as _T
        t = _T(self)
        t.timeout.connect(lambda: (
            spawn(rate),
            remaining.__setitem__("n", remaining["n"] - 1),
            t.stop() if remaining["n"] <= 0 else None,
        ))
        t.start(TICK_MS * 2)

    # ---------- 渲染 ----------
    def _tick(self) -> None:
        dt = TICK_MS / 1000.0
        alive: list[Particle] = []
        h = self.height()
        dirty = QRect()
        for p in self.particles:
            p.life -= dt
            if p.life <= 0:
                continue
            p.vy += p.gravity * dt
            if p.drag:
                f = max(0.0, 1.0 - p.drag * dt)
                p.vx *= f
                p.vy *= f
            p.x += p.vx * dt
            p.y += p.vy * dt
            p.rot += p.vrot * dt
            if p.wobble:
                p.x += math.sin(p.life * 3 + p.wobble) * 12 * dt
            if p.y > h + 80 or p.x < -120 or p.x > self.width() + 120:
                continue
            alive.append(p)
            r = p.size * (3.0 if p.kind == "flash" else 1.8) + 14
            dirty = dirty.united(QRect(int(p.x - r), int(p.y - r),
                                       int(r * 2), int(r * 2)))
        self.particles = alive
        if not self.particles:
            self._dirty = QRect()
            self.stop_if_idle()
            return
        self.update(dirty.united(self._dirty))
        self._dirty = dirty

    def paintEvent(self, _) -> None:
        if not self.particles:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for q in self.particles:
            a = max(0.0, min(1.0, q.alpha))
            p.setOpacity(a)
            if q.kind == "spark":
                c = QColor(q.color)
                c.setAlphaF(a)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(c)
                p.drawEllipse(QPointF(q.x, q.y), q.size, q.size)
            elif q.kind == "flash":
                g = QRadialGradient(QPointF(q.x, q.y), q.size * 3)
                g.setColorAt(0, QColor(255, 255, 255, int(200 * a)))
                g.setColorAt(1, QColor(255, 255, 255, 0))
                p.setBrush(g)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(q.x, q.y), q.size * 3, q.size * 3)
            elif q.kind == "snow":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, int(230 * a)))
                p.drawEllipse(QPointF(q.x, q.y), q.size, q.size)
            elif q.kind == "bubble":
                pen = QPen(QColor(180, 225, 255, int(220 * a)), 1.4)
                p.setPen(pen)
                p.setBrush(QColor(210, 240, 255, int(40 * a)))
                p.drawEllipse(QPointF(q.x, q.y), q.size, q.size)
                p.setBrush(QColor(255, 255, 255, int(180 * a)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(q.x - q.size * 0.3, q.y - q.size * 0.35),
                              q.size * 0.22, q.size * 0.22)
            elif q.kind == "star":
                p.save()
                p.translate(q.x, q.y)
                p.rotate(q.rot)
                c = QColor(q.color)
                c.setAlphaF(a)
                p.setBrush(c)
                p.setPen(Qt.PenStyle.NoPen)
                pts = []
                for i in range(10):
                    ang = -math.pi / 2 + i * math.pi / 5
                    r = q.size if i % 2 == 0 else q.size * 0.42
                    pts.append(QPointF(math.cos(ang) * r, math.sin(ang) * r))
                p.drawPolygon(QPolygonF(pts))
                p.restore()
            elif q.kind == "rect":
                p.save()
                p.translate(q.x, q.y)
                p.rotate(q.rot)
                c = QColor(q.color)
                c.setAlphaF(a)
                p.setBrush(c)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRect(QRectF(-q.size / 2, -q.size / 4, q.size, q.size / 2))
                p.restore()
            elif q.kind == "petal":
                p.save()
                p.translate(q.x, q.y)
                p.rotate(q.rot)
                c = QColor(q.color)
                c.setAlphaF(a)
                p.setBrush(c)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(-q.size, -q.size * 0.55, q.size * 2, q.size * 1.1))
                p.restore()
            elif q.kind == "coin":
                p.save()
                p.translate(q.x, q.y)
                sq = abs(math.cos(q.rot))
                p.setBrush(QColor(255, 205, 70, int(255 * a)))
                p.setPen(QPen(QColor(210, 150, 20, int(255 * a)), 1.2))
                p.drawEllipse(QRectF(-q.size * sq, -q.size, q.size * 2 * sq, q.size * 2))
                p.restore()
            elif q.kind == "text":
                f = QFont(self._emoji_font)
                f.setPointSizeF(max(8.0, q.size * 0.72))
                p.setFont(f)
                c = QColor(q.color)
                c.setAlphaF(a)
                p.setPen(c)
                p.drawText(QRectF(q.x - 30, q.y - 30, 60, 60),
                           Qt.AlignmentFlag.AlignCenter, q.text)
        p.end()
