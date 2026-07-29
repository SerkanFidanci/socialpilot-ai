import 'package:flutter_test/flutter_test.dart';
import 'package:socialpilot_mobile/models/business.dart';
import 'package:socialpilot_mobile/models/processing_summary.dart';
import 'package:socialpilot_mobile/models/upload_session.dart';

import 'support/fixtures.dart';

void main() {
  group('API model parsing', () {
    test('parses a business', () {
      final business = Business.fromJson({
        'id': 'b1',
        'name': 'Kahve Dükkanı',
        'slug': 'kahve-dukkani',
        'status': 'active',
        'timezone': 'Europe/Istanbul',
        'created_by_user_id': 'u1',
      });
      expect(business.name, 'Kahve Dükkanı');
      expect(business.isActive, isTrue);
    });

    test('parses an upload session with its part instructions', () {
      final session = UploadSession.fromJson({
        'id': 's1',
        'asset_id': 'a1',
        'status': 'created',
        'expires_at': '2026-07-30T10:15:00Z',
        'parts': [
          {'part_number': 1, 'upload_url': 'https://storage.test/1'},
          {'part_number': 2, 'upload_url': 'https://storage.test/2'},
        ],
      });
      expect(session.assetId, 'a1');
      expect(session.parts.map((part) => part.partNumber), [1, 2]);
      expect(session.expiresAt.isUtc, isTrue);
    });

    test('parses a full processing summary', () {
      final summary = ProcessingSummary.fromJson(summaryJson());
      expect(summary.currentStep, ProcessingStep.completed);
      expect(summary.isCompleted, isTrue);
      expect(summary.asset.byteSize, 2048);
      expect(summary.technicalMetadata?.resolution, '1920 x 1080');
      expect(summary.scenes, hasLength(2));
      expect(summary.transcript?.fullText, 'birinci sahne');
      expect(summary.transcriptSegments.single.speakerLabel, 'konusmaci-1');
      expect(summary.understandings, hasLength(2));
      expect(summary.understandings.first.isTranscriptOnly, isTrue);
      expect(summary.understandings.last.isNoContext, isTrue);
      expect(summary.coverage?.isFull, isTrue);
      expect(summary.terminalFailureCode, isNull);
    });

    test('tolerates a summary with no analysis rows yet', () {
      final json = summaryJson(currentStep: 'security_check')
        ..['technical_metadata'] = null
        ..['transcript'] = null
        ..['coverage'] = null
        ..['scenes'] = <Map<String, dynamic>>[]
        ..['transcript_segments'] = <Map<String, dynamic>>[]
        ..['understandings'] = <Map<String, dynamic>>[];
      final summary = ProcessingSummary.fromJson(json);
      expect(summary.currentStep, ProcessingStep.securityCheck);
      expect(summary.technicalMetadata, isNull);
      expect(summary.transcript, isNull);
      expect(summary.coverage, isNull);
      expect(summary.scenes, isEmpty);
      expect(summary.isTerminal, isFalse);
    });

    test('joins understandings to their scenes in timeline order', () {
      final summary = ProcessingSummary.fromJson(summaryJson());
      final joined = summary.understandingsWithScenes;
      expect(joined, hasLength(2));
      expect(joined.first.$1?.sceneIndex, 0);
      expect(joined.last.$1?.sceneIndex, 1);
    });

    test('reports an unknown scene reference as a null scene, not a crash', () {
      final summary = ProcessingSummary.fromJson(
        summaryJson(
          understandings: [understandingJson(sceneId: 'missing-scene', mode: 'visual')],
        ),
      );
      expect(summary.understandingsWithScenes.single.$1, isNull);
      expect(summary.understandingsWithScenes.single.$2.isFrameBacked, isTrue);
    });

    test('ignores malformed list entries instead of throwing', () {
      final json = summaryJson()..['scenes'] = <Object>['nonsense', 42];
      expect(ProcessingSummary.fromJson(json).scenes, isEmpty);
    });
  });

  group('processing step mapping', () {
    test('maps every backend step value', () {
      const wire = {
        'uploading': ProcessingStep.uploading,
        'uploaded': ProcessingStep.uploaded,
        'security_check': ProcessingStep.securityCheck,
        'technical_analysis': ProcessingStep.technicalAnalysis,
        'scene_speech_analysis': ProcessingStep.sceneSpeechAnalysis,
        'video_understanding': ProcessingStep.videoUnderstanding,
        'completed': ProcessingStep.completed,
        'failed': ProcessingStep.failed,
      };
      wire.forEach((value, expected) {
        expect(ProcessingStep.fromWire(value), expected, reason: value);
      });
    });

    test('an unknown step is null and never treated as terminal', () {
      expect(ProcessingStep.fromWire('teleporting'), isNull);
      final summary = ProcessingSummary.fromJson(summaryJson(currentStep: 'teleporting'));
      expect(summary.currentStep, isNull);
      expect(summary.rawCurrentStep, 'teleporting');
      expect(summary.isTerminal, isFalse);
      expect(summary.stateOf(ProcessingStep.uploaded), PipelineStepState.pending);
    });

    test('only completed and failed are terminal', () {
      expect(ProcessingStep.completed.isTerminal, isTrue);
      expect(ProcessingStep.failed.isTerminal, isTrue);
      for (final step in [
        ProcessingStep.uploading,
        ProcessingStep.uploaded,
        ProcessingStep.securityCheck,
        ProcessingStep.technicalAnalysis,
        ProcessingStep.sceneSpeechAnalysis,
        ProcessingStep.videoUnderstanding,
      ]) {
        expect(step.isTerminal, isFalse, reason: step.name);
      }
    });

    test('checklist marks earlier steps done and the current one active', () {
      final summary = ProcessingSummary.fromJson(
        summaryJson(currentStep: 'scene_speech_analysis'),
      );
      expect(summary.stateOf(ProcessingStep.uploaded), PipelineStepState.done);
      expect(summary.stateOf(ProcessingStep.securityCheck), PipelineStepState.done);
      expect(summary.stateOf(ProcessingStep.technicalAnalysis), PipelineStepState.done);
      expect(summary.stateOf(ProcessingStep.sceneSpeechAnalysis), PipelineStepState.active);
      expect(summary.stateOf(ProcessingStep.videoUnderstanding), PipelineStepState.pending);
      expect(summary.stateOf(ProcessingStep.completed), PipelineStepState.pending);
    });

    test('a completed pipeline marks every row done', () {
      final summary = ProcessingSummary.fromJson(summaryJson());
      for (final step in kDisplayedSteps) {
        expect(summary.stateOf(step), PipelineStepState.done, reason: step.name);
      }
    });

    test('a failed pipeline marks the final row failed', () {
      final summary = ProcessingSummary.fromJson(
        summaryJson(currentStep: 'failed', terminalFailureCode: 'MEDIA_PROBE_FAILED'),
      );
      expect(summary.isFailed, isTrue);
      expect(summary.stateOf(ProcessingStep.completed), PipelineStepState.failed);
      expect(summary.stateOf(ProcessingStep.technicalAnalysis), PipelineStepState.done);
      expect(summary.terminalFailureCode, 'MEDIA_PROBE_FAILED');
    });
  });

  group('coverage model', () {
    test('reports partial coverage and the skipped scene count', () {
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
      final coverage = summary.coverage!;
      expect(coverage.isFull, isFalse);
      expect(coverage.skippedSceneCount, 2);
      expect(coverage.frameBackedSceneCount, 3);
    });
  });
}
