# Android 开发环境搭建记录

> 2026-10-22 实装并验证。机器：AMD Ryzen 7 8845HS / 23.3GB / Windows 10.0.26200

## 一句话现状

**Android 部分你早就装好了，缺的是「命令行可见」和 Flutter。补齐之后已经能构建 APK。**

---

## 装了什么

| 组件 | 位置 | 版本 | 说明 |
|---|---|---|---|
| Android Studio | `D:\Android\Android Studio` | 已存在 | 本次未动 |
| Android SDK | `D:\Android\Sdk` | 36.1.0 | 已存在 |
| adb / platform-tools | `D:\Android\Sdk\platform-tools` | 37.0.0 | 已存在，本次加进 PATH |
| build-tools | `D:\Android\Sdk\build-tools` | 33 / 34 / 36.1 / 37 | 已存在 |
| **cmdline-tools** | `D:\Android\Sdk\cmdline-tools\latest` | 19.0 | **本次新装**（原来没有，所以没有 sdkmanager） |
| **system-images** | `D:\Android\Sdk\system-images\android-34\google_apis\x86_64` | API 34 | **本次新装**（4.2GB） |
| **AVD** | `C:\Users\admin\.android\avd\dafeiyu.avd` | Pixel 6 / Android 14 | **本次新建** |
| **Flutter SDK** | `D:\flutter` | 3.47.7 stable / Dart 3.13.5 | **本次新装** |
| JDK | `D:\Java_JDK\jdk-21.0.12` | 21 LTS | 已存在；Flutter 实际用 Studio 自带的 JBR 21 |

## 环境变量（已写入用户级，重启终端生效）

```
ANDROID_HOME              = D:\Android\Sdk
ANDROID_SDK_ROOT          = D:\Android\Sdk
PUB_HOSTED_URL            = https://pub.flutter-io.cn
FLUTTER_STORAGE_BASE_URL  = https://storage.flutter-io.cn
```

PATH 追加了 5 项：

```
D:\Android\Sdk\platform-tools
D:\Android\Sdk\cmdline-tools\latest\bin
D:\Android\Sdk\emulator
D:\Android\Sdk\build-tools\37.0.0
D:\flutter\bin
```

**验证：** 新开一个终端，依次敲 `adb version`、`sdkmanager --version`、`flutter --version`，三个都有输出就对了。

---

## 模拟器

AVD 名 `dafeiyu`（Pixel 6 / Android 14 / google_apis / x86_64）。
加速用 **WHPX**，实测 `emulator -accel-check` 返回：

```
accel: 0
WHPX(10.0.26200) is installed and usable.
```

`0` 就是可用。**不需要**再装 AEHD，也不要装 HAXM（AMD 用不了）。

启动方式二选一：

```bat
REM 方式一：双击脚本
dafeiyu_pet\tools\start_emulator.bat

REM 方式二：命令行
emulator -avd dafeiyu -gpu auto -no-snapshot-save -no-boot-anim
```

> 注意：`aehd` 服务当前是 Stopped，这是**正常的**——WHPX 和 AEHD 互斥，
> 走 WHPX 就不该开 AEHD。别去手动启动它。

---

## 真机调试（比模拟器更值得测）

1. 手机开「开发者选项」→「USB 调试」
2. 数据线连电脑，手机弹窗选「允许」
3. `adb devices` 应该能看到设备
4. `flutter devices` 会列出来

**已实测：目前没有真机连接。** M0 的播放验证最终要落到真机上，
因为模拟器的 GPU 和真机（尤其 Mali/Adreno）在 VP9-alpha 解码上可能不一样。

---

## 已知的无害告警

| 告警 | 原因 | 要不要管 |
|---|---|---|
| `SDK XML versions up to 3 ... version 4` | 命令行工具 19.0 与 Studio 的 XML 版本时间差 | 不用管，功能正常 |
| `flutter doctor` 里 `Windows Version` 崩溃 | 它在跑 `reg query`，被本机安全策略拦了 | 你自己的终端里不会出现 |
| `Visual Studio [X]` | 那是给 Windows 桌面 App 用的 | 做 Android 不用管 |
| `NO_PROXY is not set` | 你走代理 | 不用管 |

---

## 一旦要上 iOS

现在是 Android-only。将来要上 iOS 必须具备：

- macOS + Xcode（Windows 上做不了）
- **HEVC-alpha 编码也必须在 macOS 上做**（VideoToolbox）
- 那 100 段素材要重新编码一套

所以「未来上 iOS」不是加一班，是加一条产线。

---

## 本次踩的坑

1. **`flutter --version` 卡死 5 分钟**：它在连 pub.dev 和 storage.googleapis.com 检查更新。
   设了 `PUB_HOSTED_URL` / `FLUTTER_STORAGE_BASE_URL` 国内镜像后 20 秒完成。
   顺带删掉残留的 `bin\cache\flutter.bat.lock` 和 `flutter_tools.stamp`。
2. **`cmdline-tools` 必须放在 `cmdline-tools\latest\`**，不能直接放 `cmdline-tools\`，
   否则 sdkmanager 找不到自己的路径。
3. **Flutter 不要装在 Program Files**：需要写权限，官方也是建议用户目录或盘根。
   所以放 `D:\flutter`。
4. **模拟器不能用 `&` 挂后台**：会被当前 shell 回收，进程静默退出且日志为空。
   要用脚本或独立窗口启动。
