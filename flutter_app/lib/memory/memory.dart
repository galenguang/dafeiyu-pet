import 'memory_extract.dart';
import 'memory_recall.dart';
import 'memory_rules.dart';
import 'memory_store.dart';
import 'memory_voice.dart';
import '../core/settings.dart';

/// 记忆门面。UI 和聊天只跟这一个类打交道。
///
/// 里面套着四样东西，但对外只暴露少数几个方法：
/// - `store`    数据（JSON 落盘、去重、淘汰）
/// - `rules`    本地规则轨（永远跑，零成本）
/// - `extractor` 模型轨（按闸门跑）
/// - `voice`    人格化台词
///
/// 对应桌面版 `app/memory/core.py`。这一层的意义是让聊天页不需要知道
/// 「亲密度怎么涨」「抽取攒几轮」这些细节。
class Memory {
  final MemoryStore store;
  final MemoryExtractor extractor;
  final AppSettings Function() cfg;

  Memory(this.store, this.cfg) : extractor = MemoryExtractor(store, cfg);

  static Future<Memory> boot(String path, AppSettings Function() cfg) async {
    final store = await MemoryStore.open(path);
    return Memory(store, cfg);
  }

  bool get enabled => cfg().memoryEnabled;

  // ---------------- 对话 ----------------
  /// 主人说了一句。规则轨同步跑（零延迟），模型轨按闸门异步跑。
  void noteUser(String text) {
    if (!enabled) return;
    store.noteChat(userText: text);
    final cands = MemoryRules.extract(text);
    if (cands.isNotEmpty) MemoryRules.apply(store, cands);
    // 不 await：抽取可能要几秒，不能卡住聊天
    extractor.onUser(text);
    extractor.maybeRun();
  }

  void noteReply(String text) {
    if (!enabled) return;
    extractor.onReply(text);
  }

  /// 手动教一件事。直接写 facts 并置顶，永不淘汰。
  bool teach(String text) {
    final t = text.trim();
    if (t.isEmpty) return false;
    return store.addFact(t, confidence: 1.0, source: 'manual', pinned: true);
  }

  /// 面板「整理一下」：立刻跑一次模型抽取。
  Future<int> organize() async {
    if (!enabled) return 0;
    return extractor.organizeNow();
  }

  // ---------------- 给 prompt / 台词 ----------------
  /// 塞进 system prompt 的那一小段。没有记忆时返回空串。
  String contextBlock(String query) {
    if (!enabled) return '';
    try {
      return MemoryRecall.buildMemoryBlock(store, query: query);
    } catch (_) {
      return '';
    }
  }

  /// 记忆味的一句台词。返回 null 就让调用方走普通台词池。
  String? line(String situation, {String? last}) {
    if (!enabled || !cfg().memorySpeak) return null;
    try {
      return MemoryVoice.pick(store, situation, last: last);
    } catch (_) {
      return null;
    }
  }

  Future<void> flush() => store.flush();

  void dispose() => store.dispose();
}
