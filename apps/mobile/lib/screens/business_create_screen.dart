import 'package:flutter/material.dart';

import '../api/api_exception.dart';
import '../repositories/business_repository.dart';
import '../widgets/error_banner.dart';

/// Creates a business and returns it to the caller.
class BusinessCreateScreen extends StatefulWidget {
  const BusinessCreateScreen({super.key, required this.businesses});

  final BusinessRepository businesses;

  @override
  State<BusinessCreateScreen> createState() => _BusinessCreateScreenState();
}

class _BusinessCreateScreenState extends State<BusinessCreateScreen> {
  final _formKey = GlobalKey<FormState>();
  final _nameController = TextEditingController();
  final _timezoneController = TextEditingController(text: 'Europe/Istanbul');
  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _nameController.dispose();
    _timezoneController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_submitting || !(_formKey.currentState?.validate() ?? false)) {
      return; // Guards against a double tap creating two businesses.
    }
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      final business = await widget.businesses.create(
        name: _nameController.text.trim(),
        timezone: _timezoneController.text.trim(),
      );
      if (!mounted) return;
      Navigator.of(context).pop(business);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error.message;
        _submitting = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Yeni işletme')),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: ErrorBanner(message: _error!),
              ),
            TextFormField(
              key: const Key('business-name-field'),
              controller: _nameController,
              maxLength: 160,
              textInputAction: TextInputAction.next,
              decoration: const InputDecoration(
                labelText: 'İşletme adı',
                border: OutlineInputBorder(),
              ),
              validator: (value) =>
                  (value == null || value.trim().isEmpty) ? 'İşletme adı gerekli.' : null,
            ),
            const SizedBox(height: 12),
            TextFormField(
              key: const Key('business-timezone-field'),
              controller: _timezoneController,
              maxLength: 64,
              decoration: const InputDecoration(
                labelText: 'Saat dilimi',
                helperText: 'Örnek: Europe/Istanbul',
                border: OutlineInputBorder(),
              ),
              validator: (value) =>
                  (value == null || value.trim().isEmpty) ? 'Saat dilimi gerekli.' : null,
            ),
            const SizedBox(height: 24),
            FilledButton.icon(
              key: const Key('business-create-button'),
              onPressed: _submitting ? null : _submit,
              icon: _submitting
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.check),
              label: Text(_submitting ? 'Oluşturuluyor...' : 'Oluştur'),
            ),
          ],
        ),
      ),
    );
  }
}
