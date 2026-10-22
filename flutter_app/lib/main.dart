import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';

import 'chat/chat_session.dart';
import 'core/settings.dart';
import 'memory/memory.dart';
import 'native/native_bridge.dart';
import 'pet/action_library_page.dart';
import 'pet/action_registry.dart';
import 'pet/action_scheduler.dart';
import 'pet/clip_renderer.dart';
import 'pet/manifest_loader.dart';
import 'pet/pet_stage.dart';
import 'pet/selftest_page.dart';
import 'ui/chat_page.dart';
import 'ui/settings_page.dart';

void main() {
  runApp(const DaFeiYuApp());
}

class DaFeiYuApp extends StatelessWidget {
  const DaFeiYuApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '大肥鱼桌宠',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorSchemeSeed: const Color(0xFF4A90D9),
        scaffoldBackgroundColor: const Color(0xFFF2F4F8),
        // 手机上系统字体足够。刻意不打包雅黑——它不可再分发，
        // 而且 Android 自带思源黑体，中文字形完全够看。
        fontFamilyFallback: const [
          'Noto Sans CJK SC',
          'Source Han Sans SC',
          'PingFang SC',
        ],
      ),
      home: const HomePage(),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> with WidgetsBindingObserver {
  SettingsController? _settings;
  Memory? _memory;
  ChatSession? _chat;

  ActionRegistry? _registry;
  ActionScheduler? _scheduler;
  ClipRenderer? _renderer;

  String? _error;
  bool _booting = true;

  /// 在画中画小窗里。小窗尺寸很小，底栏和气泡要藏掉。
  bool _inPip = false;

  String _bubble = '';
  Timer? _bubbleTimer;
  String _lastLine = '';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    NativeBridge.onPipChanged((inPip) {
      if (!mounted) return;
      setState(() => _inPip = inPip);
    });
    _boot();
  }

  Future<void> _boot() async {
    try {
      // 顺序有讲究：设置 → 数据目录 → 记忆/聊天 → 素材 → 渲染器。
      // 记忆依赖设置里的开关，渲染器依赖素材，不能并行。
      final settings = await SettingsController.boot();
      final dir = await SettingsStore.dir();
      final memory = await Memory.boot(
        '${dir.path}/memory.json',
        () => settings.value,
      );
      final chat = ChatSession(
        cfg: () => settings.value,
        memory: memory,
        historyPath: '${dir.path}/chat_history.json',
      );

      final m = await ManifestLoader.load();
      final reg = ActionRegistry(m);
      final sch = ActionScheduler(reg)
        ..autoPerform = settings.value.autoPerform;
      final rd = ClipRenderer();
      final ok = await rd.start();
      if (!ok) {
        setState(() {
          _error = '本地素材服务启动失败';
          _booting = false;
        });
        return;
      }
      if (!mounted) return;

      settings.addListener(_onSettingsChanged);

      setState(() {
        _settings = settings;
        _memory = memory;
        _chat = chat;
        _registry = reg;
        _scheduler = sch;
        _renderer = rd;
        _booting = false;
      });

      // 页面 ready 之后再下发第一条指令，否则会被静默丢弃
      rd.waitFor('ready').then((_) {
        if (!mounted) return;
        sch.play('idle', returnToIdle: false);
        sch.restartIdleClock();
        if (settings.value.facingLeft) rd.setFlip(true);
        _say(_greeting());
      });
    } catch (e) {
      setState(() {
        _error = '启动失败：$e';
        _booting = false;
      });
    }
  }

  /// 设置变了：把能立刻生效的项同步下去。
  /// （尺寸和朝向是声明式的，靠 setState 重建就够了）
  void _onSettingsChanged() {
    final s = _settings?.value;
    if (s == null || !mounted) return;
    _scheduler?.autoPerform = s.autoPerform;
    _renderer?.setFlip(s.facingLeft);
    setState(() {});
  }

  /// 启动问候。有记忆味的一句就先用它，没有就走按时段的普通问候。
  String _greeting() {
    final memLine = _memory?.line('greet', last: _lastLine);
    if (memLine != null && memLine.isNotEmpty) {
      _lastLine = memLine;
      return memLine;
    }
    return _timeGreeting();
  }

  String _timeGreeting() {
    final h = DateTime.now().hour;
    if (h >= 23 || h < 5) return '这么晚还没睡啊';
    if (h < 9) return '早，今天也要好好吃饭';
    if (h < 12) return '上午好';
    if (h < 14) return '午饭吃了没';
    if (h < 18) return '下午好';
    if (h < 23) return '今天也辛苦了';
    return '在忙呀';
  }

  void _say(String text, {int seconds = 5}) {
    if (text.isEmpty) return;
    _bubbleTimer?.cancel();
    setState(() => _bubble = text);
    _bubbleTimer = Timer(Duration(seconds: seconds), () {
      if (mounted) setState(() => _bubble = '');
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _bubbleTimer?.cancel();
    _settings?.removeListener(_onSettingsChanged);
    _scheduler?.dispose();
    _renderer?.dispose();
    _memory?.dispose();
    super.dispose();
  }

  /// 切后台就暂停解码，回前台恢复。
  /// 不做这个的话，手机放进兜里 10 分钟也会一直在解码 VP9-alpha。
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    switch (state) {
      case AppLifecycleState.paused:
      case AppLifecycleState.hidden:
        _renderer?.pause();
        // 趁还在前台把记忆落盘，后台可能直接被系统杀掉
        _memory?.flush();
      case AppLifecycleState.resumed:
        _renderer?.resume();
      default:
        break;
    }
  }

  // ================= 页面跳转 =================
  void _openChat() {
    final chat = _chat;
    final settings = _settings;
    if (chat == null || settings == null) return;
    Navigator.of(context)
        .push(
          MaterialPageRoute(
            builder: (_) =>
                ChatPage(chat: chat, settings: settings, memory: _memory),
          ),
        )
        // 聊完回来，记忆可能多了几条，顺便刷新一下台词池
        .then((_) {
          if (mounted) setState(() {});
        });
  }

  void _openSettings() {
    final settings = _settings;
    if (settings == null) return;
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) =>
            SettingsPage(settings: settings, memory: _memory, onExit: _exitApp),
      ),
    );
  }

  void _openLibrary() {
    final reg = _registry;
    final sch = _scheduler;
    if (reg == null || sch == null) return;
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => ActionLibraryPage(registry: reg, scheduler: sch),
      ),
    );
  }

  void _openSelftest() {
    final reg = _registry;
    final rd = _renderer;
    if (reg == null || rd == null) return;
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => SelftestPage(registry: reg, renderer: rd),
      ),
    );
  }

  /// 真退出：先落盘、再停服务、最后杀进程。
  Future<void> _exitApp() async {
    await _memory?.flush();
    await _settings?.flush();
    _renderer?.dispose(); // 顺手停掉本地素材服务
    await NativeBridge.exitApp();
  }

  // ================= 交互 =================
  // 台词与时长都对齐桌面版 `pet.py`：
  //   _on_single_click  -> 随机 click 反应 + CLICK_LINES，播 2.8 秒
  //   mouseDoubleClick  -> 桌面上是弹菜单，手机上改成随机表演
  //   mouseMoveEvent    -> 切 drag 循环 + DRAG_LINES
  //   mouseReleaseEvent -> squash 0.8 秒，50% 概率 DROP_LINES

  static const _clickLines = [
    '别戳啦，怪不好意思的',
    '干嘛呀～',
    '痒！',
    '再戳我就咬你了',
    '手拿开，我要工作了',
    '你这是第几次戳我了',
    '唔……好吧，让你戳一下',
  ];

  static const _dragLines = [
    '哇——轻点轻点！',
    '起飞咯——',
    '放我下来！……好吧，再玩一次',
    '晕鱼了晕鱼了……',
    '我恐高！真的！',
    '你要把我扔哪儿去',
  ];

  static const _dropLines = ['啪叽。', '落地成功，姿势优雅', '地板好凉', '再来一次！'];

  final _rng = Random();

  /// 随机取一句，尽量不连着说同一句（桌面版 `lines.pick` 的 last 参数）。
  String _pick(List<String> pool) {
    if (pool.isEmpty) return '';
    if (pool.length == 1) return pool.first;
    for (var i = 0; i < 6; i++) {
      final s = pool[_rng.nextInt(pool.length)];
      if (s != _lastLine) {
        _lastLine = s;
        return s;
      }
    }
    final s = pool[_rng.nextInt(pool.length)];
    _lastLine = s;
    return s;
  }

  void _onTapPet() {
    final reg = _registry!;
    final sch = _scheduler!;
    final clicks = reg.byCat('click');
    if (clicks.isNotEmpty) {
      // 素材每段 10 秒，桌面版只播 2.8 秒就切走
      final c = reg.pickWeighted(clicks, avoid: sch.lastId, rng: _rng);
      sch.play(c.id, limitMs: 2800);
    } else {
      sch.performNow();
    }
    // 有记忆味的一句就用它，没有就走普通点击台词
    final memLine = _memory?.line('click', last: _lastLine);
    _say(
      (memLine != null && memLine.isNotEmpty) ? memLine : _pick(_clickLines),
    );
  }

  void _onDoubleTapPet() {
    // 双击：随机表演一个动作
    _scheduler!.performNow();
  }

  void _onDropPet() {
    // 桌面版松手有一半概率冒一句，每次都说话会很吵
    if (_rng.nextDouble() < 0.5) _say(_pick(_dropLines));
  }

  void _setFacing(bool left) {
    final s = _settings;
    if (s == null || s.value.facingLeft == left) return;
    s.update((v) => v.facingLeft = left);
    _renderer?.setFlip(left);
  }

  void _onPosition(Offset p) {
    _settings?.update((s) {
      s.petX = p.dx;
      s.petY = p.dy;
    });
  }

  // ================= 构建 =================
  @override
  Widget build(BuildContext context) {
    if (_booting) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    if (_error != null) {
      return Scaffold(
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(
                  Icons.error_outline,
                  size: 40,
                  color: Color(0xFFE05B5B),
                ),
                const SizedBox(height: 12),
                Text(_error!, textAlign: TextAlign.center),
                const SizedBox(height: 20),
                FilledButton(
                  onPressed: () {
                    setState(() {
                      _error = null;
                      _booting = true;
                    });
                    _boot();
                  },
                  child: const Text('重试'),
                ),
              ],
            ),
          ),
        ),
      );
    }

    final s = _settings!.value;

    final stage = PetStage(
      registry: _registry!,
      scheduler: _scheduler!,
      renderer: _renderer!,
      facingLeft: s.facingLeft,
      widthFactor: s.petWidthFactor,
      initialPos: Offset(s.petX, s.petY),
      onTap: _onTapPet,
      onDoubleTap: _onDoubleTapPet,
      onFacing: _setFacing,
      onDragStart: () => _say(_pick(_dragLines)),
      onDrop: _onDropPet,
      onPosition: _onPosition,
    );

    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            // 气泡浮在舞台上方，不占布局
            Expanded(
              child: Stack(
                children: [
                  stage,
                  if (!_inPip)
                    Positioned(
                      left: 20,
                      right: 20,
                      top: 8,
                      child: Align(
                        alignment: Alignment.topCenter,
                        child: BubbleOverlay(text: _bubble),
                      ),
                    ),
                ],
              ),
            ),
            // 小窗里空间太小，底栏整个藏掉
            if (!_inPip)
              _BottomBar(
                onChat: _openChat,
                onLibrary: _openLibrary,
                onPerform: () => _scheduler!.performNow(),
                onFlip: () => _setFacing(!s.facingLeft),
                onSettings: _openSettings,
                onSelftest: _openSelftest,
              ),
          ],
        ),
      ),
    );
  }
}

