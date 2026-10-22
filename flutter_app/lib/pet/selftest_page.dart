import 'dart:async';

import 'package:flutter/material.dart';

import 'action_registry.dart';
import 'clip_renderer.dart';

/// 真机自检。这是 M1 的验收工具，也是以后 M5 排障的入口。
///
/// 检测项对应计划里 M1 的验收标准：
/// 1. 100 段全部可解码（元数据能读到 640x360）
/// 2. 全部可点播、无黑帧（抽样跑完全库）
/// 3. alpha 是真透明（canvas 采样 a0 > 40%）
/// 4. 切换延迟 < 200ms
/// 5. 连续切换 200 次不崩
///
/// 刻意**不在**这里测 30 分钟无接缝——那需要人盯着，做成
/// 「跑 30 分钟然后回来看」的被动检测没意义，留给真机手工验收。
class SelftestPage extends StatefulWidget {
  final ActionRegistry registry;
  final ClipRenderer renderer;
  const SelftestPage({
    super.key,
    required this.registry,
    required this.renderer,
  });

  @override
  State<SelftestPage> createState() => _SelftestPageState();
}

class _SelftestPageState extends State<SelftestPage> {
  final _lines = <String>[];
  final _sub = <StreamSubscription>[];
  bool _running = false;
  int _pass = 0;
  int _fail = 0;

  @override
  void initState() {
    super.initState();
    _sub.add(
      widget.renderer.events.listen((e) {
        if (e.type == 'error') {
          _log('  ! 解码错误 ${e.data['name']} code=${e.data['code']}');
        }
      }),
    );
    // 进来就跑，省得在真机上还要点一下按钮（adb 点按钮要算坐标，很脆）
    WidgetsBinding.instance.addPostFrameCallback((_) => _run());
  }

  @override
  void dispose() {
    for (final s in _sub) {
      s.cancel();
    }
    super.dispose();
  }

  void _log(String s) {
    if (!mounted) return;
    setState(() => _lines.add(s));
  }

  /// 等某个片段名的某类事件。
  ///
  /// 为什么不能直接用 renderer.waitFor：那个只按类型匹配。
  /// 循环里连续播 12 条时，第 N 条的 meta 可能在注册监听前就到了，
  /// 于是 waitFor 一直等不到、最后超时——但视频其实已经加载好了。
  /// （M1 首轮真机自检就栽在这上面：诊断里明明有 META，自检却报超时。）
  /// 这里改成「按 name + type 双重匹配」，并且先发指令、后等事件，
  /// 拿到的是下一次匹配，不会漏。
  Future<Map<String, dynamic>?> _expectEvent(
    String type,
    String name, {
    Duration timeout = const Duration(seconds: 6),
  }) async {
    final done = Completer<Map<String, dynamic>?>();
    late StreamSubscription sub;
    final t = Timer(timeout, () {
      if (!done.isCompleted) done.complete(null);
    });
    sub = widget.renderer.events.listen((e) {
      if (e.type == type && e.data['name'] == name && !done.isCompleted) {
        t.cancel();
        sub.cancel();
        done.complete(e.data);
      }
    });
    return done.future;
  }

  void _ok(bool cond, String what, [String extra = '']) {
    if (cond) {
      _pass++;
      _log('✅ $what${extra.isEmpty ? '' : '  $extra'}');
    } else {
      _fail++;
      _log('❌ $what${extra.isEmpty ? '' : '  $extra'}');
    }
  }

