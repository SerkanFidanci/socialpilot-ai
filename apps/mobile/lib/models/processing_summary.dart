/// Client mirror of the backend aggregate processing summary.
///
/// Parsing is tolerant of absent optional blocks because early polls arrive
/// before analysis rows exist, but it never invents values.
library;

/// The pipeline position reported by the backend, in display order.
enum ProcessingStep {
  uploading,
  uploaded,
  securityCheck,
  technicalAnalysis,
  sceneSpeechAnalysis,
  videoUnderstanding,
  completed,
  failed;

  /// Maps the backend wire value; an unknown value is reported as null.
  static ProcessingStep? fromWire(String value) {
    return switch (value) {
      'uploading' => ProcessingStep.uploading,
      'uploaded' => ProcessingStep.uploaded,
      'security_check' => ProcessingStep.securityCheck,
      'technical_analysis' => ProcessingStep.technicalAnalysis,
      'scene_speech_analysis' => ProcessingStep.sceneSpeechAnalysis,
      'video_understanding' => ProcessingStep.videoUnderstanding,
      'completed' => ProcessingStep.completed,
      'failed' => ProcessingStep.failed,
      _ => null,
    };
  }

  /// Terminal steps end polling; nothing further will change on its own.
  bool get isTerminal => this == ProcessingStep.completed || this == ProcessingStep.failed;
}

/// The ordered steps the processing screen renders as a checklist.
const List<ProcessingStep> kDisplayedSteps = <ProcessingStep>[
  ProcessingStep.uploaded,
  ProcessingStep.securityCheck,
  ProcessingStep.technicalAnalysis,
  ProcessingStep.sceneSpeechAnalysis,
  ProcessingStep.videoUnderstanding,
  ProcessingStep.completed,
];

/// How a displayed step relates to the reported current step.
enum PipelineStepState { done, active, pending, failed }

class StageState {
  const StageState({
    required this.status,
    this.safeErrorCode,
    this.jobStatus,
    this.attemptCount = 0,
    this.maxAttempts = 0,
  });

  factory StageState.fromJson(Map<String, dynamic> json) {
    return StageState(
      status: json['status'] as String? ?? 'unknown',
      safeErrorCode: json['safe_error_code'] as String?,
      jobStatus: json['job_status'] as String?,
      attemptCount: json['attempt_count'] as int? ?? 0,
      maxAttempts: json['max_attempts'] as int? ?? 0,
    );
  }

  final String status;
  final String? safeErrorCode;
  final String? jobStatus;
  final int attemptCount;
  final int maxAttempts;
}

class MediaAssetInfo {
  const MediaAssetInfo({
    required this.id,
    required this.contentType,
    required this.byteSize,
    required this.status,
    required this.ingestStatus,
  });

  factory MediaAssetInfo.fromJson(Map<String, dynamic> json) {
    return MediaAssetInfo(
      id: json['id'] as String,
      contentType: json['content_type'] as String,
      byteSize: json['byte_size'] as int,
      status: json['status'] as String,
      ingestStatus: json['ingest_status'] as String,
    );
  }

  final String id;
  final String contentType;
  final int byteSize;
  final String status;
  final String ingestStatus;
}

class TechnicalMetadata {
  const TechnicalMetadata({
    required this.containerFormat,
    required this.durationMs,
    required this.hasAudio,
    this.videoCodec,
    this.width,
    this.height,
    this.displayAspectRatio,
    this.audioCodec,
  });

  factory TechnicalMetadata.fromJson(Map<String, dynamic> json) {
    return TechnicalMetadata(
      containerFormat: json['container_format'] as String,
      durationMs: json['duration_ms'] as int,
      hasAudio: json['has_audio'] as bool? ?? false,
      videoCodec: json['video_codec'] as String?,
      width: json['width'] as int?,
      height: json['height'] as int?,
      displayAspectRatio: json['display_aspect_ratio'] as String?,
      audioCodec: json['audio_codec'] as String?,
    );
  }

  final String containerFormat;
  final int durationMs;
  final bool hasAudio;
  final String? videoCodec;
  final int? width;
  final int? height;
  final String? displayAspectRatio;
  final String? audioCodec;

  String? get resolution => (width != null && height != null) ? '$width x $height' : null;
}

class Scene {
  const Scene({
    required this.id,
    required this.sceneIndex,
    required this.startMs,
    required this.endMs,
    required this.durationMs,
    required this.confidence,
  });

  factory Scene.fromJson(Map<String, dynamic> json) {
    return Scene(
      id: json['id'] as String,
      sceneIndex: json['scene_index'] as int,
      startMs: json['start_ms'] as int,
      endMs: json['end_ms'] as int,
      durationMs: json['duration_ms'] as int,
      confidence: (json['confidence'] as num).toDouble(),
    );
  }

