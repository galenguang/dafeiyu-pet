import 'dart:convert';


import '../core/settings.dart';
import '../llm/llm_client.dart';
import 'memory_model.dart';
import 'memory_rules.dart';
import 'memory_store.dart';

/// 模型轨：用大模型从对话里抽「值得长期记住」的信息。
///
/// 对应桌面版 `app/memory/extract.py`。三条约束原样保留：
///
/// - **只提候选，不改不删已有记忆**（add-only）。防幻觉覆盖真实画像。
/// - **输出侧硬约束**：模型给的文本要重新清洗，超长/带引号/带换行的直接丢。
/// - **连续失败就本次运行内关掉**，别让用户每次聊天都等一个必然失败的请求。
class MemoryExtractor {
  final MemoryStore store;
  final AppSettings Function() cfg;

  /// 攒够几条用户发言才抽一次。
  int _pending = 0;

  /// 连续失败次数。到 3 次就不再试了（本次运行内）。
  int _failStreak = 0;
  static const int failStreakLimit = 3;

  bool _busy = false;
  bool get busy => _busy;
  bool get disabled => _failStreak >= failStreakLimit;

  /// 攒着待抽的对话文本。
  final List<String> _buffer = [];
  static const int maxBuffer = 24;

  MemoryExtractor(this.store, this.cfg);

  void onUser(String text) {
    _pending++;
    _buffer.add('主人：${MemModel.clipText(text, 60)}');
    while (_buffer.length > maxBuffer) {
      _buffer.removeAt(0);
    }
  }

  void onReply(String text) {
    _buffer.add('你：${MemModel.clipText(text, 40)}');
    while (_buffer.length > maxBuffer) {
      _buffer.removeAt(0);
    }
  }

  bool shouldRun() {
    if (disabled || _busy) return false;
    final c = cfg();
    if (!c.memoryEnabled || !c.memoryLlmExtract) return false;
    if (LlmClient.notReadyReason(c) != null) return false;
    return _pending >= c.memoryExtractEvery;
  }

  /// 立刻抽一次（面板「整理一下」也走这里）。
  Future<int> organizeNow() async {
    if (_busy) return 0;
    final c = cfg();
    if (LlmClient.notReadyReason(c) != null) return 0;
    return _run(c);
  }

  /// 攒够了就抽。调用方不需要 await（不阻塞聊天）。
  Future<int> maybeRun() async {
    if (!shouldRun()) return 0;
    return _run(cfg());
  }

  Future<int> _run(AppSettings c) async {
    if (_buffer.isEmpty) return 0;
    _busy = true;
    final convo = _buffer.join('\n');
    try {
      final reply = await LlmClient.complete(
        c,
        [
          {'role': 'system', 'content': '你是记忆整理器，只输出 JSON。'},
          {'role': 'user', 'content': extractPrompt(convo)},
        ],
        maxTokens: 500,
        temperature: 0.2,
      );
      final obj = _parseJson(reply);
      if (obj == null) {
        store.countExtract(ok: false);
        _failStreak++;
        return 0;
      }
      final cands = jsonToCandidates(obj);
      final added = MemoryRules.apply(store, cands, source: 'chat');
      store.countExtract(ok: true);
      _failStreak = 0;
      _pending = 0;
      _buffer.clear();
      return added;
    } catch (e) {
      MemLog.write('[memory] 抽取失败: $e');
      store.countExtract(ok: false);
      _failStreak++;
      return 0;
    } finally {
      _busy = false;
    }
  }

  /// 模型很爱加 Markdown 围栏或前后废话，这里尽量把它剥出来。
  static Map<String, dynamic>? _parseJson(String raw) {
    var s = raw.trim();
    final fence = RegExp(r'```(?:json)?').firstMatch(s);
    if (fence != null) {
      s = s.substring(fence.end);
      final end = s.lastIndexOf('```');
      if (end >= 0) s = s.substring(0, end);
    }
    final start = s.indexOf('{');
    final stop = s.lastIndexOf('}');
    if (start < 0 || stop <= start) return null;
    s = s.substring(start, stop + 1);
    try {
      final j = jsonDecode(s);
      if (j is Map) return Map<String, dynamic>.from(j);
    } catch (_) {}
    return null;
  }

