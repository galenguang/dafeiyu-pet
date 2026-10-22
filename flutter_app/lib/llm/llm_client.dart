import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../core/settings.dart';

/// 一个预设：只是帮用户少打几个字，选完仍然可以随手改。
class LlmPreset {
  final String name;
  final String base;
  final String model;
  final String hint;
  const LlmPreset(this.name, this.base, this.model, this.hint);
}

/// 大模型接入：任何兼容 OpenAI 接口格式的服务都能用。
///
/// 不绑定厂商，只认三样东西：base_url / api_key / model。
/// DeepSeek、OpenAI、通义千问、Kimi、智谱、硅基流动、Ollama、LM Studio、
/// vLLM、各种 one-api / new-api 中转站，只要是 OpenAI 格式就都能跑。
///
/// 这份实现是桌面版 `app/llm.py` 的移植，**错误文案逐字对齐**——
/// 手机上气泡和对话框能显示的字数有限，那些话都是压缩过的。
class LlmClient {
  static const List<LlmPreset> presets = [
    LlmPreset(
      'DeepSeek',
      'https://api.deepseek.com/v1',
      'deepseek-chat',
      'platform.deepseek.com 申请',
    ),
    LlmPreset(
      'OpenAI',
      'https://api.openai.com/v1',
      'gpt-4o-mini',
      'platform.openai.com 申请',
    ),
    LlmPreset(
      '通义千问',
      'https://dashscope.aliyuncs.com/compatible-mode/v1',
      'qwen-plus',
      '阿里云百炼控制台申请',
    ),
    LlmPreset(
      'Kimi',
      'https://api.moonshot.cn/v1',
      'moonshot-v1-8k',
      'platform.moonshot.cn 申请',
    ),
    LlmPreset(
      '智谱 GLM',
      'https://open.bigmodel.cn/api/paas/v4',
      'glm-4-flash',
      'open.bigmodel.cn 申请，flash 免费',
    ),
    LlmPreset(
      '硅基流动',
      'https://api.siliconflow.cn/v1',
      'Qwen/Qwen2.5-7B-Instruct',
      'cloud.siliconflow.cn 申请',
    ),
    LlmPreset(
      'Ollama（本地）',
      'http://localhost:11434/v1',
      'qwen2.5:7b',
      '本地跑，Key 随便填或留空；模型名用 ollama list 看',
    ),
    LlmPreset(
      'LM Studio（本地）',
      'http://localhost:1234/v1',
      'local-model',
      '本地跑，Key 留空即可',
    ),
    LlmPreset('自定义', '', '', '直接填服务商给的 Base URL，一般以 /v1 结尾'),
  ];

  static LlmPreset? presetOf(String name) {
    for (final p in presets) {
      if (p.name == name) return p;
    }
    return null;
  }

  // ---------------- 地址处理 ----------------
  /// 把用户填的 Base URL 收拾成标准形式（末尾不带斜杠）。
  ///
  /// 容忍这些写法：
  ///   api.deepseek.com              -> https://api.deepseek.com/v1
  ///   https://api.openai.com        -> https://api.openai.com/v1
  ///   https://api.openai.com/v1/    -> https://api.openai.com/v1
  ///   https://x/v1/chat/completions -> https://x/v1
  ///   http://localhost:11434        -> http://localhost:11434/v1
  static String normalizeBase(String base) {
    var b = base.trim();
    while (b.endsWith('/')) {
      b = b.substring(0, b.length - 1);
    }
    if (b.isEmpty) return '';
    if (!b.startsWith('http://') && !b.startsWith('https://')) {
      final host = b.split('/').first.split(':').first.toLowerCase();
      const local = {'localhost', '127.0.0.1', '0.0.0.0', '::1', '10.0.2.2'};
      b = (local.contains(host) ? 'http://' : 'https://') + b;
    }
    for (final suffix in ['/chat/completions', '/completions']) {
      if (b.endsWith(suffix)) {
        b = b.substring(0, b.length - suffix.length);
        break;
      }
    }
    while (b.endsWith('/')) {
      b = b.substring(0, b.length - 1);
    }
    final uri = Uri.tryParse(b);
    final path = uri?.path ?? '';
    if (path.isEmpty || path == '/') b = '$b/v1';
    return b;
  }

  static String chatUrl(String base) {
    final b = normalizeBase(base);
    return b.isEmpty ? '' : '$b/chat/completions';
  }

  static bool isLocal(String base) {
    final b = normalizeBase(base).toLowerCase();
    return b.contains('localhost') ||
        b.contains('127.0.0.1') ||
        b.contains('0.0.0.0') ||
        b.contains('10.0.2.2') ||
        b.contains('192.168.') ||
        b.contains('10.');
  }

  /// 能不能直接开聊。返回 null 表示可以，否则是给用户看的原因。
  static String? notReadyReason(AppSettings c) {
    if (normalizeBase(c.llmBaseUrl).isEmpty) return '还没配接口地址';
    if (c.llmModel.trim().isEmpty) return '还没填模型名';
    if (c.llmApiKey.trim().isEmpty && !isLocal(c.llmBaseUrl)) {
      return '还没填 API Key';
    }
    return null;
  }

  static String maskKey(String key) {
    if (key.isEmpty) return '';
    if (key.length <= 8) return '•' * key.length;
    return '${key.substring(0, 4)}${'•' * 6}${key.substring(key.length - 4)}';
  }

