import 'dart:io';
import 'dart:math';

import 'package:dafeiyu/pet/action_registry.dart';
import 'package:dafeiyu/pet/action_scheduler.dart';
import 'package:dafeiyu/pet/manifest.dart';
import 'package:flutter_test/flutter_test.dart';

/// M1 的单元测试。
///
/// 这些断言不是凑数的——每一条都对应一个桌面版曾经踩过的行为：
/// - 加权抽取要真的按权重走（否则某些稀有动作永远看不到）
/// - 一定要避开刚播完那条（否则会出现「同一个动作连播三遍」）
/// - loop 片段不能被自动结束（否则 idle 播 10 秒就停了）
/// - 时长必须来自实测帧数，不能是猜的默认值
void main() {
  late Manifest manifest;
  late ActionRegistry reg;

  setUpAll(() async {
    TestWidgetsFlutterBinding.ensureInitialized();
    // 不用 rootBundle：在纯 Dart 测试里读文件更直接，
    // 也让同样的断言能在 tool/verify_core.dart 里原样跑。
    final raw = File('assets/manifest.json').readAsStringSync();
    manifest = Manifest.parse(raw);
    reg = ActionRegistry(manifest);
  });

  group('manifest', () {
    test('100 段，12 分类，28 招牌', () {
      expect(manifest.clips.length, 100);
      expect(manifest.categories.length, 12);
      expect(manifest.features.length, 28);
    });

    test('画布与帧率', () {
      expect(manifest.stageW, 640);
      expect(manifest.stageH, 360);
      expect(manifest.fps, 24);
      expect(manifest.aspectRatio, closeTo(16 / 9, 0.001));
    });

    test('时长来自实测帧数，不是猜的', () {
      final idle = reg.byId('idle')!;
      expect(idle.frames, 241);
      // 241 帧 / 24fps = 10042ms
      expect(idle.durationMs, 10042);
    });

    test('截断素材按真实帧数记录', () {
      final wipe = reg.byId('museWipe')!;
      expect(wipe.frames, 192);
      expect(wipe.durationMs, 8000);
    });

    test('每条 clip 都有 crop 与 anchor，且不越界', () {
      for (final c in manifest.clips) {
        expect(c.crop.length, 4, reason: c.id);
        expect(c.anchor.length, 2, reason: c.id);
        final x = c.crop[0], y = c.crop[1], w = c.crop[2], h = c.crop[3];
        expect(x >= 0 && y >= 0, true, reason: '${c.id} 起点为负');
        expect(x + w <= 640, true, reason: '${c.id} 横向越界');
        expect(y + h <= 360, true, reason: '${c.id} 纵向越界');
        expect(w > 0 && h > 0, true, reason: '${c.id} 尺寸非法');
      }
    });

    test('loop 恰好 6 条，与桌面版一致', () {
      final loops =
          manifest.clips.where((c) => c.loop).map((c) => c.id).toSet();
      expect(loops, {'crabWalk', 'drag', 'floatStep', 'idle', 'run', 'sleep'});
    });
  });

  group('ActionRegistry', () {
    test('按 id 取', () {
      expect(reg.byId('idle')?.name, '待机');
      expect(reg.byId('不存在的'), isNull);
    });

    test('按分类取，数目对得上', () {
      expect(reg.byCat('eat').length, 20);
      expect(reg.byCat('daily').length, 16);
      expect(reg.byCat('festival').length, 14);
      expect(reg.byCat('idle').length, 2);
      expect(reg.byCat('不存在的分类'), isEmpty);
    });

    test('加权抽取服从权重', () {
      // idle 分类只有 idle 和 sleep 两条，权重 14 : 3
      final pool = reg.byCat('idle');
      final rng = Random(42);
      final counts = <String, int>{};
      for (var i = 0; i < 4000; i++) {
        final c = reg.pickWeighted(pool, rng: rng);
        counts[c.id] = (counts[c.id] ?? 0) + 1;
      }
      final a = counts['idle'] ?? 0;
      final b = counts['sleep'] ?? 0;
      final expected =
          reg.byId('idle')!.weight / reg.byId('sleep')!.weight;
      final got = a / b;
      expect((got - expected).abs() / expected, lessThan(0.12),
          reason: '期望 $expected，实际 $got（idle=$a sleep=$b）');
    });

    test('一定避开刚播完那条', () {
      final pool = reg.byCat('idle');
      final rng = Random(7);
      for (var i = 0; i < 500; i++) {
        final c = reg.pickWeighted(pool, avoid: 'idle', rng: rng);
        expect(c.id, isNot('idle'), reason: 'avoid 失效');
      }
    });

    test('池子里只有一条时，avoid 不会抽空', () {
      final single = [reg.byId('idle')!];
      final c = reg.pickWeighted(single, avoid: 'idle');
      expect(c.id, 'idle');
    });

    test('randomAct 只返回 kind=act', () {
      final rng = Random(1);
      for (var i = 0; i < 300; i++) {
        final c = reg.randomAct(rng: rng);
        expect(c.kind, 'act');
      }
    });

    test('resolveFeature 展开 pool 与 action', () {
      final poolF = manifest.features.firstWhere((f) => f.key == 'random_eat');
      final c1 = reg.resolveFeature(poolF);
      expect(c1, isNotNull);
      expect(c1!.cat, 'eat');

      final actF = manifest.features.firstWhere((f) => f.key == 'sleep');
      final c2 = reg.resolveFeature(actF);
      expect(c2?.id, 'sleep');
    });
  });

  group('ActionScheduler', () {
    test('初始就是待机', () {
      final s = ActionScheduler(reg);
      expect(s.current.id, 'idle');
      expect(s.isPlaying, false);
      s.dispose();
    });

    test('点播普通动作后 isPlaying 为真', () {
      final s = ActionScheduler(reg);
      s.play('dance');
      expect(s.current.id, 'dance');
      expect(s.isPlaying, true);
      s.dispose();
    });

    test('loop 片段不自动结束', () async {
      final s = ActionScheduler(reg);
      s.play('sleep');
      expect(s.current.id, 'sleep');
      // sleep 的 durationMs 是 10042，等 300ms 不该结束
      await Future<void>.delayed(const Duration(milliseconds: 300));
      expect(s.current.id, 'sleep', reason: 'loop 被误结束');
      s.dispose();
    });

    test('未知 id 不改变状态', () {
      final s = ActionScheduler(reg);
      s.play('idle');
      final before = s.current.id;
      s.play('根本不存在');
      expect(s.current.id, before);
      s.dispose();
    });

    test('重复 play 同一条 loop 不重置', () {
      final s = ActionScheduler(reg);
      s.play('idle', returnToIdle: false);
      final t1 = s.current;
      s.play('idle', returnToIdle: false);
      expect(identical(t1, s.current), true, reason: '重放会引入黑帧');
      s.dispose();
    });
  });
}