  /// 输出侧硬约束：不信任模型给的文本。
  static String _clean(dynamic v, [int maxLen = 20]) {
    if (v is! String) return '';
    var t = v.replaceAll('\r', ' ').replaceAll('\n', ' ').trim();
    if (t.length > maxLen) return '';
    if (t.contains('{') || t.contains('}') || t.contains('"')) return '';
    t = MemModel.stripFirstPerson(t);
    t = t.replaceAll(RegExp(r'^[ ，,。.、]+|[ ，,。.、]+$'), '');
    if (t.length < 2) return '';
    return t;
  }

  /// 把模型吐的 JSON 转成候选。畸形字段一律跳过，不抛异常。
  static List<Candidate> jsonToCandidates(Map<String, dynamic> obj) {
    final out = <Candidate>[];
    final prof = obj['profile'] is Map
        ? Map<String, dynamic>.from(obj['profile'] as Map)
        : <String, dynamic>{};

    final name = _clean(prof['call_me'], 8);
    if (name.isNotEmpty) {
      out.add(Candidate(CandKind.callMe, name, confidence: 0.9));
    }
    final job = _clean(prof['occupation'], 16);
    if (job.isNotEmpty) {
      out.add(Candidate(CandKind.occupation, job, confidence: 0.85));
    }

    for (final k in ['likes', 'dislikes', 'habits']) {
      final v = prof[k];
      if (v is! List) continue;
      for (final it in v.take(3)) {
        final t = _clean(it, 14);
        if (t.isEmpty) continue;
        out.add(
          Candidate(
            k == 'likes'
                ? CandKind.like
                : (k == 'dislikes' ? CandKind.dislike : CandKind.habit),
            t,
            confidence: 0.85,
          ),
        );
      }
    }

    final facts = obj['facts'];
    if (facts is List) {
      for (final it in facts.take(3)) {
        final t = _clean(it, 30);
        if (t.length >= 2) {
          out.add(Candidate(CandKind.fact, t, confidence: 0.8));
        }
      }
    }

    final eps = obj['episodes'];
    if (eps is List) {
      for (final it in eps.take(2)) {
        if (it is! Map) continue;
        final t = _clean(it['text'], 30);
        if (t.length < 2) continue;
        final kind = (it['kind'] ?? 'event').toString();
        out.add(
          Candidate(
            CandKind.episode,
            t,
            confidence: 0.7,
            episodeKind:
                const ['preference', 'event', 'promise', 'mood'].contains(kind)
                ? kind
                : 'event',
          ),
        );
      }
    }
    return out;
  }

  static String extractPrompt(String convo) =>
      '''你是记忆整理器。从下面的对话里抽取值得长期记住的信息，只输出 JSON，不要解释、不要 Markdown。

只抽这些：
- 主人的称呼、职业、身份
- 明确的喜好 / 厌恶（要有具体对象，不是"今天好烦"）
- 长期习惯、作息（熬夜、久坐、固定时间做某事）
- 正在做 / 正在学的事
- 明确要求你记住的事

不要抽：一次性的情绪、寒暄、宠物的台词、你的推测、没有具体对象的好恶。

输出格式（没有的键直接省略；每条 text 用第三人称，不超过 20 字，不要引号不要换行）：
{"profile":{"call_me":"","occupation":"","likes":[""],"dislikes":[""],"habits":[""]},
 "facts":[""],
 "episodes":[{"kind":"preference|event|promise|mood","text":""}],
 "style":{"prefer":"","avoid":[""]}}

最多 3 条 facts、2 条 episodes。宁可少抽，不要编。

对话：
$convo''';
}
