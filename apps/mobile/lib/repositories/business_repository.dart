import '../api/api_client.dart';
import '../models/business.dart';

/// Reads and creates the tenants the demo user can work in.
class BusinessRepository {
  const BusinessRepository(this._client);

  final ApiClient _client;

  Future<List<Business>> list() async {
    final result = await _client.get('/v1/businesses');
    if (result is! List) {
      return const <Business>[];
    }
    return result
        .whereType<Map<String, dynamic>>()
        .map(Business.fromJson)
        .toList(growable: false);
  }

  Future<Business> create({required String name, required String timezone}) async {
    final result = await _client.post(
      '/v1/businesses',
      body: {'name': name, 'timezone': timezone},
    );
    return Business.fromJson(result as Map<String, dynamic>);
  }
}
