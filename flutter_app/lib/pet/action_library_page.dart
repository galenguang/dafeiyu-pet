import 'package:flutter/material.dart';

import 'action_registry.dart';
import 'action_scheduler.dart';
import 'manifest.dart';

/// 动作库：12 个分类 + 100 段点播。
/// 对应桌面版 `ui/panel.py` 的 28 个招牌互动 + 完整动作库。
class ActionLibraryPage extends StatefulWidget {
  final ActionRegistry registry;
  final ActionScheduler scheduler;
  const ActionLibraryPage({
    super.key,
    required this.registry,
    required this.scheduler,
  });

  @override
  State<ActionLibraryPage> createState() => _ActionLibraryPageState();
}

class _ActionLibraryPageState extends State<ActionLibraryPage>
    with SingleTickerProviderStateMixin {
  late TabController _tabs;

  @override
  void initState() {
    super.initState();
    // 第一个 tab 放「招牌互动」，后面每个分类一个
    _tabs = TabController(
      length: widget.registry.categories.length + 1,
      vsync: this,
    );
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final cats = widget.registry.categories;
    return Scaffold(
      appBar: AppBar(
        title: const Text('动作库'),
        bottom: TabBar(
          controller: _tabs,
          isScrollable: true,
          tabAlignment: TabAlignment.start,
          tabs: [
            const Tab(text: '招牌'),
            for (final c in cats) Tab(text: '${c.label} ${c.icon}'),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tabs,
        children: [
          _FeatureGrid(registry: widget.registry, scheduler: widget.scheduler),
          for (final c in cats)
            _ClipGrid(
              clips: widget.registry.byCat(c.key),
              scheduler: widget.scheduler,
            ),
        ],
      ),
    );
  }
}

class _FeatureGrid extends StatelessWidget {
  final ActionRegistry registry;
  final ActionScheduler scheduler;
  const _FeatureGrid({required this.registry, required this.scheduler});

  @override
  Widget build(BuildContext context) {
    final items = registry.features;
    return GridView.builder(
      padding: const EdgeInsets.all(12),
      gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
        maxCrossAxisExtent: 112,
        mainAxisSpacing: 10,
        crossAxisSpacing: 10,
        childAspectRatio: 0.94,
      ),
      itemCount: items.length,
      itemBuilder: (ctx, i) {
        final f = items[i];
        return _Tile(
          icon: f.icon,
          label: f.label,
          sub: f.kind == 'pool' ? '随机' : f.kind,
          onTap: () {
            scheduler.playFeature(f);
            Navigator.of(ctx).pop();
          },
        );
      },
    );
  }
}

class _ClipGrid extends StatelessWidget {
  final List<Clip> clips;
  final ActionScheduler scheduler;
  const _ClipGrid({required this.clips, required this.scheduler});

  @override
  Widget build(BuildContext context) {
    if (clips.isEmpty) {
      return const Center(child: Text('这个分类还没有动作'));
    }
    return GridView.builder(
      padding: const EdgeInsets.all(12),
      gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
        maxCrossAxisExtent: 104,
        mainAxisSpacing: 10,
        crossAxisSpacing: 10,
        childAspectRatio: 0.9,
      ),
      itemCount: clips.length,
      itemBuilder: (ctx, i) {
        final c = clips[i];
        return _Tile(
          icon: c.icon,
          label: c.name,
          sub: c.loop ? '循环' : '${(c.durationMs / 1000).toStringAsFixed(1)}s',
          onTap: () {
            scheduler.play(c.id, returnToIdle: true);
            Navigator.of(ctx).pop();
          },
        );
      },
    );
  }
}

class _Tile extends StatelessWidget {
  final String icon;
  final String label;
  final String sub;
  final VoidCallback onTap;
  const _Tile({
    required this.icon,
    required this.label,
    required this.sub,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 8),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(icon, style: const TextStyle(fontSize: 24)),
              const SizedBox(height: 4),
              Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  fontSize: 12.5,
                  fontWeight: FontWeight.w600,
                  color: Color(0xFF2B2F38),
                ),
              ),
              Text(
                sub,
                style: const TextStyle(
                  fontSize: 10.5,
                  color: Color(0xFF9AA0AE),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