  final String id;
  final int sceneIndex;
  final int startMs;
  final int endMs;
  final int durationMs;
  final double confidence;
}

class TranscriptSegment {
  const TranscriptSegment({
    required this.segmentIndex,
    required this.startMs,
    required this.endMs,
    required this.text,
    required this.confidence,
    this.speakerLabel,
  });

  factory TranscriptSegment.fromJson(Map<String, dynamic> json) {
    return TranscriptSegment(
      segmentIndex: json['segment_index'] as int,
      startMs: json['start_ms'] as int,
      endMs: json['end_ms'] as int,
      text: json['text'] as String,
      confidence: (json['confidence'] as num).toDouble(),
      speakerLabel: json['speaker_label'] as String?,
    );
  }

  final int segmentIndex;
  final int startMs;
  final int endMs;
  final String text;
  final double confidence;
  final String? speakerLabel;
}

class Transcript {
  const Transcript({
    required this.language,
    required this.durationMs,
    required this.fullText,
    required this.provider,
    required this.status,
  });

  factory Transcript.fromJson(Map<String, dynamic> json) {
    return Transcript(
      language: json['language'] as String,
      durationMs: json['duration_ms'] as int,
      fullText: json['full_text'] as String,
      provider: json['provider'] as String,
      status: json['status'] as String,
    );
  }

  final String language;
  final int durationMs;
  final String fullText;
  final String provider;
  final String status;

  bool get hasSpeech => status != 'no_speech';
}

class SceneUnderstanding {
  const SceneUnderstanding({
    required this.id,
    required this.sceneId,
    required this.status,
    required this.provider,
    required this.modelName,
    required this.summary,
    required this.visualDescription,
    required this.confidence,
    required this.labels,
    required this.objects,
    required this.actions,
    required this.visibleText,
    required this.dominantTopics,
    required this.safetyFlags,
    this.analysisMode,
    this.visualInputAvailable,
  });

  factory SceneUnderstanding.fromJson(Map<String, dynamic> json) {
    return SceneUnderstanding(
      id: json['id'] as String,
      sceneId: json['scene_id'] as String,
      status: json['status'] as String,
      provider: json['provider'] as String,
      modelName: json['model_name'] as String,
      summary: json['summary'] as String,
      visualDescription: json['visual_description'] as String,
      confidence: (json['confidence'] as num).toDouble(),
      labels: _strings(json['labels']),
      objects: _strings(json['objects']),
      actions: _strings(json['actions']),
      visibleText: _strings(json['visible_text']),
      dominantTopics: _strings(json['dominant_topics']),
      safetyFlags: _strings(json['safety_flags']),
      analysisMode: json['analysis_mode'] as String?,
      visualInputAvailable: json['visual_input_available'] as bool?,
    );
  }

  final String id;
  final String sceneId;
  final String status;
  final String provider;
  final String modelName;
  final String summary;
  final String visualDescription;
  final double confidence;
  final List<String> labels;
  final List<String> objects;
  final List<String> actions;
  final List<String> visibleText;
  final List<String> dominantTopics;
  final List<String> safetyFlags;
  final String? analysisMode;
  final bool? visualInputAvailable;

  bool get isTranscriptOnly => analysisMode == 'transcript_only';
  bool get isNoContext => analysisMode == 'no_context';
  bool get isFrameBacked => visualInputAvailable == true;
}

class Coverage {
  const Coverage({
    required this.totalSceneCount,
    required this.analyzedSceneCount,
    required this.skippedSceneCount,
    required this.coverage,
    required this.frameBackedSceneCount,
    required this.transcriptOnlySceneCount,
    required this.noContextSceneCount,
  });

  factory Coverage.fromJson(Map<String, dynamic> json) {
    return Coverage(
      totalSceneCount: json['total_scene_count'] as int,
      analyzedSceneCount: json['analyzed_scene_count'] as int,
      skippedSceneCount: json['skipped_scene_count'] as int,
      coverage: json['coverage'] as String,
      frameBackedSceneCount: json['frame_backed_scene_count'] as int,
      transcriptOnlySceneCount: json['transcript_only_scene_count'] as int,
      noContextSceneCount: json['no_context_scene_count'] as int,
    );
  }

  final int totalSceneCount;
  final int analyzedSceneCount;
  final int skippedSceneCount;
  final String coverage;
  final int frameBackedSceneCount;
  final int transcriptOnlySceneCount;
  final int noContextSceneCount;

  bool get isFull => coverage == 'full';
}

class ProcessingSummary {
  const ProcessingSummary({
    required this.asset,
    required this.ingest,
    required this.technical,
    required this.sceneSpeech,
    required this.videoUnderstanding,
    required this.scenes,
    required this.transcriptSegments,
    required this.understandings,
    required this.currentStep,
    required this.rawCurrentStep,
    this.uploadStatus,
    this.detectedContentType,
    this.malwareScanStatus,
    this.technicalMetadata,
    this.transcript,
    this.coverage,
    this.terminalFailureCode,
    this.scenesTruncated = false,
    this.transcriptSegmentsTruncated = false,
    this.understandingsTruncated = false,
  });

