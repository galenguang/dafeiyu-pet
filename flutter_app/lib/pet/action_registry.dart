import 'dart:math';

import 'manifest.dart';

/// 动作注册表：按 id / 分类 / 类型索引，并提供加权随机抽取。
///
/// 对应桌面版 `app/actions.py` 的 `ACTIONS` / `by_cat` / `by_kind`。
/// 唯一的区别是数据来源从 Python 字面量换成了 manifest.json。
class ActionRegistry {
  final List<Clip> all;
  final List<Category> categories;
  final List<Feature> features;

  /// 画布尺寸（manifest 的 `stage`）。crop / anchor 都是以它为坐标系的。
  final int stageW;
  final int stageH;

  final Map<String, Clip> _byId = {};
  final Map<String, List<Clip>> _byCat = {};
  final Map<String, List<Clip>> _byKind = {};

  ActionRegistry._(
    this.all,
    this.categories,
    this.features,
    this.stageW,
    this.stageH,
  );

  factory ActionRegistry(Manifest m) {
    final reg = ActionRegistry._(
      m.clips,
      m.categories,
      m.features,
      m.stageW,
      m.stageH,
    );
    for (final c in m.clips) {
      reg._byId[c.id] = c;
      (reg._byCat[c.cat] ??= []).add(c);
      (reg._byKind[c.kind] ??= []).add(c);
    }
    return reg;
  }

  int get length => all.length;

  Clip? byId(String id) => _byId[id];

  /// 按分类取。别名 `muse` 在桌面版里既是分类也是动作 id，这里不做特判，
  /// 调用方想取动作就用 [byId]。
  List<Clip> byCat(String cat) => _byCat[cat] ?? const [];

  List<Clip> byKind(String kind) => _byKind[kind] ?? const [];

  /// 待机动作。桌面版约定是 `kind == 'idle'` 的那几条，
  /// 其中 `idle` 本身是 loop，用来撑住"什么都没发生"的时刻。
  Clip get idle => _byId['idle'] ?? all.first;

  /// 对应桌面版 `ACT_DURATION.get(id, 9.6)`——但那边是猜的默认值，
  /// 这里用 manifest 里的实测帧数算，不再有 0.44s 尾巴多播的问题。
  int durationMsOf(String id) => _byId[id]?.durationMs ?? 10000;

  /// 加权随机，并尽量避开 [avoid]（通常是刚播完的那个）。
  ///
  /// 为什么不用简单的「抽到重复就重抽」：当某个分类里只有 2 条、
  /// 其中一条权重是另一条的 10 倍时，重抽会让低权重那条几乎永远抽不到。
  /// 这里改成先把 avoid 那条摘出去，在剩下的里按权重抽——
  /// 只要池子里还有别的，就一定会换个动作。
  Clip pickWeighted(List<Clip> pool, {String? avoid, Random? rng}) {
    if (pool.isEmpty) return idle;
    var candidates = pool;
    if (avoid != null && pool.length > 1) {
      final filtered = pool.where((c) => c.id != avoid).toList();
      if (filtered.isNotEmpty) candidates = filtered;
    }
    final r = rng ?? Random();
    final total = candidates.fold<double>(0, (s, c) => s + max(0, c.weight));
    if (total <= 0) return candidates[r.nextInt(candidates.length)];
    var x = r.nextDouble() * total;
    for (final c in candidates) {
      x -= max(0, c.weight);
      if (x <= 0) return c;
    }
    return candidates.last;
  }

  /// 从某个分类里抽。分类不存在就退回待机。
  Clip pickFromCat(String cat, {String? avoid, Random? rng}) =>
      pickWeighted(byCat(cat), avoid: avoid, rng: rng);

  /// 抽一条普通动作（`kind == 'act'`）。
  /// `cat` 为 null 时从全库抽。没有 `act` 类型的就退回全库，
  /// 避免某个分类里全是 idle/move 时抽到空。
  Clip randomAct({String? cat, String? avoid, Random? rng}) {
    final pool = cat == null
        ? byKind('act')
        : byCat(cat).where((c) => c.kind == 'act').toList();
    return pickWeighted(
      pool.isEmpty ? all : pool.toList(),
      avoid: avoid,
      rng: rng,
    );
  }

  /// 全部片段 crop 的并集 `[x, y, w, h]`（画布坐标）。
  ///
  /// 实测这个并集是 634x360——被烟花、蝶舞光环、吹气球这几条特效撑满了，
  /// 和整幅画布几乎没区别。所以**没有拿它做裁切渲染**：裁了也放不大多少，
  /// 反而会把那 12 条特效片段的边缘切掉。留着它是给以后做图集方案参考。
  List<int> get cropUnion {
    if (all.isEmpty) return [0, 0, stageW, stageH];
    var x0 = 1 << 30, y0 = 1 << 30, x1 = 0, y1 = 0;
    for (final c in all) {
      final x = c.crop[0], y = c.crop[1], w = c.crop[2], h = c.crop[3];
      if (x < x0) x0 = x;
      if (y < y0) y0 = y;
      if (x + w > x1) x1 = x + w;
      if (y + h > y1) y1 = y + h;
    }
    return [x0, y0, x1 - x0, y1 - y0];
  }

  /// 包围盒里真正有像素的部分占多大比例。
  ///
  /// 实测出来的：idle 的 crop 宽度是画布的 38.9%，但手机上量实际渲染出的
  /// 轮廓只有 33.1%（`build/shots/a1.png` / `g1.png` 的像素统计）。
  /// crop 是带余量的包围盒，比轮廓宽一圈，这圈得折掉——
  /// 否则「大小 55%」实际只显示成 46%，滑块上的数字就是骗人的。
  static const double _cropPad = 0.85;

  /// 角色本体在画布里的横向占比（用待机动作的包围盒算，已折掉包围盒余量）。
  ///
  /// 两个用途：算「拖动时她最多能贴到多边上」，以及把设置里的
  /// 「她占屏幕多宽」换算成渲染框尺寸。两处都该按**看得见的轮廓**算，
  /// 所以这里用折过余量的值。
  /// 用 idle 而不是并集，理由同上——并集等于整幅，算出来就是 100%，没意义。
  double get charWFrac {
    final c = idle.crop;
    if (c.length < 4 || c[2] <= 0 || stageW <= 0) return 0.34;
    return (c[2] / stageW * _cropPad).clamp(0.1, 1.0);
  }

  /// 展开一条招牌互动成一个具体动作。
  /// `pool` 类型按分类抽，`action` 类型直接对应某条动作。
  Clip? resolveFeature(Feature f, {String? avoid, Random? rng}) {
    if (f.kind == 'action') return byId(f.target);
    if (f.kind == 'pool') return pickFromCat(f.target, avoid: avoid, rng: rng);
    // special 类型（如 look_balance）在手机版没有对应能力，返回 null
    return null;
  }
}
