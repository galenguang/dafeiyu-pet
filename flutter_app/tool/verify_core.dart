// M1 核心逻辑自检。用 `dart run tool/verify_core.dart` 直接跑，
// 不依赖 flutter_test —— 因为 flutter_tester 在部分环境里
// 会因为 WebSocket 升级被拦而连不上（本机就是），
// 而这些逻辑本来就与 Flutter 运行时无关，没必要被它卡住。
//
// 断言口径与 test/core_test.dart 一致，只是换了个执行器。

import 'dart:io';
import 'dart:math';

import 'package:dafeiyu/pet/action_registry.dart';
import 'package:dafeiyu/pet/action_scheduler.dart';
import 'package:dafeiyu/pet/manifest.dart';

// 这是命令行自检脚本，print 就是它的输出手段。
// ignore_for_file: avoid_print

int _pass = 0;
int _fail = 0;
final _failures = <String>[];

void check(bool cond, String what, [String extra = '']) {
  if (cond) {
    _pass++;
  } else {
    _fail++;
    _failures.add('$what${extra.isEmpty ? '' : '  → $extra'}');
    print('  ❌ $what${extra.isEmpty ? '' : '  → $extra'}');
  }
}

void eq(Object? got, Object? want, String what) {
  check(got == want, what, '期望 $want，实际 $got');
}

void group(String name) => print('\n[$name]');

/// 直接从文件读 manifest，绕开 Flutter 的 rootBundle。
/// 路径相对 flutter_app 根目录，所以用 `dart run` 时工作目录要是 flutter_app。
Manifest loadManifestFromFile() {
  final f = File('assets/manifest.json');
  if (!f.existsSync()) {
    stderr.writeln('找不到 assets/manifest.json，请在 flutter_app 目录下运行');
    exit(1);
  }
  return Manifest.parse(f.readAsStringSync());
}

