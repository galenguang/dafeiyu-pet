import 'dart:async';
import 'dart:math';

import 'manifest.dart';
import 'action_registry.dart';

/// 一次点播的状态。
class PlaybackState {
  final Clip clip;
  final int startedAtMs;
  final bool done;
  const PlaybackState({
    required this.clip,
    required this.startedAtMs,
    this.done = false,
  });

  PlaybackState copyWith({bool? done}) => PlaybackState(
    clip: clip,
    startedAtMs: startedAtMs,
    done: done ?? this.done,
  );
}

/// 动作调度：
/// - 点播一段（并自动在 durationMs 后回到待机或下一段）
/// - 空闲时按权重自己表演
/// - loop 的片段（idle / sleep / run 等 6 条）不自动结束
///
/// 对应桌面版 `pet.py` 里的 `play_action` / `_auto_perform` /
/// `_schedule_next_idle`。
///
/// **刻意不继承 ChangeNotifier**：那会把 Flutter 的 foundation 拖进来，
/// 让这个文件无法在纯 Dart VM 里跑（`dart run tool/verify_core.dart` 会炸在
/// flutter 的 ui.Image 上）。这里手写一个极简的监听者列表，
/// 功能上就是 ChangeNotifier 的子集，够用。
///
/// 定时器纪律：任何时刻只有一个 `_endTimer` 和一个 `_idleTimer`。
/// 桌宠最忌讳定时器叠在一起——每切一次动作就多一个没清掉的 Timer，
/// 跑一整天就会看到动作频率莫名变快。
class ActionScheduler {
  final ActionRegistry reg;
  final Random _rng;

  final List<void Function()> _listeners = [];

  PlaybackState? _state;
  Timer? _endTimer;
  Timer? _idleTimer;
  String? _lastId;
  bool _disposed = false;

  /// 空闲多久后自己表演一次。桌面版是 12s 起步、之后 22s 左右抖动。
  final int idleMinMs;
  final int idleMaxMs;

  /// 是否允许空闲表演（设置页可关）。
  bool autoPerform = true;

  ActionScheduler(
    this.reg, {
    Random? rng,
    this.idleMinMs = 12000,
    this.idleMaxMs = 26000,
  }) : _rng = rng ?? Random();

  // ---------------- 极简监听 ----------------
  void addListener(void Function() fn) => _listeners.add(fn);

  void removeListener(void Function() fn) => _listeners.remove(fn);

  void _notify() {
    for (final fn in List<void Function()>.from(_listeners)) {
      try {
        fn();
      } catch (_) {
        // 监听者抛异常不该影响调度本身
      }
    }
  }

  // ---------------- 状态 ----------------
  Clip get current => _state?.clip ?? reg.idle;
  String? get lastId => _lastId;
  bool get isPlaying => _state != null && !_state!.done;

  /// 0..1 的播放进度。loop 片段恒为 0。
  double get progress {
    final s = _state;
    if (s == null) return 0;
    if (s.clip.loop) return 0;
    final elapsed = DateTime.now().millisecondsSinceEpoch - s.startedAtMs;
    return (elapsed / s.clip.durationMs).clamp(0.0, 1.0);
  }

  // ---------------- 播放 ----------------
  /// 点播一段。`returnToIdle` 为 true 时播完回待机。
  ///
  /// [limitMs] 是「只播这么久就切走」，用于短互动：素材每段都是 241 帧
  /// ≈ 10 秒，但点击反应在桌面版只播 2.8 秒、落地缓冲只播 0.8 秒
  /// （`pet.py` 的 `play_action(..., duration=2.8)`）。
  /// 不传就是整段播完。
  void play(String id, {bool returnToIdle = true, int? limitMs}) {
    final clip = reg.byId(id);
    if (clip == null) return;
    _play(clip, returnToIdle: returnToIdle, limitMs: limitMs);
  }

  void _play(Clip clip, {bool returnToIdle = true, int? limitMs}) {
    if (_disposed) return;
    _endTimer?.cancel();
    _idleTimer?.cancel();

    // 同一条 loop 视频不要重放：重设 src 会触发一次黑帧
    if (clip.id == _lastId && clip.loop && isPlaying) {
      return;
    }
    _lastId = clip.id;
    _state = PlaybackState(
      clip: clip,
      startedAtMs: DateTime.now().millisecondsSinceEpoch,
    );
    _notify();

    // loop 片段默认永远不自己结束，等外部切走；但给了 limitMs 就照时限切
    if (clip.loop && limitMs == null) {
      return;
    }
    _endTimer = Timer(Duration(milliseconds: limitMs ?? clip.durationMs), () {
      if (_disposed) return;
      _state = _state?.copyWith(done: true);
      _notify();
      if (returnToIdle) {
        final idle = reg.idle;
        _lastId = idle.id;
        _state = PlaybackState(
          clip: idle,
          startedAtMs: DateTime.now().millisecondsSinceEpoch,
        );
        _notify();
      }
      _scheduleAuto();
    });
  }

  /// 抽一条随机动作（避开当前和上一条）。
  Clip randomAct({String? cat}) =>
      reg.randomAct(cat: cat, avoid: _lastId, rng: _rng);

  /// 从某个分类抽并播放。
  void playFromCat(String cat) {
    final c = reg.pickFromCat(cat, avoid: _lastId, rng: _rng);
    _play(c);
  }

  /// 播一条招牌互动。
  void playFeature(Feature f) {
    final c = reg.resolveFeature(f, avoid: _lastId, rng: _rng);
    if (c != null) _play(c);
  }

  /// 排下一次空闲表演。当前动作不是待机就不排，播完再排。
  void _scheduleAuto() {
    if (_disposed || !autoPerform) return;
    if (current.id != reg.idle.id) return;
    final ms = idleMinMs + _rng.nextInt(max(1, idleMaxMs - idleMinMs));
    _idleTimer?.cancel();
    _idleTimer = Timer(Duration(milliseconds: ms), () {
      if (_disposed || !autoPerform) return;
      if (current.id != reg.idle.id) return;
      _play(randomAct());
    });
  }

  /// App 回到前台或刚启动时调一次，开始空闲节拍。
  void restartIdleClock() => _scheduleAuto();

  /// 立刻来一段。用户想看她动的时候用。
  void performNow({String? cat}) => _play(randomAct(cat: cat));

  void dispose() {
    _disposed = true;
    _endTimer?.cancel();
    _idleTimer?.cancel();
    _listeners.clear();
  }
}
