import 'package:flutter/material.dart';

import '../core/settings.dart';
import '../llm/llm_client.dart';
import '../memory/memory.dart';
import '../native/native_bridge.dart';
import 'memory_page.dart';

/// 设置页。四块：外观、大模型、记忆、应用。
///
/// 布局刻意用「一屏滚到底」而不是多级菜单——手机上多一层跳转就多一次
/// 找不到东西的机会，这些设置加起来也没多到需要分页。
class SettingsPage extends StatefulWidget {
  final SettingsController settings;
  final Memory? memory;
  final VoidCallback? onExit;

  const SettingsPage({
    super.key,
    required this.settings,
    this.memory,
    this.onExit,
  });

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  late final TextEditingController _base;
  late final TextEditingController _key;
  late final TextEditingController _model;
  late final TextEditingController _timeout;

  bool _showKey = false;
  bool _testing = false;
  String? _testMsg;
  bool _testOk = false;
  bool _pipSupported = false;

  @override
  void initState() {
    super.initState();
    final s = widget.settings.value;
    _base = TextEditingController(text: s.llmBaseUrl);
    _key = TextEditingController(text: s.llmApiKey);
    _model = TextEditingController(text: s.llmModel);
    _timeout = TextEditingController(text: '${s.llmTimeout}');
    NativeBridge.pipSupported().then((v) {
      if (mounted) setState(() => _pipSupported = v);
    });
  }

  @override
  void dispose() {
    _base.dispose();
    _key.dispose();
    _model.dispose();
    _timeout.dispose();
    super.dispose();
  }

  void _applyPreset(String? name) {
    if (name == null) return;
    final p = LlmClient.presetOf(name);
    if (p == null) return;
    setState(() {
      if (p.base.isNotEmpty) _base.text = p.base;
      if (p.model.isNotEmpty) _model.text = p.model;
    });
    widget.settings.update((s) {
      s.llmPreset = name;
      if (p.base.isNotEmpty) s.llmBaseUrl = p.base;
      if (p.model.isNotEmpty) s.llmModel = p.model;
    });
  }

  Future<void> _test() async {
    setState(() {
      _testing = true;
      _testMsg = null;
    });
    // 先把输入框里的值落到设置，否则测的还是旧的
    _commitText();
    try {
      final msg = await LlmClient.ping(widget.settings.value);
      if (!mounted) return;
      setState(() {
        _testOk = true;
        _testMsg = msg;
      });
    } on LlmException catch (e) {
      if (!mounted) return;
      setState(() {
        _testOk = false;
        _testMsg = e.message;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _testOk = false;
        _testMsg = '失败：$e';
      });
    } finally {
      if (mounted) setState(() => _testing = false);
    }
  }

