import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';

import 'action_registry.dart';
import 'action_scheduler.dart';
import 'clip_renderer.dart';
import 'manifest.dart';

/// 舞台上的一层：把「调度器选了哪段」翻译成「渲染器播哪段」。
///
/// 桌面版 `pet.py` 里这部分和窗口、气泡、菜单全揉在 1383 行里；
/// 这里刻意只做这一件事，方便单测和替换渲染后端。
///
/// **不包含** `_alpha_hit`（逐像素命中测试）与 `nativeEvent`。
/// 那两个在桌面版存在的唯一目的是「窗口浮在别的应用上时让点击穿透」，
/// 独立 App 里她只在自己画布上，这套东西整个不需要。
/// 手机上用手势区域近似命中：渲染框已经按角色包围盒裁过，
/// 和逐像素判定差别很小（见 [ActionRegistry.cropUnion]）。
class PetStage extends StatefulWidget {
  final ActionRegistry registry;
  final ActionScheduler scheduler;
  final ClipRenderer renderer;

  /// 点她一下时回调（可选）。
  final VoidCallback? onTap;

  /// 双击时回调（可选）。桌面版双击是弹菜单，手机上用户要的是
  /// 「随机表演一个动作」，语义在这里由调用方决定。
  final VoidCallback? onDoubleTap;

  /// 拖动方向变化时回调，`true` 表示朝左。
  /// 桌面版 `mouseMoveEvent` 里 `delta.x() > 12` 就转身，这里等价。
  final ValueChanged<bool>? onFacing;

  /// 开始拖 / 松手。桌面版在这两个时刻分别冒 DRAG_LINES / DROP_LINES。
  final VoidCallback? onDragStart;
  final VoidCallback? onDrop;

  /// 松手时的最终位置（归一化 0..1）。调用方据此持久化，
  /// 下次打开她还站在原处。
  final ValueChanged<Offset>? onPosition;

  /// 初始位置（归一化 0..1）。
  final Offset initialPos;

  /// 是否镜像（朝左）。
  final bool facingLeft;

  /// 她**本人**占舞台宽度的比例。
  ///
  /// 注意语义是「角色本体」而不是「渲染框」：640x360 的画布里她只占
  /// 约 39%（见 [ActionRegistry.charWFrac]），左右是透明留白。
  /// 按渲染框算的话滑块拉到 100% 她也只有屏幕的 39% 宽，调起来像没反应；
  /// 所以这里按本体算，渲染框相应放大到屏幕外——留白透明，看不出来。
  final double widthFactor;

  /// 拖动中播的片段（桌面版是 `drag.webm`，loop）。
  final String dragClipId;

  /// 松手后的缓冲动作与时长（桌面版 `play_action("squash", duration=0.8)`）。
  final String dropClipId;
  final int dropMs;

  const PetStage({
    super.key,
    required this.registry,
    required this.scheduler,
    required this.renderer,
    this.onTap,
    this.onDoubleTap,
    this.onFacing,
    this.onDragStart,
    this.onDrop,
    this.onPosition,
    this.initialPos = const Offset(0.5, 0.5),
    this.facingLeft = false,
    this.widthFactor = 0.55,
    this.dragClipId = 'drag',
    this.dropClipId = 'squash',
    this.dropMs = 800,
  });

  @override
  State<PetStage> createState() => _PetStageState();
}

class _PetStageState extends State<PetStage> {
  String? _shown;

  /// 左上角在「可移动余量」里的归一化位置，`(0,0)` 贴左上、`(1,1)` 贴右下。
  ///
  /// 存归一化而不是像素：横竖屏切换、分屏、折叠屏展开时舞台尺寸会变，
  /// 存像素会让鱼跑到屏幕外面去。
  late Offset _pos = widget.initialPos;

  /// 上一次拖动时的横向符号，避免每帧都回调转身。
  int _lastDxSign = 0;

  /// 是否真的处于拖动中。
  ///
  /// **必须有这个标志**：`onPanCancel` 在「按下去没移动就抬手」时也会触发
  /// （手势被 tap 抢走时 pan 会 cancel）。没有它的话，一次普通单击会先走
  /// `_endDrag` → 播 squash → 冒 DROP_LINES，看起来像「点一下她蹦一下」，
  /// 而且把真正的 onTap 台词盖掉。
  bool _dragging = false;

