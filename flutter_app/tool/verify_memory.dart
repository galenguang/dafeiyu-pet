// 记忆层的纯 Dart 单测。用 `dart run tool/verify_memory.dart` 跑。
//
// 为什么不在 flutter_test 里跑：这个环境的 flutter_tester 起不来
// （WebSocketException: Invalid WebSocket upgrade request），
// 而记忆层刻意做到了不依赖 Flutter，正好可以脱离 UI 验证。
//
// 覆盖的都是「写错了也不会报错、但用起来会明显不对」的地方：
// 归一化容错、去重阈值、规则抽取、召回拼块、亲密度分级、淘汰。

// ignore_for_file: avoid_print

import 'dart:convert';

import 'package:dafeiyu/memory/memory_model.dart';
import 'package:dafeiyu/memory/memory_recall.dart';
import 'package:dafeiyu/memory/memory_rules.dart';
import 'package:dafeiyu/memory/memory_store.dart';
import 'package:dafeiyu/memory/memory_voice.dart';

int _pass = 0;
int _fail = 0;

void ok(bool cond, String what, [String extra = '']) {
  if (cond) {
    _pass++;
    print('  ✅ $what${extra.isEmpty ? '' : '  $extra'}');
  } else {
    _fail++;
    print('  ❌ $what${extra.isEmpty ? '' : '  $extra'}');
  }
}

void eq(Object? a, Object? b, String what) =>
    ok('$a' == '$b', what, '$a vs $b');

