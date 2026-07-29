import 'dart:async';

import 'package:flutter/material.dart';

import '../config/app_config.dart';
import '../models/business.dart';
import '../models/processing_summary.dart';
import '../repositories/media_repository.dart';
import '../repositories/processing_poller.dart';
import '../widgets/error_banner.dart';
import '../widgets/step_checklist.dart';
import '../widgets/step_labels.dart';
import 'result_screen.dart';

/// Polls the aggregate summary and shows each pipeline step until terminal.
class ProcessingScreen extends StatefulWidget {
  const ProcessingScreen({
    super.key,
    required this.business,
    required this.assetId,
    required this.media,
    this.pollInterval,
  });

  final Business business;
  final String assetId;
  final MediaRepository media;

  /// Overridable so widget tests do not wait on the production cadence.
  final Duration? pollInterval;

  @override
  State<ProcessingScreen> createState() => _ProcessingScreenState();
}

class _ProcessingScreenState extends State<ProcessingScreen> {
  late final ProcessingPoller _poller;
  StreamSubscription<PollResult>? _subscription;
  ProcessingSummary? _summary;
  String? _error;

  @override
  void initState() {
    super.initState();
    _poller = ProcessingPoller(
      repository: widget.media,
      businessId: widget.business.id,
      assetId: widget.assetId,
      interval: widget.pollInterval ?? const AppConfig(apiBaseUrl: '', identityToken: '').pollInterval,
    );
    _subscription = _poller.results.listen(_onResult);
    _poller.start();
  }

  void _onResult(PollResult result) {
    if (!mounted) {
      return;
    }
    setState(() {
      if (result.hasError) {
        _error = result.error;
      } else {
        _error = null;
        _summary = result.summary;
      }
    });
  }

  @override
  void dispose() {
    // Cancel the subscription before the poller so no callback fires after unmount.
    _subscription?.cancel();
    _poller.dispose();
    super.dispose();
  }

  void _openResult() {
    final summary = _summary;
    if (summary == null) {
      return;
    }
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => ResultScreen(business: widget.business, summary: summary),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final summary = _summary;
    return Scaffold(
      appBar: AppBar(title: const Text('İşleniyor')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 16),
              child: ErrorBanner(
                message: _error!,
                onRetry: _poller.isRunning ? null : _restart,
              ),
            ),
          if (summary == null)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 48),
              child: Center(child: CircularProgressIndicator()),
            )
          else ...[
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Durum', style: theme.textTheme.titleMedium),
                    const SizedBox(height: 4),
                    Text(
                      summary.currentStep == null
                          ? 'Bilinmeyen adım: ${summary.rawCurrentStep}'
                          : stepLabel(summary.currentStep!),
                      key: const Key('current-step-label'),
                      style: theme.textTheme.headlineSmall,
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '${formatBytes(summary.asset.byteSize)} · ${summary.asset.contentType}',
                      style: theme.textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: StepChecklist(summary: summary),
              ),
            ),
            if (summary.isFailed)
              Padding(
                padding: const EdgeInsets.only(top: 16),
                child: ErrorBanner(
                  message: 'İşlem tamamlanamadı.'
                      '${summary.terminalFailureCode == null ? '' : ' Kod: ${summary.terminalFailureCode}'}',
                ),
              ),
            const SizedBox(height: 16),
            FilledButton.icon(
              key: const Key('open-result-button'),
              onPressed: summary.isTerminal ? _openResult : null,
              icon: const Icon(Icons.analytics),
              label: Text(
                summary.isTerminal ? 'Sonuçları gör' : 'Analiz sürüyor...',
              ),
            ),
            if (!summary.isTerminal)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  'Durum otomatik yenileniyor.',
                  textAlign: TextAlign.center,
                  style: theme.textTheme.bodySmall,
                ),
              ),
          ],
        ],
      ),
    );
  }

  void _restart() {
    setState(() => _error = null);
    // A stopped poller cannot resume, so replace the screen's polling state.
    Navigator.of(context).pushReplacement(
      MaterialPageRoute(
        builder: (_) => ProcessingScreen(
          business: widget.business,
          assetId: widget.assetId,
          media: widget.media,
          pollInterval: widget.pollInterval,
        ),
      ),
    );
  }
}
