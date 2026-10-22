import 'dart:math';

/// 极简日志。
///
/// 刻意**不用** Flutter 的 `debugPrint`——那会把整个记忆层绑死在 Flutter 上，
/// `dart run tool/verify_memory.dart` 就跑不起来。而这一层（数据归一、去重、
/// 抽取、召回）恰恰是最需要脱离 UI 单测的部分：1000 行移植逻辑，靠点真机
/// 是测不出「相似度阈值是不是写错了」的。
class MemLog {
  static bool enabled = true;

  static void write(String msg) {
    if (!enabled) return;
    // ignore: avoid_print
    print(msg);
  }
}

/// 记忆的数据模型与文本工具。
///
/// **JSON 结构与桌面版 `app/memory/schema.py` 完全一致**（同名 key、同默认值），
/// 这样两边的 `memory.json` 可以互相拷来拷去。改字段前先看那边。
class MemModel {
  static const int schemaVersion = 1;

  /// 各类条目的上限。超了就按「权重 × 新鲜度」淘汰。
  static const int maxEpisodes = 200;
  static const int maxFacts = 60;
  static const int maxLikes = 30;
  static const int maxDislikes = 30;
  static const int maxHabits = 30;

  /// 单条文本上限。气泡和 prompt 都放不下长句。
  static const int maxText = 40;

  static String nowIso() => DateTime.now().toIso8601String();

  static String todayStr([DateTime? d]) {
    final t = d ?? DateTime.now();
    return '${t.year.toString().padLeft(4, '0')}-'
        '${t.month.toString().padLeft(2, '0')}-'
        '${t.day.toString().padLeft(2, '0')}';
  }

  /// 两个日期之间差几天（按自然日，不是 24 小时）。
  static int daysBetween(String fromYmd, [DateTime? now]) {
    try {
      final p = fromYmd.split('-').map(int.parse).toList();
      final from = DateTime(p[0], p[1], p[2]);
      final t = now ?? DateTime.now();
      final to = DateTime(t.year, t.month, t.day);
      return to.difference(from).inDays;
    } catch (_) {
      return 0;
    }
  }

  static int daysSince(String iso, [DateTime? now]) {
    try {
      final from = DateTime.parse(iso);
      return (now ?? DateTime.now()).difference(from).inDays;
    } catch (_) {
      return 9999;
    }
  }

  static String clipText(String s, [int n = maxText]) {
    final t = s.replaceAll(RegExp(r'\s+'), ' ').trim();
    return t.length <= n ? t : '${t.substring(0, n - 1)}…';
  }

  static final _punct = RegExp(
    r'[\s\u3000,，。.、!！?？;；:：\"'
    r"'“”‘’()（）\[\]【】<>《》…~～\-—_/\\|]+",
  );

  /// 归一化：去标点、去空白、转小写。去重和相似度都基于它。
  static String normalizeText(String s) =>
      s.toLowerCase().replaceAll(_punct, '');

  static final _leadPronoun = RegExp(r'^(我|俺|咱|本人)(的|是|在|有|喜欢|爱|讨厌|烦|叫|想)?');

  /// 模型和规则抽出来的常常带「我」，而记忆要用第三人称存。
  static String stripFirstPerson(String s) =>
      s.replaceFirst(_leadPronoun, '').trim();

  /// 中文没有空格，按字符二元组做相似度的粒度刚好。
  static Set<String> bigrams(String s) {
    final t = normalizeText(s);
    if (t.length < 2) return t.isEmpty ? {} : {t};
    final out = <String>{};
    for (var i = 0; i + 1 < t.length; i++) {
      out.add(t.substring(i, i + 2));
    }
    return out;
  }

  static double jaccard(String a, String b) {
    final x = bigrams(a), y = bigrams(b);
    if (x.isEmpty || y.isEmpty) return 0;
    var inter = 0;
    for (final g in x) {
      if (y.contains(g)) inter++;
    }
    final union = x.length + y.length - inter;
    return union == 0 ? 0 : inter / union;
  }

  /// 一方包含另一方（"喝咖啡" vs "咖啡"）也算重复。
  static bool containsEither(String a, String b) {
    final x = normalizeText(a), y = normalizeText(b);
    if (x.isEmpty || y.isEmpty) return false;
    if (x.length < 2 || y.length < 2) return false;
    return x.contains(y) || y.contains(x);
  }

  static String intimacyLabel(int v) {
    if (v >= 80) return '自己人';
    if (v >= 60) return '很熟了';
    if (v >= 40) return '算朋友';
    if (v >= 20) return '有点眼熟';
    return '还不太熟';
  }

