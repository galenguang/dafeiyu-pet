import 'dart:math';

import 'memory_model.dart';
import 'memory_recall.dart';
import 'memory_store.dart';

/// 把记忆变成人格化台词。
///
/// 对应桌面版 `app/memory/voice.py`，但砍掉了「按比例混入」的复杂调度——
/// 手机上气泡空间更小，一句记着的事就够，不需要 35% 这种配额控制。
///
/// 三条底线（和桌面版一致）：
/// - 亲密度低于 60 不提负面记忆（"你上次说讨厌加班"听着像挖苦）
/// - 超过两周的旧事不再翻出来
/// - 每句不超过 30 字，气泡一行放得下
class MemoryVoice {
  static const int maxLine = 30;
  static const double eventMaxAgeDays = 14;

  /// 认识天数的纪念节点。
  static const Set<int> anniversaries = {7, 30, 50, 100, 200, 300, 365, 500};

  /// 深夜时段（23:30 - 04:00）。
  static bool isLateNight(DateTime now) {
    final m = now.hour * 60 + now.minute;
    return m >= 23 * 60 + 30 || m < 4 * 60;
  }

  /// 根据场景挑一句和记忆有关的台词。没有合适的返回 null，
  /// 调用方就退回普通台词池。
  static String? pick(
    MemoryStore store,
    String situation, {
    String? last,
    Random? rng,
    DateTime? nowOverride,
  }) {
    if (store.isEmpty) return null;
    final now = nowOverride ?? DateTime.now();
    final cands = candidates(store, situation, now: now);
    if (cands.isEmpty) return null;
    final r = rng ?? Random();

    // 优先挑和上一句不同的
    for (var i = 0; i < 5; i++) {
      final s = cands[r.nextInt(cands.length)];
      if (s != last) return s;
    }
    return cands[r.nextInt(cands.length)];
  }

  static List<String> candidates(
    MemoryStore store,
    String situation, {
    DateTime? now,
  }) {
    final t = now ?? DateTime.now();
    switch (situation) {
      case 'greet':
        return _greet(store, t);
      case 'click':
        return _click(store, t);
      case 'idle':
        return _idle(store, t);
      default:
        return const [];
    }
  }

  static String _clip(String s) =>
      s.length <= maxLine ? s : '${s.substring(0, maxLine - 1)}…';

  // ---------------- 启动问候 ----------------
  static List<String> _greet(MemoryStore store, DateTime now) {
    final out = <String>[];

    // 认识天数的整数节点优先——这是最容易让人「哦」一下的一句
    final days = store.daysTogether;
    if (anniversaries.contains(days)) {
      out.add(_clip('认识你$days天啦'));
    }

    final late = MemoryRecall.recentEpisode(store, kinds: {'late_night'});
    if (late != null) {
      final age = MemModel.daysSince((late['created'] ?? '').toString(), now);
      if (age <= 3 && isLateNight(now)) {
        out.add(_clip('又是这个点，昨天也没睡好'));
      } else if (age <= 7) {
        out.add(_clip('最近老熬夜，眼圈都黑了'));
      }
    }

    final name = store.callMe;
    if (name.isNotEmpty && now.hour >= 5 && now.hour < 11) {
      out.add(_clip('$name，早'));
    }

    final like = MemoryRecall.topLike(store);
    if (like.isNotEmpty) {
      if (now.hour >= 6 && now.hour < 10) {
        out.add(_clip('要不要来点$like'));
      } else if (now.hour >= 20 && now.hour < 24) {
        out.add(_clip('今天$like了吗'));
      }
    }

    final fact = MemoryRecall.topFact(store);
    if (fact != null) {
      final text = fact['text'].toString();
      if (text.length <= 16) {
        out.add(_clip('我还记着你$text呢'));
      }
    }
    return out;
  }

  // ---------------- 被戳 ----------------
  static List<String> _click(MemoryStore store, DateTime now) {
    final out = <String>[];
    final like = MemoryRecall.topLike(store);
    if (like.isNotEmpty) {
      out.add(_clip('又想$like了？'));
      out.add(_clip('戳我也没$like喝'));
    }
    final dislike = MemoryRecall.topDislike(store);
    if (dislike.isNotEmpty && store.intimacy >= 60) {
      out.add(_clip('$dislike的事先放放'));
    }
    final fact = MemoryRecall.topFact(store);
    if (fact != null) {
      final t = fact['text'].toString();
      if (t.length <= 14) out.add(_clip('你说的$t我还记着'));
    }
    return out;
  }

  // ---------------- 空闲 ----------------
  static List<String> _idle(MemoryStore store, DateTime now) {
    final out = <String>[];
    final ep = MemoryRecall.recentEpisode(store, kinds: {'event', 'promise'});
    if (ep != null) {
      final age = MemModel.daysSince((ep['created'] ?? '').toString(), now);
      if (age <= eventMaxAgeDays) {
        final text = ep['text'].toString();
        if (text.length <= 20) out.add(_clip('$text，还记得吗'));
      }
    }
    if (isLateNight(now)) {
      out.add(_clip('这个点了，该睡了'));
    }
    final habit = store.profile['habits'];
    if (habit is List && habit.isNotEmpty) {
      final h = MemModel.asMap(habit.first)['text']?.toString() ?? '';
      if (h.length >= 2 && h.length <= 12) {
        out.add(_clip('你老是$h'));
      }
    }
    return out;
  }
}
