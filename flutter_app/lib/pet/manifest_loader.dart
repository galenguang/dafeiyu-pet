import 'package:flutter/services.dart' show rootBundle;

import 'manifest.dart';

/// Flutter 侧唯一的 IO 入口。
///
/// 之所以单独一个文件：`manifest.dart` 要保持纯 Dart，
/// 才能在 `dart run` 里被测试。`rootBundle` 一引进来就绑死 Flutter 了。
class ManifestLoader {
  static Manifest? _cached;

  static Future<Manifest> load({bool force = false}) async {
    if (_cached != null && !force) return _cached!;
    final raw = await rootBundle.loadString('assets/manifest.json');
    _cached = Manifest.parse(raw);
    return _cached!;
  }

  /// 测试用：直接注入一份已解析的。
  static void override(Manifest m) => _cached = m;
}