  static String newId(String prefix, List<Map<String, dynamic>> items) {
    var maxN = 0;
    final re = RegExp('^$prefix(\\d+)\$');
    for (final it in items) {
      final m = re.firstMatch((it['id'] ?? '').toString());
      if (m != null) {
        final n = int.tryParse(m.group(1)!) ?? 0;
        if (n > maxN) maxN = n;
      }
    }
    return '$prefix${maxN + 1}';
  }

  static Map<String, dynamic> defaultMemory() {
    final today = todayStr();
    return {
      'schema': schemaVersion,
      'updated_at': nowIso(),
      'profile': {
        'call_me': '',
        'occupation': '',
        'likes': <Map<String, dynamic>>[],
        'dislikes': <Map<String, dynamic>>[],
        'habits': <Map<String, dynamic>>[],
        'style': {'prefer': '', 'avoid': <String>[], 'updated_at': null},
        'facts': <Map<String, dynamic>>[],
      },
      'relationship': {
        'first_meet': today,
        'days_together': 1,
        'streak_days': 1,
        'last_seen_date': today,
        'intimacy': 0,
        'mood': 'normal',
        'total_chats': 0,
        'nickname_for_pet': '',
      },
      'episodes': <Map<String, dynamic>>[],
      'stats': {
        'late_nights': {'total': 0, 'last_date': null, 'consecutive': 0},
        'daily': <String, dynamic>{},
        'counters': {
          'chats_total': 0,
          'extract_runs': 0,
          'extract_fail': 0,
          'episodes_added': 0,
        },
      },
      'meta': {'spoken': <String, dynamic>{}},
    };
  }

  // ---------------- 容错归一 ----------------
  // 文件可能被用户手改过、或者从桌面版拷过来时字段缺失，
  // 这里逐项兜底，任何畸形输入都不该让 App 起不来。

  static Map<String, dynamic> asMap(dynamic v) =>
      v is Map ? Map<String, dynamic>.from(v) : <String, dynamic>{};

  static List<Map<String, dynamic>> asList(dynamic v) {
    if (v is! List) return [];
    return v.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList();
  }

  static String asStr(dynamic v, [int n = maxText]) =>
      v is String ? clipText(v, n) : '';

  static double asDouble(dynamic v, {double def = 0.5}) {
    final d = v is num ? v.toDouble() : def;
    return d.clamp(0.0, 1.0);
  }

  static int asInt(dynamic v, {int def = 0, int lo = 0, int hi = 1000000}) {
    final d = v is num ? v.toInt() : def;
    return d.clamp(lo, hi);
  }

  static bool asBool(dynamic v, {bool def = false}) => v is bool ? v : def;

