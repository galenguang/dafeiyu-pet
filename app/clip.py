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
from collections import OrderedDict

import av
from PySide6.QtGui import QImage

from . import config

# 每段动画的解码线程数。实测（640x360 vp9 + alpha）线程 1/2/4/8 的吞吐都是
# 200fps 左右——瓶颈在 YUV→RGBA 转换，多线程帮不上忙，只会每段多开十几个
# 线程。播放只需要 24fps，2 个线程留了八倍余量，够弱机器用。
DECODE_THREADS = 2


class DecodeError(RuntimeError):
    pass


def _rgba_to_qimage(rgba) -> QImage:
    """RGBA 帧 → QImage。

    不用 frame.to_ndarray()：那会依赖 numpy，而且要多一次拷贝。
    直接读 plane 的字节，再 copy() 一次保证 QImage 拥有自己的数据。
    """
    plane = rgba.planes[0]
    return QImage(bytes(plane), rgba.width, rgba.height, plane.line_size,
                  QImage.Format.Format_RGBA8888).copy()


def _make_converter(stream):
    """建一个 yuva420p → rgba 的滤镜图，供解码线程反复使用。

    为什么不用 ``frame.reformat()``：那是每帧新建一个滤镜图 + 一个新的
    swscale 线程池（实测每帧 3.2ms、每段动画多出十几个线程）。这里建成
    一次复用，并把滤镜图的线程数钉成 1 —— 实测吞吐从 166fps 提到 1600fps
    左右，而且一个线程都不多开。
    """
    graph = av.filter.Graph()
    graph.threads = 1                 # 必须在 add() 之前设，否则 PyAV 会拒绝
    src = graph.add_buffer(template=stream)
    conv = graph.add("format", "pix_fmts=rgba")
    sink = graph.add("buffersink")
    graph.link_nodes(src, conv, sink)
    graph.configure()
    # graph 必须跟 src/sink 一起活着：PyAV 的滤镜节点只借用了图的指针，
    # graph 被回收后再 push 就是野指针（实测直接 access violation）。
    return graph, src, sink


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

    def stop(self, wait: bool = True) -> None:
        """停止解码线程。

        ``wait=False`` 用于缓存淘汰：解码线程卡在 ``queue.put`` 上最多 0.1 秒
        就会自己退出，没必要在 UI 线程里 join 等它——那会让切动画卡一下。
        """
        self._stop.set()
        # 清空队列，避免后台线程卡在 put 上
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        if self._thread is not None:
            if wait:
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
                try:
                    ctx.thread_count = DECODE_THREADS
                except Exception:
                    pass
                # 转换滤镜图建一次用到底；万一这版 PyAV 不支持就退回逐帧 reformat
                conv_graph = conv_src = conv_sink = None
                try:
                    conv_graph, conv_src, conv_sink = _make_converter(stream)
                except Exception:
                    conv_graph = conv_src = conv_sink = None
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
                            if conv_src is not None:
                                conv_src.push(frame)
                                rgba = conv_sink.pull()
                            else:
                                rgba = frame.reformat(format="rgba")
                            img = _rgba_to_qimage(rgba)
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
    """按文件名管理 ClipPlayer，避免重复开关。

    每段动画常驻一个解码线程 + 6 帧缓冲（640x360 RGBA 约 5.5MB）。100 段
    动画全留着就是上百个线程、几百 MB 内存，所以只保留最近用过的
    ``MAX_PLAYERS`` 段，最久没用的先淘汰；正在播的那段永远不淘汰。
    """

    MAX_PLAYERS = 8

    def __init__(self, directory: str | None = None):
        self.dir = directory or config.clip_dir()
        self._players: OrderedDict[str, ClipPlayer] = OrderedDict()
        self._missing: set[str] = set()
        self._active: str | None = None

    def path_of(self, filename: str) -> str:
        return os.path.join(self.dir, filename)

    def exists(self, filename: str) -> bool:
        return os.path.exists(self.path_of(filename))

    def get(self, filename: str, loop: bool = True) -> ClipPlayer | None:
        if filename in self._missing:
            return None
        player = self._players.get(filename)
        if player is not None and player.loop == loop and player.error is None:
            self._players.move_to_end(filename)
            self._active = filename
            return player
        if player is not None:
            self.drop(filename)
        if not self.exists(filename):
            self._missing.add(filename)
            return None
        player = ClipPlayer(self.path_of(filename), loop=loop)
        player.start()
        self._players[filename] = player
        self._active = filename
        self._evict()
        return player

    def _evict(self) -> None:
        while len(self._players) > self.MAX_PLAYERS:
            for name in list(self._players):
                if name == self._active:
                    continue
                player = self._players.pop(name)
                player.stop(wait=False)
                break
            else:      # 只剩正在播的那一段了，没法再淘汰
                break

    def drop(self, filename: str) -> None:
        player = self._players.pop(filename, None)
        if player is not None:
            player.stop()
        if self._active == filename:
            self._active = None

    def drop_all(self) -> None:
        for name in list(self._players):
            self.drop(name)
        self._active = None

    def cached(self) -> int:
        return len(self._players)
