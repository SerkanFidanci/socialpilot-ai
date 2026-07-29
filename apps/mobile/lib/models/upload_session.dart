/// One bounded upload instruction returned by the API for a single part.
class UploadPart {
  const UploadPart({required this.partNumber, required this.uploadUrl});

  factory UploadPart.fromJson(Map<String, dynamic> json) {
    return UploadPart(
      partNumber: json['part_number'] as int,
      uploadUrl: json['upload_url'] as String,
    );
  }

  final int partNumber;
  final String uploadUrl;
}

/// A created multipart upload session; bytes go straight to object storage.
class UploadSession {
  const UploadSession({
    required this.id,
    required this.assetId,
    required this.status,
    required this.expiresAt,
    required this.parts,
  });

  factory UploadSession.fromJson(Map<String, dynamic> json) {
    return UploadSession(
      id: json['id'] as String,
      assetId: json['asset_id'] as String,
      status: json['status'] as String,
      expiresAt: DateTime.parse(json['expires_at'] as String),
      parts: (json['parts'] as List<dynamic>)
          .map((part) => UploadPart.fromJson(part as Map<String, dynamic>))
          .toList(growable: false),
    );
  }

  final String id;
  final String assetId;
  final String status;
  final DateTime expiresAt;
  final List<UploadPart> parts;
}

/// A part the client finished transferring, echoed back on completion.
class CompletedPart {
  const CompletedPart({required this.partNumber, required this.etag});

  final int partNumber;
  final String etag;

  Map<String, dynamic> toJson() => {'part_number': partNumber, 'etag': etag};
}