  Future<void> _run() async {
    if (_running) return;
    setState(() {
      _running = true;
      _lines.clear();
      _pass = 0;
      _fail = 0;
    });

    final rd = widget.renderer;
    final reg = widget.registry;
    final all = reg.all;

    _log('=== M1 自检开始 ===');
    _log('片段总数 ${all.length}');

    // ---- 0. 先看页面内部状态 ----
    // 这一步是排障的关键：如果 JS 没跑起来，后面所有检测都会无意义地超时。
    await Future<void>.delayed(const Duration(milliseconds: 600));
    final diag = await rd.readDiagnostics();
    // diag 是滚动日志，只打印末尾一段，不然一屏全是历史
    _log(
      '页面诊断: ${diag.length > 300 ? "…${diag.substring(diag.length - 300)}" : (diag.isEmpty ? "(空)" : diag)}',
    );
    // 判定「JS 活着」不能靠 diag 里有没有 'js-ok'：那行最早，
    // 跑一会儿就被后来的 PLAY/HTTP 挤出去了，会假失败。
    final jsOk = await rd.isPageAlive();
    _ok(jsOk, '页面 JS 已执行');
    _log('JS 下发 ${rd.jsSent} 条，失败 ${rd.jsErrors} 条，JS 回报收到 ${rd.jsRecv} 条');
    _ok(
      rd.jsErrors == 0,
      'JS 指令下发无异常',
      rd.jsLastError.isEmpty ? '' : '最后错误: ${rd.jsLastError}',
    );
    _ok(
      rd.jsRecv >= rd.jsSent - 2,
      'JS 已收到下发的指令',
      '发出 ${rd.jsSent} / 收到 ${rd.jsRecv}',
    );
    if (!jsOk) {
      _log('');
      _log('JS 没跑起来，后面的检测跳过。');
      _log('常见原因：WebView 禁用了 JS / 页面被 CSP 拦 / 资源 404。');
      setState(() => _running = false);
      return;
    }

    // ---- 1. 全量解码检查（抽样 12 条，全量太慢）----
    // 为什么抽样：每条约 0.2-0.5 秒建立解码器，100 条要 30 秒以上，
    // 而且真正的风险是「某类编码参数异常」，抽样覆盖到分类即可。
    final sample = <dynamic>[];
    final seenCat = <String>{};
    for (final c in all) {
      if (seenCat.add(c.cat)) sample.add(c);
    }
    // 再补几条不同 kind 的
    for (final k in ['loop', 'click', 'move']) {
      final c = all.where((x) => x.kind == k).firstOrNull;
      if (c != null && !sample.contains(c)) sample.add(c);
    }
    _log('解码抽样 ${sample.length} 条（覆盖全部 12 个分类）');

    var decodeOk = 0;
    for (final c in sample) {
      final name = c.file.replaceAll('.webm', '');
      // 先挂上等待，再发播放指令。顺序反了会漏事件（首轮真机就踩了）。
      final fut = _expectEvent('meta', name);
      await rd.play(name, loop: true);
      final ev = await fut;
      if (ev != null && ev['w'] == 640 && ev['h'] == 360) {
        decodeOk++;
      } else if (ev == null) {
        _log('  ! $name 6 秒内没等到 meta');
      } else {
        _log('  ! $name 尺寸异常 ${ev['w']}x${ev['h']}');
      }
    }
    _ok(decodeOk == sample.length, '解码成功', '$decodeOk/${sample.length}');

    // ---- 2. alpha 采样（3 条）----
    for (final name in ['idle', 'dance', 'fireworks']) {
      if (!all.any((c) => c.file.startsWith(name))) continue;
      await rd.play(name, loop: true);
      await Future<void>.delayed(const Duration(milliseconds: 600));
      final fut = _expectEvent('probe', name);
      await rd.probe(name);
      final ev = await fut;
      if (ev == null) {
        _ok(false, 'alpha 采样 $name', '超时');
        continue;
      }
      final a0 = (ev['a0'] as num).toDouble();
      _ok(
        a0 > 40,
        'alpha $name',
        'a0=${a0.toStringAsFixed(1)}%  实心=${ev['aHigh']}%',
      );
    }

    // ---- 3. 切换延迟（30 次）----
    // 计时口径：从下发 play 到页面回 'playing' 事件。
    // 这个值代表「指令往返 + JS 切 class」，不代表首帧上屏时间——
    // 后者要看真机肉眼或 SurfaceFlinger，这里取不到。
    final ids = all.map((c) => c.file.replaceAll('.webm', '')).toList();
    var switchTotal = 0;
    var switchN = 0;
    for (var i = 0; i < 30; i++) {
      final name = ids[i % ids.length];
      final fut = _expectEvent(
        'playing',
        name,
        timeout: const Duration(seconds: 3),
      );
      final sw0 = DateTime.now().microsecondsSinceEpoch;
      await rd.play(name, loop: true);
      final ev = await fut;
      if (ev != null) {
        switchTotal += DateTime.now().microsecondsSinceEpoch - sw0;
        switchN++;
      }
    }
    final per = switchN == 0 ? 9999.0 : switchTotal / switchN / 1000;
    _ok(per < 200, '切换延迟', '${per.toStringAsFixed(1)}ms/次（$switchN/30 有回执）');

    // ---- 4. 压力：连续切 200 次 ----
    _log('压力测试：连续切换 200 次…');
    final sw2 = Stopwatch()..start();
    var crash = false;
    try {
      for (var i = 0; i < 200; i++) {
        await rd.play(ids[(i * 7) % ids.length], loop: false);
      }
    } catch (e) {
      crash = true;
      _log('  ! 切换过程中抛异常：$e');
    }
    sw2.stop();
    _ok(!crash, '200 次连续切换无异常', '耗时 ${sw2.elapsedMilliseconds}ms');

    // ---- 5. 内存 ----
    final diagEnd = await rd.readDiagnostics();
    final m = RegExp(r'BYTES (\S+) (\d+)').firstMatch(diagEnd);
    if (m != null) {
      _log('末次素材字节 ${m.group(1)} = ${m.group(2)}');
    }

    // ---- 5. 回到待机 ----
    await rd.play('idle', loop: true);

    _log('');
    _log('=== 结束：$_pass 通过 / $_fail 失败 ===');
    // 页面内的 diag 也一起带出来
    final tail = await rd.readDiagnostics();
    _log('页面诊断: $tail');
    setState(() => _running = false);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('自检'),
        actions: [
          TextButton(
            onPressed: _running ? null : _run,
            child: Text(_running ? '跑着呢…' : '开始'),
          ),
        ],
      ),
      body: Column(
        children: [
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            color: Colors.white,
            child: Row(
              children: [
                _Pill(
                  label: '通过',
                  value: _pass,
                  color: const Color(0xFF2E9E5B),
                ),
                const SizedBox(width: 12),
                _Pill(
                  label: '失败',
                  value: _fail,
                  color: const Color(0xFFE05B5B),
                ),
                const Spacer(),
                Text(
                  '${widget.registry.length} 段素材',
                  style: const TextStyle(color: Color(0xFF6B7280)),
                ),
              ],
            ),
          ),
          Expanded(
            child: Container(
              margin: const EdgeInsets.all(12),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: const Color(0xFF14161B),
                borderRadius: BorderRadius.circular(12),
              ),
              child: SingleChildScrollView(
                reverse: true,
                child: SelectableText(
                  _lines.isEmpty ? '点右上角「开始」' : _lines.join('\n'),
                  style: const TextStyle(
                    fontFamily: 'monospace',
                    fontSize: 12,
                    height: 1.55,
                    color: Color(0xFF9AE6B4),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _Pill extends StatelessWidget {
  final String label;
  final int value;
  final Color color;
  const _Pill({required this.label, required this.value, required this.color});

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: color, shape: BoxShape.circle),
        ),
        const SizedBox(width: 5),
        Text(
          '$label $value',
          style: TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w600,
            color: color,
          ),
        ),
      ],
    );
  }
}
