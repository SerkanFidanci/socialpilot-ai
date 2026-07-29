import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:socialpilot_mobile/api/api_exception.dart';
import 'package:socialpilot_mobile/main.dart';
import 'package:socialpilot_mobile/models/business.dart';
import 'package:socialpilot_mobile/models/processing_summary.dart';
import 'package:socialpilot_mobile/models/upload_session.dart';
import 'package:socialpilot_mobile/repositories/business_repository.dart';
import 'package:socialpilot_mobile/repositories/media_repository.dart';
import 'package:socialpilot_mobile/screens/business_list_screen.dart';
import 'package:socialpilot_mobile/screens/processing_screen.dart';
import 'package:socialpilot_mobile/screens/result_screen.dart';
import 'package:socialpilot_mobile/screens/upload_screen.dart';
import 'package:socialpilot_mobile/config/app_config.dart';
import 'package:socialpilot_mobile/widgets/coverage_card.dart';
import 'package:socialpilot_mobile/widgets/step_labels.dart';

import 'support/fixtures.dart';

const _business = Business(
  id: 'b1',
  name: 'Kahve Dükkanı',
  slug: 'kahve-dukkani',
  status: 'active',
  timezone: 'Europe/Istanbul',
);

/// Counts upload attempts so a duplicate tap is observable.
class CountingMediaRepository extends MediaRepository {
  CountingMediaRepository({this.completer}) : super(clientFor(FakeHttpClient(const {})));

  /// When set, the transfer blocks until completed, holding the upload in flight.
  final Completer<void>? completer;
  int sessionsCreated = 0;
  int transfers = 0;
  int completions = 0;

  @override
  Future<String> checksumOf(File file, {void Function(int sent)? onProgress}) async {
    onProgress?.call(1);
    return 'a' * 64;
  }

  @override
  Future<UploadSession> createSession({
    required String businessId,
    required String filename,
    required String contentType,
    required int byteSize,
    required String checksum,
    required int partCount,
    required String idempotencyKey,
  }) async {
    sessionsCreated += 1;
    return UploadSession(
      id: 's1',
      assetId: 'asset-1',
      status: 'created',
      expiresAt: DateTime.utc(2026, 7, 30, 10, 15),
      parts: const [UploadPart(partNumber: 1, uploadUrl: 'https://storage.test/1')],
    );
  }

  @override
  Future<List<CompletedPart>> transferParts({
    required File file,
    required UploadSession session,
    required int byteSize,
    required String contentType,
    void Function(int sentBytes)? onProgress,
  }) async {
    transfers += 1;
    onProgress?.call(byteSize);
    if (completer != null) {
      await completer!.future;
    }
    return const [CompletedPart(partNumber: 1, etag: 'etag-1')];
  }

  @override
  Future<String> completeSession({
    required String businessId,
    required String sessionId,
    required String checksum,
    required List<CompletedPart> parts,
    required String idempotencyKey,
  }) async {
    completions += 1;
    return 'asset-1';
  }

  @override
  Future<ProcessingSummary> processingSummary({
    required String businessId,
    required String assetId,
  }) async {
    return ProcessingSummary.fromJson(summaryJson());
  }
}

/// Fails every upload so the error path is observable.
class FailingMediaRepository extends CountingMediaRepository {
  FailingMediaRepository() : super();

  @override
  Future<UploadSession> createSession({
    required String businessId,
    required String filename,
    required String contentType,
    required int byteSize,
    required String checksum,
    required int partCount,
    required String idempotencyKey,
  }) async {
    throw const NetworkException();
  }
}

class StubBusinessRepository extends BusinessRepository {
  StubBusinessRepository(this.items, {this.error}) : super(clientFor(FakeHttpClient(const {})));

  final List<Business> items;
  final ApiException? error;

  @override
  Future<List<Business>> list() async {
    if (error != null) {
      throw error!;
    }
    return items;
  }
}

