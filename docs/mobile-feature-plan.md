# 手机版新增功能可行性评估

> 评估时间：2026-10-22
> 前置状态：M1（渲染内核）刚通过真机自检，9 项全绿。

## 0. 先说结论

| # | 需求 | 能不能做 | 成本 | 风险 |
|---|------|---------|------|------|
| 1 | 单击效果和电脑版一样 | ✅ 已实现 | 0.5 天 | 无 |
| 2 | 双击随机表演一个动作 | ✅ 已实现 | 0.1 天 | 无 |
| 3 | 用手拖动大肥鱼 | ✅ 已实现 | 1 天 | 无 |
| 4 | 聊天对话框 | ✅ 能 | 1.5 天 | 低 |
| 5 | 自配置模型（OpenAI 格式） | ✅ 能 | 0.5～1 天 | 低（Key 存储） |
| 6 | 退出时真正关掉 App 页面 | ✅ 能 | 0.2 天 | 无 |
| 7 | 悬浮在其他应用之上 | ⚠️ 能，但代价大 | 3～5 天 | **高** |
| 8 | 记忆功能 | ✅ 能 | 3～9 天 | 中 |

**1～6 全部可以放心做，合计约 4 天。**
**7 与之前锁定的产品形态直接冲突，需要你重新拍一次板（见 §2）。**
**8 是里面工作量最大的一项，且可以分阶段做（见 §3）。**

---

## 1. 已经做完的三项（1/2/3）

这三项在本轮已经落地，不用再评估，写在这里是为了记录实现口径。

### 单击 —— 对齐 `pet.py::_on_single_click`

桌面版逻辑：

```python
reaction = random.choice(["clickWave","clickJoy","clickGiggle","clickShy","clickAngry"])
self.play_action(reaction, speak=False, duration=2.8)
self.say(lines.pick(lines.CLICK_LINES, ...))
```

手机版：从 manifest 的 `click` 分类（5 条）加权随机抽一条，用新加的
`ActionScheduler.play(id, limitMs: 2800)` 限时 2.8 秒后自动切回待机，同时冒
`CLICK_LINES` 里的台词。

**为什么需要 `limitMs`**：素材每段都是 241 帧 ≈ 10 秒，而桌面版的点击反应只播
2.8 秒。没有这个参数的话，戳一下她要愣 10 秒，手感完全不同。

### 双击 —— 有意与桌面版不同

桌面版双击是**弹右键菜单**。手机上你要的是「随机表演一个动作」，所以这里直接
`performNow()`，不弹任何 UI。这是唯一一处刻意偏离桌面版行为的地方。

### 拖动 —— 对齐 `mouseMoveEvent` / `mouseReleaseEvent`

| 桌面版 | 手机版 |
|--------|--------|
| 位移 > 8px 判定为拖动 | 同 |
| 切 `drag.webm` 循环，不自动结束 | `scheduler.play('drag', returnToIdle: false)` |
| `abs(delta.x) > 12` 转身 | `abs(delta.dx) > 8` 触发 `onFacing` |
| 松手 `snap_into_screen()` 防止跑出屏幕 | 归一化坐标天然被 `clamp(0,1)` 限制 |
| 松手播 `squash` 0.8 秒 | `play('squash', limitMs: 800)` |
| 50% 概率冒 `DROP_LINES` | 同 |

**顺带解决了一个布局问题**：原来整幅 640x360 铺满屏幕宽度，画布和舞台一样宽，
横向可移动余量是 0——根本拖不动。

试过按 `crop` 裁切渲染（只画角色包围盒），但实测**并集是 634x360**——被烟花、
蝶舞光环、吹气球这 12 条特效片段撑满了，和整幅画布几乎没区别，裁了也放不大，
反而会把那些特效的边缘切掉。这条路放弃了。

最后用的是更简单的办法：渲染框保持整幅，但**允许渲染框左右各约 30% 的透明留白
移出屏幕**，拖动边界按角色本体（`idle` 的 crop，占画布宽 38.9%）算。这样既保持
原来的观感，横向又有约 660px 的真实拖动范围。