Future<void> main() async {
  final manifest = loadManifestFromFile();
  final reg = ActionRegistry(manifest);

  // ------------------------------------------------------------------
  group('manifest');
  eq(manifest.clips.length, 100, '片段总数 100');
  eq(manifest.categories.length, 12, '分类 12 个');
  eq(manifest.features.length, 28, '招牌互动 28 条');
  eq(manifest.stageW, 640, '画布宽 640');
  eq(manifest.stageH, 360, '画布高 360');
  eq(manifest.fps, 24, '帧率 24');
  check((manifest.aspectRatio - 16 / 9).abs() < 0.001, '宽高比 16:9');

  final idleClip = reg.byId('idle')!;
  eq(idleClip.frames, 241, 'idle 帧数 241');
  eq(idleClip.durationMs, 10042, 'idle 时长按帧算 10042ms（不是猜的 9600）');

  final wipe = reg.byId('museWipe')!;
  eq(wipe.frames, 192, 'museWipe 帧数 192（截断素材）');
  eq(wipe.durationMs, 8000, 'museWipe 时长 8000ms');

  var cropBad = 0;
  for (final c in manifest.clips) {
    if (c.crop.length != 4 || c.anchor.length != 2) {
      cropBad++;
      continue;
    }
    final x = c.crop[0], y = c.crop[1], w = c.crop[2], h = c.crop[3];
    if (x < 0 || y < 0 || w <= 0 || h <= 0 || x + w > 640 || y + h > 360) {
      cropBad++;
    }
  }
  eq(cropBad, 0, '100 条 crop/anchor 全部合法且不越界');

  final loops =
      manifest.clips.where((c) => c.loop).map((c) => c.id).toSet();
  check(
    loops.length == 6 &&
        loops.containsAll(['idle', 'sleep', 'run', 'drag', 'crabWalk',
                           'floatStep']),
    'loop 恰好 6 条且与桌面版一致',
    loops.join(','),
  );

  // ------------------------------------------------------------------
  group('ActionRegistry');
  eq(reg.byId('idle')?.name, '待机', '按 id 取名');
  check(reg.byId('不存在') == null, '未知 id 返回 null');
  eq(reg.byCat('eat').length, 20, 'eat 分类 20 条');
  eq(reg.byCat('daily').length, 16, 'daily 分类 16 条');
  eq(reg.byCat('festival').length, 14, 'festival 分类 14 条');
  eq(reg.byCat('idle').length, 2, 'idle 分类 2 条');
  eq(reg.byCat('不存在').length, 0, '未知分类返回空');

  // 加权：idle(14) : sleep(3) ≈ 4.67。
  // 注意不是 14:4 —— manifest 里 sleep 的 weight 是 3，
  // 我一开始按桌面版注释猜成 4，被实测数据纠正了。
  final pool = reg.byCat('idle');
  final rng = Random(42);
  final counts = <String, int>{};
  for (var i = 0; i < 4000; i++) {
    final c = reg.pickWeighted(pool, rng: rng);
    counts[c.id] = (counts[c.id] ?? 0) + 1;
  }
  final wIdle = reg.byId('idle')!.weight;
  final wSleep = reg.byId('sleep')!.weight;
  final expected = wIdle / wSleep;
  final ratio = (counts['idle'] ?? 0) / max(1, counts['sleep'] ?? 0);
  check(
    (ratio - expected).abs() / expected < 0.12,
    '加权抽取服从权重 ${wIdle.toStringAsFixed(0)}:${wSleep.toStringAsFixed(0)}',
    '期望 ${expected.toStringAsFixed(2)}，实测 ${ratio.toStringAsFixed(2)}',
  );

  var avoided = 0;
  final rng2 = Random(7);
  for (var i = 0; i < 500; i++) {
    if (reg.pickWeighted(pool, avoid: 'idle', rng: rng2).id != 'idle') {
      avoided++;
    }
  }
  eq(avoided, 500, 'avoid 500/500 次都避开了刚播的那条');

  eq(reg.pickWeighted([idleClip], avoid: 'idle').id, 'idle',
      '池里只有一条时 avoid 不会抽空');

  var actOnly = true;
  for (var i = 0; i < 300; i++) {
    if (reg.randomAct(rng: Random(i)).kind != 'act') actOnly = false;
  }
  check(actOnly, 'randomAct 只返回 kind=act');

  final poolF = manifest.features.firstWhere((f) => f.key == 'random_eat');
  final resolved = reg.resolveFeature(poolF);
  check(resolved != null && resolved.cat == 'eat',
      'resolveFeature 展开 pool → eat 分类', resolved?.id ?? 'null');
  final actF = manifest.features.firstWhere((f) => f.key == 'sleep');
  eq(reg.resolveFeature(actF)?.id, 'sleep', 'resolveFeature 展开 action');

  // ------------------------------------------------------------------
  group('ActionScheduler');
  final s = ActionScheduler(reg);
  eq(s.current.id, 'idle', '初始就是待机');
  eq(s.isPlaying, false, '初始未播放');

  s.play('dance');
  eq(s.current.id, 'dance', '点播 dance 生效');
  eq(s.isPlaying, true, '点播后 isPlaying');

  s.play('根本不存在');
  eq(s.current.id, 'dance', '未知 id 不改变状态');

  // loop 不自动结束
  final s2 = ActionScheduler(reg);
  s2.play('sleep');
  await Future<void>.delayed(const Duration(milliseconds: 300));
  eq(s2.current.id, 'sleep', 'loop 片段 300ms 后仍在播（没被误结束）');

  // 重复 play 同一条 loop 返回同一对象
  final s3 = ActionScheduler(reg);
  s3.play('idle', returnToIdle: false);
  final t1 = s3.current;
  s3.play('idle', returnToIdle: false);
  check(identical(t1, s3.current), '重复 play 同一条 loop 不重置（避免黑帧）');

  // 普通动作到期回待机
  final s4 = ActionScheduler(reg);
  final short = manifest.clips
      .where((c) => !c.loop)
      .reduce((a, b) => a.durationMs <= b.durationMs ? a : b);
  s4.play(short.id);
  await Future<void>.delayed(
      Duration(milliseconds: short.durationMs + 250));
  eq(s4.current.id, 'idle', '普通动作播完自动回待机（${short.id}）');

  s.dispose();
  s2.dispose();
  s3.dispose();
  s4.dispose();

  // ------------------------------------------------------------------
  print('\n${'=' * 52}');
  print('通过 $_pass   失败 $_fail');
  if (_fail > 0) {
    print('\n失败项：');
    for (final f in _failures) {
      print('  - $f');
    }
    exit(1);
  }
  print('✅ 全部通过');
}
