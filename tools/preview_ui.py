# -*- coding: utf-8 -*-
"""把各个界面渲染成 PNG，方便检查样式（开发用）。"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "preview")


def _save(widget, name: str, bg=(238, 242, 250)) -> None:
    """把窗口渲染到带底色的 PNG，方便看清白色气泡和阴影。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    pm = widget.grab()
    img = QImage(pm.size(), QImage.Format.Format_ARGB32)
    img.fill(QColor(*bg))
    p = QPainter(img)
    p.drawPixmap(0, 0, pm)
    p.end()
    img.save(os.path.join(OUT, name))


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication(sys.argv)

    from app.pet import PetWindow
    pet = PetWindow()
    pet.show()

    # 面板
    for cat, name in (("feature", "panel_feature"), ("eat", "panel_eat"),
                      ("music", "panel_music")):
        pet.panel.set_category(cat)
        app.processEvents()
        pet.panel.grab().save(os.path.join(OUT, f"{name}.png"))
    pet.panel.hide()

    # 体检面板（等采样两次）
    pet.health.show()
    for _ in range(40):
        app.processEvents()
        time.sleep(0.1)
    pet.health.refresh()
    app.processEvents()
    pet.health.grab().save(os.path.join(OUT, "health.png"))
    pet.health.hide()

    # 气泡
    pet.say("摸鱼使我快乐，老板来了记得叫我")
    for _ in range(10):
        app.processEvents()
        time.sleep(0.05)
    _save(pet, "bubble.png")

    # 一个动作
    pet.play_action("maidDance")
    for _ in range(40):
        app.processEvents()
        time.sleep(0.03)
    _save(pet, "action.png")

    print("预览已输出到", OUT)
    pet.tray.hide()
    QTimer.singleShot(100, app.quit)
    app.exec()


if __name__ == "__main__":
    main()
