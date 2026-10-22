import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';


import 'memory_model.dart';

/// 记忆的持久化与增删改。
///
/// 对应桌面版 `app/memory/store.py`。三条纪律：
///
/// 1. **add-only**：模型抽出来的东西只能新增，不能改也不能删已有记忆。
///    一个抽错的模型最多多记一条废话，不能把「主人叫小明」改成「小强」。
/// 2. **去重**：同一件事反复说（"我喜欢咖啡" × 10）只留一条，涨权重。
/// 3. **节流落盘**：聊天时每秒可能写好几次，攒 2 秒再写，
///    但 [flush] 随时可以强制写（App 退到后台时调）。
class MemoryStore {
  Map<String, dynamic> data;

  /// 文件路径。为空表示只在内存里（测试用）。
  final String? path;

  bool _dirty = false;
  Timer? _saveTimer;

  /// 相似到这个程度就算同一条。
  static const double duplicateJaccard = 0.72;

  MemoryStore(this.data, {this.path});

  static Future<MemoryStore> open(String path) async {
    Map<String, dynamic> raw = {};
    try {
      final f = File(path);
      if (f.existsSync()) {
        final s = await f.readAsString();
        final j = jsonDecode(s);
        if (j is Map) raw = Map<String, dynamic>.from(j);
      }
    } catch (e) {
      MemLog.write('[memory] 读取失败，按空记忆处理: $e');
    }
    final m = MemModel.normalize(raw);
    MemModel.touchDay(m);
    final st = MemoryStore(m, path: path);
    st._saveNow();
    return st;
  }

  /// 内存实例，不落盘。给单测和「预览」用。
  factory MemoryStore.memory([Map<String, dynamic>? raw]) {
    final m = MemModel.normalize(raw ?? {});
    MemModel.touchDay(m);
    return MemoryStore(m);
  }

  // ---------------- 读 ----------------
  Map<String, dynamic> get profile => MemModel.asMap(data['profile']);

  Map<String, dynamic> get relationship => MemModel.asMap(data['relationship']);

  List<Map<String, dynamic>> get episodes => MemModel.asList(data['episodes']);

  List<Map<String, dynamic>> get facts => MemModel.asList(profile['facts']);

  List<Map<String, dynamic>> get likes => MemModel.asList(profile['likes']);

  List<Map<String, dynamic>> get dislikes =>
      MemModel.asList(profile['dislikes']);

  String get callMe => (profile['call_me'] ?? '').toString();

  int get intimacy => MemModel.asInt(relationship['intimacy'], lo: 0, hi: 100);

  int get daysTogether =>
      MemModel.asInt(relationship['days_together'], def: 1, lo: 1);

  bool get isEmpty {
    final p = profile;
    return (p['call_me'] ?? '') == '' &&
        (p['occupation'] ?? '') == '' &&
        likes.isEmpty &&
        dislikes.isEmpty &&
        facts.isEmpty &&
        episodes.isEmpty;
  }