  /// 整份归一化。缺什么补什么，多的丢掉。
  static Map<String, dynamic> normalize(dynamic raw) {
    final base = defaultMemory();
    final m = asMap(raw);
    if (m.isEmpty) return base;

    final prof = asMap(m['profile']);
    final rel = asMap(m['relationship']);
    final stats = asMap(m['stats']);
    final meta = asMap(m['meta']);

    Map<String, dynamic> weighted(Map<String, dynamic> src) => {
      'text': asStr(src['text']),
      'weight': asDouble(src['weight'], def: 0.6),
    };

    List<Map<String, dynamic>> weightedList(dynamic v, int limit) {
      final seen = <String>{};
      final out = <Map<String, dynamic>>[];
      for (final it in asList(v)) {
        final w = weighted(it);
        final t = w['text'] as String;
        if (t.length < 2) continue;
        if (!seen.add(normalizeText(t))) continue;
        out.add(w);
        if (out.length >= limit) break;
      }
      return out;
    }

    final facts = <Map<String, dynamic>>[];
    for (final it in asList(prof['facts'])) {
      final t = asStr(it['text']);
      if (t.length < 2) continue;
      facts.add({
        'id': asStr(it['id'], 16).isEmpty
            ? newId('f', facts)
            : asStr(it['id'], 16),
        'text': t,
        'confidence': asDouble(it['confidence'], def: 0.7),
        'source': asStr(it['source'], 12).isEmpty
            ? 'chat'
            : asStr(it['source'], 12),
        'pinned': asBool(it['pinned']),
        'created': asStr(it['created'], 40).isEmpty
            ? nowIso()
            : asStr(it['created'], 40),
      });
      if (facts.length >= maxFacts) break;
    }

    final episodes = <Map<String, dynamic>>[];
    for (final it in asList(m['episodes'])) {
      final t = asStr(it['text']);
      if (t.length < 2) continue;
      episodes.add({
        'id': asStr(it['id'], 16).isEmpty
            ? newId('e', episodes)
            : asStr(it['id'], 16),
        'text': t,
        'kind': asStr(it['kind'], 16).isEmpty ? 'event' : asStr(it['kind'], 16),
        'mood': asStr(it['mood'], 12).isEmpty
            ? 'normal'
            : asStr(it['mood'], 12),
        'weight': asDouble(it['weight'], def: 0.6),
        'created': asStr(it['created'], 40).isEmpty
            ? nowIso()
            : asStr(it['created'], 40),
      });
      if (episodes.length >= maxEpisodes) break;
    }

    return {
      'schema': schemaVersion,
      'updated_at': nowIso(),
      'profile': {
        'call_me': asStr(prof['call_me'], 12),
        'occupation': asStr(prof['occupation'], 16),
        'likes': weightedList(prof['likes'], maxLikes),
        'dislikes': weightedList(prof['dislikes'], maxDislikes),
        'habits': weightedList(prof['habits'], maxHabits),
        'style': {
          'prefer': asStr(asMap(prof['style'])['prefer'], 30),
          'avoid': (asMap(prof['style'])['avoid'] is List)
              ? (asMap(prof['style'])['avoid'] as List)
                    .whereType<String>()
                    .take(10)
                    .toList()
              : <String>[],
          'updated_at': asMap(prof['style'])['updated_at'],
        },
        'facts': facts,
      },
      'relationship': {
        'first_meet': asStr(rel['first_meet'], 12).isEmpty
            ? todayStr()
            : asStr(rel['first_meet'], 12),
        'days_together': asInt(rel['days_together'], def: 1, lo: 1),
        'streak_days': asInt(rel['streak_days'], def: 1, lo: 1),
        'last_seen_date': asStr(rel['last_seen_date'], 12).isEmpty
            ? todayStr()
            : asStr(rel['last_seen_date'], 12),
        'intimacy': asInt(rel['intimacy'], lo: 0, hi: 100),
        'mood': asStr(rel['mood'], 12).isEmpty
            ? 'normal'
            : asStr(rel['mood'], 12),
        'total_chats': asInt(rel['total_chats']),
        'nickname_for_pet': asStr(rel['nickname_for_pet'], 12),
      },
      'episodes': episodes,
      'stats': {
        'late_nights': asMap(stats['late_nights']),
        'daily': asMap(stats['daily']),
        'counters': asMap(stats['counters']),
      },
      'meta': {'spoken': asMap(meta['spoken'])},
    };
  }

  /// 跨天检查：更新「认识几天」「连续几天」。
  static void touchDay(Map<String, dynamic> m, [DateTime? now]) {
    final rel = asMap(m['relationship']);
    final today = todayStr(now);
    final last = (rel['last_seen_date'] ?? '').toString();
    if (last == today) return;

    final gap = daysBetween(last, now);
    if (gap == 1) {
      rel['streak_days'] = asInt(rel['streak_days'], def: 1, lo: 1) + 1;
    } else if (gap > 1) {
      rel['streak_days'] = 1;
    }
    rel['last_seen_date'] = today;
    rel['days_together'] = max(
      1,
      daysBetween((rel['first_meet'] ?? today).toString(), now) + 1,
    );
    m['relationship'] = rel;
  }

  /// 亲密度只涨不跌，且有上限。涨得越来越慢，避免聊两句就「自己人」。
  static int bumpIntimacy(Map<String, dynamic> m, [int delta = 1]) {
    final rel = asMap(m['relationship']);
    final cur = asInt(rel['intimacy'], lo: 0, hi: 100);
    // 越高越难涨：60 以后每次只加 1 的一半概率
    var add = delta;
    if (cur >= 80) {
      add = 0;
    } else if (cur >= 60) {
      add = Random().nextDouble() < 0.5 ? 1 : 0;
    }
    final v = (cur + add).clamp(0, 100);
    rel['intimacy'] = v;
    m['relationship'] = rel;
    return v;
  }

  /// 片段超上限时淘汰：权重低、又旧、又没被复述过的先走。
  static int evictEpisodes(Map<String, dynamic> m) {
    final eps = asList(m['episodes']);
    if (eps.length <= maxEpisodes) return 0;
    final now = DateTime.now();
    double score(Map<String, dynamic> e) {
      final w = asDouble(e['weight'], def: 0.6);
      final age = daysSince((e['created'] ?? '').toString(), now).toDouble();
      return w * 100 - age;
    }

    eps.sort((a, b) => score(b).compareTo(score(a)));
    final removed = eps.length - maxEpisodes;
    m['episodes'] = eps.take(maxEpisodes).toList();
    return removed;
  }
}
