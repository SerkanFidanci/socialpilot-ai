import 'package:flutter/material.dart';

import '../models/processing_summary.dart';
import 'step_labels.dart';

/// Renders the pipeline as an ordered checklist with an explicit state per row.
class StepChecklist extends StatelessWidget {
  const StepChecklist({super.key, required this.summary});

  final ProcessingSummary summary;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final step in kDisplayedSteps)
          _StepRow(
            key: ValueKey(step),
            step: step,
            state: summary.stateOf(step),
            failureCode: step == ProcessingStep.completed ? summary.terminalFailureCode : null,
          ),
      ],
    );
  }
}

class _StepRow extends StatelessWidget {
  const _StepRow({
    super.key,
    required this.step,
    required this.state,
    this.failureCode,
  });

  final ProcessingStep step;
  final PipelineStepState state;
  final String? failureCode;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final (icon, color) = switch (state) {
      PipelineStepState.done => (Icons.check_circle, colors.primary),
      PipelineStepState.active => (Icons.autorenew, colors.tertiary),
      PipelineStepState.failed => (Icons.error, colors.error),
      PipelineStepState.pending => (Icons.radio_button_unchecked, colors.outline),
    };
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          state == PipelineStepState.active
              ? SizedBox(
                  width: 24,
                  height: 24,
                  child: Padding(
                    padding: const EdgeInsets.all(2),
                    child: CircularProgressIndicator(strokeWidth: 2.5, color: color),
                  ),
                )
              : Icon(icon, color: color, semanticLabel: stepLabel(step)),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  stepLabel(step),
                  style: theme.textTheme.titleSmall?.copyWith(
                    color: state == PipelineStepState.pending ? colors.outline : null,
                    fontWeight: state == PipelineStepState.active ? FontWeight.w700 : FontWeight.w500,
                  ),
                ),
                if (state != PipelineStepState.pending)
                  Text(
                    failureCode != null && state == PipelineStepState.failed
                        ? 'Hata kodu: $failureCode'
                        : stepDescription(step),
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: state == PipelineStepState.failed ? colors.error : colors.onSurfaceVariant,
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
