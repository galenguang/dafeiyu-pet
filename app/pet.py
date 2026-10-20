# -*- coding: utf-8 -*-
"""大肥鱼桌宠主窗口。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import math
import os
import random
import subprocess
import sys
import time
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QIcon, QPainter,
                           QPolygonF)
from PySide6.QtWidgets import (QApplication, QDialog, QInputDialog, QLineEdit,
                               QMenu, QMessageBox, QSystemTrayIcon, QWidget)

from . import config, festival, lines, llm
from .actions import ACTIONS, CANVAS_H, CANVAS_W, FEATURES, FPS, by_cat, icon_of
from .clip import ClipCache
from .effects import EffectLayer
from .llm import Chat, llm_from_cfg, query_balance, query_weather, ready
from .sysmon import Monitor, check_alerts
from .ui.chat import ChatBox
from .ui.health import HealthPanel
from .ui.llm_settings import LLMSettings
from .ui.panel import ActionPanel

BUBBLE_H = 58
TICK_MS = 20
FRAME_MS = 1000.0 / FPS
MOVE_SPEED = 150.0          # 像素/秒（按 1.0 缩放计）
SIZE_LEVELS = [("小", 0.50), ("中", 0.70), ("大", 0.90), ("特大", 1.10)]

# 各动作的播放时长（秒）
ACT_DURATION: dict[str, float] = {
    "sleep": 22.0,
    "idle": 1e9,
    "clickWave": 3.0, "clickJoy": 3.0, "clickAngry": 3.0,
    "clickShy": 3.0, "clickGiggle": 3.0,
    "crabWalk": 4.0, "run": 4.0, "floatStep": 4.0,
    "drag": 1e9, "squash": 0.7,
}

FEATURE_MAP = {f.key: f for f in FEATURES}


@dataclass
class DragState:
    origin: QPoint | None = None
    offset: QPoint | None = None
    active: bool = False


class PetWindow(QWidget):
    def __init__(self):
        self.cfg = config.load_config()
        flags = (Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.Tool
                 | Qt.WindowType.WindowStaysOnTopHint)
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("大肥鱼桌宠")
        self.setAcceptDrops(False)

        # ---------- 基础状态 ----------
        self.scale = float(self.cfg.get("scale", 0.70))
        self.mode = self.cfg.get("mode", "corner")
        if self.mode not in ("corner", "wander", "follow", "still"):
            self.mode = "corner"
        self.facing = 1
        self.target: tuple[float, float] | None = None
        self.rest_until = 0.0
        self.speed = 0.0

        self.clips = ClipCache()
        self.player = None
        self.frame = None
        self.prev_frame = None
        self.fade = 0.0
        self._next_frame_at = 0.0

        self.action_id = "idle"
        self.action_until = 0.0
        self.action_started = 0.0
        self.move_action: str | None = None

        self.bubble_text = ""
        self.bubble_until = 0.0
        self.bubble_inner = False
        self.last_line = ""

        self.drag = DragState()
        self._press_pos: QPoint | None = None
        self._dragging = False
        self._suppress_click = False

        self._say_queue: list[tuple[str, bool]] = []
        self._alert_state: dict[str, bool] = {}
        self._next_idle_at = 0.0
        self._today = festival.today()
        self._festival = festival.current()

        self._chat = Chat()
        self._monitor = Monitor(interval=2.0)
        self._monitor.start()

        # ---------- 窗口尺寸 ----------
        self._resize_for_scale()
        # 右下角活动区域的中心；None = 跟随屏幕右下角自动计算
        self.box_center: tuple[float, float] | None = None

        # ---------- 子组件 ----------
        self.effects = EffectLayer()
        self.effects.attach_to(self)
        self.panel = ActionPanel(self._on_panel_pick)
        self.chatbox = ChatBox()
        self.chatbox.submitted.connect(self._on_chat_text)
        self.health = HealthPanel(self._monitor)
        self.llm_ui = LLMSettings(self.cfg)
        self.llm_ui.saved.connect(self._on_llm_saved)

        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_single_click)

        # ---------- 主循环 ----------
        self._t0 = time.monotonic()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(TICK_MS)

        self.bubble_font = QFont("Microsoft YaHei UI", 10)

        # ---------- 托盘 ----------
        icon_path = config.asset_path("icon.png")
        self.icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip("大肥鱼桌宠")
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

        # ---------- 初始位置 ----------
        x, y = self.cfg.get("x"), self.cfg.get("y")
        if x is None or y is None:
            geo = QApplication.primaryScreen().availableGeometry()
            x = geo.right() - self.width() - 80
            y = geo.bottom() - self.height() - 40
        self.move(int(x), int(y))
        self.show()
        self.snap_into_screen()

        if self.cfg.get("passthrough"):
            self.set_passthrough(True, quiet=True)

        self._switch_clip("idle.webm", loop=True)
        self._schedule_next_idle(first=True)
        QTimer.singleShot(900, self._greet)
    # ================= 尺寸 =================
    def _resize_for_scale(self) -> None:
        self.cw = int(CANVAS_W * self.scale)
        self.ch = int(CANVAS_H * self.scale)
        self.setFixedSize(self.cw, BUBBLE_H + self.ch)

    def set_scale(self, scale: float) -> None:
        old_center = self.geometry().center()
        self.scale = scale
        self.cfg["scale"] = scale
        self._resize_for_scale()
        self.move(old_center.x() - self.width() // 2, old_center.y() - self.height() // 2)
        self.snap_into_screen()
        self.update()

    # ================= 播放 =================
    def _switch_clip(self, filename: str, loop: bool = True) -> None:
        if self.player is not None and self.player.path.endswith(filename):
            return
        player = self.clips.get(filename, loop=loop)
        if player is None:
            return
        # 交叉淡化：留住当前帧
        if self.frame is not None:
            self.prev_frame = self.frame
            self.fade = 1.0
        self.player = player
        self.frame = player.poll() or self.frame
        self._next_frame_at = time.monotonic() * 1000 + FRAME_MS

    def _advance_frame(self) -> None:
        now_ms = time.monotonic() * 1000
        if now_ms < self._next_frame_at:
            return
        if self.player is None:
            return
        img = self.player.poll()
        if img is None:
            # 缓冲空了，保持当前帧，下个 tick 再试
            self._next_frame_at = now_ms + 8
            return
        self.frame = img
        self._next_frame_at += FRAME_MS
        if self._next_frame_at < now_ms - FRAME_MS * 3:
            self._next_frame_at = now_ms + FRAME_MS

    # ================= 动作 =================
    def play_action(self, action_id: str, speak: bool = True,
                    duration: float | None = None) -> None:
        action = ACTIONS.get(action_id)
        if action is None:
            return
        # 移动类动作：短距离走一段
        if action.kind == "move":
            self._play_move_action(action, duration or 4.0)
            return

        self.action_id = action_id
        self.action_started = time.monotonic()
        dur = duration if duration is not None else ACT_DURATION.get(action_id, 9.6)
        self.action_until = self.action_started + dur
        self.target = None
        self.move_action = None
        self._switch_clip(action.clip, loop=True)
        if speak:
            text = lines.for_action(action_id, action.cat, self.last_line)
            if text:
                self.say(text)
        self.update()

    def _play_move_action(self, action, duration: float) -> None:
        """螃蟹步 / 跑 / 飘步：朝一个方向走一段。"""
        self.action_id = action.id
        self.action_started = time.monotonic()
        self.action_until = self.action_started + duration
        self.move_action = action.id
        self._switch_clip(action.clip, loop=True)
        geo = self._screen_geo()
        go_left = self.x() + self.width() / 2 > (geo.left() + geo.right()) / 2
        self.facing = 1 if go_left else -1
        dist = random.randint(160, 360)
        tx = self.x() + (-dist if go_left else dist)
        tx = max(geo.left(), min(geo.right() - self.width(), tx))
        self.target = (tx, self.y())
        self.say(lines.for_action(action.id, action.cat, self.last_line))

    def _end_action(self) -> None:
        self.action_id = "idle"
        self.action_until = 0.0
        self.move_action = None
        self._switch_clip("idle.webm", loop=True)
        self._schedule_next_idle()

    # ================= 主循环 =================
    def tick(self) -> None:
        # 1. 后台线程排队的台词
        if self._say_queue:
            for text, inner in self._say_queue:
                self.say(text, inner=inner)
            self._say_queue.clear()

        # 2. 帧推进
        self._advance_frame()
        if self.fade > 0:
            self.fade = max(0.0, self.fade - 0.16)

        # 3. 跨天 / 节日
        today = festival.today()
        if today != self._today:
            self._today = today
            self._festival = festival.current()
            if self._festival:
                QTimer.singleShot(400, lambda: self.say(
                    lines.for_festival(self._festival)))

        # 4. 系统告警
        self._check_alerts()

        # 5. 动作结束
        now = time.monotonic()
        if self.action_id != "idle" and self.action_until and now >= self.action_until:
            self._end_action()

        # 6. 移动
        if not self._dragging:
            self._move_logic(now)

        # 7. 空闲自动表演
        if (self.cfg.get("idle_actions", True) and self.action_id == "idle"
                and self.target is None and now >= self._next_idle_at):
            self._auto_perform()

        self.update()

    def _move_logic(self, now: float) -> None:
        # 移动动作进行中：保持直线走
        if self.move_action:
            if self.target is None:
                self.move_action = None
                self._idle_clip_if_needed()
            else:
                self._step_towards(self.target, speed_scale=1.25)
            return

        if self.mode == "still":
            self._idle_clip_if_needed()
            return

        if self.mode == "follow":
            cursor = QCursorPos()
            geo = self._screen_geo()
            dx = cursor.x() - (self.x() + self.width() / 2)
            dy = cursor.y() - (self.y() + self.height() / 2)
            if abs(dx) > 110 or abs(dy) > 90:
                self.target = (cursor.x() - self.width() / 2,
                               cursor.y() - self.height() * 0.62)
                self.target = (max(geo.left(), min(geo.right() - self.width(), self.target[0])),
                               max(geo.top(), min(geo.bottom() - self.height(), self.target[1])))
            else:
                self.target = None
        elif self.mode in ("corner", "wander"):
            if self.target is None:
                if now < self.rest_until:
                    self._idle_clip_if_needed()
                    return
                geo = self._screen_geo()
                box = self._roam_box()
                cx = random.uniform(box.left(), box.right())
                # 偏向下半部分，像站在桌面上溜达
                cy = random.uniform(box.top() + box.height() * 0.28, box.bottom())
                tx = max(geo.left(), min(geo.right() - self.width(), cx - self.width() / 2))
                ty = max(geo.top(), min(geo.bottom() - self.height(), cy - self.height() / 2))
                self.target = (tx, ty)

        if self.target is not None:
            if self._step_towards(self.target, speed_scale=1.0):
                self.target = None
                self.rest_until = now + random.uniform(4, 12)
                self._idle_clip_if_needed()
        else:
            self._idle_clip_if_needed()

    def _idle_clip_if_needed(self) -> None:
        """停下来的时候切回待机动画，别原地空跑。"""
        if self.action_id != "idle" or self.player is None:
            return
        if not self.player.path.endswith("idle.webm"):
            self._switch_clip("idle.webm", loop=True)

    def _step_towards(self, target: tuple[float, float], speed_scale: float = 1.0) -> bool:
        """返回是否已到达。"""
        cx = self.x() + self.width() / 2
        cy = self.y() + self.height() / 2
        dx, dy = target[0] + self.width() / 2 - cx, target[1] + self.height() / 2 - cy
        dist = math.hypot(dx, dy)
        if dist < 8:
            self.speed = 0.0
            return True
        step = MOVE_SPEED * speed_scale * TICK_MS / 1000.0
        self.move(int(cx + dx / dist * step - self.width() / 2),
                  int(cy + dy / dist * step - self.height() / 2))
        if abs(dx) > 4:
            self.facing = 1 if dx > 0 else -1
        self._ensure_move_clip()
        return False

    def _ensure_move_clip(self) -> None:
        if self.action_id != "idle" or self.move_action:
            return
        name = "crab-walk.webm" if self.mode == "follow" else "run.webm"
        self._switch_clip(name, loop=True)

    def _screen_geo(self):
        scr = self.screen() or QApplication.primaryScreen()
        return scr.availableGeometry()

    # ---------- 活动范围 ----------
    def _box_size(self) -> tuple[float, float]:
        geo = self._screen_geo()
        # 右下角活动区：只给一点横向/纵向余量，让鱼待得住、又不至于跑到屏幕中间挡事。
        # 上限压到屏幕的 36%，保证她始终在右下角那一块活动。
        bw = min(max(self.width() * 1.1, 420.0), geo.width() * 0.36)
        bh = min(max(self.height() * 1.0, 320.0), geo.height() * 0.36)
        return bw, bh

    def _default_box_center(self) -> tuple[float, float]:
        geo = self._screen_geo()
        bw, bh = self._box_size()
        return (geo.right() - bw / 2 - 12, geo.bottom() - bh / 2 - 8)

    def _roam_box(self) -> QRectF:
        """桌宠「中心点」允许活动的矩形。"""
        geo = self._screen_geo()
        if self.mode == "wander":
            return QRectF(geo)
        bw, bh = self._box_size()
        cx, cy = self.box_center or self._default_box_center()
        cx = max(geo.left() + bw / 2, min(geo.right() - bw / 2, cx))
        cy = max(geo.top() + bh / 2, min(geo.bottom() - bh / 2, cy))
        return QRectF(cx - bw / 2, cy - bh / 2, bw, bh)

    def _reanchor_box(self) -> None:
        """拖拽结束后，把活动区重新锚定在落点附近，尊重用户的摆放。"""
        geo = self._screen_geo()
        bw, bh = self._box_size()
        cx = self.x() + self.width() / 2
        cy = self.y() + self.height() / 2
        cx = max(geo.left() + bw / 2, min(geo.right() - bw / 2, cx))
        cy = max(geo.top() + bh / 2, min(geo.bottom() - bh / 2, cy))
        self.box_center = (cx, cy)

    # ================= 自动表演 =================
    def _schedule_next_idle(self, first: bool = False) -> None:
        base = float(self.cfg.get("idle_interval", 45))
        jitter = base * random.uniform(0.6, 1.4)
        self._next_idle_at = time.monotonic() + (12.0 if first else jitter)

    def _auto_perform(self) -> None:
        pool = [a for a in ACTIONS.values() if a.kind == "act" and a.cat != "click"]
        # 节日加成：当天节日相关动作权重翻倍
        if self._festival:
            fav = set(festival.festival_pool(self._festival))
            weights = [a.weight * (3.0 if a.id in fav else 1.0) for a in pool]
        else:
            weights = [a.weight for a in pool]
        if not pool:
            return
        action = random.choices(pool, weights=weights, k=1)[0]
        self.play_action(action.id)
        self._schedule_next_idle()

    # ================= 互动入口 =================
    def _on_panel_pick(self, kind: str, key: str) -> None:
        if kind == "action":
            self.play_action(key)
            return
        self.run_feature(key)

    def run_feature(self, key: str) -> None:
        feature = FEATURE_MAP.get(key)
        if feature is None:
            return
        if feature.kind == "action":
            self.play_action(feature.target)
        elif feature.kind == "pool":
            self._play_from_cat(feature.target)
        elif feature.kind == "special":
            self._special(feature.target)

    def _play_from_cat(self, cat: str) -> None:
        pool = [a for a in by_cat(cat) if a.kind in ("act", "idle")]
        if not pool:
            return
        action = random.choices(pool, weights=[max(0.1, a.weight) for a in pool], k=1)[0]
        self.play_action(action.id)

    def _special(self, kind: str) -> None:
        if kind == "festival":
            self._do_festival()
        elif kind == "balance":
            self._do_balance()
        elif kind == "mooncake_token":
            self._do_mooncake_token()

    def _do_festival(self) -> None:
        name = self._festival or festival.current()
        fav = festival.festival_pool(name)
        if fav:
            action = ACTIONS.get(random.choice(fav))
            if action:
                self.play_action(action.id, speak=False)
                self.say(lines.for_festival(name) if name else "节日快乐！")
                return
        # 今天不是节日：自己造一个
        self._play_from_cat("festival")
        nxt = festival.upcoming(60)
        if nxt:
            days, fname = nxt[0]
            self.say(f"今天不过节，{days} 天后是{fname}，先热热身")
        else:
            self.say("今天不是节日，那我自己过")

    def _do_balance(self) -> None:
        cur = llm_from_cfg(self.cfg)
        self._play_from_cat("balance")
        if not cur.get("api_key"):
            self.say("没设 Key，只能看看我的空钱包了")
            return
        if not llm.is_deepseek(cur["base_url"]):
            self.say("这个接口查不了余额，只有 DeepSeek 有")
            return
        self.say("让我查查余额…")

        def ok(text: str, available: bool) -> None:
            self._say_queue.append((f"余额 {text}", False))

        def err(msg: str) -> None:
            self._say_queue.append((msg, False))

        query_balance(cur, ok, err)

    def _do_mooncake_token(self) -> None:
        tokens = random.randint(800, 64000)
        self.play_action("mooncake", speak=False)
        self.say(f"这月饼值 {tokens:,} token，一口闷了")
        cx = self.x() + self.width() / 2
        cy = self.y() + BUBBLE_H + self.ch * 0.45
        for i in range(5):
            QTimer.singleShot(i * 130, lambda c=cx, y=cy: self.effects.burst(
                c + random.uniform(-40, 40), y + random.uniform(-30, 30), 8, "coin"))

    # ================= 点击 / 拖拽 =================
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_pos = e.globalPosition().toPoint()
            self._dragging = False
            self._suppress_click = False
            self.panel.hide()
            self.chatbox.hide()

    def mouseMoveEvent(self, e) -> None:
        if not (e.buttons() & Qt.MouseButton.LeftButton) or self._press_pos is None:
            return
        delta = e.globalPosition().toPoint() - self._press_pos
        if not self._dragging and delta.manhattanLength() > 8:
            self._dragging = True
            self.drag.offset = e.globalPosition().toPoint() - QPoint(self.x(), self.y())
            self.action_id = "drag"
            self.action_until = 0.0
            self._switch_clip("drag.webm", loop=True)
            self.target = None
            self.say(lines.pick(lines.DRAG_LINES, self.last_line))
        if self._dragging and self.drag.offset is not None:
            self.move(e.globalPosition().toPoint() - self.drag.offset)
            if abs(delta.x()) > 12:
                self.facing = 1 if delta.x() > 0 else -1
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._dragging:
            self._dragging = False
            self.drag.offset = None
            self._press_pos = None
            self._suppress_click = True
            self.snap_into_screen()   # 拖到屏幕外时拉回来，别让鱼失踪
            self._reanchor_box()
            self.play_action("squash", speak=False, duration=0.8)
            self.rest_until = time.monotonic() + random.uniform(3, 6)
            if random.random() < 0.5:
                QTimer.singleShot(500, lambda: self.say(
                    lines.pick(lines.DROP_LINES, self.last_line)))
        else:
            self._press_pos = None
            self._click_timer.start(240)

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            self._suppress_click = True   # 双击之后别再补一次单击动画
            self.panel.popup_near(self)

    def _on_single_click(self) -> None:
        """单击：随机一个点击反应动画 + 气泡，不弹面板（避免挡屏幕）。"""
        if self._suppress_click:
            self._suppress_click = False
            return
        reaction = random.choice(
            ["clickWave", "clickJoy", "clickGiggle", "clickShy", "clickAngry"])
        self.play_action(reaction, speak=False, duration=2.8)
        self.say(lines.pick(lines.CLICK_LINES, self.last_line))

    # ================= 气泡 =================
    def say(self, text: str, inner: bool = False, duration: float = 3.4) -> None:
        if not text:
            return
        if text == self.last_line and not text.startswith(("DeepSeek", "天气")):
            return
        self.last_line = text
        self.bubble_inner = inner
        self.bubble_text = f"（{text}）" if inner else text
        self.bubble_until = time.monotonic() + duration
        self.update()

    def _greet(self) -> None:
        if self.cfg.get("first_run", True):
            self.cfg["first_run"] = False
            config.save_config(self.cfg)
            self.say("我是大肥鱼～单击有惊喜，双击有更多")
            return
        if self._festival:
            self.say(lines.for_festival(self._festival))
        else:
            self.say(lines.pick(lines.GREETINGS, self.last_line))

    # ================= 系统告警 =================
    def _check_alerts(self) -> None:
        if not self.cfg.get("health_alerts", True):
            return
        snap = self._monitor.snapshot
        if snap.ts == 0:
            return
        for alert in check_alerts(snap, self._alert_state):
            if alert.kind == "cpu":
                self.say(lines.pick(lines.ALERT_CPU).format(v=int(alert.value)))
            elif alert.kind == "ram":
                self.say(lines.pick(lines.ALERT_RAM).format(v=int(alert.value)))
            elif alert.kind == "gputemp":
                self.say(lines.pick(lines.ALERT_GPU_TEMP).format(v=int(alert.value)))
            elif alert.kind == "cputemp":
                self.say(f"CPU 都 {int(alert.value)} 度了，我快熟了")
            elif alert.kind == "battery":
                self.say(lines.pick(lines.ALERT_BATTERY).format(v=int(alert.value)))
            elif alert.kind == "disk":
                self.say(lines.pick(lines.ALERT_DISK).format(v=100 - int(alert.value)))
            self.play_action("startled", speak=False, duration=3.0)

    # ================= 绘制 =================
    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        now = time.monotonic()
        if self.bubble_text and now < self.bubble_until:
            self._paint_bubble(p, now)

        if self.frame is None:
            return

        target = QRectF(0, BUBBLE_H, self.cw, self.ch)
        if self.fade > 0 and self.prev_frame is not None:
            self._draw_frame(p, self.prev_frame, target, self.fade)
        self._draw_frame(p, self.frame, target, 1.0)

    def _draw_frame(self, p: QPainter, img, rect: QRectF, opacity: float) -> None:
        p.save()
        p.setOpacity(opacity)
        if self.facing < 0:
            p.translate(rect.center())
            p.scale(-1, 1)
            p.translate(-rect.center())
        p.drawImage(rect, img)
        p.restore()

    def _paint_bubble(self, p: QPainter, now: float) -> None:
        f = QFont(self.bubble_font)
        if self.bubble_inner:
            f.setItalic(True)
            bg, fg = QColor(240, 241, 246, 242), QColor(122, 126, 140)
        else:
            bg, fg = QColor(255, 255, 255, 246), QColor(46, 50, 66)
        fm = QFontMetrics(f)
        max_w = max(120, self.cw - 20)
        text = self.bubble_text
        rows, cur = [], ""
        for ch in text:
            if fm.horizontalAdvance(cur + ch) > max_w - 22:
                rows.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            rows.append(cur)

        bw = max(fm.horizontalAdvance(r) for r in rows) + 22
        bh = len(rows) * fm.height() + 14
        head_y = BUBBLE_H + 68 * self.scale
        bx = (self.cw - bw) / 2
        by = max(2.0, head_y - bh - 8)

        # 淡出
        remain = self.bubble_until - now
        alpha = 1.0 if remain > 0.5 else max(0.0, remain / 0.5)
        p.save()
        p.setOpacity(alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(bx, by, bw, bh), 11, 11)
        # 小尾巴
        tail_x = self.cw / 2
        p.drawPolygon(QPolygonF([
            QPointF(tail_x - 6, by + bh - 1),
            QPointF(tail_x + 6, by + bh - 1),
            QPointF(tail_x, by + bh + 7),
        ]))
        p.setPen(fg)
        p.setFont(f)
        for i, row in enumerate(rows):
            p.drawText(QRectF(bx, by + 7 + i * fm.height(), bw, fm.height()),
                       Qt.AlignmentFlag.AlignCenter, row)
        p.restore()

    # ================= 命中测试 =================
    def nativeEvent(self, eventType, message):
        """WM_NCHITTEST：透明像素让鼠标穿透到下面的窗口。"""
        try:
            if bytes(eventType) == b"windows_generic_MSG":
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == 0x0084:  # WM_NCHITTEST
                    # lParam 是物理像素坐标，Qt 用的是逻辑像素，要按 DPI 换算
                    dpr = self.devicePixelRatioF() or 1.0
                    sx = ctypes.c_short(msg.lParam & 0xFFFF).value / dpr
                    sy = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value / dpr
                    if not self._alpha_hit(sx, sy):
                        return True, -1  # HTTRANSPARENT
        except Exception:
            pass
        return super().nativeEvent(eventType, message)

    def _alpha_hit(self, sx: float, sy: float) -> bool:
        """sx/sy 为逻辑屏幕坐标；只有落在角色不透明像素上才算命中。"""
        if self.frame is None:
            return True
        lx = sx - self.x()
        ly = sy - self.y()
        if ly < BUBBLE_H:
            return False          # 气泡只是装饰，不挡鼠标
        if not (0 <= lx < self.cw and ly < BUBBLE_H + self.ch):
            return False
        cx = int(lx / self.scale)
        cy = int((ly - BUBBLE_H) / self.scale)
        if self.facing < 0:
            cx = CANVAS_W - 1 - cx
        if not (0 <= cx < self.frame.width() and 0 <= cy < self.frame.height()):
            return False
        return self.frame.pixelColor(cx, cy).alpha() > 20

    # ================= 菜单 =================
    def contextMenuEvent(self, e) -> None:
        self._build_menu().exec(e.globalPos())

    def _build_menu(self) -> QMenu:
        m = QMenu(self)
        m.setStyleSheet("""
            QMenu { background:#ffffff; border:1px solid #e6e9f0; border-radius:10px;
                    padding:6px; font-family:"Microsoft YaHei UI"; font-size:12px; color:#1f2430; }
            QMenu::item { padding:6px 26px 6px 14px; border-radius:6px; }
            QMenu::item:selected { background:#eef2ff; color:#4d6bfe; }
            QMenu::separator { height:1px; background:#eef0f6; margin:5px 8px; }
            QMenu::right-arrow { width:10px; }
        """)

        act_menu = m.addMenu("🎬 动作点播")
        for cat, label in (
                ("idle", "待机"), ("daily", "日常"), ("eat", "吃货"),
                ("festival", "节日"), ("perform", "表演"), ("music", "歌舞"),
                ("fx", "特效"), ("muse", "碎碎念"), ("balance", "余额"),
                ("move", "移动"), ("click", "点击反应")):
            sub = act_menu.addMenu(f"{label}")
            for a in by_cat(cat):
                sub.addAction(f"{icon_of(a)} {a.name}", lambda _=False, i=a.id: self.play_action(i))
        act_menu.addSeparator()
        act_menu.addAction("🎲 随机来一个", self._auto_perform)

        m.addAction("🎛 互动面板", lambda: self.panel.popup_near(self))
        # 体检面板开着时，菜单项直接变成「关闭」，一次点击就能收掉
        m.addAction("✖ 关闭体检报告" if self.health.isVisible() else "🩺 体检报告",
                    self.toggle_health)
        m.addAction("💬 跟她说句话", self._open_chat)
        m.addAction("💰 看看余额", self._do_balance)
        m.addAction("🌤 查看天气", self._do_weather)
        m.addSeparator()

        mode_menu = m.addMenu("🚶 模式")
        for label, key in (("右下角活动（不挡办公）", "corner"),
                           ("全屏散步", "wander"),
                           ("跟随鼠标", "follow"),
                           ("原地待着", "still")):
            a = mode_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(self.mode == key)
            a.triggered.connect(lambda _=False, k=key: self.set_mode(k))

        size_menu = m.addMenu("📏 大小")
        for label, val in SIZE_LEVELS:
            a = size_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(abs(self.scale - val) < 0.01)
            a.triggered.connect(lambda _=False, v=val: self.set_scale(v))

        set_menu = m.addMenu("⚙️ 设置")
        ka = set_menu.addAction("🤖 模型设置…")
        ka.triggered.connect(self._set_llm)
        ca = set_menu.addAction("设置城市…")
        ca.triggered.connect(self._set_city)
        set_menu.addSeparator()
        ta = set_menu.addAction("窗口置顶")
        ta.setCheckable(True)
        ta.setChecked(bool(self.cfg.get("topmost", True)))
        ta.triggered.connect(self.set_topmost)
        pa = set_menu.addAction("鼠标穿透（托盘可解除）")
        pa.setCheckable(True)
        pa.setChecked(bool(self.cfg.get("passthrough", False)))
        pa.triggered.connect(self.set_passthrough)
        aa = set_menu.addAction("开机自启")
        aa.setCheckable(True)
        aa.setChecked(bool(self.cfg.get("autostart", False)))
        aa.triggered.connect(self.set_autostart)
        ia = set_menu.addAction("空闲自动表演")
        ia.setCheckable(True)
        ia.setChecked(bool(self.cfg.get("idle_actions", True)))
        ia.triggered.connect(self.set_idle_actions)
        ha = set_menu.addAction("健康提醒")
        ha.setCheckable(True)
        ha.setChecked(bool(self.cfg.get("health_alerts", True)))
        ha.triggered.connect(self.set_health_alerts)

        m.addSeparator()
        m.addAction("🫥 显示 / 隐藏", self.toggle_visible)
        m.addAction("📌 回到屏幕内", self.snap_into_screen)
        m.addAction("ℹ️ 关于", self._about)
        m.addAction("❌ 退出", self.quit_app)
        return m

    # ================= 功能 =================
    def _open_chat(self) -> None:
        ok, why = ready(llm_from_cfg(self.cfg))
        if not ok:
            self.say(why)
            QTimer.singleShot(600, lambda: self.llm_ui.popup_near(self))
            return
        self.chatbox.popup_near(self)

    def _on_chat_text(self, text: str) -> None:
        self.say("让我想想…", duration=8)

        def reply(msg: str) -> None:
            self._say_queue.append((msg, False))

        def fail(msg: str) -> None:
            self._say_queue.append((msg, False))

        self._chat.ask(llm_from_cfg(self.cfg), text, reply, fail)

    def _do_weather(self) -> None:
        city = self.cfg.get("city", "汕头")
        self.say(f"查一下{city}的天气…")

        def ok(text: str) -> None:
            self._say_queue.append((text, False))

        def err(msg: str) -> None:
            self._say_queue.append((msg, False))

        query_weather(city, ok, err)

    def _set_llm(self) -> None:
        self.llm_ui.cfg = self.cfg
        self.llm_ui.popup_near(self)

    def _on_llm_saved(self, cfg: dict) -> None:
        self.cfg = cfg
        cur = llm_from_cfg(cfg)
        self.say(f"换好模型了：{cur['model']}", duration=3.6)

    def _set_city(self) -> None:
        city, ok = QInputDialog.getText(
            self, "设置城市", "输入城市名（如 汕头 / 北京）:",
            QLineEdit.EchoMode.Normal, self.cfg.get("city", "汕头"))
        if ok and city.strip():
            self.cfg["city"] = city.strip()
            config.save_config(self.cfg)
            self.say(f"城市设为{city.strip()}了")

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.cfg["mode"] = mode
        config.save_config(self.cfg)
        self.target = None
        if mode in ("corner", "wander"):
            self.box_center = None
        label = {"corner": "好，我就在右下角待着",
                 "wander": "那我到处逛逛",
                 "follow": "跟着你走",
                 "still": "原地待着"}[mode]
        self.say(label)

    def set_topmost(self, on: bool) -> None:
        self.cfg["topmost"] = bool(on)
        config.save_config(self.cfg)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        self.show()

    def set_passthrough(self, on: bool, quiet: bool = False) -> None:
        self.cfg["passthrough"] = bool(on)
        config.save_config(self.cfg)
        try:
            hwnd = int(self.winId())
            GWL_EXSTYLE = -20
            WS_EX_LAYERED, WS_EX_TRANSPARENT = 0x80000, 0x20
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            style |= WS_EX_LAYERED
            if on:
                style |= WS_EX_TRANSPARENT
            else:
                style &= ~WS_EX_TRANSPARENT
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
        except Exception as exc:
            print("[passthrough]", exc)
        if not quiet:
            self.say("我隐身了，右键托盘图标解除～" if on else "我又能碰到了")

    def set_autostart(self, on: bool) -> None:
        self.cfg["autostart"] = bool(on)
        config.save_config(self.cfg)
        startup = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                               "Start Menu", "Programs", "Startup", "大肥鱼桌宠.lnk")
        try:
            if on:
                if getattr(sys, "frozen", False):
                    target, args, workdir = sys.executable, "", config.app_dir()
                else:
                    pyw = os.path.join(config.app_dir(), ".venv", "Scripts", "pythonw.exe")
                    target = pyw if os.path.exists(pyw) else sys.executable
                    args = f'"{os.path.join(config.app_dir(), "main.py")}"'
                    workdir = config.app_dir()
                ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{}');"
                      "$s.TargetPath='{}';$s.Arguments='{}';$s.WorkingDirectory='{}';$s.Save()"
                      .format(startup, target, args, workdir))
                subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               check=True)
                self.say("已设置开机自启")
            else:
                if os.path.exists(startup):
                    os.remove(startup)
                self.say("已取消开机自启")
        except Exception as exc:
            QMessageBox.warning(self, "开机自启", f"设置失败：{exc}")

    def set_idle_actions(self, on: bool) -> None:
        self.cfg["idle_actions"] = bool(on)
        config.save_config(self.cfg)
        self.say("会自己表演的" if on else "那我安静待着")

    def set_health_alerts(self, on: bool) -> None:
        self.cfg["health_alerts"] = bool(on)
        config.save_config(self.cfg)
        self.say("电脑不舒服我会提醒你" if on else "不提醒了")

    def snap_into_screen(self) -> None:
        geo = self._screen_geo()
        x = max(geo.left(), min(geo.right() - self.width(), self.x()))
        y = max(geo.top(), min(geo.bottom() - self.height(), self.y()))
        self.move(int(x), int(y))

    def toggle_health(self) -> None:
        """体检面板开关：开着就关，关着就弹。"""
        if self.health.isVisible():
            self.health.hide()
        else:
            self.health.popup_near(self)

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    def _on_tray(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Context:
            self.tray.setContextMenu(self._build_menu())
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visible()

    def _about(self) -> None:
        fest = f"\n今天是「{self._festival}」" if self._festival else ""
        QMessageBox.information(
            self, "关于大肥鱼桌宠",
            "大肥鱼桌宠 v1.0\n\n"
            "一只陪你摸鱼的 DeepSeek 鲸鱼娘。\n"
            "100 段透明动画 · 28 个招牌互动 · 电脑体检 · AI 聊天\n\n"
            f"动作素材来自开源项目 dsh-pet（作者 PC2005-cloud），非商用。{fest}")

    def quit_app(self) -> None:
        self.cfg["x"], self.cfg["y"] = self.x(), self.y()
        config.save_config(self.cfg)
        self.clips.drop_all()
        self._monitor.stop()
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, e) -> None:
        self.quit_app()


def QCursorPos() -> QPoint:
    from PySide6.QtGui import QCursor
    return QCursor.pos()
