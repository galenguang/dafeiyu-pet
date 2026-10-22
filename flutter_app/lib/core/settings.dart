import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../native/native_bridge.dart';

/// 一份设置。字段命名对齐桌面版 `app/config.py` 的 `DEFAULT_CONFIG`，
/// 能对上的尽量用同一个 key，方便以后两边互相导入。
///
/// 只搬手机版用得上的项。桌面版的 `autostart` / `passthrough` /
/// `health_monitor` / `agent_*` 这些手机上不存在，不带。
class AppSettings {
  /// 她**本人**占屏幕宽度的比例。0.3 很小，0.9 几乎铺满。
  ///
  /// 注意这不是「渲染框占多宽」：640x360 的画布里她只占约 39%，
  /// 左右都是透明留白。如果按渲染框算，滑块拉到 100% 她也只有屏幕的
  /// 39% 宽，用户会觉得「拖了没反应」。所以这里按角色本体算，
  /// 渲染框会相应放大到屏幕外——留白透明，看不出来。
  double petWidthFactor;

  /// 她在舞台里的归一化位置（0..1），下次打开还站在原处。
  double petX;
  double petY;

  /// 是否朝左。
  bool facingLeft;

  /// 空闲时自动表演。
  bool autoPerform;

  // ---- 大模型（任何兼容 OpenAI 接口格式的服务都能用）----
  String llmPreset;
  String llmBaseUrl;
  String llmApiKey;
  String llmModel;
  int llmTimeout;

  // ---- 记忆 ----
  bool memoryEnabled;

  /// 用模型抽取（关掉就只走本地规则）。
  bool memoryLlmExtract;

  /// 允许在台词里提起记得的事。
  bool memorySpeak;

  /// 攒够几轮用户发言才抽一次。
  int memoryExtractEvery;

  AppSettings({
    this.petWidthFactor = 0.55,
    this.petX = 0.5,
    this.petY = 0.5,
    this.facingLeft = false,
    this.autoPerform = true,
    this.llmPreset = 'DeepSeek',
    this.llmBaseUrl = 'https://api.deepseek.com/v1',
    this.llmApiKey = '',
    this.llmModel = 'deepseek-chat',
    this.llmTimeout = 30,
    this.memoryEnabled = true,
    this.memoryLlmExtract = true,
    this.memorySpeak = true,
    this.memoryExtractEvery = 6,
  });

  factory AppSettings.fromJson(Map<String, dynamic> j) {
    final d = AppSettings();
    double num2(String k, double def) {
      final v = j[k];
      return v is num ? v.toDouble() : def;
    }

    int int2(String k, int def) {
      final v = j[k];
      return v is num ? v.toInt() : def;
    }

    bool bool2(String k, bool def) {
      final v = j[k];
      return v is bool ? v : def;
    }

    String str2(String k, String def) {
      final v = j[k];
      return v is String && v.isNotEmpty ? v : def;
    }

    return AppSettings(
      petWidthFactor: num2(
        'pet_width_factor',
        d.petWidthFactor,
      ).clamp(0.3, 0.9),
      petX: num2('pet_x', d.petX).clamp(0.0, 1.0),
      petY: num2('pet_y', d.petY).clamp(0.0, 1.0),
      facingLeft: bool2('facing_left', d.facingLeft),
      autoPerform: bool2('idle_actions', d.autoPerform),
      llmPreset: str2('llm_preset', d.llmPreset),
      llmBaseUrl: str2('llm_base_url', d.llmBaseUrl),
      llmApiKey: j['llm_api_key'] is String ? j['llm_api_key'] as String : '',
      llmModel: str2('llm_model', d.llmModel),
      llmTimeout: int2('llm_timeout', d.llmTimeout).clamp(5, 120),
      memoryEnabled: bool2('memory_enabled', d.memoryEnabled),
      memoryLlmExtract: bool2('memory_llm_extract', d.memoryLlmExtract),
      memorySpeak: bool2('memory_speak', d.memorySpeak),
      memoryExtractEvery: int2(
        'memory_extract_every',
        d.memoryExtractEvery,
      ).clamp(2, 50),
    );
  }

  Map<String, dynamic> toJson() => {
    'pet_width_factor': petWidthFactor,
    'pet_x': petX,
    'pet_y': petY,
    'facing_left': facingLeft,
    'idle_actions': autoPerform,
    'llm_preset': llmPreset,
    'llm_base_url': llmBaseUrl,
    'llm_api_key': llmApiKey,
    'llm_model': llmModel,
    'llm_timeout': llmTimeout,
    'memory_enabled': memoryEnabled,
    'memory_llm_extract': memoryLlmExtract,
    'memory_speak': memorySpeak,
    'memory_extract_every': memoryExtractEvery,
  };

  AppSettings copy() => AppSettings.fromJson(toJson());
}

/// 设置读写。落盘到 `<filesDir>/settings.json`，写法和桌面版一样
/// 用「临时文件 + rename」，避免写一半被系统杀掉留下半截 JSON。
class SettingsStore {
  static const _fileName = 'settings.json';

  static File? _file;
  static Directory? _dir;

  /// 应用私有目录。测试环境（没有 MethodChannel）会退回临时目录。
  static Future<Directory> dir() async {
    final cached = _dir;
    if (cached != null) return cached;
    String? base;
    try {
      base = await NativeBridge.filesDir();
    } catch (_) {
      base = null;
    }
    base ??= Directory.systemTemp.path;
    final d = Directory('$base/dafeiyu');
    if (!d.existsSync()) d.createSync(recursive: true);
    _dir = d;
    return d;
  }

  static Future<File> file() async {
    final cached = _file;
    if (cached != null) return cached;
    final d = await dir();
    final f = File('${d.path}/$_fileName');
    _file = f;
    return f;
  }

  static Future<AppSettings> load() async {
    try {
      final f = await file();
      if (!f.existsSync()) return AppSettings();
      final raw = await f.readAsString();
      final j = jsonDecode(raw);
      if (j is Map<String, dynamic>) return AppSettings.fromJson(j);
      if (j is Map) return AppSettings.fromJson(Map<String, dynamic>.from(j));
    } catch (e) {
      debugPrint('[settings] 读取失败: $e');
    }
    return AppSettings();
  }

  static Future<void> save(AppSettings s) async {
    try {
      final f = await file();
      final tmp = File('${f.path}.tmp');
      await tmp.writeAsString(jsonEncode(s.toJson()), flush: true);
      await tmp.rename(f.path);
    } catch (e) {
      debugPrint('[settings] 保存失败: $e');
    }
  }
}

/// 全局设置控制器。UI 用 [ListenableBuilder] 订阅即可。
class SettingsController extends ChangeNotifier {
  AppSettings value;
  bool loaded = false;

  SettingsController(this.value);

  static Future<SettingsController> boot() async {
    final s = await SettingsStore.load();
    final c = SettingsController(s)..loaded = true;
    return c;
  }

  /// 改一项并落盘。`save: false` 用于拖动滑杆这种高频场景，
  /// 松手时再调一次 [flush]。
  void update(void Function(AppSettings s) f, {bool save = true}) {
    f(value);
    notifyListeners();
    if (save) SettingsStore.save(value);
  }

  Future<void> flush() => SettingsStore.save(value);
}
