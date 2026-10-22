import 'package:flutter/material.dart';

import '../core/settings.dart';
import '../memory/memory.dart';
import '../memory/memory_model.dart';

/// 记忆面板：看得见、能改、能删。
///
/// 桌面版这里是 1021 行的复杂面板（分页 + 时间轴 + 编辑弹窗）。
/// 手机上刻意做薄：一屏看完，所有操作都是点一下，不引入第二个层级。
/// 理由是手机上没人会花两分钟整理记忆，但「看到她记住了什么」很重要。
class MemoryPage extends StatefulWidget {
  final Memory memory;
  final SettingsController settings;
  const MemoryPage({super.key, required this.memory, required this.settings});

  @override
  State<MemoryPage> createState() => _MemoryPageState();
}

class _MemoryPageState extends State<MemoryPage> {
  bool _organizing = false;

  @override
  Widget build(BuildContext context) {
    final store = widget.memory.store;
    final rel = store.relationship;
    final days = store.daysTogether;
    final streak = MemModel.asInt(rel['streak_days'], def: 1, lo: 1);

    return Scaffold(
      appBar: AppBar(
        title: const Text('她记得的事'),
        actions: [
          IconButton(
            tooltip: '整理一下',
            icon: _organizing
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.auto_fix_high),
            onPressed: _organizing ? null : _organize,
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          _relationCard(days, streak),
          _section('教她一件事'),
          _card(
            child: Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    icon: const Icon(Icons.add, size: 18),
                    label: const Text('手动记一条'),
                    onPressed: _teach,
                  ),
                ),
              ],
            ),
          ),
          _section('记着的事实'),
          _facts(),
          _section('喜欢'),
          _chips(store.likes, empty: '还不知道你喜欢什么'),
          _section('不喜欢'),
          _chips(store.dislikes, empty: '还没听你抱怨过什么'),
          _section('最近的事'),
          _episodes(),
          const SizedBox(height: 26),
          Center(
            child: TextButton(
              onPressed: _confirmClear,
              child: const Text(
                '清空全部记忆',
                style: TextStyle(color: Color(0xFFE05B5B)),
              ),
            ),
          ),
        ],
      ),
    );
  }

  // ---------------- 卡片 ----------------
  Widget _relationCard(int days, int streak) {
    final store = widget.memory.store;
    final name = store.callMe;
    final job = (store.profile['occupation'] ?? '').toString();
    final intimacy = store.intimacy;

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [Color(0xFF4A90D9), Color(0xFF6BA8E5)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            name.isEmpty ? '还没问你叫什么' : '你是$name${job.isEmpty ? '' : '，$job'}',
            style: const TextStyle(
              color: Colors.white,
              fontSize: 16,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              _stat('$days', '认识天数'),
              _stat('$streak', '连续天数'),
              _stat(MemModel.intimacyLabel(intimacy), '关系'),
            ],
          ),
          const SizedBox(height: 12),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: intimacy / 100,
              minHeight: 5,
              backgroundColor: Colors.white24,
              valueColor: const AlwaysStoppedAnimation(Colors.white),
            ),
          ),
        ],
      ),
    );
  }

  Widget _stat(String value, String label) => Expanded(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          value,
          style: const TextStyle(
            color: Colors.white,
            fontSize: 18,
            fontWeight: FontWeight.w700,
          ),
        ),
        Text(
          label,
          style: const TextStyle(color: Colors.white70, fontSize: 11),
        ),
      ],
    ),
  );

  Widget _facts() {
    final facts = widget.memory.store.facts;
    if (facts.isEmpty) {
      return _card(
        child: const Text(
          '还是空的。多聊聊，或者点上面「手动记一条」。',
          style: TextStyle(fontSize: 13, color: Color(0xFF8A8F99)),
        ),
      );
    }
    // 置顶的排前面
    final sorted = List<Map<String, dynamic>>.from(facts)
      ..sort((a, b) {
        final pa = a['pinned'] == true ? 1 : 0;
        final pb = b['pinned'] == true ? 1 : 0;
        return pb - pa;
      });

    return _card(
      child: Column(
        children: [
          for (var i = 0; i < sorted.length; i++) ...[
            if (i > 0) const Divider(height: 14),
            Row(
              children: [
                Icon(
                  sorted[i]['pinned'] == true ? Icons.push_pin : Icons.circle,
                  size: sorted[i]['pinned'] == true ? 16 : 6,
                  color: sorted[i]['pinned'] == true
                      ? const Color(0xFF4A90D9)
                      : const Color(0xFFC4C9D2),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    '${sorted[i]['text']}',
                    style: const TextStyle(fontSize: 14, height: 1.4),
                  ),
                ),
                IconButton(
                  visualDensity: VisualDensity.compact,
                  icon: const Icon(
                    Icons.close,
                    size: 16,
                    color: Color(0xFFB0B5BE),
                  ),
                  onPressed: () => setState(() {
                    widget.memory.store.removeFact('${sorted[i]['id']}');
                  }),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Widget _chips(List<Map<String, dynamic>> items, {required String empty}) {
    if (items.isEmpty) {
      return _card(
        child: Text(
          empty,
          style: const TextStyle(fontSize: 13, color: Color(0xFF8A8F99)),
        ),
      );
    }
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: items
          .map(
            (e) => Chip(
              label: Text('${e['text']}', style: const TextStyle(fontSize: 13)),
              visualDensity: VisualDensity.compact,
            ),
          )
          .toList(),
    );
  }

  Widget _episodes() {
    final eps = widget.memory.store.episodes;
    if (eps.isEmpty) {
      return _card(
        child: const Text(
          '还没有值得记住的事',
          style: TextStyle(fontSize: 13, color: Color(0xFF8A8F99)),
        ),
      );
    }
    return _card(
      child: Column(
        children: [
          for (var i = 0; i < eps.length && i < 20; i++) ...[
            if (i > 0) const Divider(height: 12),
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '${eps[i]['text']}',
                        style: const TextStyle(fontSize: 14, height: 1.4),
                      ),
                      Text(
                        _ago(MemModel.daysSince('${eps[i]['created']}')),
                        style: const TextStyle(
                          fontSize: 11,
                          color: Color(0xFF9AA0AA),
                        ),
                      ),
                    ],
                  ),
                ),
                IconButton(
                  visualDensity: VisualDensity.compact,
                  icon: const Icon(
                    Icons.close,
                    size: 16,
                    color: Color(0xFFB0B5BE),
                  ),
                  onPressed: () => setState(() {
                    widget.memory.store.removeEpisode('${eps[i]['id']}');
                  }),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  // ---------------- 操作 ----------------
  Future<void> _teach() async {
    final ctrl = TextEditingController();
    final text = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('教她一件事'),
        content: TextField(
          controller: ctrl,
          autofocus: true,
          maxLength: 40,
          decoration: const InputDecoration(
            hintText: '比如：我的生日是 3 月 12 号',
            border: OutlineInputBorder(),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('算了'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, ctrl.text),
            child: const Text('记住'),
          ),
        ],
      ),
    );
    if (text != null && text.trim().isNotEmpty) {
      widget.memory.teach(text);
      if (mounted) setState(() {});
    }
  }

  Future<void> _organize() async {
    setState(() => _organizing = true);
    final n = await widget.memory.organize();
    if (!mounted) return;
    setState(() => _organizing = false);
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(n > 0 ? '整理好了，新记住 $n 条' : '没找到新东西'),
        duration: const Duration(seconds: 2),
      ),
    );
  }

  Future<void> _confirmClear() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('清空全部记忆'),
        content: const Text('称呼、喜好、事实、所有片段都会删掉，不可恢复。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('算了'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: const Color(0xFFE05B5B),
            ),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('清空'),
          ),
        ],
      ),
    );
    if (ok == true) {
      widget.memory.store.clearAll();
      if (mounted) setState(() {});
    }
  }

  Widget _section(String title) => Padding(
    padding: const EdgeInsets.fromLTRB(4, 18, 4, 8),
    child: Text(
      title,
      style: const TextStyle(
        fontSize: 13,
        fontWeight: FontWeight.w700,
        color: Color(0xFF6B7280),
        letterSpacing: 0.5,
      ),
    ),
  );

  Widget _card({required Widget child}) => Container(
    width: double.infinity,
    padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
    decoration: BoxDecoration(
      color: Colors.white,
      borderRadius: BorderRadius.circular(14),
      boxShadow: const [
        BoxShadow(
          color: Color(0x0D000000),
          blurRadius: 10,
          offset: Offset(0, 2),
        ),
      ],
    ),
    child: child,
  );
}

/// 把「几天前」说成人话。
///
/// 直接拼 `$d 天前` 会出现「0 天前」——刚记下来的事写着 0 天前很怪，
/// 中文里这种情形一律说「今天」。1 天也习惯说「昨天」。
String _ago(int days) {
  if (days <= 0) return '今天';
  if (days == 1) return '昨天';
  if (days < 30) return '$days 天前';
  if (days < 365) return '${days ~/ 30} 个月前';
  return '${days ~/ 365} 年前';
}
