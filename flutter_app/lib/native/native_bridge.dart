import 'package:flutter/services.dart';

/// 原生能力桥（对应 `MainActivity.kt` 里的 `dafeiyu/native` 通道）。
///
/// 只包三件 Flutter 侧做不到的事：拿私有目录、真退出、画中画。
/// 刻意不引 `path_provider`——pubspec 的依赖纪律里写明了「这 20 行自己写」。
class NativeBridge {
  static const MethodChannel _ch = MethodChannel('dafeiyu/native');

  static String? _cachedFilesDir;

  /// 应用私有目录（`/data/data/<pkg>/files`）。
  /// 缓存一次：这个值不会变，每次进设置页都跨通道问一遍没必要。
  static Future<String?> filesDir() async {
    if (_cachedFilesDir != null) return _cachedFilesDir;
    try {
      _cachedFilesDir = await _ch.invokeMethod<String>('filesDir');
    } catch (_) {
      _cachedFilesDir = null;
    }
    return _cachedFilesDir;
  }

  static Future<bool> pipSupported() async {
    try {
      return await _ch.invokeMethod<bool>('pipSupported') ?? false;
    } catch (_) {
      return false;
    }
  }

  static Future<bool> enterPip() async {
    try {
      return await _ch.invokeMethod<bool>('enterPip') ?? false;
    } catch (_) {
      return false;
    }
  }

  /// 真正退出：finishAndRemoveTask + killProcess。
  /// 调完这个进程就没了，后面的代码不会执行，所以不要 await 之后还做事。
  static Future<void> exitApp() async {
    try {
      await _ch.invokeMethod<bool>('exit');
    } catch (_) {}
  }

  /// 进出小窗的回调。小窗里要藏掉底栏和气泡。
  static void onPipChanged(void Function(bool inPip) cb) {
    _ch.setMethodCallHandler((call) async {
      if (call.method == 'pipChanged') {
        cb(call.arguments == true);
      }
      return null;
    });
  }
}
