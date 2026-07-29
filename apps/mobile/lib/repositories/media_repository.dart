import 'dart:async';
import 'dart:io';
import 'dart:math';

import 'package:convert/convert.dart';
import 'package:crypto/crypto.dart';

import '../api/api_client.dart';
import '../models/processing_summary.dart';
import '../models/upload_session.dart';

/// Progress of a running upload, from checksum to completion.
class UploadProgress {
  const UploadProgress({
    required this.phase,
    required this.sentBytes,
    required this.totalBytes,
    this.assetId,
  });

  final UploadPhase phase;
  final int sentBytes;
  final int totalBytes;
  final String? assetId;

  double get fraction => totalBytes <= 0 ? 0 : (sentBytes / totalBytes).clamp(0.0, 1.0);
}

enum UploadPhase { hashing, creatingSession, transferring, completing, done }

/// Coordinates the direct-upload contract and reads the processing summary.
///
/// Media bytes always go from the device to the object-storage part URLs the API
/// returns; they never pass through the API.
class MediaRepository {
  const MediaRepository(this._client);

  static const int _chunkSize = 1 << 20; // 1 MiB
  static const int _partSize = 8 << 20; // 8 MiB per multipart part

  final ApiClient _client;

  /// Streams the file to compute SHA-256 without loading it into memory.
  Future<String> checksumOf(File file, {void Function(int sent)? onProgress}) async {
    final digest = AccumulatorSink<Digest>();
    final sink = sha256.startChunkedConversion(digest);
    var sent = 0;
    await for (final chunk in file.openRead()) {
      sink.add(chunk);
      sent += chunk.length;
      onProgress?.call(sent);
    }
    sink.close();
    return digest.events.single.toString();
  }

  static int partCountFor(int byteSize) {
    if (byteSize <= 0) {
      return 1;
    }
    return max(1, (byteSize + _partSize - 1) ~/ _partSize);
  }

  Future<UploadSession> createSession({
    required String businessId,
    required String filename,
    required String contentType,
    required int byteSize,
    required String checksum,
    required int partCount,
    required String idempotencyKey,
  }) async {
    final result = await _client.post(
      '/v1/businesses/$businessId/media/uploads',
      body: {
        'filename': filename,
        'content_type': contentType,
        'byte_size': byteSize,
        'sha256_checksum': checksum,
        'part_count': partCount,
      },
      idempotencyKey: idempotencyKey,
    );
    return UploadSession.fromJson(result as Map<String, dynamic>);
  }

  /// Uploads each part straight to storage, reporting cumulative byte progress.
  Future<List<CompletedPart>> transferParts({
    required File file,
    required UploadSession session,
    required int byteSize,
    required String contentType,
    void Function(int sentBytes)? onProgress,
  }) async {
    final completed = <CompletedPart>[];
    var sent = 0;
    for (final part in session.parts) {
      final start = (part.partNumber - 1) * _partSize;
      if (start >= byteSize && part.partNumber > 1) {
        break;
      }
      final end = min(start + _partSize, byteSize);
      final length = end - start;
      var partSent = 0;
      final stream = file.openRead(start, end).map((chunk) {
        partSent += chunk.length;
        onProgress?.call(sent + partSent);
        return chunk;
      });
      final etag = await _client.putPart(
        uploadUrl: part.uploadUrl,
        stream: stream,
        length: length,
        contentType: contentType,
      );
      sent += length;
      onProgress?.call(sent);
      completed.add(CompletedPart(partNumber: part.partNumber, etag: etag));
    }
    return completed;
  }

  Future<String> completeSession({
    required String businessId,
    required String sessionId,
    required String checksum,
    required List<CompletedPart> parts,
    required String idempotencyKey,
  }) async {
    final result = await _client.post(
      '/v1/businesses/$businessId/media/uploads/$sessionId/complete',
      body: {
        'sha256_checksum': checksum,
        'parts': parts.map((part) => part.toJson()).toList(growable: false),
      },
      idempotencyKey: idempotencyKey,
    );
    return (result as Map<String, dynamic>)['id'] as String;
  }

  Future<void> cancelSession({
    required String businessId,
    required String sessionId,
  }) async {
    await _client.post('/v1/businesses/$businessId/media/uploads/$sessionId/cancel');
  }

  Future<ProcessingSummary> processingSummary({
    required String businessId,
    required String assetId,
  }) async {
    final result = await _client.get(
      '/v1/businesses/$businessId/media/$assetId/processing-summary',
    );
    return ProcessingSummary.fromJson(result as Map<String, dynamic>);
  }

  /// Chunk size used when hashing, exposed so callers can report progress.
  static int get chunkSize => _chunkSize;
}
