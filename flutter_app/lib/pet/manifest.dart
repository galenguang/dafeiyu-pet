import 'dart:convert';

/// 一段动画的元数据。对应 `build/manifest.json` 里 `clips[]` 的一项。
///
/// 与桌面版 `app/actions.py` 的 `Action` 是同一份数据的两种表示，
/// manifest 由 `tools/build_manifest.py` 生成，两者的字段一致性
/// 在生成时已经逐条比对过（0 差异）。
class Clip {
  final String id;
  final String file;
  final String name;
  final String cat;
  final String kind;
  final double weight;
  final bool loop;
  final String icon;
  final int frames;
  final int durationMs;

  /// 画布坐标下的内容包围盒 `[x, y, w, h]`。
  /// 桌面版恒为整幅，这里带着是为了手机上减少 WebView 的绘制区域，
  /// 也是将来做图集方案时的裁切依据。
  final List<int> crop;

  /// 锚点，目前是 `[画布中心X, bbox底边]`，用于漫游时对齐脚底。
  final List<int> anchor;

  const Clip({
    required this.id,
    required this.file,
    required this.name,
    required this.cat,
    required this.kind,
    required this.weight,
    required this.loop,
    required this.icon,
    required this.frames,
    required this.durationMs,
    required this.crop,
    required this.anchor,
  });

  factory Clip.fromJson(Map<String, dynamic> j) {
    return Clip(
      id: j['id'] as String,
      file: j['file'] as String,
      name: j['name'] as String,
      cat: j['cat'] as String,
      kind: j['kind'] as String,
      weight: (j['weight'] as num).toDouble(),
      loop: j['loop'] as bool? ?? false,
      icon: j['icon'] as String? ?? '',
      frames: j['frames'] as int,
      durationMs: j['durationMs'] as int,
      crop: (j['crop'] as List).cast<int>(),
      anchor: (j['anchor'] as List).cast<int>(),
    );
  }

  /// 播放器用的片段名：文件名去掉扩展名。
  String get clipName => file.replaceAll('.webm', '');
}

class Category {
  final String key;
  final String label;
  final String icon;
  const Category({required this.key, required this.label, required this.icon});

  factory Category.fromJson(Map<String, dynamic> j) => Category(
    key: j['key'] as String,
    label: j['label'] as String,
    icon: j['icon'] as String,
  );
}

/// 招牌互动（桌面版 `FEATURES` 28 条）。
/// kind=`pool` 时 target 是分类名；kind=`action` 时 target 是动作 id。
class Feature {
  final String key;
  final String label;
  final String icon;
  final String kind;
  final String target;
  const Feature({
    required this.key,
    required this.label,
    required this.icon,
    required this.kind,
    required this.target,
  });

  factory Feature.fromJson(Map<String, dynamic> j) => Feature(
    key: j['key'] as String,
    label: j['label'] as String,
    icon: j['icon'] as String,
    kind: j['kind'] as String,
    target: j['target'] as String,
  );
}

/// 整份 manifest。
///
/// 刻意**不 import Flutter**。加载通过 [Manifest.parse] 传入字符串，
/// 由调用方决定从 asset、文件还是网络读。这样这个文件可以在纯 Dart VM
/// 里跑（`dart run tool/verify_core.dart`），测试不用被 flutter_test 卡住。
class Manifest {
  final int version;
  final int stageW;
  final int stageH;
  final int fps;
  final int alphaHitThreshold;
  final List<Category> categories;
  final List<Feature> features;
  final List<Clip> clips;

  Manifest({
    required this.version,
    required this.stageW,
    required this.stageH,
    required this.fps,
    required this.alphaHitThreshold,
    required this.categories,
    required this.features,
    required this.clips,
  });

  /// 从 JSON 字符串解析。纯函数，无 IO。
  factory Manifest.parse(String raw) {
    final j = jsonDecode(raw) as Map<String, dynamic>;
    final stage = j['stage'] as Map<String, dynamic>;
    return Manifest(
      version: j['version'] as int,
      stageW: stage['w'] as int,
      stageH: stage['h'] as int,
      fps: j['fps'] as int,
      alphaHitThreshold: j['alphaHitThreshold'] as int,
      categories: (j['categories'] as List)
          .map((e) => Category.fromJson(e as Map<String, dynamic>))
          .toList(),
      features: (j['features'] as List)
          .map((e) => Feature.fromJson(e as Map<String, dynamic>))
          .toList(),
      clips: (j['clips'] as List)
          .map((e) => Clip.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }

  /// 画布宽高比。WebView 要按这个比例显示，否则角色会被拉伸。
  double get aspectRatio => stageW / stageH;
}
