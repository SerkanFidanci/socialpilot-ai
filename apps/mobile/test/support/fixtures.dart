import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:socialpilot_mobile/api/api_client.dart';
import 'package:socialpilot_mobile/config/app_config.dart';

/// A configuration that never reaches the network in tests.
const testConfig = AppConfig(
  apiBaseUrl: 'http://localhost:8000',
  identityToken: 'test-token',
  pollInterval: Duration(milliseconds: 10),
);

/// Records requests and replies from a scripted queue of responses.
class FakeHttpClient extends http.BaseClient {
  FakeHttpClient(this.responses);

  /// Handlers keyed by `METHOD path`, each called with the decoded body.
  final Map<String, http.Response Function(String? body)> responses;
  final List<String> calls = <String>[];
  final List<Map<String, String>> headers = <Map<String, String>>[];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final key = '${request.method} ${request.url.path}';
    calls.add(key);
    headers.add(Map<String, String>.from(request.headers));
    final handler = responses[key];
    if (handler == null) {
      return http.StreamedResponse(
        Stream.value(utf8.encode(jsonEncode({'code': 'NOT_STUBBED'}))),
        404,
      );
    }
    final body = request is http.Request ? request.body : null;
    final response = handler(body);
    return http.StreamedResponse(
      Stream.value(response.bodyBytes),
      response.statusCode,
      headers: response.headers,
    );
  }
}

http.Response jsonResponse(Object value, {int status = 200}) {
  return http.Response(
    jsonEncode(value),
    status,
    headers: {'content-type': 'application/json'},
  );
}

ApiClient clientFor(FakeHttpClient fake) => ApiClient(config: testConfig, client: fake);

/// A processing-summary payload matching the backend contract.
Map<String, dynamic> summaryJson({
  String currentStep = 'completed',
  String? terminalFailureCode,
  Map<String, dynamic>? coverage,
  List<Map<String, dynamic>>? scenes,
  List<Map<String, dynamic>>? understandings,
  Map<String, dynamic>? transcript,
  List<Map<String, dynamic>>? segments,
  Map<String, dynamic>? technicalMetadata,
}) {
  return {
    'asset': {
      'id': 'asset-1',
      'business_id': 'business-1',
      'content_type': 'video/mp4',
      'byte_size': 2048,
      'sha256_checksum': 'a' * 64,
      'status': 'uploaded',
      'ingest_status': 'ready_for_analysis',
      'created_at': '2026-07-30T10:00:00Z',
      'uploaded_at': '2026-07-30T10:00:01Z',
    },
    'upload': {
      'status': 'completed',
      'expected_part_count': 1,
      'expires_at': '2026-07-30T10:15:00Z',
      'completed_at': '2026-07-30T10:00:01Z',
    },
    'detected_content_type': 'video/mp4',
    'malware_scan_status': 'clean',
    'ingest': stageJson('ready_for_analysis', jobStatus: 'succeeded'),
    'technical': stageJson('completed', jobStatus: 'succeeded'),
    'technical_metadata': technicalMetadata ??
        {
          'container_format': 'mp4',
          'duration_ms': 4000,
          'file_size': 2048,
          'video_codec': 'h264',
          'width': 1920,
          'height': 1080,
          'display_aspect_ratio': '16:9',
          'frame_rate_numerator': 30,
          'frame_rate_denominator': 1,
          'bit_rate': 2000000,
          'rotation_degrees': 0,
          'has_audio': true,
          'audio_codec': 'aac',
          'audio_sample_rate': 48000,
          'audio_channel_count': 2,
          'stream_count': 2,
        },
    'scene_speech': stageJson('completed', jobStatus: 'succeeded'),
    'video_understanding': stageJson('completed', jobStatus: 'succeeded'),
    'scenes': scenes ??
        [
          sceneJson(id: 'scene-1', index: 0, start: 0, end: 500),
          sceneJson(id: 'scene-2', index: 1, start: 500, end: 1000),
        ],
    'scenes_truncated': false,
    'transcript': transcript ??
        {
          'language': 'tr',
          'duration_ms': 4000,
          'full_text': 'birinci sahne',
          'provider': 'fake',
          'status': 'completed',
        },
    'transcript_segments': segments ??
        [
          {
            'segment_index': 0,
            'start_ms': 100,
            'end_ms': 400,
            'text': 'birinci sahne',
            'confidence': 0.9,
            'speaker_label': 'konusmaci-1',
          },
        ],
    'transcript_segments_truncated': false,
    'understandings': understandings ??
        [
          understandingJson(sceneId: 'scene-1', mode: 'transcript_only'),
          understandingJson(sceneId: 'scene-2', mode: 'no_context'),
        ],
    'understandings_truncated': false,
    'coverage': coverage ??
        {
          'total_scene_count': 2,
          'analyzed_scene_count': 2,
          'skipped_scene_count': 0,
          'coverage': 'full',
          'frame_backed_scene_count': 0,
          'transcript_only_scene_count': 1,
          'no_context_scene_count': 1,
        },
    'current_step': currentStep,
    'terminal_failure_code': terminalFailureCode,
  };
}

Map<String, dynamic> stageJson(String status, {String? jobStatus, String? errorCode}) {
  return {
    'status': status,
    'safe_error_code': errorCode,
    'job_status': jobStatus,
    'attempt_count': 1,
    'max_attempts': 3,
    'started_at': null,
    'finished_at': null,
  };
}

Map<String, dynamic> sceneJson({
  required String id,
  required int index,
  required int start,
  required int end,
}) {
  return {
    'id': id,
    'scene_index': index,
    'start_ms': start,
    'end_ms': end,
    'duration_ms': end - start,
    'confidence': 1.0,
  };
}

Map<String, dynamic> understandingJson({
  required String sceneId,
  required String mode,
  double confidence = 0.5,
}) {
  return {
    'id': 'understanding-$sceneId',
    'scene_id': sceneId,
    'status': 'completed',
    'provider': 'fake-vlm',
    'model_name': 'deterministic',
    'summary': 'Sahne analiz edildi',
    'visual_description': 'Belirleyici gorsel sahne',
    'transcript_context': 'birinci sahne',
    'confidence': confidence,
    'labels': ['sahne'],
    'objects': ['masa'],
    'actions': ['konusma'],
    'visible_text': <String>[],
    'dominant_topics': ['tanitim'],
    'safety_flags': <String>[],
    'analysis_mode': mode,
    'visual_input_available': mode == 'visual' || mode == 'visual_and_transcript',
  };
}
