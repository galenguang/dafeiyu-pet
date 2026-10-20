# 大肥鱼桌宠 🐋

一只陪你摸鱼的 DeepSeek 鲸鱼娘桌宠。透明无边框、置顶、可拖拽，**默认只在屏幕右下角小范围活动，不挡你办公**。

- **100 段透明动画**（VP9 alpha 视频，640×360 / 24fps）
- **28 个招牌互动** + 完整动作点播
- **电脑体检**：CPU / 内存 / 显卡 / 磁盘 / 网络 / 电池 / 进程
- **AI 聊天**：DeepSeek，能查余额、报天气
- **节日自动感知**：农历节日用预生成表，当天会主动过节

**仓库**：[Gitee](https://gitee.com/albot-crane/dafeiyu-pet) · [GitHub](https://github.com/star211199/dafeiyu-pet)

**直接玩（不用装 Python）**：

| 下载 | 说明 |
|------|------|
| [GitHub Releases](https://github.com/star211199/dafeiyu-pet/releases/tag/v1.0) | 一个 zip 搞定，115MB |
| [Gitee Releases](https://gitee.com/albot-crane/dafeiyu-pet/releases/tag/v1.0) | 拆成 `part1` / `part2` 两卷（Gitee 附件单文件限 100MB），**两个都下载，解压到同一个文件夹** |

---

## 看一眼

<p align="center">
  <img src="docs/screenshots/pet.png" width="620" alt="大肥鱼桌宠">
</p>

<p align="center">
  <img src="docs/screenshots/bubble.png" width="230" alt="点一下冒气泡">
  <img src="docs/screenshots/panel.png" width="330" alt="28 个招牌互动">
</p>

<p align="center">
  <img src="docs/screenshots/health.png" width="300" alt="电脑体检报告">
</p>

---

## 怎么玩

| 操作 | 效果 |
|------|------|
| **左键单击** | 随机一个点击反应动画 + 气泡台词 |
| **左键双击** | 打开互动面板（28 个招牌互动 + 100 个动作分类） |
| **按住拖拽** | 拖着走，松手会「啪叽」落地，活动范围跟着落点走 |
| **右键** | 完整菜单（动作点播 / 体检 / 聊天 / 模式 / 大小 / 设置） |
| **托盘图标** | 单击显示隐藏，右键同款菜单 |

### 弹出窗口怎么关、怎么拖

| 窗口 | 特性 |
|------|------|
| **互动面板**（双击鱼） | `Qt.Popup` 弹层，点外面或按 Esc 自动收起 |
| **体检报告**（右键菜单） | 独立窗口：右上角 `×`、`Esc`、菜单项「✖ 关闭体检报告」三种关法；按住面板任意位置（包括指标条、文字）都能拖动，拖出屏幕会自动吸回 |

> 无边框 `Qt.Tool` 窗口不会自己处理拖动和 Esc，必须显式实现。体检面板的拖动靠
> `eventFilter` 接管子控件的鼠标事件 —— 注意过滤器里 **不能 return True**，
> 否则 Qt 不会建立鼠标 grab，表现就是「拖一下就断」。

鱼身以外的透明区域**鼠标可以穿透**（靠 `WM_NCHITTEST` 按像素 alpha 判定），
所以窗口虽然有一大块，但不会挡住你点下面的东西。

---

## 28 个招牌互动

| | | | |
|---|---|---|---|
| 🎪 随机表演 | ✨ 随机特效 | 🎊 节日限定 | 🎵 随机歌舞 |
| 🍚 随便吃点 | 😴 打盹 | 🙆 伸懒腰 | 😱 吓一跳 |
| 🍲 涮火锅 | 🏠 随机日常 | 💰 看看余额 | 🥮 Token吃月饼 |
| 💃 女仆舞 | 🎤 哼歌 | 🍦 吃冰淇淋 | 🍿 吃零食 |
| 🎆 放烟花 | 🧧 收红包 | 🕺 宅舞 | 🐋 蓝鲸现世 |
| 🐱 撸猫 | 🫧 鲸吐泡泡 | 🌊 鲸尾拍地 | 👻 小幽灵 |
| 💻 写代码 | 🍪 偷吃被抓 | 💢 气急败坏 | 🦀 螃蟹步 |

其中「随机表演 / 随机特效 / 节日限定 / 随机歌舞 / 随便吃点 / 随机日常」是从对应分类里按权重随机；
「看看余额」会真的去查 DeepSeek 账户余额（需要设置 Key）；
「Token吃月饼」会在月饼上撒金币并报一个 token 数。

---

## 完整动作库（100 个）

| 分类 | 数量 | 举例 |
|------|------|------|
| 待机 | 2 | 待机、打盹 |
| 日常 | 16 | 写代码、撸猫、偷吃被抓、气急败坏、照镜子、刷牙、换衣服 |
| 吃货 | 20 | 涮火锅、吃冰淇淋、吃零食、吃饺子、吃青团、吃汤圆、嗦面、吃粽子 |
| 节日 | 14 | 放烟花、收红包、吃月饼、舞狮、写福字、堆雪人、圣诞树、万圣南瓜、放天灯、放河灯 |
| 表演 | 14 | 扑克魔术、变鸽子、变花、吹气球、抛接球、放风筝、踢毽子、下五子棋、玩魔方 |
| 歌舞 | 7 | 女仆舞、宅舞、哼歌、摇摆舞、女仆行礼、吹笛子、拉小提琴 |
| 特效 | 7 | 蓝鲸现世、鲸吐泡泡、鲸尾拍地、小幽灵、兽耳光环、蝶舞光环、落叶堆 |
| 碎碎念 | 4 | 碎碎念、沉思、擦擦脸、盯屏幕 |
| 余额 | 6 | 余额空空、余额告急、金币叮当、余额爆表、余额正常、数钱 |
| 点击反应 | 5 | 挥手、开心、生气、害羞、偷笑 |

---

## 电脑体检

右键 → 体检报告，或者互动面板外的菜单入口。面板每秒刷新：

- **综合健康分**（0-100 环形图）+ 一句人话结论
- **CPU**：占用率 / 主频 / 线程数 / 温度（能读到才显示）
- **内存**：占用率 + 已用/总量
- **显卡**：占用 / 温度 / 显存（NVIDIA 走 NVML，没有就自动隐藏）
- **磁盘**：每个分区的占用
- **网络**：实时上下行速率
- **电池**：电量 + 充电状态
- **占用最高**：CPU 与内存 Top5 进程

超阈值会主动冒泡提醒（CPU ≥90%、内存 ≥92%、显卡 ≥82°C、CPU ≥88°C、磁盘 ≥93%、电量 ≤15%），
同一项不会反复刷屏，回落之后才会再次触发。

---

## AI 聊天 / 天气 / 余额

右键 → 设置 → 填 DeepSeek Key（[platform.deepseek.com](https://platform.deepseek.com) 申请）。

- **聊天**：右键「跟她说句话」，或菜单里的入口；保留最近 40 条上下文
- **余额**：查 DeepSeek 账户余额
- **天气**：`wttr.in`，城市在设置里改

Key 存在 `%APPDATA%\DafeiyuPet\config.json`，不会进仓库。

---

## 模式与大小

| 模式 | 说明 |
|------|------|
| **右下角活动**（默认） | 只在屏幕右下角一块区域里溜达（上限占屏 36%），不挡办公 |
| 全屏散步 | 整块屏幕随便走 |
| 跟随鼠标 | 鼠标跑远了就跟过去，靠近了就停下 |
| 原地待着 | 不动，只做小动作 |

大小四档：小 / 中 / 大 / 特大（相对 640×360 画布 0.50 / 0.70 / 0.90 / 1.10）。

---

## 运行（源码）

需要 **Python 3.11+**，Windows 10/11。

```bash
git clone https://gitee.com/albot-crane/dafeiyu-pet.git
cd dafeiyu-pet

python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

> 仓库里已经带全部 100 段动画素材（约 50MB），clone 下来直接就能跑。
> GitHub 访问慢的话用 Gitee 那条地址，内容一致。

## 打包成 exe

```bash
.venv\Scripts\pyinstaller --noconfirm --clean 大肥鱼桌宠.spec
.venv\Scripts\python tools\prune.py dist\大肥鱼桌宠        # 瘦身 + 自检
```

产物在 `dist\大肥鱼桌宠\`，整个文件夹一起拷给别人即可，双击就能用，不需要装 Python。

**成品（211MB，含 100 段动画素材）不打进 git 仓库**，避免 clone 变慢。现成 exe 走 Releases：

- **GitHub**：`dafeiyu-pet-v1.0-win64.zip`，115MB，解压双击 `大肥鱼桌宠.exe`
- **Gitee**：`part1.zip`（61MB）+ `part2.zip`（59MB），两个都要下，**都解压到同一个文件夹**（Windows 会提示「合并文件夹」，选是），再双击 `大肥鱼桌宠.exe`

> Gitee 的 Release 附件单文件上限 100MB，所以拆了卷；两卷内容不重叠，合起来就是完整的 `大肥鱼桌宠/` 目录。

> 用目录形式而不是 `--onefile`：素材有 50MB，单文件每次启动都要解压一遍，会明显变慢。
> 杀毒软件可能对 PyInstaller 产物误报，加信任即可。
> `av.libs`（65MB）是 ffmpeg 的静态依赖，删了会 `ImportError: DLL load failed`，别动。

### 打包后的自检与诊断

| 命令 | 作用 |
|------|------|
| `大肥鱼桌宠.exe --selftest` | 素材齐全性、解码、系统监控三项检查，结果写 `%TEMP%\dafeiyu_selftest.txt` |
| `大肥鱼桌宠.exe --probe` | 起窗口跑 3 秒，把几何/DPI/监控/自截图写到 `%TEMP%\dafeiyu_probe\` |
| `tools\click.py click\|dblclick\|rclick\|drag` | 用 Win32 消息模拟鼠标，做端到端交互测试 |
| `tools\gdi_shot.py` | 纯 GDI 截屏（不建 QApplication，不会抢焦点导致面板自动隐藏） |
| `tools\wins.py` | 列出桌宠窗口的位置/尺寸/可见性 |

> 排查踩过的坑：桌宠用命名互斥量做单实例。**上一个实例没退干净时，新启动的 exe 只会弹一个「已经有一只大肥鱼」的提示框**，
> 看起来就像「exe 启动后什么都没有」。遇到这种情况先确认没有残留进程。

---

## 技术要点

**透明视频怎么播的。** 素材是 VP9 + 独立 alpha 平面的 webm（`ALPHA_MODE=1`）。
ffmpeg 默认的 vp9 解码路径会丢掉 alpha，必须显式用 `libvpx-vp9` 解码器上下文。
这里通过 PyAV 手动 demux + `CodecContext.create("libvpx-vp9", "r")` 解码，
实测 640×360 能跑到约 1000 fps（远高于 24fps 播放需求），
所以用「后台线程 + 有界队列预取 6 帧」的方式喂主线程，界面不卡。

**为什么不用逐帧图片。** 100 段 × 241 帧，转成 WebP 逐帧存要 300MB 左右；
直接放原始 webm 只有 50MB，画质还更好。

**透明区域鼠标穿透。** 处理 `WM_NCHITTEST`，按当前帧的 alpha 判断光标下是不是角色身体，
不是就返回 `HTTRANSPARENT` 让点击落到下面的窗口。
注意 Win32 消息给的是**物理像素**、Qt 用的是**逻辑像素**，高 DPI 下要按
`devicePixelRatio` 换算，否则 200% 缩放的屏幕上命中会整体偏一倍。

**农历节日。** 用 `lunardate` 离线生成 2020-2040 的节日对照表固化进代码，
运行时不依赖任何第三方库。

---

## 目录结构

```
dafeiyu_pet/
├── main.py                 入口（单实例、DPI、异常兜底）
├── app/
│   ├── config.py           路径与配置（存 %APPDATA%）
│   ├── actions.py          100 个动作注册表 + 28 个招牌互动定义
│   ├── clip.py             VP9-alpha 解码播放器（线程 + 预取）
│   ├── pet.py              主窗口：播放 / 移动 / 交互 / 菜单 / 托盘
│   ├── effects.py          全屏粒子层（烟花 / 泡泡 / 雪花 / 爱心）
│   ├── sysmon.py           系统指标采集 + 阈值告警
│   ├── festival.py         公历 + 农历节日判定
│   ├── lines.py            台词库
│   ├── llm.py              DeepSeek 对话 / 余额 / 天气
│   └── ui/                 互动面板、体检面板、聊天输入框
├── assets/
│   ├── webm/               100 段透明动画
│   └── icon.png / .ico
├── docs/screenshots/       README 用截图
├── tools/                  图标生成、界面预览、素材处理、交互测试
└── 大肥鱼桌宠.spec         PyInstaller 配置
```

---

## 素材与致谢

- 动画素材来自开源项目 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet)（作者 PC2005-cloud），**非商用**。
- 参考项目：[1190fasheqi/dafeiyu-pet](https://github.com/1190fasheqi/dafeiyu-pet)（三视图 + PySide6 实现思路）。
- 形象为 DeepSeek「鲸鱼娘 · 大肥鱼」二创，版权归原作者。

## 协议

MIT（仅代码；动画素材版权归 dsh-pet 作者，请遵循其协议，勿商用）