位置存**归一化坐标**（0..1）而不是像素，横竖屏切换、分屏、折叠屏展开时
不会跑到屏幕外面去。

### 实现时踩到的两个坑（记下来免得再犯）

1. **`onPanCancel` 在纯点击时也会触发**。按下去没移动就抬手，pan 会被 tap 抢走
   并 cancel 到 `onPanCancel`，于是单击先走一遍「松手」逻辑（播 squash + 冒
   DROP_LINES），把真正的点击台词盖掉。修法：加 `_dragging` 标志，
   `_endDrag` 里先判空返回。
2. **WebView 会吃掉纯点击**。拖动能用是因为位移手势在竞技场里能赢过原生 View，
   但一次没有位移的点击会被 WebView 自己消费，Flutter 的 `onTap` 永远不触发——
   表现是「戳她没反应，只有拖着走才有反应」。修法：`ClipRendererView` 里给
   `WebViewWidget` 包一层 `IgnorePointer`（页面本身没有可交互元素，
   整块交给 Flutter 处理是对的）。

另外自检页「页面 JS 已执行」原来是在 diag 文本里找 `js-ok`——那是滚动日志，
跑几十秒后最早那行会被挤掉，导致假失败。改成直接探测 `window.__cmd64`
是否存在（`ClipRenderer.isPageAlive()`）。

---

## 2. 悬浮窗：需要你重新决策

### 冲突点

已批准的方案里明确写了：

> 形态：**独立养成 App（打开才在）**，不做悬浮窗

当时的理由是「手机上悬浮窗的维护成本远高于它带来的收益」。基于这个决定，
`pet_stage.dart` 里刻意删掉了桌面版的 `_alpha_hit`（逐像素命中测试）和
`nativeEvent`，注释写着：

> 那两个在桌面版存在的唯一目的是「窗口浮在别的应用上时让点击穿透」，
> 独立 App 里她只在自己画布上，这套东西整个不需要。

**现在要做悬浮窗，等于把这套子系统整个加回来，外加一层 Android 原生。**

### 真要做，成本在哪

1. `SYSTEM_ALERT_WINDOW` 权限——不是运行时权限，得跳系统设置页让用户手动开，
   拒绝率不低。
2. 前台服务（Android 14 起必须声明 `foregroundServiceType`），常驻通知栏。
3. 悬浮窗里得有第二个 WebView（或第二个 FlutterEngine）去渲染那 100 段视频，
   和 App 内的渲染器共用同一套 loopback 素材服务。
4. **点击穿透**——悬浮窗默认会吃掉下面 App 的点击。不做逐像素命中，她就变成一块
   挡在屏幕上的砖。这正是 `_alpha_hit` 要解决的问题（桌面版用
   `setMask` + 逐帧 alpha 采样做形状窗口）。
5. 国产 ROM（ColorOS / OriginOS / HyperOS）后台杀进程极凶，得引导用户加电池优化
   白名单，否则「第二天发现她不在了」。

**合计 3～5 天，且交付后仍有「在别人手机上活不过一晚」的兼容风险。**

### 三个选项

| 方案 | 效果 | 成本 | 建议 |
|------|------|------|------|
| **A. 不做** | 保持独立 App | 0 | 维持原决策，最稳 |
| **B. 画中画（PiP）** | 缩小成系统小窗，能浮在其他 App 上、可被系统拖动 | **1 天** | ⭐ 性价比最高 |
| **C. 真悬浮窗** | 完全自由，可盖住任何界面 | 3～5 天 | 只有确实需要才做 |

方案 B 是折中：Android 8+ 原生支持 PiP，调 `enterPictureInPictureMode()` 即可，
不用申请任何敏感权限，不用前台服务，ROM 也不会杀。代价是小窗有系统边框、
位置由系统管、窗内不能拖动角色、点击只能弹系统菜单。

如果目标是「能在刷视频时看到她」，B 就够了。如果目标是「她能飘在微信上面随时
戳」，那只能选 C。