class _BottomBar extends StatelessWidget {
  final VoidCallback onChat;
  final VoidCallback onLibrary;
  final VoidCallback onPerform;
  final VoidCallback onFlip;
  final VoidCallback onSettings;
  final VoidCallback onSelftest;

  const _BottomBar({
    required this.onChat,
    required this.onLibrary,
    required this.onPerform,
    required this.onFlip,
    required this.onSettings,
    required this.onSelftest,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(6, 6, 6, 10),
      child: Row(
        children: [
          _Btn(icon: Icons.chat_bubble_outline, label: '聊天', onTap: onChat),
          _Btn(icon: Icons.grid_view_rounded, label: '动作库', onTap: onLibrary),
          _Btn(icon: Icons.auto_awesome, label: '来一段', onTap: onPerform),
          _Btn(icon: Icons.flip, label: '转身', onTap: onFlip),
          _Btn(icon: Icons.tune, label: '设置', onTap: onSettings),
          _Btn(icon: Icons.science_outlined, label: '自检', onTap: onSelftest),
        ],
      ),
    );
  }
}

class _Btn extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  const _Btn({required this.icon, required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 20, color: const Color(0xFF4A5160)),
              const SizedBox(height: 3),
              Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  fontSize: 10.5,
                  color: Color(0xFF6B7280),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