  @override
  void initState() {
    super.initState();
    widget.scheduler.addListener(_sync);
    widget.renderer.addListener(_onRenderer);
    _sync();
  }

  @override
  void dispose() {
    widget.scheduler.removeListener(_sync);
    widget.renderer.removeListener(_onRenderer);
    super.dispose();
  }

  /// 页面 ready 的那一刻补一次下发。
  ///
  /// 不补的话会「她整个人不见了」：`initState` 里的第一次 `_sync` 会把
  /// `idle` 记成「已下发」，但那时候 WebView 还没加载完，指令被丢掉；
  /// 之后调度器再切到 `idle` 时 `_sync` 一看名字没变就直接 return，
  /// 结果一条播放指令都没真正执行过，页面永远是全透明。
  void _onRenderer() {
    if (widget.renderer.ready) _sync();
  }

  /// 调度器变了就下发播放指令。同一段不重复下发。
  void _sync() {
    if (!mounted) return;
    final clip = widget.scheduler.current;
    final name = clip.file.replaceAll('.webm', '');
    if (name == _shown) return;
    // 页面没 ready 之前下发的指令会被静默丢弃，所以这时候**不记账**，
    // 等 ready 之后 _onRenderer 会重新走到这里。
    if (!widget.renderer.ready) return;
    _shown = name;
    widget.renderer.play(name, loop: clip.loop, flip: widget.facingLeft);
    _primeNext(clip);
  }

  /// 猜下一个可能播的动作并预加载，减少切换时的首帧延迟。
  /// 只预加载 2 个，多了会占用解码器实例。
  void _primeNext(Clip cur) {
    final idle = widget.registry.idle;
    final names = <String>{};
    if (cur.id != idle.id) names.add(idle.file.replaceAll('.webm', ''));
    final guess = widget.registry.randomAct(avoid: cur.id);
    names.add(guess.file.replaceAll('.webm', ''));
    widget.renderer.preload(names);
  }

  void _startDrag() {
    if (_dragging) return;
    _dragging = true;
    _lastDxSign = 0;
    // 桌面版一进入拖动就切到 drag 循环动作，并且**不**自动结束
    widget.scheduler.play(widget.dragClipId, returnToIdle: false);
    widget.onDragStart?.call();
  }

  void _move(Offset delta, double availX, double availY) {
    if (availX <= 0 && availY <= 0) return;

    // 转身：桌面版阈值 12px，这里按逻辑像素取 8
    if (delta.dx.abs() > 8) {
      final sign = delta.dx > 0 ? 1 : -1;
      if (sign != _lastDxSign) {
        _lastDxSign = sign;
        widget.onFacing?.call(sign < 0);
      }
    }

    setState(() {
      _pos = Offset(
        availX <= 0 ? _pos.dx : (_pos.dx + delta.dx / availX).clamp(0.0, 1.0),
        availY <= 0 ? _pos.dy : (_pos.dy + delta.dy / availY).clamp(0.0, 1.0),
      );
    });
  }

