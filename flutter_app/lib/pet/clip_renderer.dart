import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';

import 'clip_server.dart';

/// WebView 发回来的事件。
@immutable
class StageEvent {
  final String type; // ready / playing / meta / probe / error / preloaded
  final Map<String, dynamic> data;
  const StageEvent(this.type, this.data);

  @override
  String toString() => 'StageEvent($type, $data)';
}

/// VP9-alpha 的播放入口。
///
/// 设计要点（每一条都是踩过坑才定的）：
///
/// 1. **先起服务再建 WebView**。页面里的 `/clip/xxx` 是相对路径，
///    依赖服务端口已知。反过来会导致首批帧 404。
/// 2. **预建、只切 opacity**。换 `src` 会触发重新加载和一次黑帧；
///    页面里维护一个 video 池，切动作只改 class。
/// 3. **背景必须是透明的**。`setBackgroundColor(Colors.transparent)`
///    漏掉就是一块白板，而且在浅色主题下不容易发现。
/// 4. **预加载窗口**。全 100 段一次性建 video 会吃掉大量解码器实例，
///    手机上直接 OOM。这里只保证「下一步可能用到的 N 个」已就绪。
class ClipRenderer extends ChangeNotifier {
  final ClipServer server = ClipServer();
  WebViewController? _controller;

  final _events = StreamController<StageEvent>.broadcast();
  Stream<StageEvent> get events => _events.stream;

  String? _currentName;
  bool _flip = false;
  bool _ready = false;
  bool _paused = false;
  final Set<String> _preloaded = {};

  /// 一次最多保留多少个解码器实例。实测 6 个以内很稳，
  /// 超过 12 个在低端机上开始出现 "Too many open files"。
  static const int maxPool = 8;

  bool get ready => _ready;
  String? get currentName => _currentName;
  bool get flip => _flip;

  /// 等页面 ready + 服务起来。返回是否成功。
  Future<bool> start() async {
    // 必须先起服务再建 WebView：页面里的 /clip/xxx 是相对路径，
    // 端口未知时首批请求会 404。
    await server.start();
    final c = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      // 透明背景：这两行缺一个都会露出白底
      ..setBackgroundColor(const Color(0x00000000))
      ..addJavaScriptChannel(
        'stagePost',
        onMessageReceived: (msg) {
          try {
            final m = jsonDecode(msg.message) as Map<String, dynamic>;
            final t = (m['type'] as String?) ?? 'unknown';
            // 调试期把关键事件打到 logcat，方便 adb logcat -s flutter 看
            if (t != 'playing') {
              debugPrint('[stage] $t ${jsonEncode(m)}');
            }
            if (t == 'recv') _jsRecv++;
            if (t == 'ready') _ready = true;
            if (t == 'playing') _currentName = m['name'] as String?;
            _events.add(StageEvent(t, m));
          } catch (_) {}
        },
      )
      ..setNavigationDelegate(
        NavigationDelegate(
          onPageFinished: (_) {
            // onPageFinished 早于 JS 的 load 事件是常见情况，
            // 这里补一次 ready，防止永远等不到
            _markReady();
          },
          onWebResourceError: (e) {
            debugPrint('[stage] 资源错误 ${e.errorCode} ${e.description}');
          },
        ),
      );

    // 先赋给字段，再发起加载。
    //
    // 顺序反了会丢指令：onPageFinished 早于 loadRequest 的 Future 完成时，
    // ready 事件会先到，上层立刻开始下发 play，而此刻 _controller 还是
    // null，_js() 直接 return —— 指令静默消失。
    // M1 自检第一轮就是这个症状：JS 下发计数只有 2 条，其余全丢。
    _controller = c;
    await c.loadRequest(
      Uri.parse('http://127.0.0.1:${server.port}/index.html'),
    );
    return true;
  }

  /// 页面可以收指令了。
  ///
  /// 除了置内部标志，还要**补发一个 ready 事件**——因为 `onPageFinished`
  /// 往往早于页面脚本里的 `post({type:'ready'})`，甚至（通道名写错时）
  /// 那条 ready 永远收不到。上层 `waitFor('ready')` 等的是事件流，
  /// 这里不发的话它只能干等到超时（8 秒），首屏白等。
  void _markReady() {
    if (_ready) return;
    _ready = true;
    _events.add(const StageEvent('ready', {'type': 'ready', 'from': 'native'}));
    notifyListeners();
  }

  Future<void> _js(Map<String, dynamic> payload) async {
    final c = _controller;
    if (c == null) {
      _jsErrors++;
      _jsLastError = 'controller 未就绪';
      debugPrint('[stage] JS 丢弃（controller 未就绪）: $payload');
      return;
    }
    try {
      // 把 JSON 做 base64（ASCII 安全字符集），JS 侧再 atob 解回来。
      //
      // 为什么不用「拼字符串 + 转义引号」：
      // 之前那版 `'${json.replaceAll(...)}'` 在遇到中文、反斜杠、
      // 单引号时会把 JS 源码拼坏，runJavaScript 抛异常被 catch 吞掉，
      // 表现为「指令静默丢失」——真机上 chase 了半天才发现
      // animal-halo 这些片段连一次播放请求都没发生过。
      // base64 之后只有 [A-Za-z0-9+/=]，无论内容是什么都不可能拼坏。
      final b64 = base64Encode(utf8.encode(jsonEncode(payload)));
      await c.runJavaScript('window.__cmd64 && window.__cmd64("$b64")');
      _jsSent++;
      _jsLastError = '';
    } catch (e) {
      _jsErrors++;
      _jsLastError = '$e';
      debugPrint('[stage] JS 失败($_jsErrors): $e');
    }
  }

