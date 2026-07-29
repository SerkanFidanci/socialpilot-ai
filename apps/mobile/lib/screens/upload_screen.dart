import 'dart:io';
import 'dart:math';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../api/api_exception.dart';
import '../models/business.dart';
import '../repositories/media_repository.dart';
import '../widgets/error_banner.dart';
import '../widgets/step_labels.dart';
import 'processing_screen.dart';

/// Picks an MP4 and drives the direct-upload contract with visible progress.
class UploadScreen extends StatefulWidget {
  const UploadScreen({
    super.key,
    required this.business,
    required this.media,
    this.pickFile,
  });

  final Business business;
  final MediaRepository media;

  /// Injectable picker so widget tests never touch the platform channel.
  final Future<File?> Function()? pickFile;

  @override
  State<UploadScreen> createState() => _UploadScreenState();
}

class _UploadScreenState extends State<UploadScreen> {
  File? _file;
  int _byteSize = 0;
  String? _error;
  UploadPhase? _phase;
  int _sentBytes = 0;

  /// Single source of truth for "an upload is in flight", so a second tap on the
  /// button cannot start a duplicate upload.
  bool _uploading = false;

  bool get _canUpload => _file != null && !_uploading;

  Future<void> _pick() async {
    if (_uploading) {
      return;
    }
    setState(() => _error = null);
    try {
      final picked = widget.pickFile != null
          ? await widget.pickFile!()
          : await _pickWithPlatform();
      if (picked == null || !mounted) {
        return;
      }
      // A synchronous stat: it reads no content and keeps widget tests free of
      // real async I/O, which deadlocks against the test binding's fake clock.
      final length = picked.lengthSync();
      if (!mounted) return;
      setState(() {
        _file = picked;
        _byteSize = length;
        _phase = null;
        _sentBytes = 0;
      });
    } on Exception {
      if (!mounted) return;
      setState(() => _error = 'Dosya seçilemedi. Tekrar deneyin.');
    }
  }

  Future<File?> _pickWithPlatform() async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['mp4'],
      // Path only: the file is streamed later, never loaded into memory.
      withData: false,
    );
    final path = result?.files.single.path;
    return path == null ? null : File(path);
  }

  Future<void> _upload() async {
    final file = _file;
    if (file == null || _uploading) {
      return; // Duplicate tap guard.
    }
    setState(() {
      _uploading = true;
      _error = null;
      _sentBytes = 0;
      _phase = UploadPhase.hashing;
    });

    // One key per attempt makes a retried completion idempotent server-side.
    final idempotencyKey = 'mobile-${DateTime.now().microsecondsSinceEpoch}';
    try {
      final checksum = await widget.media.checksumOf(
        file,
        onProgress: (sent) {
          if (mounted) {
            setState(() => _sentBytes = sent);
          }
        },
      );
      if (!mounted) return;
      setState(() {
        _phase = UploadPhase.creatingSession;
        _sentBytes = 0;
      });

      final session = await widget.media.createSession(
        businessId: widget.business.id,
        filename: _filenameOf(file),
        contentType: 'video/mp4',
        byteSize: _byteSize,
        checksum: checksum,
        partCount: MediaRepository.partCountFor(_byteSize),
        idempotencyKey: '$idempotencyKey-create',
      );
      if (!mounted) return;
      setState(() => _phase = UploadPhase.transferring);

      final parts = await widget.media.transferParts(
        file: file,
        session: session,
        byteSize: _byteSize,
        contentType: 'video/mp4',
        onProgress: (sent) {
          if (mounted) {
            setState(() => _sentBytes = min(sent, _byteSize));
          }
        },
      );
      if (!mounted) return;
      setState(() => _phase = UploadPhase.completing);

      final assetId = await widget.media.completeSession(
        businessId: widget.business.id,
        sessionId: session.id,
        checksum: checksum,
        parts: parts,
        idempotencyKey: '$idempotencyKey-complete',
      );
      if (!mounted) return;
      setState(() => _phase = UploadPhase.done);
      await Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => ProcessingScreen(
            business: widget.business,
            assetId: assetId,
            media: widget.media,
          ),
        ),
      );
      if (!mounted) return;
      setState(() {
        _uploading = false;
        _phase = null;
      });
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error.message;
        _uploading = false;
        _phase = null;
      });
    } on FileSystemException {
      if (!mounted) return;
      setState(() {
        _error = 'Video dosyası okunamadı.';
        _uploading = false;
        _phase = null;
      });
    }
  }

  static String _filenameOf(File file) {
    final segments = file.path.split(RegExp(r'[/\\]'));
    final name = segments.isEmpty ? 'video.mp4' : segments.last;
    return name.isEmpty ? 'video.mp4' : name;
  }

  String get _phaseLabel {
    return switch (_phase) {
      UploadPhase.hashing => 'Sağlama toplamı hesaplanıyor',
      UploadPhase.creatingSession => 'Yükleme oturumu oluşturuluyor',
      UploadPhase.transferring => 'Video yükleniyor',
      UploadPhase.completing => 'Yükleme tamamlanıyor',
      UploadPhase.done => 'Yükleme tamamlandı',
      null => '',
    };
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final file = _file;
    final showProgress = _phase != null;
    final fraction = _byteSize <= 0 ? 0.0 : (_sentBytes / _byteSize).clamp(0.0, 1.0);
    return Scaffold(
      appBar: AppBar(title: Text(widget.business.name)),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 16),
              child: ErrorBanner(message: _error!),
            ),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Video seçin', style: theme.textTheme.titleMedium),
                  const SizedBox(height: 4),
                  Text(
                    'Telefonunuzdan bir MP4 dosyası seçin.',
                    style: theme.textTheme.bodySmall,
                  ),
                  const SizedBox(height: 12),
                  OutlinedButton.icon(
                    key: const Key('pick-video-button'),
                    onPressed: _uploading ? null : _pick,
                    icon: const Icon(Icons.video_library),
                    label: Text(file == null ? 'MP4 seç' : 'Farklı dosya seç'),
                  ),
                  if (file != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 12),
                      child: ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(Icons.movie),
                        title: Text(_filenameOf(file)),
                        subtitle: Text(formatBytes(_byteSize)),
                      ),
                    ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          if (showProgress)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(_phaseLabel, style: theme.textTheme.titleSmall),
                    const SizedBox(height: 8),
                    LinearProgressIndicator(
                      key: const Key('upload-progress'),
                      value: _phase == UploadPhase.creatingSession ||
                              _phase == UploadPhase.completing
                          ? null
                          : fraction,
                    ),
                    const SizedBox(height: 8),
                    Text(
                      '${formatBytes(_sentBytes)} / ${formatBytes(_byteSize)}'
                      '  (${(fraction * 100).toStringAsFixed(0)}%)',
                      style: theme.textTheme.bodySmall,
                    ),
                  ],
                ),
              ),
            ),
          const SizedBox(height: 16),
          FilledButton.icon(
            key: const Key('start-upload-button'),
            onPressed: _canUpload ? _upload : null,
            icon: _uploading
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.cloud_upload),
            label: Text(_uploading ? 'Yükleniyor...' : 'Yükle ve analiz et'),
          ),
        ],
      ),
    );
  }
}