---

## 3. 记忆功能：能做，但要先定做到什么程度

### 桌面版有什么

`app/memory/` 共 9 个文件、约 **2566 行 Python**：

| 文件 | 行数 | 作用 |
|------|------|------|
| `schema.py` | 480 | 数据结构定义 + 版本迁移 |
| `store.py` | 432 | JSON 落盘、去重、淘汰、关系值 |
| `voice.py` | 351 | 把记忆变成人格化台词 |
| `extract.py` | 326 | 用模型抽取事实（攒 N 轮抽一次） |
| `behavior.py` | 315 | 行为观察（熬夜、长时段） |
| `recall.py` | 281 | 召回，拼成 system prompt 里那段 |
| `rules.py` | 228 | 本地规则抽取（不耗 token） |
| `core.py` | 153 | 门面 |

外加 `ui/memory.py` 1021 行的记忆管理面板。

### 移植到 Dart 的两种口径

| 口径 | 内容 | 成本 |
|------|------|------|
| **精简版** | schema + store + LLM 抽取 + recall + voice；砍掉 behavior 行为观察，rules 只留高频几条 | **3 天** |
| **忠实版** | 上面全量 + 行为观察 + 完整规则轨 | 5～6 天 |
| **+ 记忆面板 UI** | 看/删/置顶事实、看关系值、手动教她一件事 | +1.5 天 |

技术上没有拦路虎：桌面版这套是纯 Python、不依赖 Qt、数据就是一个 JSON 文件
（`%APPDATA%/DafeiyuPet/memory.json`），在手机上换成 `getApplicationSupportDirectory()`
即可。真正的成本是 2500 行逻辑要在 Dart 里重写一遍并重新调 prompt。

### 依赖关系

记忆和聊天共用同一条链路：

```
用户输入 → 聊天对话框 → OpenAI 兼容接口 → 回复
                ↓
         记忆抽取（攒够 N 轮）
                ↓
         写 memory.json
                ↓
         下次拼进 system prompt
```

所以**必须先做 4（聊天）+ 5（模型配置），才能做 8（记忆）**。顺序不能反。

---

## 4. 建议排期

```
第 1 天   聊天对话框 + 模型配置（含测试连接）        ← 已具备全部前置
第 2 天   记忆精简版（store + LLM 抽取 + recall）
第 3 天   记忆台词人格化 + 记忆面板 UI
第 4 天   退出清理 + 真机回归
---------- 以上 4 天交付「能聊天、能记事」的版本 ----------
第 5 天   PiP 画中画（方案 B）
---------- 如果确实要真悬浮窗，从这里再追加 3～5 天 ----------
```

## 5. 一个需要你确认的歧义

「没有退出手机 app 进程的时候，可以关掉 app 页面」这句我读不准，两种理解：

- **A**：需要一个「退出」按钮，点了真的结束进程、关掉页面，而不是退到后台挂着。
- **B**：App 没退出时，能单独把某个页面（比如聊天窗口）关掉。

我先按 A 理解并实现（底部菜单加「退出」，走 `finishAndRemoveTask` 并顺手停掉
本地素材服务）。如果是 B，告诉我具体指哪个页面。

---

## 6. 落地结果（2026-10-22）

评估阶段的 6 个功能全部实现并在真机（OnePlus Ace 3V / Android 15）上跑通。
本节只记**最终做了什么、为什么这么做、踩了什么坑**，不再重复评估过程。

### 6.1 新增模块

| 文件 | 职责 | 依赖 |
| --- | --- | --- |
| `lib/core/settings.dart` | 设置持久化（`ChangeNotifier`） | 仅 `dart:io` + MethodChannel |
| `lib/native/native_bridge.dart` | `filesDir` / `pipSupported` / `enterPip` / `exit` | MethodChannel |
| `lib/llm/llm_client.dart` | OpenAI 兼容客户端 + 9 个预设 + 中文错误 | `dart:io HttpClient` |
| `lib/chat/chat_session.dart` | 会话、历史落盘、记忆注入 | 上面两个 |
| `lib/memory/*.dart`（6 个） | 记忆数据层/规则抽取/模型抽取/检索/台词 | **纯 Dart，不 import flutter** |
| `lib/ui/chat_page.dart` | 聊天界面 | — |
| `lib/ui/settings_page.dart` | 外观 / 大模型 / 记忆 / 应用 | — |
| `lib/ui/memory_page.dart` | 记忆面板 | — |

