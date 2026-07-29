import 'package:flutter/material.dart';

import '../models/processing_summary.dart';

/// Shows whether every scene was analyzed, and how the analyzed ones were done.
class CoverageCard extends StatelessWidget {
  const CoverageCard({super.key, required this.coverage});

  final Coverage coverage;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final isFull = coverage.isFull;
    return Card(
      color: isFull ? colors.primaryContainer : colors.tertiaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  isFull ? Icons.verified : Icons.pie_chart,
                  color: isFull ? colors.onPrimaryContainer : colors.onTertiaryContainer,
                ),
                const SizedBox(width: 8),
                Text(
                  isFull ? 'Tam kapsam' : 'Kısmi kapsam',
                  style: theme.textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.w700,
                    color: isFull ? colors.onPrimaryContainer : colors.onTertiaryContainer,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              '${coverage.analyzedSceneCount} / ${coverage.totalSceneCount} sahne analiz edildi',
              style: theme.textTheme.bodyMedium,
            ),
            if (coverage.skippedSceneCount > 0)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'Analiz edilmeyen sahne: ${coverage.skippedSceneCount}',
                  style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
                ),
              ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                _Chip(
                  icon: Icons.image,
                  label: 'Kare destekli: ${coverage.frameBackedSceneCount}',
                ),
                _Chip(
                  icon: Icons.record_voice_over,
                  label: 'Yalnızca transkript: ${coverage.transcriptOnlySceneCount}',
                ),
                _Chip(
                  icon: Icons.help_outline,
                  label: 'Bağlam yok: ${coverage.noContextSceneCount}',
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip({required this.icon, required this.label});

  final IconData icon;
  final String label;

  @override
  Widget build(BuildContext context) {
    return Chip(
      avatar: Icon(icon, size: 18),
      label: Text(label),
      visualDensity: VisualDensity.compact,
    );
  }
}
