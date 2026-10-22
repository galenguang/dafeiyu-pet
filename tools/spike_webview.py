# -*- coding: utf-8 -*-
"""M0 真机验证：VP9-alpha 能不能在 Android WebView 里正确透明。

这个脚本做三件事，全部通过 adb 在真机上完成，不需要装 App：
1. 起一个本地 HTTP 服务，把 build/clips/ 下的 .webm 和测试页发出去
2. adb reverse 把手机上的端口映射回电脑（手机自己访问 127.0.0.1:PORT）
3. 用手机 WebView 打开测试页，页面自己跑检测并把结果 POST 回来

为什么不让手机直接访问电脑 IP：adb reverse 走 USB，不依赖 WiFi 同网段，
也不受路由器 AP 隔离影响。而且这正是 Flutter 版要用的方案（Dart HttpServer
绑 loopback），顺手一起验了。

用法：
    python tools/spike_webview.py             # 用默认设备
    python tools/spike_webview.py --port 8765
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIPS = os.path.join(ROOT, "build", "clips")
ADB = r"D:/Android/Sdk/platform-tools/adb.exe"

# 挑三段：一段最简单的、一段元素最多的、一段几乎铺满画布的
SAMPLES = ["idle", "dance", "fireworks"]

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VP9-alpha 真机验证</title>
<style>
  html,body{margin:0;height:100%;font:14px/1.5 system-ui,sans-serif;background:#fff}
  #stage{position:relative;width:100vw;height:60vh;overflow:hidden;background:#00a0ff}
  #stage video{position:absolute;left:50%;top:50%;width:100%;height:100%;
    transform:translate(-50%,-50%);object-fit:contain;opacity:0;
    transition:opacity .08s linear;pointer-events:none;background:transparent}
  #stage video.on{opacity:1}
  #hud{position:absolute;left:8px;top:8px;color:#fff;text-shadow:0 1px 3px #000;
    font-size:12px;line-height:1.6;z-index:9}
  #panel{padding:8px 10px}
  #panel button{margin:2px 4px 2px 0;padding:6px 12px;border:1px solid #ccc;
    border-radius:6px;background:#f7f7f7;font-size:13px}
  #panel button:active{background:#e0e0e0}
  #log{font-family:ui-monospace,monospace;font-size:11px;white-space:pre-wrap;
    background:#111;color:#0f0;padding:8px;margin:8px 0;border-radius:6px;
    max-height:38vh;overflow:auto}
  .pass{color:#0f0}.fail{color:#f66}.warn{color:#fc0}
</style></head>
<body>
<div id="stage">
  <div id="hud"></div>
</div>
<div id="panel">
  <div>
    <button data-bg="#00a0ff">蓝</button>
    <button data-bg="#000000">黑</button>
    <button data-bg="#ffffff">白</button>
    <button data-bg="#ff0000">红</button>
  </div>
  <div>
    <button id="play">重播</button>
    <button id="report">上报结果</button>
  </div>
  <div id="log"></div>
</div>
<script>
const SAMPLES = __SAMPLES__;
const stage = document.getElementById('stage');
const hud = document.getElementById('hud');
const logEl = document.getElementById('log');
const out = [];
function log(s, cls) {
  out.push(s);
  logEl.innerHTML += '<span class="' + (cls || '') + '">' + s + '</span>\n';
  logEl.scrollTop = logEl.scrollHeight;
}

// 预建 video，切动作只改 class 不换 src —— 换 src 会 reload + 黑帧
const vids = {};
for (const name of SAMPLES) {
  const v = document.createElement('video');
  v.src = '/clip/' + name;
  v.muted = true;
  v.loop = true;
  v.playsInline = true;
  v.setAttribute('playsinline', '');
  v.preload = 'auto';
  stage.appendChild(v);
  vids[name] = v;
}
let cur = null;
function show(name) {
  if (cur === name) return;
  for (const k in vids) vids[k].classList.toggle('on', k === name);
  cur = name;
}

// ---- 检测 1：能不能解码（readyState / videoWidth）
function waitMeta(v) {
  return new Promise((res) => {
    if (v.readyState >= 1 && v.videoWidth) return res(true);
    const t = setTimeout(() => res(false), 8000);
    v.addEventListener('loadedmetadata', () => { clearTimeout(t); res(true); });
    v.addEventListener('error', () => { clearTimeout(t); res(false); });
  });
}

// ---- 检测 2：透明是真的吗（drawImage 到 canvas，取像素 alpha）
// 注意：video 的 alpha 只在 Chromium 的 VP9 解码路径里存在，
// 如果设备拿不到 alpha，canvas 采样会得到 alpha=255 的整块不透明像素。
function sampleAlpha(name) {
  const v = vids[name];
  const c = document.createElement('canvas');
  c.width = v.videoWidth; c.height = v.videoHeight;
  const g = c.getContext('2d', { willReadFrequently: true });
  g.clearRect(0, 0, c.width, c.height);
  g.drawImage(v, 0, 0);
  const W = c.width, H = c.height;
  const d = g.getImageData(0, 0, W, H).data;
  let a0 = 0, aLow = 0, aHigh = 0, n = W * H;
  for (let i = 3; i < d.length; i += 4) {
    const a = d[i];
    if (a === 0) a0++;
    else if (a < 20) aLow++;
    else aHigh++;
  }
  return {
    w: W, h: H,
    a0: +(a0 / n * 100).toFixed(2),
    aLow: +(aLow / n * 100).toFixed(2),
    aHigh: +(aHigh / n * 100).toFixed(2),
  };
}

// ---- 检测 3：切换延迟 + 掉帧
let frames = 0, lastT = performance.now(), fps = 0, janky = 0;
let prevT = lastT;
function raf(t) {
  const dt = t - prevT; prevT = t;
  frames++;
  if (dt > 34) janky++;              // 超过 2 帧（16.7ms x2）算一次卡顿
  if (t - lastT >= 1000) {
    fps = Math.round(frames * 1000 / (t - lastT));
    frames = 0; lastT = t;
    hud.textContent = 'FPS ' + fps + '  当前 ' + cur + '  卡顿 ' + janky;
  }
  requestAnimationFrame(raf);
}

async function run() {
  log('=== 开始检测 ===');
  log('UA: ' + navigator.userAgent.slice(0, 120));

  // 1. 元数据
  for (const name of SAMPLES) {
    const v = vids[name];
    const ok = await waitMeta(v);
    if (!ok) { log('[FAIL] ' + name + ' 无法加载/解码', 'fail'); continue; }
    log('[OK] ' + name + ' 解码 ' + v.videoWidth + 'x' + v.videoHeight +
        ' 时长 ' + v.duration.toFixed(2) + 's', 'pass');
  }

  // 2. 播放 + 透明采样
  for (const name of SAMPLES) {
    const v = vids[name];
    try { await v.play(); } catch (e) { log('[WARN] ' + name + ' play 失败: ' + e, 'warn'); }
  }
  // 让视频跑起来，取中间一帧采样
  await new Promise(r => setTimeout(r, 1200));
  show(SAMPLES[0]);
  await new Promise(r => setTimeout(r, 400));

  const alphaRes = [];
  for (const name of SAMPLES) {
    const v = vids[name];
    if (!v.videoWidth) continue;
    const s = sampleAlpha(name);
    alphaRes.push({ name, ...s });
    const verdict = s.a0 > 40 ? 'PASS' : (s.aLow > 40 ? 'FAIL(灰雾)' : 'FAIL(不透明)');
    log('[' + (s.a0 > 40 ? 'OK' : 'FAIL') + '] alpha ' + name +
        '  a0=' + s.a0 + '%  a<20=' + s.aLow + '%  实心=' + s.aHigh + '%  → ' + verdict,
        s.a0 > 40 ? 'pass' : 'fail');
  }

  // 3. 切换延迟（连切 30 次，量 requestAnimationFrame 抖动）
  const t0 = performance.now();
  for (let i = 0; i < 30; i++) {
    show(SAMPLES[i % SAMPLES.length]);
    await new Promise(r => requestAnimationFrame(r));
  }
  const switchMs = (performance.now() - t0) / 30;
  log('切换平均 ' + switchMs.toFixed(1) + 'ms/次（30 次）',
      switchMs < 200 ? 'pass' : 'warn');

  // 4. 跑 3 秒统计掉帧
  janky = 0;
  await new Promise(r => setTimeout(r, 3000));
  log('3 秒内 FPS≈' + fps + '，卡顿帧 ' + janky,
      janky < 9 ? 'pass' : 'warn');

  // 5. 内存
  const mem = performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : -1;
  log('JS 堆 ' + (mem > 0 ? mem + 'MB' : '不可读'));

  const res = {
    ua: navigator.userAgent,
    samples: SAMPLES,
    alpha: alphaRes,
    switchMs: +switchMs.toFixed(1),
    fps, janky, mem,
  };
  log('=== 检测结束 ===');
  await fetch('/report', { method: 'POST', body: JSON.stringify(res) });
  log('[已上报]', 'pass');
}

document.getElementById('play').onclick = run;
document.getElementById('report').onclick = async () => {
  await fetch('/report', { method: 'POST', body: JSON.stringify({ note: 'manual' }) });
  log('[手动上报]', 'pass');
};
document.querySelectorAll('[data-bg]').forEach(b => {
  b.onclick = () => { stage.style.background = b.dataset.bg; };
});

requestAnimationFrame(raf);
run();
</script>
</body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    report: dict | None = None
    lock = threading.Lock()

    def log_message(self, *_a):
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                    # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            page = PAGE.replace("__SAMPLES__", json.dumps(SAMPLES))
            self._send(200, page.encode("utf-8"),
                       "text/html; charset=utf-8")
            return
        if path.startswith("/clip/"):
            name = path[len("/clip/"):]
            fp = os.path.join(CLIPS, name + ".webm")
            if not os.path.isfile(fp):
                self._send(404, b"not found", "text/plain")
                return
            data = open(fp, "rb").read()
            # WebView 的 video 会发 Range 请求，不回 206 某些版本会拒绝播放
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                try:
                    s, _, e = rng[6:].partition("-")
                    start = int(s) if s else 0
                    end = int(e) if e else len(data) - 1
                    end = min(end, len(data) - 1)
                    chunk = data[start:end + 1]
                    self.send_response(206)
                    self.send_header("Content-Type", "video/webm")
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range",
                                     f"bytes {start}-{end}/{len(data)}")
                    self.send_header("Content-Length", str(len(chunk)))
                    self.end_headers()
                    self.wfile.write(chunk)
                    return
                except ValueError:
                    pass
            self.send_response(200)
            self.send_header("Content-Type", "video/webm")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self):                                   # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        if urlparse(self.path).path == "/report":
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"raw": raw}
            with Handler.lock:
                Handler.report = data
            print("\n>>> 收到手机上报:")
            print(json.dumps(data, ensure_ascii=False, indent=2))
        self._send(200, b"ok", "text/plain")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def adb(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run([ADB, *args], capture_output=True, text=True,
                          errors="replace", timeout=timeout)


def pick_device() -> str | None:
    r = adb("devices")
    lines = [ln.split()[0] for ln in r.stdout.splitlines()[1:]
             if ln.strip() and "\tdevice" in ln]
    return lines[0] if lines else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--serial", default="")
    ap.add_argument("--keep", action="store_true", help="验证完不关页面")
    args = ap.parse_args()

    if not os.path.isdir(CLIPS):
        print("缺少 build/clips/，先跑 tools/reencode_clamped.py")
        return 1
    missing = [s for s in SAMPLES
               if not os.path.isfile(os.path.join(CLIPS, s + ".webm"))]
    if missing:
        print("缺少样本素材:", missing)
        return 1

    serial = args.serial or pick_device()
    if not serial:
        print("没有已连接的设备。先 adb devices 确认手机已授权。")
        return 1
    print(f"设备: {serial}")

    srv = Server(("127.0.0.1", args.port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"本地服务: http://127.0.0.1:{args.port}")

    adb("-s", serial, "reverse", f"tcp:{args.port}", f"tcp:{args.port}")
    print(f"adb reverse 已建立")

    url = f"http://127.0.0.1:{args.port}/"
    print(f"打开: {url}")
    # 用 am start 让系统自己挑默认浏览器。不要硬指定 com.android.chrome——
    # 一加这类国内 ROM 上 Chrome 常常没装，指定的 component 不存在会静默失败。
    r = adb("-s", serial, "shell", "am", "start", "-a",
            "android.intent.action.VIEW", "-d", url)
    if "Error" in r.stdout or "Exception" in r.stdout:
        print("am start 报错:", r.stdout.strip()[:200])
    else:
        print("已在手机打开")

    print("\n等手机上报（最多 90 秒）… Ctrl+C 退出\n")
    t0 = time.time()
    try:
        while time.time() - t0 < 90:
            if Handler.report and "alpha" in Handler.report:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    ok = bool(Handler.report and "alpha" in Handler.report)
    if not ok:
        print("\n没有收到结果。可能原因：")
        print("  1. 手机上没弹「用哪个应用打开」的对话框 —— 手动选浏览器")
        print("  2. 手机锁屏 / 浏览器在后台")
        print("  3. WebView 版本太旧，JS 报错（用 chrome://inspect 看）")
    if not args.keep:
        adb("-s", serial, "reverse", "--remove", f"tcp:{args.port}")
    srv.shutdown()
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
