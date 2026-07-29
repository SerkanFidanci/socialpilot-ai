import 'package:flutter/material.dart';

/// Shows a network or backend failure with an optional retry action.
class ErrorBanner extends StatelessWidget {
  const ErrorBanner({super.key, required this.message, this.onRetry});

  final String message;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Card(
      color: colors.errorContainer,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            Icon(Icons.warning_amber_rounded, color: colors.onErrorContainer),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                message,
                style: TextStyle(color: colors.onErrorContainer),
              ),
            ),
            if (onRetry != null)
              TextButton(
                onPressed: onRetry,
                child: const Text('Tekrar dene'),
              ),
          ],
        ),
      ),
    );
  }
}
