/// Errors surfaced to the user in Turkish, without leaking transport detail.
class ApiException implements Exception {
  const ApiException(this.message, {this.code, this.statusCode});

  /// Builds a readable message from an RFC 7807 problem response.
  factory ApiException.fromProblem(int statusCode, Map<String, dynamic>? body) {
    final code = body?['code'] as String?;
    final detail = body?['detail'] as String?;
    return ApiException(
      _messageFor(statusCode, code, detail),
      code: code,
      statusCode: statusCode,
    );
  }

  final String message;
  final String? code;
  final int? statusCode;

  /// True when retrying the same request could plausibly succeed.
  bool get isRetryable => statusCode == null || statusCode! >= 500 || statusCode == 429;

  static String _messageFor(int statusCode, String? code, String? detail) {
    return switch (code) {
      'AUTHENTICATION_REQUIRED' => 'Oturum bulunamadı. Geliştirme jetonunu kontrol edin.',
      'INVALID_IDENTITY_TOKEN' => 'Kimlik jetonu geçersiz. Yapılandırmayı kontrol edin.',
      'AUTHORIZATION_DENIED' => 'Bu işlem için yetkiniz yok.',
      'BUSINESS_NOT_FOUND' => 'İşletme bulunamadı.',
      'MEDIA_ASSET_NOT_FOUND' => 'Video kaydı bulunamadı.',
      'UPLOAD_SESSION_NOT_FOUND' => 'Yükleme oturumu bulunamadı.',
      'UPLOAD_SESSION_EXPIRED' => 'Yükleme oturumunun süresi doldu. Tekrar deneyin.',
      'IDEMPOTENCY_CONFLICT' => 'Aynı anahtar farklı bir istekle kullanılmış.',
      'IDEMPOTENCY_IN_PROGRESS' => 'Aynı istek hâlâ işleniyor. Biraz bekleyin.',
      'MEDIA_TYPE_NOT_ALLOWED' => 'Bu dosya türü desteklenmiyor. MP4 seçin.',
      'MEDIA_TOO_LARGE' => 'Video izin verilen boyuttan büyük.',
      _ => _fallbackFor(statusCode, detail),
    };
  }

  static String _fallbackFor(int statusCode, String? detail) {
    if (statusCode == 401 || statusCode == 403) {
      return 'Erişim reddedildi. Geliştirme jetonunu kontrol edin.';
    }
    if (statusCode == 404) {
      return 'Kayıt bulunamadı.';
    }
    if (statusCode == 409) {
      return detail ?? 'İstek mevcut durumla çakışıyor.';
    }
    if (statusCode >= 500) {
      return 'Sunucu şu anda yanıt veremiyor. Tekrar deneyin.';
    }
    return detail ?? 'Beklenmeyen bir hata oluştu ($statusCode).';
  }

  @override
  String toString() => message;
}

/// A transport-level failure: no route to the API, DNS, TLS, or timeout.
class NetworkException extends ApiException {
  const NetworkException([super.message = 'Sunucuya ulaşılamıyor. Bağlantınızı kontrol edin.']);

  @override
  bool get isRetryable => true;
}
