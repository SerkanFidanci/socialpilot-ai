import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:socialpilot_mobile/api/api_exception.dart';
import 'package:socialpilot_mobile/models/upload_session.dart';
import 'package:socialpilot_mobile/repositories/business_repository.dart';
import 'package:socialpilot_mobile/repositories/media_repository.dart';

import 'support/fixtures.dart';

void main() {
  test('bearer token is attached and never placed in the URL', () async {
    final fake = FakeHttpClient({
      'GET /v1/businesses': (_) => jsonResponse([
            {
              'id': 'b1',
              'name': 'Demo',
              'slug': 'demo',
              'status': 'active',
              'timezone': 'UTC',
              'created_by_user_id': 'u1',
            },
          ]),
    });
    final client = clientFor(fake);
    addTearDown(client.close);

    final businesses = await BusinessRepository(client).list();

    expect(businesses.single.name, 'Demo');
    expect(fake.headers.single['Authorization'], 'Bearer test-token');
    expect(fake.calls.single, 'GET /v1/businesses');
  });

  test('an idempotency key is sent for completion', () async {
    final fake = FakeHttpClient({
      'GET /v1/businesses': (_) => jsonResponse(const []),
      '/v1/businesses/b1/media/uploads/s1/complete': (_) => jsonResponse(const {}),
      'POST /v1/businesses/b1/media/uploads/s1/complete': (_) =>
          jsonResponse({'id': 'asset-1'}),
    });
    final client = clientFor(fake);
    addTearDown(client.close);

    final assetId = await MediaRepository(client).completeSession(
      businessId: 'b1',
      sessionId: 's1',
      checksum: 'a' * 64,
      parts: const [CompletedPart(partNumber: 1, etag: 'etag-1')],
      idempotencyKey: 'key-1',
    );

    expect(assetId, 'asset-1');
    expect(fake.headers.last['Idempotency-Key'], 'key-1');
  });

  test('a problem response becomes a readable Turkish message', () async {
    final fake = FakeHttpClient({
      'GET /v1/businesses/b1/media/a1/processing-summary': (_) => jsonResponse(
            {
              'type': 'about:blank',
              'title': 'Resource not found',
              'status': 404,
              'code': 'MEDIA_ASSET_NOT_FOUND',
              'detail': 'The requested resource is not available.',
            },
            status: 404,
          ),
    });
    final client = clientFor(fake);
    addTearDown(client.close);

    await expectLater(
      MediaRepository(client).processingSummary(businessId: 'b1', assetId: 'a1'),
      throwsA(
        isA<ApiException>()
            .having((error) => error.message, 'message', 'Video kaydı bulunamadı.')
            .having((error) => error.code, 'code', 'MEDIA_ASSET_NOT_FOUND')
            .having((error) => error.isRetryable, 'isRetryable', isFalse),
      ),
    );
  });

  test('a server error is retryable and reported plainly', () async {
    final fake = FakeHttpClient({
      'GET /v1/businesses': (_) => http.Response('gateway down', 503),
    });
    final client = clientFor(fake);
    addTearDown(client.close);

    await expectLater(
      BusinessRepository(client).list(),
      throwsA(
        isA<ApiException>()
            .having((error) => error.isRetryable, 'isRetryable', isTrue)
            .having((error) => error.message, 'message', contains('Sunucu')),
      ),
    );
  });

  test('unauthorized responses point at the development token', () async {
    final fake = FakeHttpClient({
      'GET /v1/businesses': (_) => jsonResponse(
            {'code': 'INVALID_IDENTITY_TOKEN', 'status': 401},
            status: 401,
          ),
    });
    final client = clientFor(fake);
    addTearDown(client.close);

    await expectLater(
      BusinessRepository(client).list(),
      throwsA(
        isA<ApiException>().having(
          (error) => error.message,
          'message',
          contains('Kimlik jetonu geçersiz'),
        ),
      ),
    );
  });

  test('part upload returns the storage etag and sends no bearer token', () async {
    final fake = FakeHttpClient({
      'PUT /upload/part/1': (_) => http.Response('', 200, headers: {'etag': '"abc123"'}),
    });
    final client = clientFor(fake);
    addTearDown(client.close);

    final etag = await client.putPart(
      uploadUrl: 'https://storage.test/upload/part/1',
      stream: Stream.value(const [1, 2, 3]),
      length: 3,
      contentType: 'video/mp4',
    );

    expect(etag, 'abc123', reason: 'quotes are stripped for the completion payload');
    expect(fake.headers.single.containsKey('Authorization'), isFalse);
  });

  test('a part upload rejection is reported, not silently ignored', () async {
    final fake = FakeHttpClient({
      'PUT /upload/part/1': (_) => http.Response('denied', 403),
    });
    final client = clientFor(fake);
    addTearDown(client.close);

    await expectLater(
      client.putPart(
        uploadUrl: 'https://storage.test/upload/part/1',
        stream: Stream.value(const [1, 2, 3]),
        length: 3,
        contentType: 'video/mp4',
      ),
      throwsA(isA<ApiException>().having((e) => e.statusCode, 'statusCode', 403)),
    );
  });
}
