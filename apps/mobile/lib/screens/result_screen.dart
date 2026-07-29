import 'package:flutter/material.dart';

import '../models/business.dart';
import '../models/processing_summary.dart';
import '../widgets/coverage_card.dart';
import '../widgets/error_banner.dart';
import '../widgets/step_labels.dart';

/// Shows the finished analysis: video facts, scenes, transcript, understandings.
class ResultScreen extends StatelessWidget {
  const ResultScreen({super.key, required this.business, required this.summary});

  final Business business;
  final ProcessingSummary summary;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final coverage = summary.coverage;
    return Scaffold(
      appBar: AppBar(title: const Text('Analiz sonucu')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (summary.isFailed)
            Padding(
              padding: const EdgeInsets.only(bottom: 16),
              child: ErrorBanner(
                message: 'Analiz tamamlanamadı.'
                    '${summary.terminalFailureCode == null ? '' : ' Kod: ${summary.terminalFailureCode}'}',
              ),
            ),
          _VideoFacts(summary: summary),
          const SizedBox(height: 16),
          if (coverage != null) ...[
            CoverageCard(key: const Key('coverage-card'), coverage: coverage),
            const SizedBox(height: 16),
          ],
          _ScenesSection(summary: summary),
          const SizedBox(height: 16),
          _TranscriptSection(summary: summary),
          const SizedBox(height: 16),
          Text('Sahne analizleri', style: theme.textTheme.titleLarge),
          const SizedBox(height: 8),
          if (summary.understandings.isEmpty)
            const Card(
              child: Padding(
                padding: EdgeInsets.all(16),
                child: Text('Henüz sahne analizi yok.'),
              ),
            )
          else
            for (final (scene, understanding) in summary.understandingsWithScenes)
              _UnderstandingCard(scene: scene, understanding: understanding),
          if (summary.understandingsTruncated)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Text('Liste kısaltıldı; tüm sonuçlar gösterilmiyor.'),
            ),
        ],
      ),
    );
  }
}

class _VideoFacts extends StatelessWidget {
  const _VideoFacts({required this.summary});

  final ProcessingSummary summary;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final metadata = summary.technicalMetadata;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Video bilgileri', style: theme.textTheme.titleLarge),
            const SizedBox(height: 12),
            _Fact(label: 'Boyut', value: formatBytes(summary.asset.byteSize)),
            _Fact(label: 'Tür', value: summary.asset.contentType),
            if (summary.detectedContentType != null)
              _Fact(label: 'Doğrulanan tür', value: summary.detectedContentType!),
            if (summary.malwareScanStatus != null)
              _Fact(label: 'Güvenlik taraması', value: summary.malwareScanStatus!),
            if (metadata != null) ...[
              _Fact(label: 'Süre', value: formatTimecode(metadata.durationMs)),
              _Fact(label: 'Kapsayıcı', value: metadata.containerFormat),
              if (metadata.resolution != null)
                _Fact(label: 'Çözünürlük', value: metadata.resolution!),
              if (metadata.videoCodec != null)
                _Fact(label: 'Video kodeki', value: metadata.videoCodec!),
              _Fact(label: 'Ses', value: metadata.hasAudio ? 'Var' : 'Yok'),
              if (metadata.audioCodec != null)
                _Fact(label: 'Ses kodeki', value: metadata.audioCodec!),
            ] else
              const Text('Teknik analiz bilgisi henüz yok.'),
          ],
        ),
      ),
    );
  }
}

class _ScenesSection extends StatelessWidget {
  const _ScenesSection({required this.summary});

  final ProcessingSummary summary;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Sahneler (${summary.scenes.length})', style: theme.textTheme.titleLarge),
            const SizedBox(height: 8),
            if (summary.scenes.isEmpty)
              const Text('Sahne bulunamadı.')
            else
              for (final scene in summary.scenes)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  child: Row(
                    children: [
                      SizedBox(
                        width: 32,
                        child: Text(
                          '#${scene.sceneIndex + 1}',
                          style: theme.textTheme.labelLarge,
                        ),
                      ),
                      Expanded(
                        child: Text(
                          '${formatTimecode(scene.startMs)} → ${formatTimecode(scene.endMs)}',
                          style: theme.textTheme.bodyMedium,
                        ),
                      ),
                      Text(
                        formatTimecode(scene.durationMs),
                        style: theme.textTheme.bodySmall,
                      ),
                    ],
                  ),
                ),
            if (summary.scenesTruncated)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text('Sahne listesi kısaltıldı.'),
              ),
          ],
        ),
      ),
    );
  }
}