  // ---------------- 写 ----------------
  void _touch() {
    data['updated_at'] = MemModel.nowIso();
    _dirty = true;
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(seconds: 2), _saveNow);
  }

  void _saveNow() {
    _saveTimer?.cancel();
    _saveTimer = null;
    if (!_dirty) return;
    _dirty = false;
    final p = path;
    if (p == null) return;
    try {
      // 写临时文件再 rename：直接覆写时被杀进程会留下半截 JSON
      final tmp = File('$p.tmp');
      tmp.writeAsStringSync(jsonEncode(data), flush: true);
      tmp.renameSync(p);
    } catch (e) {
      MemLog.write('[memory] 保存失败: $e');
      _dirty = true;
    }
  }

  /// 立刻落盘（退到后台 / 退出前调）。
  Future<void> flush() async => _saveNow();

  void noteChat({String userText = '', String reply = ''}) {
    final rel = relationship;
    rel['total_chats'] = MemModel.asInt(rel['total_chats']) + 1;
    data['relationship'] = rel;
    final stats = MemModel.asMap(data['stats']);
    final counters = MemModel.asMap(stats['counters']);
    counters['chats_total'] = MemModel.asInt(counters['chats_total']) + 1;
    stats['counters'] = counters;
    data['stats'] = stats;
    _touch();
  }

  void bumpIntimacy([int delta = 1]) {
    MemModel.bumpIntimacy(data, delta);
    _touch();
  }

  /// 设置称呼。模型和用户都可以改，所以这条不是 add-only。
  void setCallMe(String name) {
    final t = MemModel.clipText(name, 12);
    if (t.isEmpty) return;
    final p = profile;
    p['call_me'] = t;
    data['profile'] = p;
    _touch();
  }

  void setOccupation(String job) {
    final t = MemModel.clipText(job, 16);
    if (t.isEmpty) return;
    final p = profile;
    p['occupation'] = t;
    data['profile'] = p;
    _touch();
  }

  /// 加一条事实。`pinned` 的永不淘汰，也不会被模型覆盖。
  bool addFact(
    String text, {
    double confidence = 0.7,
    String source = 'chat',
    bool pinned = false,
  }) {
    final t = MemModel.clipText(text);
    if (t.length < 2) return false;
    final p = profile;
    final list = MemModel.asList(p['facts']);

    for (final f in list) {
      final old = (f['text'] ?? '').toString();
      if (MemModel.normalizeText(old) == MemModel.normalizeText(t) ||
          MemModel.jaccard(old, t) >= duplicateJaccard ||
          MemModel.containsEither(old, t)) {
        // 已有：只抬置信度和权重，不改文字
        f['confidence'] = max(
          MemModel.asDouble(f['confidence'], def: 0.7),
          confidence,
        );
        if (pinned) f['pinned'] = true;
        p['facts'] = list;
        data['profile'] = p;
        _touch();
        return false;
      }
    }

    list.add({
      'id': MemModel.newId('f', list),
      'text': t,
      'confidence': confidence.clamp(0.0, 1.0),
      'source': source,
      'pinned': pinned,
      'created': MemModel.nowIso(),
    });
    while (list.length > MemModel.maxFacts) {
      // 先淘汰置信度最低、且没置顶的
      final idx = list.indexWhere((f) => f['pinned'] != true);
      list.removeAt(idx < 0 ? 0 : idx);
    }
    p['facts'] = list;
    data['profile'] = p;
    _touch();
    return true;
  }

  bool _addWeighted(String field, String text, {double weight = 0.6}) {
    final t = MemModel.clipText(text, 14);
    if (t.length < 2) return false;
    final p = profile;
    final list = MemModel.asList(p[field]);
    for (final it in list) {
      final old = (it['text'] ?? '').toString();
      if (MemModel.normalizeText(old) == MemModel.normalizeText(t) ||
          MemModel.jaccard(old, t) >= duplicateJaccard) {
        it['weight'] = (MemModel.asDouble(it['weight'], def: 0.6) + 0.15).clamp(
          0.0,
          1.0,
        );
        p[field] = list;
        data['profile'] = p;
        _touch();
        return false;
      }
    }
    list.add({'text': t, 'weight': weight.clamp(0.0, 1.0)});
    final limit = field == 'likes'
        ? MemModel.maxLikes
        : (field == 'dislikes' ? MemModel.maxDislikes : MemModel.maxHabits);
    while (list.length > limit) {
      list.removeAt(0); // 老的先走
    }
    p[field] = list;
    data['profile'] = p;
    _touch();
    return true;
  }

  bool addLike(String text) => _addWeighted('likes', text);
  bool addDislike(String text) => _addWeighted('dislikes', text);
  bool addHabit(String text) => _addWeighted('habits', text, weight: 0.5);

  bool addEpisode(
    String text, {
    String kind = 'event',
    String mood = 'normal',
    double weight = 0.6,
  }) {
    final t = MemModel.clipText(text);
    if (t.length < 2) return false;
    final eps = episodes;
    for (final e in eps) {
      final old = (e['text'] ?? '').toString();
      if (MemModel.normalizeText(old) == MemModel.normalizeText(t) ||
          MemModel.jaccard(old, t) >= duplicateJaccard) {
        e['weight'] = (MemModel.asDouble(e['weight'], def: 0.6) + 0.15).clamp(
          0.0,
          1.0,
        );
        data['episodes'] = eps;
        _touch();
        return false;
      }
    }
    eps.insert(0, {
      'id': MemModel.newId('e', eps),
      'text': t,
      'kind': kind,
      'mood': mood,
      'weight': weight.clamp(0.0, 1.0),
      'created': MemModel.nowIso(),
    });
    data['episodes'] = eps;
    MemModel.evictEpisodes(data);
    final stats = MemModel.asMap(data['stats']);
    final counters = MemModel.asMap(stats['counters']);
    counters['episodes_added'] = MemModel.asInt(counters['episodes_added']) + 1;
    stats['counters'] = counters;
    data['stats'] = stats;
    _touch();
    return true;
  }

  // ---------------- 面板操作 ----------------
  void removeFact(String id) {
    final p = profile;
    final list = MemModel.asList(p['facts'])..removeWhere((f) => f['id'] == id);
    p['facts'] = list;
    data['profile'] = p;
    _touch();
  }

  void togglePin(String id) {
    final p = profile;
    final list = MemModel.asList(p['facts']);
    for (final f in list) {
      if (f['id'] == id) f['pinned'] = f['pinned'] != true;
    }
    p['facts'] = list;
    data['profile'] = p;
    _touch();
  }

  void removeEpisode(String id) {
    final eps = episodes..removeWhere((e) => e['id'] == id);
    data['episodes'] = eps;
    _touch();
  }

  void clearAll() {
    final keepFirst = (relationship['first_meet'] ?? MemModel.todayStr())
        .toString();
    data = MemModel.defaultMemory();
    final rel = relationship;
    rel['first_meet'] = keepFirst;
    rel['days_together'] = max(1, MemModel.daysBetween(keepFirst) + 1);
    data['relationship'] = rel;
    _dirty = true;
    _saveNow();
  }

  /// 计数：抽取跑了几次、失败几次。
  void countExtract({bool ok = true}) {
    final stats = MemModel.asMap(data['stats']);
    final counters = MemModel.asMap(stats['counters']);
    counters['extract_runs'] = MemModel.asInt(counters['extract_runs']) + 1;
    if (!ok) {
      counters['extract_fail'] = MemModel.asInt(counters['extract_fail']) + 1;
    }
    stats['counters'] = counters;
    data['stats'] = stats;
    _touch();
  }

  void dispose() {
    _saveNow();
    _saveTimer?.cancel();
  }
}
