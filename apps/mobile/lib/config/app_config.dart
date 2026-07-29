/// Runtime configuration supplied from the environment, never hard-coded.
///
/// Values come from `--dart-define`, so no base URL and no development identity
/// token is committed to source control. See `apps/mobile/README.md` for the
/// exact launch command.
class AppConfig {
  const AppConfig({
    required this.apiBaseUrl,
    required this.identityToken,
    this.pollInterval = const Duration(seconds: 3),
    this.requestTimeout = const Duration(seconds: 20),
    this.uploadTimeout = const Duration(minutes: 10),
  });

  /// Reads configuration from compile-time environment defines.
  factory AppConfig.fromEnvironment() {
    const pollSeconds = int.fromEnvironment('POLL_INTERVAL_SECONDS', defaultValue: 3);
    return AppConfig(
      apiBaseUrl: const String.fromEnvironment('API_BASE_URL'),
      identityToken: const String.fromEnvironment('IDENTITY_TOKEN'),
      pollInterval: Duration(seconds: pollSeconds < 1 ? 1 : pollSeconds),
    );
  }

  final String apiBaseUrl;
  final String identityToken;
  final Duration pollInterval;
  final Duration requestTimeout;
  final Duration uploadTimeout;

  bool get isConfigured => apiBaseUrl.isNotEmpty && identityToken.isNotEmpty;

  /// Human-readable reason the app cannot start, or null when usable.
  String? get configurationProblem {
    if (apiBaseUrl.isEmpty) {
      return 'API_BASE_URL tanımlı değil.';
    }
    if (Uri.tryParse(apiBaseUrl)?.hasScheme != true) {
      return 'API_BASE_URL geçerli bir adres değil.';
    }
    if (identityToken.isEmpty) {
      return 'IDENTITY_TOKEN tanımlı değil.';
    }
    return null;
  }
}