  String _jsLastError = '';
  String get jsLastError => _jsLastError;

  /// 页面回报「收到指令」的次数。与 [jsSent] 对比即可判断
  /// 是 Dart 没发出去，还是 JS 没收到。
  int _jsRecv = 0;
  int get jsRecv => _jsRecv;

  /// 下发指令的成功/失败计数。自检页拿它判断「指令是不是静默丢了」。
  int _jsSent = 0;
  int _jsErrors = 0;
  int get jsSent => _jsSent;
  int get jsErrors => _jsErrors;

  /// 播放某个片段名（不含扩展名，如 `idle`）。
  Future<void> play(String name, {bool loop = false, bool? flip}) async {
    if (flip != null) _flip = flip;
    final before = _currentName;
    _currentName = name;
    await _js({'cmd': 'play', 'name': name, 'loop': loop, 'flip': _flip});
    // 池子只增不减，抽到上限就把最久没用的喊掉
    _preloaded.add(name);
    if (_preloaded.length > maxPool) {
      await _evict(name);
    }
    if (before != name) notifyListeners();
  }

  Future<void> setFlip(bool v) async {
    _flip = v;
    await _js({'cmd': 'flip', 'flip': v});
    notifyListeners();
  }

  /// 预加载一批。传入的会是接下来几条可能播放的动作。
  Future<void> preload(Iterable<String> names) async {
    final list = names.take(maxPool).toList();
    if (list.isEmpty) return;
    _preloaded.addAll(list);
    await _js({'cmd': 'preload', 'names': list});
  }

  Future<void> _evict(String keep) async {
    // 页面那边没有删除指令——建了就留着。
    // 这里只是把本地记账截断，避免无限增长。
    // 真正回收靠 WebView 换页（目前不换页）或系统内存压力。
    _preloaded
      ..clear()
      ..add(keep);
  }

  Future<void> pause() async {
    _paused = true;
    await _js({'cmd': 'pause'});
  }

  Future<void> resume() async {
    if (!_paused) return;
    _paused = false;
    await _js({'cmd': 'resume'});
  }

  /// 自检：取某个片段的 alpha 统计（canvas 采样）。
  Future<void> probe(String name) async {
    await _js({'cmd': 'probe', 'name': name});
  }

  /// 等一个特定类型的事件，超时返回 null。自检和首帧等待用。
  Future<StageEvent?> waitFor(
    String type, {
    Duration timeout = const Duration(seconds: 8),
  }) async {
    final done = Completer<StageEvent?>();
    late StreamSubscription<StageEvent> sub;
    final t = Timer(timeout, () {
      if (!done.isCompleted) done.complete(null);
    });
    sub = events.listen((e) {
      if (e.type == type && !done.isCompleted) {
        t.cancel();
        sub.cancel();
        done.complete(e);
      }
    });
    return done.future;
  }

  /// 页面脚本是否活着。
  ///
  /// 探测 `window.__cmd64` 存不存在，而**不是**去 [readDiagnostics] 的文本里
  /// 找 `js-ok`：那段日志是滚动的（只留最近 60 行），跑几十秒之后
  /// 最早那行 `js-ok` 就被后来的 PLAY/HTTP 挤掉了，自检会假失败。
  Future<bool> isPageAlive() async {
    final c = _controller;
    if (c == null) return false;
    try {
      final r = await c.runJavaScriptReturningResult(
        'typeof window.__cmd64 === "function" ? 1 : 0',
      );
      return r.toString().contains('1');
    } catch (_) {
      return false;
    }
  }

  /// 读回页面里的诊断文本。JS 通道若不通，这是唯一能看到内部状态的途径。
  Future<String> readDiagnostics() async {
    final c = _controller;
    if (c == null) return '(no controller)';
    try {
      final r = await c.runJavaScriptReturningResult(
        'window.__diag ? window.__diag() : "(no __diag)"',
      );
      final s = r.toString();
      // 返回值带 JS 字符串引号，去掉
      return s.length > 1 ? s.replaceAll(RegExp(r'^"|"$'), '') : s;
    } catch (e) {
      return '(读取失败: $e)';
    }
  }

  /// 探测某个片段是否真的能解码。返回元数据或 null。
  Future<Map<String, dynamic>?> probeMeta(
    String name, {
    Duration timeout = const Duration(seconds: 6),
  }) async {
    final fut = waitFor('meta', timeout: timeout);
    await play(name, loop: true);
    final ev = await fut;
    return ev?.data;
  }

  WebViewController? get controller => _controller;

  @override
  void dispose() {
    _events.close();
    server.stop();
    super.dispose();
  }
}
