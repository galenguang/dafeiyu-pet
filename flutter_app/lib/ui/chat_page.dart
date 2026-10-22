import 'package:flutter/material.dart';

import '../chat/chat_session.dart';
import '../core/settings.dart';
import '../llm/llm_client.dart';
import 'settings_page.dart';
import '../memory/memory.dart';

/// 聊天对话框。
///
/// 布局要点：消息列表用 `reverse: true` 的 ListView，这样新消息天然在底部，
/// 而且键盘弹起时不用手动滚动到底——这是最省事也最不容易出 bug 的做法。
class ChatPage extends StatefulWidget {
  final ChatSession chat;
  final SettingsController settings;
  final Memory? memory;

  const ChatPage({
    super.key,
    required this.chat,
    required this.settings,
    this.memory,
  });

  @override
  State<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends State<ChatPage> {
  final _input = TextEditingController();
  final _focus = FocusNode();
  final _scroll = ScrollController();

  @override
  void dispose() {
    _input.dispose();
    _focus.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final text = _input.text;
    if (text.trim().isEmpty) return;
    _input.clear();
    await widget.chat.send(text);
    // 发完把焦点留着，方便连着说
    if (mounted) _focus.requestFocus();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('和大肥鱼聊天'),
        actions: [
          IconButton(
            tooltip: '设置',
            icon: const Icon(Icons.tune),
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(
                builder: (_) => SettingsPage(
                  settings: widget.settings,
                  memory: widget.memory,
                ),
              ),
            ),
          ),
          ListenableBuilder(
            listenable: widget.chat,
            builder: (ctx, _) => IconButton(
              tooltip: '清空',
              icon: const Icon(Icons.delete_outline),
              onPressed: widget.chat.messages.isEmpty
                  ? null
                  : () => _confirmClear(context),
            ),
          ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: ListenableBuilder(
                listenable: widget.chat,
                builder: (ctx, _) {
                  final msgs = widget.chat.messages;
                  if (msgs.isEmpty) return _emptyHint();
                  return ListView.builder(
                    controller: _scroll,
                    reverse: true,
                    padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
                    itemCount: msgs.length + (widget.chat.busy ? 1 : 0),
                    itemBuilder: (ctx, i) {
                      if (widget.chat.busy && i == 0) return const _Typing();
                      final idx =
                          msgs.length - 1 - (widget.chat.busy ? i - 1 : i);
                      if (idx < 0 || idx >= msgs.length) {
                        return const SizedBox.shrink();
                      }
                      return _Bubble(msg: msgs[idx]);
                    },
                  );
                },
              ),
            ),
            _inputBar(),
          ],
        ),
      ),
    );
  }

  Widget _emptyHint() {
    final why = LlmClient.notReadyReason(widget.settings.value);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text('🐟', style: TextStyle(fontSize: 46)),
            const SizedBox(height: 14),
            Text(
              why == null ? '说点什么吧' : '$why\n先去设置里填一下模型信息',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Color(0xFF6B7280), height: 1.6),
            ),
            if (why != null) ...[
              const SizedBox(height: 18),
              FilledButton.tonal(
                onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute(
                    builder: (_) => SettingsPage(
                      settings: widget.settings,
                      memory: widget.memory,
                    ),
                  ),
                ),
                child: const Text('去设置'),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _inputBar() {
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 10),
      decoration: const BoxDecoration(
        color: Colors.white,
        border: Border(top: BorderSide(color: Color(0xFFE8EBF0))),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: TextField(
              controller: _input,
              focusNode: _focus,
              minLines: 1,
              maxLines: 4,
              textInputAction: TextInputAction.send,
              onSubmitted: (_) => _send(),
              decoration: InputDecoration(
                hintText: '说点什么…',
                isDense: true,
                filled: true,
                fillColor: const Color(0xFFF2F4F8),
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: 14,
                  vertical: 11,
                ),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(22),
                  borderSide: BorderSide.none,
                ),
              ),
            ),
          ),
          const SizedBox(width: 8),
          ListenableBuilder(
            listenable: widget.chat,
            builder: (ctx, _) => IconButton.filled(
              onPressed: widget.chat.busy ? null : _send,
              icon: const Icon(Icons.arrow_upward, size: 20),
              style: IconButton.styleFrom(minimumSize: const Size(42, 42)),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _confirmClear(BuildContext context) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('清空对话'),
        content: const Text('只清掉聊天记录，记住的事情不会删。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('算了'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('清空'),
          ),
        ],
      ),
    );
    if (ok == true) widget.chat.clear();
  }
}

class _Bubble extends StatelessWidget {
  final ChatMessage msg;
  const _Bubble({required this.msg});

  @override
  Widget build(BuildContext context) {
    final isUser = msg.isUser;
    final isError = msg.isError;

    final bg = isError
        ? const Color(0xFFFFF0F0)
        : (isUser ? const Color(0xFF4A90D9) : Colors.white);
    final fg = isError
        ? const Color(0xFFC0392B)
        : (isUser ? Colors.white : const Color(0xFF2B2F38));

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        mainAxisAlignment: isUser
            ? MainAxisAlignment.end
            : MainAxisAlignment.start,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (!isUser) ...[
            const Text('🐟', style: TextStyle(fontSize: 20)),
            const SizedBox(width: 8),
          ],
          Flexible(
            child: Container(
              constraints: BoxConstraints(
                maxWidth: MediaQuery.of(context).size.width * 0.72,
              ),
              padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 9),
              decoration: BoxDecoration(
                color: bg,
                borderRadius: BorderRadius.only(
                  topLeft: const Radius.circular(14),
                  topRight: const Radius.circular(14),
                  bottomLeft: Radius.circular(isUser ? 14 : 4),
                  bottomRight: Radius.circular(isUser ? 4 : 14),
                ),
                boxShadow: const [
                  BoxShadow(
                    color: Color(0x0D000000),
                    blurRadius: 8,
                    offset: Offset(0, 2),
                  ),
                ],
              ),
              child: Text(
                msg.text,
                style: TextStyle(fontSize: 15, height: 1.45, color: fg),
              ),
            ),
          ),
          if (isUser) const SizedBox(width: 6),
        ],
      ),
    );
  }
}

class _Typing extends StatelessWidget {
  const _Typing();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Text('🐟', style: TextStyle(fontSize: 20)),
          SizedBox(width: 8),
          Text(
            '正在想…',
            style: TextStyle(fontSize: 13, color: Color(0xFF8A8F99)),
          ),
        ],
      ),
    );
  }
}