class _TranscriptSection extends StatelessWidget {
  const _TranscriptSection({required this.summary});

  final ProcessingSummary summary;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final transcript = summary.transcript;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Transkript', style: theme.textTheme.titleLarge),
            const SizedBox(height: 8),
            if (transcript == null)
              const Text('Transkript henüz yok.')
            else if (!transcript.hasSpeech)
              const Text('Videoda konuşma algılanmadı.')
            else ...[
              Text('Dil: ${transcript.language}', style: theme.textTheme.bodySmall),
              const SizedBox(height: 8),
              Text(transcript.fullText, style: theme.textTheme.bodyMedium),
              const Divider(height: 24),
              Text('Segmentler', style: theme.textTheme.titleSmall),
              const SizedBox(height: 4),
              for (final segment in summary.transcriptSegments)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '${formatTimecode(segment.startMs)} → ${formatTimecode(segment.endMs)}'
                        '${segment.speakerLabel == null ? '' : ' · ${segment.speakerLabel}'}',
                        style: theme.textTheme.labelSmall,
                      ),
                      Text(segment.text, style: theme.textTheme.bodyMedium),
                    ],
                  ),
                ),
              if (summary.transcriptSegmentsTruncated)
                const Padding(
                  padding: EdgeInsets.only(top: 8),
                  child: Text('Segment listesi kısaltıldı.'),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

class _UnderstandingCard extends StatelessWidget {
  const _UnderstandingCard({required this.scene, required this.understanding});

  final Scene? scene;
  final SceneUnderstanding understanding;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    scene == null
                        ? 'Sahne'
                        : 'Sahne #${scene!.sceneIndex + 1} · '
                            '${formatTimecode(scene!.startMs)} → ${formatTimecode(scene!.endMs)}',
                    style: theme.textTheme.titleSmall,
                  ),
                ),
                Text(
                  '%${(understanding.confidence * 100).toStringAsFixed(0)}',
                  style: theme.textTheme.labelLarge,
                ),
              ],
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                Chip(
                  label: Text(analysisModeLabel(understanding.analysisMode)),
                  backgroundColor: understanding.isFrameBacked
                      ? colors.primaryContainer
                      : colors.surfaceContainerHighest,
                  visualDensity: VisualDensity.compact,
                ),
                if (understanding.isTranscriptOnly)
                  const Chip(
                    key: Key('transcript-only-chip'),
                    label: Text('Görsel kare yok'),
                    visualDensity: VisualDensity.compact,
                  ),
                if (understanding.isNoContext)
                  const Chip(
                    key: Key('no-context-chip'),
                    label: Text('Bağlam yok'),
                    visualDensity: VisualDensity.compact,
                  ),
              ],
            ),
            const SizedBox(height: 12),
            Text('Özet', style: theme.textTheme.labelMedium),
            Text(understanding.summary, style: theme.textTheme.bodyMedium),
            const SizedBox(height: 8),
            Text('Görsel açıklama', style: theme.textTheme.labelMedium),
            Text(understanding.visualDescription, style: theme.textTheme.bodyMedium),
            if (understanding.labels.isNotEmpty)
              _Tags(label: 'Etiketler', values: understanding.labels),
            if (understanding.objects.isNotEmpty)
              _Tags(label: 'Nesneler', values: understanding.objects),
            if (understanding.actions.isNotEmpty)
              _Tags(label: 'Eylemler', values: understanding.actions),
            if (understanding.visibleText.isNotEmpty)
              _Tags(label: 'Görünen metin', values: understanding.visibleText),
            if (understanding.dominantTopics.isNotEmpty)
              _Tags(label: 'Konular', values: understanding.dominantTopics),
            if (understanding.safetyFlags.isNotEmpty)
              _Tags(label: 'Güvenlik işaretleri', values: understanding.safetyFlags),
          ],
        ),
      ),
    );
  }
}

class _Tags extends StatelessWidget {
  const _Tags({required this.label, required this.values});

  final String label;
  final List<String> values;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: Theme.of(context).textTheme.labelMedium),
          const SizedBox(height: 4),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final value in values)
                Chip(label: Text(value), visualDensity: VisualDensity.compact),
            ],
          ),
        ],
      ),
    );
  }
}

class _Fact extends StatelessWidget {
  const _Fact({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 140,
            child: Text(label, style: theme.textTheme.labelMedium),
          ),
          Expanded(child: Text(value, style: theme.textTheme.bodyMedium)),
        ],
      ),
    );
  }
}
