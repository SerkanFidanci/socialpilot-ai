import 'package:flutter/material.dart';

import '../api/api_exception.dart';
import '../models/business.dart';
import '../repositories/business_repository.dart';
import '../repositories/media_repository.dart';
import '../widgets/error_banner.dart';
import 'business_create_screen.dart';
import 'upload_screen.dart';

/// Entry screen: choose an existing business or create one.
class BusinessListScreen extends StatefulWidget {
  const BusinessListScreen({
    super.key,
    required this.businesses,
    required this.media,
  });

  final BusinessRepository businesses;
  final MediaRepository media;

  @override
  State<BusinessListScreen> createState() => _BusinessListScreenState();
}

class _BusinessListScreenState extends State<BusinessListScreen> {
  List<Business>? _businesses;
  String? _error;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await widget.businesses.list();
      if (!mounted) return;
      setState(() {
        _businesses = result;
        _loading = false;
      });
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error.message;
        _loading = false;
      });
    }
  }

  Future<void> _openCreate() async {
    final created = await Navigator.of(context).push<Business>(
      MaterialPageRoute(
        builder: (_) => BusinessCreateScreen(businesses: widget.businesses),
      ),
    );
    if (created == null || !mounted) {
      return;
    }
    setState(() => _businesses = [...?_businesses, created]);
    await _openUpload(created);
  }

  Future<void> _openUpload(Business business) async {
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => UploadScreen(business: business, media: widget.media),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final businesses = _businesses;
    return Scaffold(
      appBar: AppBar(title: const Text('İşletme seçin')),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _openCreate,
        icon: const Icon(Icons.add_business),
        label: const Text('Yeni işletme'),
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 96),
          children: [
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: ErrorBanner(message: _error!, onRetry: _load),
              ),
            if (_loading && businesses == null)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 48),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (businesses != null && businesses.isEmpty)
              const _EmptyState()
            else
              for (final business in businesses ?? const <Business>[])
                Card(
                  child: ListTile(
                    key: ValueKey(business.id),
                    leading: const Icon(Icons.storefront),
                    title: Text(business.name),
                    subtitle: Text('${business.timezone} · ${business.status}'),
                    trailing: const Icon(Icons.chevron_right),
                    enabled: business.isActive,
                    onTap: business.isActive ? () => _openUpload(business) : null,
                  ),
                ),
          ],
        ),
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 48),
      child: Column(
        children: [
          const Icon(Icons.storefront_outlined, size: 56),
          const SizedBox(height: 12),
          Text(
            'Henüz bir işletme yok',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 4),
          const Text('Başlamak için yeni bir işletme oluşturun.'),
        ],
      ),
    );
  }
}