  // ---------------- 错误文案 ----------------
  /// 从各家五花八门的错误体里挖出一句人话。
  static String _apiMessage(String body) {
    try {
      final data = jsonDecode(body);
      if (data is Map) {
        final err = data['error'];
        if (err is Map) {
          final m = err['message'] ?? err['msg'];
          if (m is String && m.isNotEmpty) return m;
        } else if (err is String && err.isNotEmpty) {
          return err;
        }
        for (final k in ['message', 'msg', 'detail']) {
          final v = data[k];
          if (v is String && v.isNotEmpty) return v;
        }
      }
    } catch (_) {}
    return body.trim();
  }

  static String friendlyError(int status, String body) {
    final detail = _apiMessage(body);
    final low = detail.toLowerCase();
    switch (status) {
      case 401:
        return 'Key 不对或没权限';
      case 403:
        return '这个 Key 没权限用这个模型';
      case 404:
        return '接口地址不对，检查 Base URL';
      case 429:
        return '请求太频繁，等下再说';
      case 400:
        if (low.contains('model') &&
            (low.contains('not exist') ||
                low.contains('not found') ||
                low.contains('invalid'))) {
          return '模型名不对，检查 model';
        }
        if (low.contains('context') || low.contains('too long')) {
          return '聊太长了，清一下历史吧';
        }
        return detail.isEmpty ? '请求参数被拒了' : _clip(detail, 30);
    }
    if (status >= 500) return '服务端抽风了（$status）';
    return detail.isEmpty ? '请求失败（$status）' : _clip(detail, 30);
  }

  static String _clip(String s, int n) =>
      s.length <= n ? s : '${s.substring(0, n)}…';

  // ---------------- 请求 ----------------
  /// 发一次对话请求，返回正文。失败抛 [LlmException]（文案可直接显示）。
  ///
  /// 推理模型（o1/o3、部分 GLM）不收 `temperature` / `max_tokens`，
  /// 遇到 400 时按报错自动降级重试一次，和桌面版一致。
  static Future<String> complete(
    AppSettings c,
    List<Map<String, String>> messages, {
    int maxTokens = 400,
    double temperature = 0.95,
  }) async {
    final payload = <String, dynamic>{
      'model': c.llmModel.trim(),
      'messages': messages,
      'max_tokens': maxTokens,
      'temperature': temperature,
      'stream': false,
    };

    var resp = await _post(c, payload);
    if (resp.status == 400) {
      final low = _apiMessage(resp.body).toLowerCase();
      var changed = false;
      if (low.contains('temperature')) {
        payload.remove('temperature');
        changed = true;
      }
      if (low.contains('max_tokens') &&
          !payload.containsKey('max_completion_tokens')) {
        payload['max_completion_tokens'] = payload.remove('max_tokens');
        changed = true;
      }
      if (changed) resp = await _post(c, payload);
    }

    if (resp.status != 200) {
      throw LlmException(friendlyError(resp.status, resp.body));
    }

    try {
      final data = jsonDecode(resp.body);
      final choices = (data is Map ? data['choices'] : null) as List?;
      final first = (choices != null && choices.isNotEmpty) ? choices[0] : null;
      var content = (first is Map ? first['message'] : null);
      if (content is Map) content = content['content'];
      if (content is List) {
        content = content
            .whereType<Map>()
            .map((p) => p['text']?.toString() ?? '')
            .join();
      }
      final text = content?.toString().trim() ?? '';
      if (text.isEmpty) throw LlmException('模型没说话，换个模型试试');
      return text;
    } on LlmException {
      rethrow;
    } catch (_) {
      throw LlmException('返回格式看不懂，确认是 OpenAI 接口');
    }
  }

  /// 发一个最小请求验证 base_url / key / model 是否配套。
  static Future<String> ping(AppSettings c) async {
    final why = notReadyReason(c);
    if (why != null) throw LlmException(why);
    final text = await complete(
      c,
      [
        {'role': 'user', 'content': '说个“在”'},
      ],
      maxTokens: 16,
      temperature: 0.3,
    );
    return '连上了：${_clip(text.replaceAll('\n', ' '), 16)}';
  }

  static Future<_Resp> _post(
    AppSettings c,
    Map<String, dynamic> payload,
  ) async {
    final url = chatUrl(c.llmBaseUrl);
    if (url.isEmpty) throw LlmException('还没配接口地址');
    final client = HttpClient()
      ..connectionTimeout = Duration(seconds: c.llmTimeout);
    try {
      final req = await client.postUrl(Uri.parse(url));
      req.headers.contentType = ContentType(
        'application',
        'json',
        charset: 'utf-8',
      );
      final key = c.llmApiKey.trim();
      if (key.isNotEmpty) req.headers.set('Authorization', 'Bearer $key');
      final raw = utf8.encode(jsonEncode(payload));
      // **必须显式设 Content-Length**。不设的话 dart:io 会改用 chunked
      // 传输编码，一部分网关和自建中转站不认，直接回 400。
      req.contentLength = raw.length;
      req.add(raw);

      final resp = await req.close().timeout(Duration(seconds: c.llmTimeout));
      final body = await resp
          .transform(utf8.decoder)
          .join()
          .timeout(Duration(seconds: c.llmTimeout));
      return _Resp(resp.statusCode, body);
    } on TimeoutException {
      throw LlmException('等太久了，接口有点慢');
    } on HandshakeException {
      throw LlmException('HTTPS 证书有问题');
    } on SocketException {
      throw LlmException('连不上，检查下地址和网络');
    } on FormatException {
      throw LlmException('Base URL 格式不对');
    } finally {
      client.close(force: true);
    }
  }
}

class _Resp {
  final int status;
  final String body;
  const _Resp(this.status, this.body);
}

/// 可以直接显示给用户的错误。
class LlmException implements Exception {
  final String message;
  const LlmException(this.message);
  @override
  String toString() => message;
}
