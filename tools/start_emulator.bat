@echo off
REM ============================================================
REM  启动大肥鱼测试模拟器
REM  AVD: dafeiyu  (Pixel 6 / Android 14 / x86_64 / google_apis)
REM  加速: WHPX (已实测 accel: 0，可用)
REM ============================================================
setlocal
set "ANDROID_HOME=D:\Android\Sdk"
set "ANDROID_SDK_ROOT=D:\Android\Sdk"

echo [1/2] 检查加速…
"%ANDROID_HOME%\emulator\emulator.exe" -accel-check
echo.

echo [2/2] 启动模拟器 dafeiyu …
REM -gpu auto        : 让模拟器自己挑最佳 GPU 后端
REM -no-snapshot-save: 退出时不留快照，避免下次启动读到坏状态
REM -no-boot-anim    : 跳过开机动画，快一点
"%ANDROID_HOME%\emulator\emulator.exe" -avd dafeiyu -gpu auto -no-snapshot-save -no-boot-anim

endlocal
