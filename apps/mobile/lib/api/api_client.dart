import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

import '../config/app_config.dart';
import 'api_exception.dart';

/// Thin HTTP transport for the SocialPilot API.
///
/// Holds no business rules: it attaches the bearer token, decodes JSON, and
/// converts failures into [ApiException]. The bearer token is never logged.
class ApiClient {
  ApiClient({required this.config, http.Client? client})
      : _client = client ?? http.Client(),
        _ownsClient = client == null;

  final AppConfig config;
  final http.Client _client;
  final bool _ownsClient;

  Map<String, String> get _headers => {
        'Authorization': 'Bearer ${config.identityToken}',
        'Accept': 'application/json',
      };

  Uri _uri(String path) => Uri.parse('${config.apiBaseUrl}$path');

  Future<dynamic> get(String path) async {
    return _send(() => _client.get(_uri(path), headers: _headers));
  }

  Future<dynamic> post(
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
  }) async {
    final headers = {
      ..._headers,
      if (body != null) 'Content-Type': 'application/json',
      'Idempotency-Key': ?idempotencyKey,
    };
    return _send(
      () => _client.post(
        _uri(path),
        headers: headers,
        body: body == null ? null : jsonEncode(body),
      ),
    );
  }

  /// Streams [length] bytes from [stream] to an object-storage part URL.
  ///
  /// The body is streamed, so a large video is never held in memory. Returns the
  /// storage-provided ETag for the completion call.
  Future<String> putPart({
    required String uploadUrl,
    required Stream<List<int>> stream,
    required int length,
    required String contentType,
  }) async {
    final request = http.StreamedRequest('PUT', Uri.parse(uploadUrl))
      ..headers['Content-Type'] = contentType
      ..contentLength = length;
    // Deliberately no Authorization header: the part URL carries its own grant.
    stream.listen(
      request.sink.add,
      onError: request.sink.addError,
      onDone: request.sink.close,
      cancelOnError: true,
    );
    try {
      final response = await _client
          .send(request)
          .timeout(config.uploadTimeout)
          .then(http.Response.fromStream);
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw ApiException(
          'Video parçası yüklenemedi (${response.statusCode}).',
          statusCode: response.statusCode,
        );
      }
      final etag = response.headers['etag'] ?? response.headers['ETag'];
      if (etag == null || etag.isEmpty) {
        throw const ApiException('Depolama sağlayıcısı parça etiketi döndürmedi.');
      }
      return etag.replaceAll('"', '');
    } on SocketException {
      throw const NetworkException('Depolama adresine ulaşılamıyor.');
    } on HttpException {
      throw const NetworkException('Depolama adresine ulaşılamıyor.');
    }
  }

  Future<dynamic> _send(Future<http.Response> Function() send) async {
    final http.Response response;
    try {
      response = await send().timeout(config.requestTimeout);
    } on SocketException {
      throw const NetworkException();
    } on HttpException {
      throw const NetworkException();
    } on FormatException {
      throw const ApiException('Sunucu adresi geçersiz.');
    }
    if (response.statusCode == 204 || response.body.isEmpty) {
      return null;
    }
    final dynamic decoded;
    try {
      decoded = jsonDecode(utf8.decode(response.bodyBytes));
    } on FormatException {
      if (response.statusCode >= 400) {
        throw ApiException.fromProblem(response.statusCode, null);
      }
      throw const ApiException('Sunucu yanıtı okunamadı.');
    }
    if (response.statusCode >= 400) {
      throw ApiException.fromProblem(
        response.statusCode,
        decoded is Map<String, dynamic> ? decoded : null,
      );
    }
    return decoded;
  }

  void close() {
    if (_ownsClient) {
      _client.close();
    }
  }
}