class StubSummaryRepository extends MediaRepository {
  StubSummaryRepository(this.step) : super(clientFor(FakeHttpClient(const {})));

  final String step;

  @override
  Future<ProcessingSummary> processingSummary({
    required String businessId,
    required String assetId,
  }) async {
    return ProcessingSummary.fromJson(summaryJson(currentStep: step));
  }
}

Widget wrap(Widget child) {
  return MaterialApp(
    theme: ThemeData(colorScheme: ColorScheme.fromSeed(seedColor: Colors.indigo), useMaterial3: true),
    home: child,
  );
}

/// Enlarges the test viewport so a lazily built ListView renders every child.
///
/// The default 800x600 surface leaves later cards unbuilt, and `find.text` only
/// sees widgets that were actually built.
void useTallSurface(WidgetTester tester) {
  tester.view.physicalSize = const Size(1200, 6000);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
}

/// Creates a fixture file synchronously; async I/O inside `testWidgets` would
/// deadlock against the test binding's fake clock.
File tempVideo(String name) {
  final directory = Directory.systemTemp.createTempSync('upload-test');
  final file = File('${directory.path}/$name')
    ..writeAsBytesSync(List<int>.filled(2048, 7));
  addTearDown(() => directory.deleteSync(recursive: true));
  return file;
}

