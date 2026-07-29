import 'package:flutter_test/flutter_test.dart';
import 'package:socialpilot_mobile/api/api_exception.dart';
import 'package:socialpilot_mobile/models/processing_summary.dart';
import 'package:socialpilot_mobile/repositories/media_repository.dart';
import 'package:socialpilot_mobile/repositories/processing_poller.dart';

import 'support/fixtures.dart';

/// Serves scripted summaries, then repeats the last one.
class ScriptedRepository extends MediaRepository {
  ScriptedRepository(this.steps, {this.error}) : super(clientFor(FakeHttpClient(const {})));

  final List<String> steps;
  final ApiException? error;
  int calls = 0;

  @override
  Future<ProcessingSummary> processingSummary({
    required String businessId,
    required String assetId,
  }) async {
    calls += 1;
    if (error != null) {
      throw error!;
    }
    final index = calls - 1 < steps.length ? calls - 1 : steps.length - 1;
    return ProcessingSummary.fromJson(summaryJson(currentStep: steps[index]));
  }
}

void main() {
  ProcessingPoller pollerFor(
    MediaRepository repository, {
    int maxConsecutiveErrors = 5,
  }) {
    return ProcessingPoller(
      repository: repository,
      businessId: 'b1',
      assetId: 'a1',
      interval: const Duration(milliseconds: 5),
      maxConsecutiveErrors: maxConsecutiveErrors,
    );
  }

  test('polling stops as soon as the pipeline is completed', () async {
    final repository = ScriptedRepository(['technical_analysis', 'completed']);
    final poller = pollerFor(repository);
    addTearDown(poller.dispose);
    final seen = <String>[];
    poller.results.listen((result) {
      if (result.summary != null) {
        seen.add(result.summary!.rawCurrentStep);
      }
    });

    poller.start();
    await pumpUntil(() => !poller.isRunning);
    final callsAtStop = repository.calls;
    await Future<void>.delayed(const Duration(milliseconds: 60));

    expect(poller.isRunning, isFalse);
    expect(seen, ['technical_analysis', 'completed']);
    expect(repository.calls, callsAtStop, reason: 'no request may run after terminal');
  });

  test('polling stops on a failed pipeline too', () async {
    final repository = ScriptedRepository(['failed']);
    final poller = pollerFor(repository);
    addTearDown(poller.dispose);

    poller.start();
    await pumpUntil(() => !poller.isRunning);
    await Future<void>.delayed(const Duration(milliseconds: 40));

    expect(repository.calls, 1);
  });

  test('polling keeps going while the pipeline is still working', () async {
    final repository = ScriptedRepository(['security_check']);
    final poller = pollerFor(repository);
    addTearDown(poller.dispose);

    poller.start();
    await pumpUntil(() => repository.calls >= 3);

    expect(poller.isRunning, isTrue);
    poller.stop();
    final calls = repository.calls;
    await Future<void>.delayed(const Duration(milliseconds: 40));
    expect(repository.calls, calls, reason: 'stop must cancel the timer');
  });

  test('an unknown step never stops polling', () async {
    final repository = ScriptedRepository(['teleporting']);
    final poller = pollerFor(repository);
    addTearDown(poller.dispose);

    poller.start();
    await pumpUntil(() => repository.calls >= 2);

    expect(poller.isRunning, isTrue);
  });

  test('a permanent error surfaces a message and stops polling', () async {
    final repository = ScriptedRepository(
      const [],
      error: const ApiException('Kayıt bulunamadı.', code: 'MEDIA_ASSET_NOT_FOUND', statusCode: 404),
    );
    final poller = pollerFor(repository);
    addTearDown(poller.dispose);
    final errors = <String>[];
    poller.results.listen((result) {
      if (result.hasError) {
        errors.add(result.error!);
      }
    });

    poller.start();
    await pumpUntil(() => !poller.isRunning);

    expect(errors, ['Kayıt bulunamadı.']);
    expect(repository.calls, 1, reason: 'a 404 will not fix itself');
  });

  test('transient errors retry, then give up after the configured limit', () async {
    final repository = ScriptedRepository(const [], error: const NetworkException());
    final poller = pollerFor(repository, maxConsecutiveErrors: 3);
    addTearDown(poller.dispose);
    final errors = <String>[];
    poller.results.listen((result) {
      if (result.hasError) {
        errors.add(result.error!);
      }
    });

    poller.start();
    await pumpUntil(() => !poller.isRunning);

    expect(repository.calls, 3);
    expect(errors, hasLength(3));
  });

  test('dispose closes the stream and cancels the timer', () async {
    final repository = ScriptedRepository(['security_check']);
    final poller = pollerFor(repository);

    poller.start();
    await pumpUntil(() => repository.calls >= 1);
    poller.dispose();
    final calls = repository.calls;
    await Future<void>.delayed(const Duration(milliseconds: 40));

    expect(poller.isRunning, isFalse);
    expect(repository.calls, calls);
    // A disposed poller must not restart.
    poller.start();
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(repository.calls, calls);
  });
}

/// Waits until [condition] holds, or fails the test after a bounded timeout.
Future<void> pumpUntil(bool Function() condition) async {
  final deadline = DateTime.now().add(const Duration(seconds: 5));
  while (!condition()) {
    if (DateTime.now().isAfter(deadline)) {
      fail('condition was not met before the timeout');
    }
    await Future<void>.delayed(const Duration(milliseconds: 5));
  }
}
