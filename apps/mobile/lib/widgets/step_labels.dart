import '../models/processing_summary.dart';

/// Turkish labels for each pipeline step shown on the processing screen.
String stepLabel(ProcessingStep step) {
  return switch (step) {
    ProcessingStep.uploading => 'Yükleniyor',
    ProcessingStep.uploaded => 'Yüklendi',
    ProcessingStep.securityCheck => 'Güvenlik kontrolü',
    ProcessingStep.technicalAnalysis => 'Teknik analiz',
    ProcessingStep.sceneSpeechAnalysis => 'Sahne ve konuşma analizi',
    ProcessingStep.videoUnderstanding => 'Video understanding',
    ProcessingStep.completed => 'Tamamlandı',
    ProcessingStep.failed => 'Başarısız',
  };
}

/// Short explanation of what happens during a step.
String stepDescription(ProcessingStep step) {
  return switch (step) {
    ProcessingStep.uploading => 'Video parçaları depolamaya aktarılıyor.',
    ProcessingStep.uploaded => 'Video alındı, işleme sırasına eklendi.',
    ProcessingStep.securityCheck => 'İçerik doğrulama ve zararlı yazılım taraması yapılıyor.',
    ProcessingStep.technicalAnalysis => 'Süre, çözünürlük ve kodek bilgileri çıkarılıyor.',
    ProcessingStep.sceneSpeechAnalysis => 'Sahneler ayrılıyor ve konuşma metne dönüştürülüyor.',
    ProcessingStep.videoUnderstanding => 'Sahneler görsel olarak yorumlanıyor.',
    ProcessingStep.completed => 'Tüm analiz adımları tamamlandı.',
    ProcessingStep.failed => 'İşlem tamamlanamadı.',
  };
}

/// Formats milliseconds as `mm:ss.mmm` for scene and segment timecodes.
String formatTimecode(int milliseconds) {
  final safe = milliseconds < 0 ? 0 : milliseconds;
  final minutes = safe ~/ 60000;
  final seconds = (safe % 60000) ~/ 1000;
  final millis = safe % 1000;
  final mm = minutes.toString().padLeft(2, '0');
  final ss = seconds.toString().padLeft(2, '0');
  final ms = millis.toString().padLeft(3, '0');
  return '$mm:$ss.$ms';
}

/// Formats a byte count using binary units.
String formatBytes(int bytes) {
  if (bytes < 1024) {
    return '$bytes B';
  }
  const units = ['KB', 'MB', 'GB'];
  var value = bytes / 1024;
  var unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return '${value.toStringAsFixed(1)} ${units[unit]}';
}

/// Turkish label for a service-authoritative analysis mode.
String analysisModeLabel(String? mode) {
  return switch (mode) {
    'visual' => 'Görsel',
    'visual_and_transcript' => 'Görsel + transkript',
    'transcript_only' => 'Yalnızca transkript',
    'no_context' => 'Bağlam yok',
    _ => 'Bilinmiyor',
  };
}