没有引入任何新依赖。`path_provider` / `http` / `provider` 全部用
MethodChannel、`dart:io`、`ChangeNotifier` 顶掉了，理由见 `pubspec.yaml` 的注释。

### 6.2 两个刻意的语义选择

**`pet_width_factor` 指的是「她本人占屏幕多宽」，不是「渲染框多宽」。**

640x360 的画布里她只占 39%（`ActionRegistry.charWFrac`），左右都是透明留白。
如果按渲染框算，滑块拉到 100% 她也只有屏幕的 39% 宽，用户会觉得「拖了没反应」。
所以渲染框 = 角色宽 / 39%，两侧留白允许跑到屏幕外——反正透明。
真机上量过：改之前 32%，改之后 46%（同样是 55% 的滑块值）。

再补一刀：包围盒比实际轮廓宽一圈，`_cropPad = 0.85` 折掉之后，
「大小 55%」量出来就是 55%。

**记忆的模型轨是 add-only 的。**

模型只提候选，不删不改已有条目。防的是模型幻觉把真实画像覆盖掉。
规则轨（正则）永远跑，模型轨受开关和 `memory_extract_every` 节流，
连续失败 3 次本次运行内直接关掉。

### 6.3 真机联调配方（不花一分钱 token）

```bash
python tools/fake_llm_server.py          # 假 OpenAI 服务，127.0.0.1:8899
adb reverse tcp:8899 tcp:8899            # 手机上的 localhost:8899 → 电脑
```

App 里填 Base URL `http://localhost:8899/v1`、Key `test`、模型 `fake`。
服务端会打印每个请求的 messages 条数和有没有带记忆块，一眼看出链路通没通：

```
[chat #2] messages=8 记忆块=有 用户=third
```

`messages=8` = 1 条人设 + 1 条记忆块 + 5 条历史 + 1 条当前。

### 6.4 踩到的坑（都已在代码注释里留了说明）

1. **`webview_flutter` 的 JS 通道名是 `window.stagePost.postMessage(str)`**，
   不是 `window.__stagePost`。这个名字对不上导致 M1 卡了两天——表现是
   「页面里明明 console.log 出画面了，Flutter 侧一条事件都收不到」。
2. **`dart:io HttpClient` 不显式设 `contentLength` 会改用 chunked 编码**，
   一部分网关和自建中转直接回 400。两边都得改：Dart 侧设 `req.contentLength`，
   测试服务侧兼容 chunked 读取。
3. **`onPanCancel` 在纯点击时也会触发**（pan 在手势竞技场里输给 tap 被 cancel）。
   不加 `_dragging` 守卫的话，一次单击会先走「松手」分支：播 squash、冒
   DROP_LINES，把真正的点击台词盖掉。
4. **`WebViewWidget` 会吃掉纯点击**。它背后是原生 View，位移手势还能赢，
   纯点击赢不了。包一层 `IgnorePointer` 才正常。
5. **`flutter test` 在这台机器上起不来**（WebSocket upgrade 被拒），
   所以记忆层的 44 条断言走 `dart run tool/verify_memory.dart`。
   也因此 `lib/memory/*` 一律不 import `package:flutter`——
   连 `debugPrint` 都不能用，自己写了个 `MemLog`。
6. **`adb push` 的 `/data/local/tmp/...` 会被 Git-Bash 改写成 Windows 路径**，
   要加 `MSYS_NO_PATHCONV=1`。
7. **`adb shell input tap` 的坐标是设备像素**，截图是 1080x2414。
   目测像素位置会差几十上百 px，白点半天没反应——用脚本扫像素定位才靠谱。