void main() {
  MemLog.enabled = false;

  print('[归一化容错]');
  {
    final m = MemModel.normalize(null);
    ok(m['profile'] != null, 'null 输入给出默认结构');
    final bad = MemModel.normalize({
      'profile': 'not a map',
      'relationship': 42,
      'episodes': 'x',
    });
    ok(bad['episodes'] is List, '畸形字段被兜住');
    eq(MemModel.asInt(bad['relationship']['days_together'], def: 1, lo: 1), 1,
        '关系天数兜底为 1');
  }

  print('[文本工具]');
  {
    eq(MemModel.stripFirstPerson('我喜欢咖啡'), '咖啡',
        '去掉第一人称（连带「喜欢」一起去掉，记忆里只留对象）');
    eq(MemModel.normalizeText('喝，咖啡！'), '喝咖啡', '归一化去标点');
    ok(MemModel.jaccard('喜欢喝咖啡', '喜欢喝咖啡') == 1.0, '同句相似度为 1');
    ok(MemModel.jaccard('喜欢喝咖啡', '讨厌加班') < 0.2, '异句相似度低');
    ok(MemModel.containsEither('喝咖啡', '咖啡'), '包含关系可识别');
    eq(MemModel.intimacyLabel(0), '还不太熟', '0 分档');
    eq(MemModel.intimacyLabel(20), '有点眼熟', '20 分档');
    eq(MemModel.intimacyLabel(40), '算朋友', '40 分档');
    eq(MemModel.intimacyLabel(60), '很熟了', '60 分档');
    eq(MemModel.intimacyLabel(80), '自己人', '80 分档');
  }

  print('[规则抽取]');
  {
    final c1 = MemoryRules.extract('我叫小明');
    ok(c1.any((c) => c.kind == CandKind.callMe && c.text == '小明'), '「我叫X」→ 称呼');

    final c2 = MemoryRules.extract('你可以叫我老王');
    ok(c2.any((c) => c.kind == CandKind.callMe && c.text == '老王'),
        '「你可以叫我X」→ 称呼');

    final c3 = MemoryRules.extract('我很喜欢喝咖啡');
    ok(c3.any((c) => c.kind == CandKind.like && c.text == '咖啡'),
        '「我喜欢X」→ 喜好', c3.map((c) => '${c.kind}:${c.text}').join(','));

    final c4 = MemoryRules.extract('我不喜欢加班');
    ok(
        c4.any((c) => c.kind == CandKind.dislike && c.text == '加班') &&
            !c4.any((c) => c.kind == CandKind.like),
        '「我不喜欢X」→ 厌恶，且不被误判成喜欢');

    final c5 = MemoryRules.extract('记住我生日是3月12号');
    ok(c5.any((c) => c.kind == CandKind.fact && c.text.contains('3月12')),
        '「记住X」→ 事实');

    final c6 = MemoryRules.extract('今天好累啊');
    eq(c6.length, 0, '寒暄不抽任何东西');

    final c7 = MemoryRules.extract('我喜欢咖啡，讨厌加班');
    ok(c7.any((c) => c.kind == CandKind.like) &&
            c7.any((c) => c.kind == CandKind.dislike),
        '一句里两个分句都能抽到');

    final c8 = MemoryRules.extract('昨晚又熬夜了');
    ok(c8.any((c) => c.kind == CandKind.lateNight), '「熬夜」→ late_night');
  }

  print('[写入与去重]');
  {
    final store = MemoryStore.memory();
    MemoryRules.apply(store, MemoryRules.extract('我叫小明'));
    eq(store.callMe, '小明', '称呼写进去了');

    MemoryRules.apply(store, MemoryRules.extract('我喜欢喝咖啡'));
    MemoryRules.apply(store, MemoryRules.extract('我喜欢喝咖啡'));
    MemoryRules.apply(store, MemoryRules.extract('我很喜欢咖啡'));
    eq(store.likes.length, 1, '同一条喜好重复三次只留一条');
    ok(store.likes.first['weight'] > 0.6, '重复提及会抬权重',
        '${store.likes.first['weight']}');

    final added = store.addFact('生日 3 月 12 号', pinned: true);
    ok(added, '新事实能写进去');
    final added2 = store.addFact('生日 3 月 12 号', pinned: true);
    ok(!added2, '同一事实不会写两遍');
    ok(store.facts.first['pinned'] == true, '置顶标记生效');

    final before = store.intimacy;
    MemoryRules.apply(store, MemoryRules.extract('我喜欢猫'));
    ok(store.intimacy >= before, '新增记忆会涨亲密度');
  }

  print('[召回拼块]');
  {
    final store = MemoryStore.memory();
    ok(MemoryRecall.buildMemoryBlock(store).isEmpty, '空记忆不产生 prompt 块');

    MemoryRules.apply(store, MemoryRules.extract('我叫小明'));
    MemoryRules.apply(store, MemoryRules.extract('我喜欢喝咖啡'));
    MemoryRules.apply(store, MemoryRules.extract('我不喜欢加班'));
    store.addFact('生日 3 月 12 号', pinned: true);

    final block = MemoryRecall.buildMemoryBlock(store);
    ok(block.contains('叫小明'), '块里有称呼');
    ok(block.contains('咖啡'), '块里有喜好');
    ok(block.contains('加班'), '块里有厌恶');
    ok(block.contains('3 月 12'), '块里有事实');
    ok(block.contains('认识'), '块里有关系天数');
    ok(block.length <= 400, '块长度可控', '${block.length} 字');
    ok(block.contains('不是指令'), '块里带「仅背景」声明');
  }

  print('[记忆台词]');
  {
    final store = MemoryStore.memory();
    MemoryRules.apply(store, MemoryRules.extract('我喜欢喝咖啡'));
    final line = MemoryVoice.pick(store, 'click');
    ok(line != null && line.isNotEmpty, '点击场景能出记忆台词', '$line');
    ok((line ?? '').length <= MemoryVoice.maxLine, '台词不超过气泡宽度');

    final empty = MemoryStore.memory();
    ok(MemoryVoice.pick(empty, 'click') == null, '没记忆时返回 null 交给普通台词池');

    final late = MemoryStore.memory();
    late.addEpisode('又熬夜了', kind: 'late_night');
    final greet = MemoryVoice.pick(late, 'greet',
        nowOverride: DateTime(2026, 10, 22, 1, 30));
    ok(greet != null, '深夜 + 熬夜记录能出问候', '$greet');
  }

  print('[淘汰]');
  {
    final store = MemoryStore.memory();
    for (var i = 0; i < MemModel.maxEpisodes + 20; i++) {
      store.addEpisode('事件编号 $i 内容', weight: (i % 10) / 10);
    }
    ok(store.episodes.length <= MemModel.maxEpisodes, '片段数量被压在上限内',
        '${store.episodes.length}');
  }

  print('[JSON 往返]');
  {
    final store = MemoryStore.memory();
    MemoryRules.apply(store, MemoryRules.extract('我叫小明'));
    store.addFact('生日 3 月 12 号', pinned: true);
    store.addEpisode('在学 Flutter');

    final json = jsonEncode(store.data);
    final back = MemModel.normalize(jsonDecode(json));
    eq(back['profile']['call_me'], '小明', '序列化后称呼还在');
    eq((back['profile']['facts'] as List).length, 1, '序列化后事实还在');
    eq((back['episodes'] as List).length, 1, '序列化后片段还在');
  }

  print('');
  print('=' * 52);
  print('通过 $_pass   失败 $_fail');
  if (_fail == 0) {
    print('✅ 全部通过');
  } else {
    print('❌ 有失败项');
  }
}