void main() {
  testWidgets('missing configuration shows a setup screen, not a crash', (tester) async {
    await tester.pumpWidget(
      const SocialPilotDemoApp(config: AppConfig(apiBaseUrl: '', identityToken: '')),
    );
    await tester.pump();
    expect(find.byType(ConfigurationErrorScreen), findsOneWidget);
    expect(find.textContaining('API_BASE_URL'), findsWidgets);
  });

  testWidgets('business list shows the tenants it loaded', (tester) async {
    await tester.pumpWidget(
      wrap(
        BusinessListScreen(
          businesses: StubBusinessRepository(const [_business]),
          media: CountingMediaRepository(),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Kahve Dükkanı'), findsOneWidget);
    expect(find.text('Europe/Istanbul · active'), findsOneWidget);
  });

  testWidgets('business list shows an empty state with no tenants', (tester) async {
    await tester.pumpWidget(
      wrap(
        BusinessListScreen(
          businesses: StubBusinessRepository(const []),
          media: CountingMediaRepository(),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Henüz bir işletme yok'), findsOneWidget);
  });

  testWidgets('a backend error on the business list is shown with retry', (tester) async {
    await tester.pumpWidget(
      wrap(
        BusinessListScreen(
          businesses: StubBusinessRepository(
            const [],
            error: const NetworkException(),
          ),
          media: CountingMediaRepository(),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.textContaining('Sunucuya ulaşılamıyor'), findsOneWidget);
    expect(find.text('Tekrar dene'), findsOneWidget);
  });

  testWidgets('upload button is disabled until a file is chosen', (tester) async {
    final repository = CountingMediaRepository();
    await tester.pumpWidget(
      wrap(
        UploadScreen(
          business: _business,
          media: repository,
          pickFile: () async => null,
        ),
      ),
    );
    final button = tester.widget<FilledButton>(find.byKey(const Key('start-upload-button')));
    expect(button.onPressed, isNull);
  });

  testWidgets('tapping upload twice starts only one upload', (tester) async {
    final gate = Completer<void>();
    final repository = CountingMediaRepository(completer: gate);
    final file = tempVideo('clip.mp4');
    await tester.pumpWidget(
      wrap(
        UploadScreen(
          business: _business,
          media: repository,
          pickFile: () async => file,
        ),
      ),
    );

    await tester.tap(find.byKey(const Key('pick-video-button')));
    await tester.pumpAndSettle();
    expect(find.text('clip.mp4'), findsOneWidget);

    // First tap starts the upload and holds it at the transfer stage.
    await tester.tap(find.byKey(const Key('start-upload-button')));
    await tester.pump();
    expect(find.byKey(const Key('upload-progress')), findsOneWidget);

    // Second tap must be ignored: the button is disabled while in flight.
    await tester.tap(find.byKey(const Key('start-upload-button')), warnIfMissed: false);
    await tester.pump();

    expect(repository.sessionsCreated, 1);
    expect(repository.transfers, 1);

    gate.complete();
    // Bounded pumps only: finishing the upload pushes the processing screen, whose
    // progress indicator animates forever, so pumpAndSettle would never return.
    await tester.pump();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(repository.completions, 1);

    // Unmount so the pushed screen's poller cancels its timer.
    await tester.pumpWidget(wrap(const SizedBox.shrink()));
    await tester.pump(const Duration(milliseconds: 50));
  });

  testWidgets('an upload failure is shown and the button becomes usable again', (tester) async {
    final repository = FailingMediaRepository();
    final file = tempVideo('clip.mp4');
    await tester.pumpWidget(
      wrap(
        UploadScreen(
          business: _business,
          media: repository,
          pickFile: () async => file,
        ),
      ),
    );
    await tester.tap(find.byKey(const Key('pick-video-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('start-upload-button')));
    await tester.pumpAndSettle();

    expect(find.textContaining('Sunucuya ulaşılamıyor'), findsOneWidget);
    final button = tester.widget<FilledButton>(find.byKey(const Key('start-upload-button')));
    expect(button.onPressed, isNotNull);
  });

  testWidgets('processing screen shows the active step and blocks results', (tester) async {
    await tester.pumpWidget(
      wrap(
        ProcessingScreen(
          business: _business,
          assetId: 'asset-1',
          media: StubSummaryRepository('scene_speech_analysis'),
          pollInterval: const Duration(milliseconds: 20),
        ),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 30));

    // The key is on the Text itself, so read its data rather than search descendants.
    expect(
      tester.widget<Text>(find.byKey(const Key('current-step-label'))).data,
      'Sahne ve konuşma analizi',
    );
    final button = tester.widget<FilledButton>(find.byKey(const Key('open-result-button')));
    expect(button.onPressed, isNull, reason: 'results open only after a terminal step');

    // Leaving the screen must not leave a timer behind.
    await tester.pumpWidget(wrap(const SizedBox.shrink()));
    await tester.pump(const Duration(milliseconds: 100));
  });

  testWidgets('processing screen enables results when completed', (tester) async {
    await tester.pumpWidget(
      wrap(
        ProcessingScreen(
          business: _business,
          assetId: 'asset-1',
          media: StubSummaryRepository('completed'),
          pollInterval: const Duration(milliseconds: 20),
        ),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 30));

    expect(find.text('Tamamlandı'), findsWidgets);
    final button = tester.widget<FilledButton>(find.byKey(const Key('open-result-button')));
    expect(button.onPressed, isNotNull);
    await tester.pumpWidget(wrap(const SizedBox.shrink()));
    await tester.pump(const Duration(milliseconds: 100));
  });

  testWidgets('processing screen surfaces a terminal failure code', (tester) async {
    await tester.pumpWidget(
      wrap(
        ProcessingScreen(
          business: _business,
          assetId: 'asset-1',
          media: _FailedSummaryRepository(),
          pollInterval: const Duration(milliseconds: 20),
        ),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 30));

    expect(find.textContaining('MEDIA_MALWARE_DETECTED'), findsWidgets);
    await tester.pumpWidget(wrap(const SizedBox.shrink()));
    await tester.pump(const Duration(milliseconds: 100));
  });

  testWidgets('result screen renders full coverage, scenes, transcript and analyses',
      (tester) async {
    useTallSurface(tester);
    final summary = ProcessingSummary.fromJson(summaryJson());
    await tester.pumpWidget(wrap(ResultScreen(business: _business, summary: summary)));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('coverage-card')), findsOneWidget);
    expect(find.text('Tam kapsam'), findsOneWidget);
    expect(find.text('2 / 2 sahne analiz edildi'), findsOneWidget);
    expect(find.textContaining('Analiz edilmeyen sahne'), findsNothing);
    expect(find.text('Sahneler (2)'), findsOneWidget);
    expect(find.text('1920 x 1080'), findsOneWidget);
    expect(find.text('birinci sahne'), findsWidgets);
    expect(find.text('Sahne analiz edildi'), findsNWidgets(2));
    expect(find.text('sahne'), findsWidgets);
    expect(find.byKey(const Key('transcript-only-chip')), findsOneWidget);
    expect(find.byKey(const Key('no-context-chip')), findsOneWidget);
  });

  testWidgets('result screen renders partial coverage with the skipped count', (tester) async {
    useTallSurface(tester);
    final summary = ProcessingSummary.fromJson(
      summaryJson(
        coverage: {
          'total_scene_count': 7,
          'analyzed_scene_count': 5,
          'skipped_scene_count': 2,
          'coverage': 'partial',
          'frame_backed_scene_count': 3,
          'transcript_only_scene_count': 1,
          'no_context_scene_count': 1,
        },
      ),
    );
    await tester.pumpWidget(wrap(ResultScreen(business: _business, summary: summary)));
    await tester.pumpAndSettle();

    expect(find.byType(CoverageCard), findsOneWidget);
    expect(find.text('Kısmi kapsam'), findsOneWidget);
    expect(find.text('5 / 7 sahne analiz edildi'), findsOneWidget);
    expect(find.text('Analiz edilmeyen sahne: 2'), findsOneWidget);
    expect(find.text('Kare destekli: 3'), findsOneWidget);
  });

  testWidgets('result screen handles a no-speech transcript', (tester) async {
    useTallSurface(tester);
    final summary = ProcessingSummary.fromJson(
      summaryJson(
        transcript: {
          'language': 'und',
          'duration_ms': 4000,
          'full_text': '',
          'provider': 'none',
          'status': 'no_speech',
        },
        segments: const [],
      ),
    );
    await tester.pumpWidget(wrap(ResultScreen(business: _business, summary: summary)));
    await tester.pumpAndSettle();
    expect(find.text('Videoda konuşma algılanmadı.'), findsOneWidget);
  });

  group('formatting helpers', () {
    test('timecodes are zero padded', () {
      expect(formatTimecode(0), '00:00.000');
      expect(formatTimecode(1500), '00:01.500');
      expect(formatTimecode(61250), '01:01.250');
      expect(formatTimecode(-5), '00:00.000');
    });

    test('bytes use binary units', () {
      expect(formatBytes(512), '512 B');
      expect(formatBytes(2048), '2.0 KB');
      expect(formatBytes(5 * 1024 * 1024), '5.0 MB');
    });

    test('analysis modes have Turkish labels', () {
      expect(analysisModeLabel('visual'), 'Görsel');
      expect(analysisModeLabel('transcript_only'), 'Yalnızca transkript');
      expect(analysisModeLabel('no_context'), 'Bağlam yok');
      expect(analysisModeLabel(null), 'Bilinmiyor');
    });

    test('part count grows with file size', () {
      expect(MediaRepository.partCountFor(0), 1);
      expect(MediaRepository.partCountFor(1024), 1);
      expect(MediaRepository.partCountFor(8 * 1024 * 1024), 1);
      expect(MediaRepository.partCountFor(8 * 1024 * 1024 + 1), 2);
      expect(MediaRepository.partCountFor(100 * 1024 * 1024), 13);
    });
  });
}

class _FailedSummaryRepository extends MediaRepository {
  _FailedSummaryRepository() : super(clientFor(FakeHttpClient(const {})));

  @override
  Future<ProcessingSummary> processingSummary({
    required String businessId,
    required String assetId,
  }) async {
    return ProcessingSummary.fromJson(
      summaryJson(currentStep: 'failed', terminalFailureCode: 'MEDIA_MALWARE_DETECTED'),
    );
  }
}
