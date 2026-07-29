import 'dart:async';

import '../api/api_exception.dart';
import '../models/processing_summary.dart';
import 'media_repository.dart';

/// One polling result: either a summary or a user-readable error.
class PollResult {
  const PollResult.data(this.summary) : error = null;
  const PollResult.failure(this.error) : summary = null;

  final ProcessingSummary? summary;
  final String? error;

  bool get hasError => error != null;
}

/// Polls the aggregate summary until the pipeline reaches a terminal step.
///
/// Stopping is the important behavior: a completed or failed pipeline will not
/// change again, so the timer is cancelled rather than left running. Callers must
/// call [dispose] from `State.dispose` so no timer outlives its screen.
class ProcessingPoller {
  ProcessingPoller({
    required this.repository,
    required this.businessId,
    required this.assetId,
    required this.interval,
    this.maxConsecutiveErrors = 5,
  });

  final MediaRepository repository;
  final String businessId;
  final String assetId;
  final Duration interval;

  /// Consecutive transport failures tolerated before polling gives up.
  final int maxConsecutiveErrors;

  final StreamController<PollResult> _controller = StreamController<PollResult>.broadcast();
  Timer? _timer;
  bool _inFlight = false;
  bool _stopped = false;
  int _consecutiveErrors = 0;

  Stream<PollResult> get results => _controller.stream;

  bool get isRunning => !_stopped;

  /// Fetches once immediately, then on [interval] until terminal.
  void start() {
    if (_stopped || _timer != null) {
      return;
    }
    _timer = Timer.periodic(interval, (_) => _tick());
    _tick();
  }

  Future<void> _tick() async {
    if (_stopped || _inFlight) {
      return; // Never overlap requests; a slow poll must not queue more.
    }
    _inFlight = true;
    try {
      final summary = await repository.processingSummary(
        businessId: businessId,
        assetId: assetId,
      );
      _consecutiveErrors = 0;
      if (_stopped) {
        return;
      }
      _emit(PollResult.data(summary));
      if (summary.isTerminal) {
        stop();
      }
    } on ApiException catch (error) {
      if (_stopped) {
        return;
      }
      _consecutiveErrors += 1;
      _emit(PollResult.failure(error.message));
      // A permanent error will not fix itself; repeated transport errors give up.
      if (!error.isRetryable || _consecutiveErrors >= maxConsecutiveErrors) {
        stop();
      }
    } finally {
      _inFlight = false;
    }
  }

  void _emit(PollResult result) {
    if (!_controller.isClosed) {
      _controller.add(result);
    }
  }

  /// Cancels the timer; the stream stays open so the last value remains usable.
  void stop() {
    _stopped = true;
    _timer?.cancel();
    _timer = null;
  }

  /// Releases the timer and the stream. Safe to call more than once.
  void dispose() {
    stop();
    if (!_controller.isClosed) {
      _controller.close();
    }
  }
}