  void _endDrag() {
    // 没真的拖过就别动——单击时 pan 会被 tap 抢走并 cancel 到这里
    if (!_dragging) return;
    _dragging = false;
    // 桌面版松手：播一次 squash 缓冲，然后回到待机
    widget.scheduler.play(widget.dropClipId, limitMs: widget.dropMs);
    widget.onDrop?.call();
    // 位置只在松手时回传一次：拖动过程中每帧回调会把设置文件写爆
    widget.onPosition?.call(_pos);
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (ctx, cons) {
        // 先按「她本人要多宽」定角色宽度，再反推渲染框宽度。
        // 渲染框 = 角色宽 / 39%，所以它通常比屏幕宽得多，两侧留白跑到屏幕外。
        // 这不算浪费：留白本来就是透明的，放大她必然要连画布一起放大，
        // 否则 640x360 的固定比例会把她的高度也一起锁死。
        var charW = cons.maxWidth * widget.widthFactor;
        var w = charW / widget.registry.charWFrac;
        var h = w * widget.registry.stageH / widget.registry.stageW;

        // 竖屏窄高时用高度兜底，别让她顶出舞台上下边界
        final maxH = cons.maxHeight * 0.95;
        if (h > maxH) {
          h = maxH;
          w = h * widget.registry.stageW / widget.registry.stageH;
          charW = w * widget.registry.charWFrac;
        }

        // 角色本体两侧的透明留白（对称，因为角色居中）
        final marginX = (w - charW) / 2;

        // 横向可移动余量：从「角色贴左边」到「角色贴右边」
        final availX = (cons.maxWidth - charW).clamp(0.0, double.infinity);
        final availY = (cons.maxHeight - h).clamp(0.0, double.infinity);

        return Stack(
          // Stack 默认就是 Clip.hardEdge，渲染框超出舞台的部分会被裁掉。
          // 这里不显式写 clipBehavior：本文件的 Clip 是 manifest 里的
          // 动作数据类，会把 dart:ui 的 Clip 枚举遮住。
          children: [
            Positioned(
              // 减去 marginX：pos=0 时渲染框左移一个留白，
              // 正好让角色本体贴在屏幕左边
              left: -marginX + _pos.dx * availX,
              top: _pos.dy * availY,
              width: w,
              height: h,
              child: GestureDetector(
                // opaque：整块渲染框都接手势。左右留白虽然透明，
                // 但那是画布的一部分，点到也该算点到她——
                // 桌面版才是逐像素判定，这里用手势区域近似。
                behavior: HitTestBehavior.opaque,
                onTap: widget.onTap,
                onDoubleTap: widget.onDoubleTap,
                onPanStart: (_) => _startDrag(),
                onPanUpdate: (d) => _move(d.delta, availX, availY),
                onPanEnd: (_) => _endDrag(),
                onPanCancel: _endDrag,
                child: ClipRendererView(renderer: widget.renderer),
              ),
            ),
          ],
        );
      },
    );
  }
}

/// 单纯的 WebView 宿主。
/// 独立成组件是为了以后换渲染后端（比如 SBS-H264 + shader）
/// 只改这一个文件，舞台不用动。
class ClipRendererView extends StatelessWidget {
  final ClipRenderer renderer;
  const ClipRendererView({super.key, required this.renderer});

  @override
  Widget build(BuildContext context) {
    final c = renderer.controller;
    if (c == null) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2));
    }
    // **必须包一层 IgnorePointer**。
    //
    // WebView 是原生 View，它自己会消费触摸事件。不挡掉的话：
    // 拖动能用（位移手势在竞技场里能赢过原生 View 的点击识别），
    // 但**纯点击会被 WebView 吃掉**，Flutter 的 onTap 永远不触发——
    // 表现就是「戳她没反应，只有拖着走才有反应」。
    // 页面里没有任何可交互元素（video 也设了 pointer-events:none），
    // 所以整块交给 Flutter 处理是对的。
    return IgnorePointer(child: WebViewWidget(controller: c));
  }
}

/// 简易气泡层。桌面版是 `_paint_bubble` 手绘 + 自动换行 + 描边；
/// 这里用 Flutter 的毛玻璃卡片，视觉上更贴手机，实现也短得多。
class BubbleOverlay extends StatelessWidget {
  final String text;
  final bool visible;
  final bool tailDown;

  const BubbleOverlay({
    super.key,
    required this.text,
    this.visible = true,
    this.tailDown = true,
  });

  @override
  Widget build(BuildContext context) {
    final show = visible && text.trim().isNotEmpty;
    return AnimatedOpacity(
      opacity: show ? 1 : 0,
      duration: const Duration(milliseconds: 160),
      curve: Curves.easeOut,
      child: IgnorePointer(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 11),
          decoration: BoxDecoration(
            color: Colors.white.withValues(alpha: 0.96),
            borderRadius: BorderRadius.circular(18),
            boxShadow: const [
              BoxShadow(
                color: Color(0x1A000000),
                blurRadius: 16,
                offset: Offset(0, 4),
              ),
            ],
          ),
          child: Text(
            text,
            style: const TextStyle(
              fontSize: 15,
              height: 1.45,
              color: Color(0xFF2B2F38),
            ),
          ),
        ),
      ),
    );
  }
}
