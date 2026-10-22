import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';

import '../core/settings.dart';
import '../llm/llm_client.dart';
import '../memory/memory.dart';

/// 一条消息。
class ChatMessage {
  final String role; // user / assistant / error
  final String text;
  final DateTime at;
  const ChatMessage(this.role, this.text, this.at);

  bool get isUser => role == 'user';
  bool get isError => role == 'error';

  Map<String, dynamic> toJson() => {
    'role': role,
    'text': text,
    'at': at.toIso8601String(),
  };

  static ChatMessage? fromJson(Map<String, dynamic> j) {
    final r = j['role'];
    final t = j['text'];
    if (r is! String || t is! String || t.isEmpty) return null;
    return ChatMessage(r, t, DateTime.tryParse('${j['at']}') ?? DateTime.now());
  }
}

/// 聊天会话：历史 + system prompt + 记忆注入 + 调模型。
///
/// 对应桌面版 `app/llm.py` 的 `Chat` 类。几个刻意保留的设计：
///
/// - **记忆单独作为一条 system 消息**，不拼进 user 消息。拼进去模型会
///   把它当成主人说的话，转头就复述出来。
/// - **历史只留最近 40 条**，再多既费 token 又会把当前话题挤没。
/// - 出错也写进列表（role=error），这样用户能看到「为什么没回」。
class ChatSession extends ChangeNotifier {
  static const String systemPrompt =
      '你是桌面宠物「大肥鱼」，一只 DeepSeek 鲸鱼娘。'
      '性格贱兮兮但很贴心，说话短、口语化、有点毒舌但从不真伤人。'
      '每句话不超过 25 个字，不加引号，不说教，偶尔吐槽主人摸鱼。'
      '现在你住在主人的手机里，不是电脑上了。';

  static const int maxHistory = 40;

  final AppSettings Function() cfg;
  final Memory? memory;
  final String? historyPath;

  final List<ChatMessage> _messages = [];
  List<ChatMessage> get messages => List.unmodifiable(_messages);

  bool _busy = false;
  bool get busy => _busy;

  ChatSession({required this.cfg, this.memory, this.historyPath}) {
    _load();
  }

  // ---------------- 持久化 ----------------
  void _load() {
    final p = historyPath;
    if (p == null) return;
    try {
      final f = File(p);
      if (!f.existsSync()) return;
      final j = jsonDecode(f.readAsStringSync());
      if (j is! List) return;
      for (final it in j) {
        if (it is Map) {
          final m = ChatMessage.fromJson(Map<String, dynamic>.from(it));
          if (m != null) _messages.add(m);
        }
      }
      while (_messages.length > maxHistory) {
        _messages.removeAt(0);
      }
    } catch (e) {
      debugPrint('[chat] 历史读取失败: $e');
    }
  }

  void _save() {
    final p = historyPath;
    if (p == null) return;
    try {
      final tail = _messages.length > maxHistory
          ? _messages.sublist(_messages.length - maxHistory)
          : _messages;
      File(p).writeAsStringSync(
        jsonEncode(tail.map((m) => m.toJson()).toList()),
        flush: true,
      );
    } catch (e) {
      debugPrint('[chat] 历史保存失败: $e');
    }
  }

  void clear() {
    _messages.clear();
    _save();
    notifyListeners();
  }

  /// 只清掉错误消息，历史保留。
  void dropErrors() {
    _messages.removeWhere((m) => m.isError);
    _save();
    notifyListeners();
  }

  // ---------------- 发送 ----------------
  Future<void> send(String raw) async {
    final text = raw.trim();
    if (text.isEmpty || _busy) return;

    final c = cfg();
    final why = LlmClient.notReadyReason(c);
    _messages.add(ChatMessage('user', text, DateTime.now()));
    if (why != null) {
      _messages.add(ChatMessage('error', '$why，先去设置里填一下', DateTime.now()));
      _save();
      notifyListeners();
      return;
    }

    _busy = true;
    notifyListeners();

    // 先记到记忆里（规则轨同步跑，模型轨异步跑）
    memory?.noteUser(text);

    try {
      final messages = _buildMessages(c, text);
      var reply = await LlmClient.complete(c, messages, maxTokens: 200);
      reply = reply.replaceAll('\n', ' ').trim();
      reply = reply.replaceAll(RegExp(r'^["“”\x27]+|["“”\x27]+$'), '');
      if (reply.length > 120) reply = '${reply.substring(0, 118)}…';

      _messages.add(ChatMessage('assistant', reply, DateTime.now()));
      memory?.noteReply(reply);
    } on LlmException catch (e) {
      _messages.add(ChatMessage('error', e.message, DateTime.now()));
    } catch (e) {
      _messages.add(
        ChatMessage(
          'error',
          '出错了：${e.toString().substring(0, e.toString().length.clamp(0, 30))}',
          DateTime.now(),
        ),
      );
    } finally {
      _busy = false;
      while (_messages.length > maxHistory) {
        _messages.removeAt(0);
      }
      _save();
      notifyListeners();
    }
  }

  /// 组装请求消息。
  ///
  /// 顺序很重要：system 人设 → system 记忆 → 历史 → 当前这句。
  /// 记忆放在人设之后、历史之前，模型会把它当"背景设定"而不是"说过的话"。
  List<Map<String, String>> _buildMessages(AppSettings c, String text) {
    final out = <Map<String, String>>[
      {'role': 'system', 'content': systemPrompt},
    ];
    final block = memory?.contextBlock(text) ?? '';
    if (block.isNotEmpty) {
      out.add({'role': 'system', 'content': block});
    }
    for (final m in _messages) {
      if (m.isError) continue; // 错误提示不是对话内容
      if (m.role != 'user' && m.role != 'assistant') continue;
      out.add({'role': m.role, 'content': m.text});
    }
    // _messages 最后一条已经是刚才 push 的 user，上面循环会把它带进来，
    // 所以这里不再重复追加。
    return out;
  }
}
