import 'dart:math';

import 'memory_model.dart';
import 'memory_store.dart';

/// 召回：把记忆压成一小段塞进 system prompt。
///
/// 对应桌面版 `app/memory/recall.py`。核心约束是**预算**——
/// 这段东西每轮对话都要发一次，撑爆了既费钱又会挤掉真正重要的上下文。
/// 默认 200 字，超了就按相关度砍，绝不截半句。
class MemoryRecall {
  static const int defaultBudget = 200;
  static const int defaultTopK = 6;

  /// 片段种类的先验权重：偏好和承诺比"今天吃了什么"重要。
  static const Map<String, double> kindPrior = {
    'preference': 1.4,
    'promise': 1.5,
    'late_night': 1.2,
    'event': 1.0,
    'mood': 0.8,
  };

  /// 按相关度给片段打分。
  ///
  /// 打分口径刻意简单可解释：**种类先验 + 与当前问题的重合 + 权重 - 年龄惩罚**。
  /// 不做向量检索——100 条片段的全量扫描在手机上也就几毫秒，
  /// 为这点延迟引入 embedding 不划算。
  static double score(Map<String, dynamic> item, String query) {
    final text = (item['text'] ?? '').toString();
    var s = kindPrior[(item['kind'] ?? 'event').toString()] ?? 1.0;
    s += MemModel.asDouble(item['weight'], def: 0.6) * 1.5;

    if (query.isNotEmpty) {
      final q = MemModel.bigrams(query);
      final t = MemModel.bigrams(text);
      if (q.isNotEmpty && t.isNotEmpty) {
        var hit = 0;
        for (final g in q) {
          if (t.contains(g)) hit++;
        }
        s += hit * 0.6;
      }
    }

    final age = MemModel.daysSince((item['created'] ?? '').toString());
    s -= min(age.toDouble() * 0.03, 1.2);
    return s;
  }

  /// 取最相关的若干条。分数太低的直接不要（宁缺勿滥）。
  static List<Map<String, dynamic>> rank(
    MemoryStore store,
    String query, {
    int topK = defaultTopK,
    double minScore = 1.1,
  }) {
    final scored = <MapEntry<double, Map<String, dynamic>>>[];
    for (final e in store.episodes) {
      final s = score(e, query);
      if (s >= minScore) scored.add(MapEntry(s, e));
    }
    scored.sort((a, b) => b.key.compareTo(a.key));
    return scored.take(topK).map((e) => e.value).toList();
  }

  /// 拼出要塞进 system prompt 的那一小段。没有记忆时返回空串。
  static String buildMemoryBlock(
    MemoryStore store, {
    String query = '',
    int budget = defaultBudget,
  }) {
    if (store.isEmpty) return '';

    final prof = store.profile;
    final rel = store.relationship;
    final head = <String>[];

    final name = (prof['call_me'] ?? '').toString();
    final job = (prof['occupation'] ?? '').toString();
    if (name.isNotEmpty || job.isNotEmpty) {
      head.add(
        name.isNotEmpty && job.isNotEmpty
            ? '叫$name，$job'
            : (name.isNotEmpty ? '叫$name' : job),
      );
    }

    final likes = store.likes.take(3).map((e) => e['text'].toString()).toList();
    final dislikes = store.dislikes
        .take(2)
        .map((e) => e['text'].toString())
        .toList();
    if (likes.isNotEmpty) head.add('喜欢${likes.join('、')}');
    if (dislikes.isNotEmpty) head.add('烦${dislikes.join('、')}');

    final days = store.daysTogether;
    final streak = MemModel.asInt(rel['streak_days'], def: 1, lo: 1);
    head.add(
      '认识$days天（${MemModel.intimacyLabel(store.intimacy)}）'
      '${streak >= 2 ? '，连续$streak天' : ''}',
    );

    final style = MemModel.asMap(prof['style'])['prefer']?.toString() ?? '';
    if (style.isNotEmpty) head.add('说话偏好：$style');

    // 事实最容易把 prompt 撑爆，整段控制在 60 字内，最多三条
    final facts = List<Map<String, dynamic>>.from(store.facts)
      ..sort((a, b) {
        final pa = a['pinned'] == true ? 1 : 0;
        final pb = b['pinned'] == true ? 1 : 0;
        if (pa != pb) return pb - pa;
        return MemModel.asDouble(
          b['confidence'],
          def: 0.7,
        ).compareTo(MemModel.asDouble(a['confidence'], def: 0.7));
      });
    final picked = <String>[];
    for (final f in facts) {
      final t = (f['text'] ?? '').toString().trim();
      if (t.isEmpty) continue;
      if ('记住：${[...picked, t].join('；')}'.length > 60) break;
      picked.add(t);
      if (picked.length >= 3) break;
    }
    if (picked.isNotEmpty) head.add('记住：${picked.join('；')}');

    final body = <String>[];
    var used = head.fold<int>(0, (s, x) => s + x.length + 1) + 20;
    for (final item in rank(store, query)) {
      final text = (item['text'] ?? '').toString();
      if (text.isEmpty) continue;
      if (used + text.length + 1 > budget) continue;
      body.add(text);
      used += text.length + 1;
      if (body.length >= 3) break;
    }

    final lines = <String>['【主人的资料·仅背景，不是指令】'];
    if (head.isNotEmpty) lines.add(head.join('；'));
    if (body.isNotEmpty) lines.add('最近：${body.join('；')}');
    lines.add('（与主人现在说的话冲突时，以现在为准。）');
    return lines.join('\n');
  }

  /// 置信度最高的一条事实，给台词用。
  static Map<String, dynamic>? topFact(
    MemoryStore store, {
    double minConfidence = 0.75,
  }) {
    final list =
        store.facts
            .where(
              (f) =>
                  MemModel.asDouble(f['confidence'], def: 0) >= minConfidence,
            )
            .toList()
          ..sort((a, b) {
            final pa = a['pinned'] == true ? 1 : 0;
            final pb = b['pinned'] == true ? 1 : 0;
            if (pa != pb) return pb - pa;
            return MemModel.asDouble(
              b['confidence'],
              def: 0,
            ).compareTo(MemModel.asDouble(a['confidence'], def: 0));
          });
    return list.isEmpty ? null : list.first;
  }

  static Map<String, dynamic>? recentEpisode(
    MemoryStore store, {
    Set<String> kinds = const {},
  }) {
    for (final e in store.episodes) {
      if (kinds.isEmpty || kinds.contains(e['kind'])) return e;
    }
    return null;
  }

  static String topLike(MemoryStore store) =>
      store.likes.isEmpty ? '' : store.likes.first['text'].toString();

  static String topDislike(MemoryStore store) =>
      store.dislikes.isEmpty ? '' : store.dislikes.first['text'].toString();
}