  void _commitText() {
    widget.settings.update((s) {
      s.llmBaseUrl = _base.text.trim();
      s.llmApiKey = _key.text.trim();
      s.llmModel = _model.text.trim();
      s.llmTimeout = (int.tryParse(_timeout.text.trim()) ?? 30).clamp(5, 120);
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('设置')),
      body: ListenableBuilder(
        listenable: widget.settings,
        builder: (ctx, _) {
          final s = widget.settings.value;
          return ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
            children: [
              _section('外观'),
              _card(
                child: Column(
                  children: [
                    Row(
                      children: [
                        const Text('大小'),
                        const Spacer(),
                        Text(
                          '${(s.petWidthFactor * 100).round()}%',
                          style: const TextStyle(
                            color: Color(0xFF4A5160),
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ],
                    ),
                    Slider(
                      value: s.petWidthFactor.clamp(0.3, 0.9),
                      min: 0.3,
                      max: 0.9,
                      divisions: 12,
                      label: '${(s.petWidthFactor * 100).round()}%',
                      // 拖动过程中只更新内存，松手才落盘——
                      // 每动一格写一次文件会把闪存写爆
                      onChanged: (v) => widget.settings.update(
                        (s) => s.petWidthFactor = v,
                        save: false,
                      ),
                      onChangeEnd: (_) => widget.settings.flush(),
                    ),
                    const Text(
                      '她本人占屏幕宽度的比例。太小看不清表情，太大横向就没地方拖了。',
                      style: TextStyle(fontSize: 12, color: Color(0xFF8A8F99)),
                    ),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      value: s.autoPerform,
                      onChanged: (v) =>
                          widget.settings.update((s) => s.autoPerform = v),
                      title: const Text('空闲时自己表演'),
                      subtitle: const Text('12～26 秒随机来一段'),
                    ),
                  ],
                ),
              ),

              _section('大模型'),
              _card(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    DropdownButtonFormField<String>(
                      initialValue: LlmClient.presetOf(s.llmPreset) == null
                          ? null
                          : s.llmPreset,
                      decoration: const InputDecoration(
                        labelText: '预设',
                        isDense: true,
                        border: OutlineInputBorder(),
                      ),
                      items: LlmClient.presets
                          .map(
                            (p) => DropdownMenuItem(
                              value: p.name,
                              child: Text(
                                p.name,
                                style: const TextStyle(fontSize: 14),
                              ),
                            ),
                          )
                          .toList(),
                      onChanged: _applyPreset,
                    ),
                    if (LlmClient.presetOf(s.llmPreset)?.hint case final hint?
                        when hint.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 6, left: 2),
                        child: Text(
                          hint,
                          style: const TextStyle(
                            fontSize: 12,
                            color: Color(0xFF8A8F99),
                          ),
                        ),
                      ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _base,
                      decoration: const InputDecoration(
                        labelText: 'Base URL',
                        hintText: 'https://api.deepseek.com/v1',
                        isDense: true,
                        border: OutlineInputBorder(),
                      ),
                      keyboardType: TextInputType.url,
                      autocorrect: false,
                      onEditingComplete: _commitText,
                      onTapOutside: (_) => _commitText(),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _key,
                      obscureText: !_showKey,
                      decoration: InputDecoration(
                        labelText: 'API Key',
                        isDense: true,
                        border: const OutlineInputBorder(),
                        suffixIcon: IconButton(
                          icon: Icon(
                            _showKey
                                ? Icons.visibility_off_outlined
                                : Icons.visibility_outlined,
                          ),
                          onPressed: () => setState(() => _showKey = !_showKey),
                        ),
                      ),
                      autocorrect: false,
                      enableSuggestions: false,
                      onEditingComplete: _commitText,
                      onTapOutside: (_) => _commitText(),
                    ),
                    const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      flex: 2,
                      child: TextField(
                        controller: _model,
                        decoration: const InputDecoration(
                          labelText: '模型名',
                          hintText: 'deepseek-chat',
                          isDense: true,
                          border: OutlineInputBorder(),
                        ),
                        autocorrect: false,
                        onEditingComplete: _commitText,
                        onTapOutside: (_) => _commitText(),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: TextField(
                        controller: _timeout,
                        // 标签只写「超时」，「秒」放 suffix——写「超时(秒)」
                        // 在窄列里会被截成「超…」
                        decoration: const InputDecoration(
                          labelText: '超时',
                          suffixText: '秒',
                          isDense: true,
                          border: OutlineInputBorder(),
                        ),
                        keyboardType: TextInputType.number,
                        onEditingComplete: _commitText,
                        onTapOutside: (_) => _commitText(),
                      ),
                    ),
                  ],
                ),
                    const SizedBox(height: 14),
                    FilledButton.tonal(
                      onPressed: _testing ? null : _test,
                      child: Text(_testing ? '测试中…' : '测试连接'),
                    ),
                    if (_testMsg != null)
                      Padding(
                        padding: const EdgeInsets.only(top: 10),
                        child: Row(
                          children: [
                            Icon(
                              _testOk
                                  ? Icons.check_circle_outline
                                  : Icons.error_outline,
                              size: 18,
                              color: _testOk
                                  ? const Color(0xFF2E9E5B)
                                  : const Color(0xFFE05B5B),
                            ),
                            const SizedBox(width: 6),
                            Expanded(
                              child: Text(
                                _testMsg!,
                                style: TextStyle(
                                  fontSize: 13,
                                  color: _testOk
                                      ? const Color(0xFF2E9E5B)
                                      : const Color(0xFFE05B5B),
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    const SizedBox(height: 6),
                    const Text(
                      '只要符合 OpenAI 接口格式就能用，包括各种中转站和本地 Ollama。',
                      style: TextStyle(fontSize: 12, color: Color(0xFF8A8F99)),
                    ),
                  ],
                ),
              ),

              _section('记忆'),
              _card(
                child: Column(
                  children: [
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      value: s.memoryEnabled,
                      onChanged: (v) =>
                          widget.settings.update((s) => s.memoryEnabled = v),
                      title: const Text('记住聊天内容'),
                      subtitle: const Text('关掉之后不再抽取，已有记忆保留'),
                    ),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      value: s.memoryLlmExtract,
                      onChanged: s.memoryEnabled
                          ? (v) => widget.settings.update(
                              (s) => s.memoryLlmExtract = v,
                            )
                          : null,
                      title: const Text('用模型抽取'),
                      subtitle: const Text('关掉就只走本地规则，不花 token'),
                    ),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      value: s.memorySpeak,
                      onChanged: s.memoryEnabled
                          ? (v) =>
                                widget.settings.update((s) => s.memorySpeak = v)
                          : null,
                      title: const Text('在台词里提起记得的事'),
                    ),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: OutlinedButton.icon(
                        icon: const Icon(Icons.psychology_outlined, size: 18),
                        label: const Text('打开记忆面板'),
                        onPressed: widget.memory == null
                            ? null
                            : () => Navigator.of(context).push(
                                MaterialPageRoute(
                                  builder: (_) => MemoryPage(
                                    memory: widget.memory!,
                                    settings: widget.settings,
                                  ),
                                ),
                              ),
                      ),
                    ),
                  ],
                ),
              ),

              _section('应用'),
              _card(
                child: Column(
                  children: [
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(
                        Icons.picture_in_picture_alt_outlined,
                      ),
                      title: const Text('小窗模式'),
                      subtitle: Text(
                        _pipSupported ? '缩成系统画中画，浮在其他应用上' : '这台设备不支持画中画',
                      ),
                      enabled: _pipSupported,
                      onTap: _pipSupported
                          ? () async {
                              final ok = await NativeBridge.enterPip();
                              if (!ok && context.mounted) {
                                ScaffoldMessenger.of(context).showSnackBar(
                                  const SnackBar(
                                    content: Text('进不了小窗，可能被系统拦了'),
                                  ),
                                );
                              }
                            }
                          : null,
                    ),
                    const Divider(height: 1),
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(
                        Icons.power_settings_new,
                        color: Color(0xFFE05B5B),
                      ),
                      title: const Text(
                        '退出应用',
                        style: TextStyle(color: Color(0xFFE05B5B)),
                      ),
                      subtitle: const Text('结束进程并从最近任务里移除'),
                      onTap: () => _confirmExit(context),
                    ),
                  ],
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  Future<void> _confirmExit(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('退出应用'),
        content: const Text('会结束进程并从最近任务里移除，下次要重新打开。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('算了'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('退出'),
          ),
        ],
      ),
    );
    if (ok == true) {
      await widget.memory?.flush();
      widget.onExit?.call();
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
    padding: const EdgeInsets.fromLTRB(14, 10, 14, 14),
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
