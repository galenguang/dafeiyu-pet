# -*- coding: utf-8 -*-
"""透明视频帧流播放器。

素材是 VP9 + 独立 alpha 平面的 webm。ffmpeg 默认的 vp9 解码路径会丢掉
alpha，必须显式用 libvpx-vp9 解码器上下文，这里通过 PyAV 手动 demux。

解码速度实测约 1000 fps（640x360），远高于 24fps 播放需求，所以用
后台线程 + 有界队列预取，主线程只做取帧，不会卡界面。
"""
from __future__ import annotations

import os
import queue
import threading

import av
from PySide6.QtGui import QImage

from . import config


class DecodeError(RuntimeError):
    pass


def _frame_to_qimage(frame) -> QImage:
    """把 yuva420p 帧转成 RGBA 的 QImage。

    不用 frame.to_ndarray()：那会依赖 numpy，而且要多一次拷贝。
    这里 reformat 成 rgba 后直接读 plane 的字节，实测快 80% 左右。
    """
    rgba = frame.reformat(format="rgba")
    plane = rgba.planes[0]
    # QImage 需要自己持有数据，copy() 一次保证生命周期安全
    return QImage(bytes(plane), rgba.width, rgba.height, plane.line_size,
                  QImage.Format.Format_RGBA8888).copy()


class ClipPlayer:
    """单段动画的播放器，后台线程持续预取帧。"""

    def __init__(self, path: str, loop: bool = True, buffer: int = 6):
        self.path = path
        self.loop = loop
        self._q: queue.Queue = queue.Queue(maxsize=buffer)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None
        self.frames_decoded = 0

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"clip:{os.path.basename(self.path)}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # 清空队列，避免后台线程卡在 put 上
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    # ---------- 取帧 ----------
    def poll(self) -> QImage | None:
        """非阻塞取一帧；没准备好就返回 None。"""
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return None

    def buffered(self) -> int:
        return self._q.qsize()

    # ---------- 解码线程 ----------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                container = av.open(self.path)
            except Exception as exc:
                self.error = f"打开失败: {exc}"
                return
            try:
                stream = container.streams.video[0]
                stream.thread_type = "AUTO"
                ctx = av.CodecContext.create("libvpx-vp9", "r")
                eof = False
                for packet in container.demux(stream):
                    if self._stop.is_set():
                        return
                    if packet.size == 0:
                        continue
                    for frame in ctx.decode(packet):
                        if self._stop.is_set():
                            return
                        try:
                            img = _frame_to_qimage(frame)
                        except Exception as exc:
                            self.error = f"解码失败: {exc}"
                            return
                        self.frames_decoded += 1
                        # 阻塞放入；stop() 会清空队列让这里退出
                        while not self._stop.is_set():
                            try:
                                self._q.put(img, timeout=0.1)
                                break
                            except queue.Full:
                                continue
                        if self._stop.is_set():
                            return
                eof = True
            except Exception as exc:
                self.error = f"解码异常: {exc}"
                return
            finally:
                try:
                    container.close()
                except Exception:
                    pass
            if not self.loop or eof is False:
                break


class ClipCache:
    """按文件名管理 ClipPlayer，避免重复开关。"""

    def __init__(self, directory: str | None = None):
        self.dir = directory or config.clip_dir()
        self._players: dict[str, ClipPlayer] = {}
        self._missing: set[str] = set()

    def path_of(self, filename: str) -> str:
        return os.path.join(self.dir, filename)

    def exists(self, filename: str) -> bool:
        return os.path.exists(self.path_of(filename))

    def get(self, filename: str, loop: bool = True) -> ClipPlayer | None:
        if filename in self._missing:
            return None
        player = self._players.get(filename)
        if player is not None and player.loop == loop and player.error is None:
            return player
        if player is not None:
            self.drop(filename)
        if not self.exists(filename):
            self._missing.add(filename)
            return None
        player = ClipPlayer(self.path_of(filename), loop=loop)
        player.start()
        self._players[filename] = player
        return player

    def drop(self, filename: str) -> None:
        player = self._players.pop(filename, None)
        if player is not None:
            player.stop()

    def drop_all(self) -> None:
        for name in list(self._players):
            self.drop(name)