  factory ProcessingSummary.fromJson(Map<String, dynamic> json) {
    final rawStep = json['current_step'] as String;
    return ProcessingSummary(
      asset: MediaAssetInfo.fromJson(json['asset'] as Map<String, dynamic>),
      uploadStatus: (json['upload'] as Map<String, dynamic>?)?['status'] as String?,
      detectedContentType: json['detected_content_type'] as String?,
      malwareScanStatus: json['malware_scan_status'] as String?,
      ingest: StageState.fromJson(json['ingest'] as Map<String, dynamic>),
      technical: StageState.fromJson(json['technical'] as Map<String, dynamic>),
      technicalMetadata: json['technical_metadata'] == null
          ? null
          : TechnicalMetadata.fromJson(json['technical_metadata'] as Map<String, dynamic>),
      sceneSpeech: StageState.fromJson(json['scene_speech'] as Map<String, dynamic>),
      videoUnderstanding: StageState.fromJson(json['video_understanding'] as Map<String, dynamic>),
      scenes: _list(json['scenes'], Scene.fromJson),
      scenesTruncated: json['scenes_truncated'] as bool? ?? false,
      transcript: json['transcript'] == null
          ? null
          : Transcript.fromJson(json['transcript'] as Map<String, dynamic>),
      transcriptSegments: _list(json['transcript_segments'], TranscriptSegment.fromJson),
      transcriptSegmentsTruncated: json['transcript_segments_truncated'] as bool? ?? false,
      understandings: _list(json['understandings'], SceneUnderstanding.fromJson),
      understandingsTruncated: json['understandings_truncated'] as bool? ?? false,
      coverage: json['coverage'] == null
          ? null
          : Coverage.fromJson(json['coverage'] as Map<String, dynamic>),
      currentStep: ProcessingStep.fromWire(rawStep),
      rawCurrentStep: rawStep,
      terminalFailureCode: json['terminal_failure_code'] as String?,
    );
  }

  final MediaAssetInfo asset;
  final String? uploadStatus;
  final String? detectedContentType;
  final String? malwareScanStatus;
  final StageState ingest;
  final StageState technical;
  final TechnicalMetadata? technicalMetadata;
  final StageState sceneSpeech;
  final StageState videoUnderstanding;
  final List<Scene> scenes;
  final bool scenesTruncated;
  final Transcript? transcript;
  final List<TranscriptSegment> transcriptSegments;
  final bool transcriptSegmentsTruncated;
  final List<SceneUnderstanding> understandings;
  final bool understandingsTruncated;
  final Coverage? coverage;

  /// Null when the backend reports a step this client build does not know.
  final ProcessingStep? currentStep;
  final String rawCurrentStep;
  final String? terminalFailureCode;

  /// An unrecognized step is never treated as terminal, so polling keeps going.
  bool get isTerminal => currentStep?.isTerminal ?? false;

  bool get isFailed => currentStep == ProcessingStep.failed;

  bool get isCompleted => currentStep == ProcessingStep.completed;

  /// Understandings joined to their scene, in timeline order.
  List<(Scene?, SceneUnderstanding)> get understandingsWithScenes {
    final byId = {for (final scene in scenes) scene.id: scene};
    return understandings
        .map((value) => (byId[value.sceneId], value))
        .toList(growable: false);
  }

  /// Resolves how a checklist row should render for the reported current step.
  PipelineStepState stateOf(ProcessingStep step) {
    final current = currentStep;
    if (current == null) {
      return PipelineStepState.pending;
    }
    if (current == ProcessingStep.failed) {
      return step == ProcessingStep.completed ? PipelineStepState.failed : PipelineStepState.done;
    }
    final currentIndex = kDisplayedSteps.indexOf(current);
    final stepIndex = kDisplayedSteps.indexOf(step);
    if (currentIndex < 0 || stepIndex < 0) {
      return PipelineStepState.pending;
    }
    if (stepIndex < currentIndex) {
      return PipelineStepState.done;
    }
    return stepIndex == currentIndex
        ? (current == ProcessingStep.completed ? PipelineStepState.done : PipelineStepState.active)
        : PipelineStepState.pending;
  }
}

List<String> _strings(Object? value) {
  if (value is! List) {
    return const <String>[];
  }
  return value.whereType<String>().toList(growable: false);
}

List<T> _list<T>(Object? value, T Function(Map<String, dynamic>) parse) {
  if (value is! List) {
    return <T>[];
  }
  return value
      .whereType<Map<String, dynamic>>()
      .map(parse)
      .toList(growable: false);
}
