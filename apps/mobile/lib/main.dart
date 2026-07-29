import 'package:flutter/material.dart';

import 'api/api_client.dart';
import 'config/app_config.dart';
import 'repositories/business_repository.dart';
import 'repositories/media_repository.dart';
import 'screens/business_list_screen.dart';

void main() {
  runApp(SocialPilotDemoApp(config: AppConfig.fromEnvironment()));
}

/// Root of the media-analysis demo client.
class SocialPilotDemoApp extends StatefulWidget {
  const SocialPilotDemoApp({super.key, required this.config});

  final AppConfig config;

  @override
  State<SocialPilotDemoApp> createState() => _SocialPilotDemoAppState();
}

class _SocialPilotDemoAppState extends State<SocialPilotDemoApp> {
  late final ApiClient _client;

  @override
  void initState() {
    super.initState();
    _client = ApiClient(config: widget.config);
  }

  @override
  void dispose() {
    _client.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final problem = widget.config.configurationProblem;
    return MaterialApp(
      title: 'SocialPilot AI Demo',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF3F51B5)),
        useMaterial3: true,
      ),
      home: problem != null
          ? ConfigurationErrorScreen(problem: problem)
          : BusinessListScreen(
              businesses: BusinessRepository(_client),
              media: MediaRepository(_client),
            ),
    );
  }
}

/// Shown when the build has no API base URL or development identity token.
class ConfigurationErrorScreen extends StatelessWidget {
  const ConfigurationErrorScreen({super.key, required this.problem});

  final String problem;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('Yapılandırma eksik')),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.settings_suggest, size: 56, color: theme.colorScheme.error),
            const SizedBox(height: 16),
            Text(problem, textAlign: TextAlign.center, style: theme.textTheme.titleMedium),
            const SizedBox(height: 16),
            Text(
              'Uygulamayı --dart-define ile API_BASE_URL ve IDENTITY_TOKEN '
              'değerlerini vererek başlatın. Ayrıntılar için apps/mobile/README.md '
              'dosyasına bakın.',
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium,
            ),
          ],
        ),
      ),
    );
  }
}
