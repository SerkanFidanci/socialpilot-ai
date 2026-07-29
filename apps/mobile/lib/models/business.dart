/// A tenant the signed-in demo user belongs to.
class Business {
  const Business({
    required this.id,
    required this.name,
    required this.slug,
    required this.status,
    required this.timezone,
  });

  factory Business.fromJson(Map<String, dynamic> json) {
    return Business(
      id: json['id'] as String,
      name: json['name'] as String,
      slug: json['slug'] as String,
      status: json['status'] as String,
      timezone: json['timezone'] as String,
    );
  }

  final String id;
  final String name;
  final String slug;
  final String status;
  final String timezone;

  bool get isActive => status == 'active';
}
