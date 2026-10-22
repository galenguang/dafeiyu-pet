import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/services.dart' show rootBundle;

/// 给 WebView 用的本地素材服务器。
///
/// 为什么必须起 HTTP 服务，不能直接用 `file://` 或 `loadFlutterAsset`：
///
/// 1. WebView 读 `file:///android_asset/...` 时，不同 WebView 版本对
///    跨源资源（video）的策略不一致，有的版本直接拒绝播放。
/// 2. ASSET 协议只认识 Flutter 注册过的 asset，`.webm` 是二进制，
///    走 `loadFlutterAsset` 会被 MIME 猜错。
/// 3. 捆 127.0.0.1 的 loopback 服务是唯一在所有版本上行为一致的方案。
///    这个方案的可行性在 M0 spike 里已经用 adb reverse 等价验证过。
///
/// 端口取 0 让系统分配，避免和其它 App 抢 8080/8765。
class ClipServer {
  HttpServer? _server;

  /// 当前监听端口。WebView 加载页面前必须先 await [start]。
  int get port => _server?.port ?? 0;

  bool get running => _server != null;

  /// 这些 MIME 不写对，WebView 会拒绝当 video 播。
  static const _mime = {
    '.webm': 'video/webm',
    '.mp4': 'video/mp4',
    '.html': 'text/html; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
  };

  Future<int> start() async {
    if (_server != null) return _server!.port;
    // 只绑 loopback：不对外网暴露，也不弹防火墙
    final s = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server = s;
    s.listen(_handle, onError: (_) {});
    return s.port;
  }

  Future<void> stop() async {
    final s = _server;
    _server = null;
    await s?.close(force: true);
  }

  Future<void> _handle(HttpRequest req) async {
    try {
      final path = req.uri.path;
      if (path == '/' || path == '/index.html') {
        final html = await _indexHtml();
        _send(
          req,
          200,
          utf8.encode(html),
          _mime['.html']!,
          contentTypeCharset: false,
        );
        return;
      }
      if (path.startsWith('/clip/')) {
        var name = path.substring('/clip/'.length);
        if (name.contains('..') || name.contains('/') || name.contains(r'\')) {
          _send(req, 400, utf8.encode('bad path'), 'text/plain');
          return;
        }
        if (!name.endsWith('.webm')) name = '$name.webm';
        await _sendAsset(req, 'assets/clips/$name', _mime['.webm']!);
        return;
      }
      _send(req, 404, utf8.encode('not found'), 'text/plain');
    } catch (_) {
      try {
        _send(req, 500, utf8.encode('error'), 'text/plain');
      } catch (_) {}
    }
  }

  void _send(
    HttpRequest req,
    int code,
    List<int> body,
    String mime, {
    bool contentTypeCharset = true,
  }) {
    final res = req.response;
    res.statusCode = code;
    res.headers.set(HttpHeaders.contentTypeHeader, mime);
    res.headers.set(HttpHeaders.contentLengthHeader, '${body.length}');
    res.headers.set(HttpHeaders.cacheControlHeader, 'no-store');
    res.add(body);
    res.close();
  }

  /// 支持 Range：WebView 的 video 元素一定会发 Range 请求，
  /// 不回 206 的实现在部分机型上表现为「一直转圈不播」。
  Future<void> _sendAsset(HttpRequest req, String key, String mime) async {
    ByteData data;
    try {
      data = await rootBundle.load(key);
    } catch (_) {
      _send(req, 404, utf8.encode('asset not found'), 'text/plain');
      return;
    }
    final bytes = data.buffer.asUint8List(
      data.offsetInBytes,
      data.lengthInBytes,
    );
    final total = bytes.length;
    final range = req.headers.value(HttpHeaders.rangeHeader);

    if (range != null && range.startsWith('bytes=')) {
      final m = RegExp(r'bytes=(\d*)-(\d*)').firstMatch(range);
      if (m != null) {
        var start = m.group(1)!.isEmpty ? 0 : int.parse(m.group(1)!);
        var end = m.group(2)!.isEmpty ? total - 1 : int.parse(m.group(2)!);
        if (start >= total) {
          final res = req.response;
          res.statusCode = 416;
          res.headers.set(HttpHeaders.contentRangeHeader, 'bytes */$total');
          await res.close();
          return;
        }
        if (end >= total) end = total - 1;
        final chunk = Uint8List.sublistView(bytes, start, end + 1);
        final res = req.response;
        res.statusCode = 206;
        res.headers.set(HttpHeaders.contentTypeHeader, mime);
        res.headers.set(HttpHeaders.acceptRangesHeader, 'bytes');
        res.headers.set(
          HttpHeaders.contentRangeHeader,
          'bytes $start-$end/$total',
        );
        res.headers.set(HttpHeaders.contentLengthHeader, '${chunk.length}');
        res.headers.set(HttpHeaders.cacheControlHeader, 'max-age=86400');
        res.add(chunk);
        await res.close();
        return;
      }
    }

    final res = req.response;
    res.statusCode = 200;
    res.headers.set(HttpHeaders.contentTypeHeader, mime);
    res.headers.set(HttpHeaders.acceptRangesHeader, 'bytes');
    res.headers.set(HttpHeaders.contentLengthHeader, '$total');
    res.headers.set(HttpHeaders.cacheControlHeader, 'max-age=86400');
    res.add(bytes);
    await res.close();
  }

  Future<String> _indexHtml() async {
    // 页面本身也走 assets，方便改样式不用重编 Dart。
    try {
      return await rootBundle.loadString('assets/stage.html');
    } catch (_) {
      return _fallbackHtml;
    }
  }

  static const _fallbackHtml = '''
<!DOCTYPE html><html><head><meta charset="utf-8">
<style>html,body{margin:0;background:transparent}</style></head>
<body><script>console.log('stage.html 缺失')</script></body></html>
''';
}
