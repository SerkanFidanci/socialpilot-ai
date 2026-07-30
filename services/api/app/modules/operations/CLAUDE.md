# operations — dayanıklılık modülü

**Sahibi:** arka plan işi, deneme kaydı, transactional outbox, idempotency anahtarı ve
audit log. Sistemin "bir daha çalıştırılabilir" ve "izlenebilir" olma garantisi.
**Sahibi değil:** domain iş kuralları (ilgili modülde), Celery uygulama nesnesi ve worker
composition (→ `../../infrastructure/celery_app.py`, `../../worker/`).

## Değişmezler

- **Event yazımı domain yazımıyla aynı transaction'dadır.** Outbox satırı commit edilmeden mesaj yayınlanmaz (ADR-005).
- Her job'un **status, timeout, deneme sayısı, correlation ID ve dead-letter** yolu vardır; bunlardan biri eksikse job tanımı eksiktir.
- Yalnızca **geçici** hata yeniden denenir; kalıcı hata dead-letter'a gider.
- Idempotency anahtarı istek parmak iziyle (`request_fingerprint`) eşleşmiyorsa çakışma sayılır; aynı anahtarla farklı gövde sessizce kabul edilmez.
- Tüm kayıtlar tenant-kapsamlıdır; `OperationsRepository` üzerinden filtresiz erişim yok.
- Durum geçişleri `JobStateService` üzerinden yapılır; model alanı elle güncellenmez.

## Dosyalar

| Dosya | İş |
|---|---|
| `models.py` | `OutboxEvent`, `BackgroundJob`, `JobAttempt`, `IdempotencyKey`, `AuditLog` + durum enum'ları |
| `service.py` | `IdempotencyService`, `OperationsService`, `JobStateService`, `JobRecoveryService`, `OutboxDispatchService`, `OutboxPublisherPort`, job timeout hesabı |
| `repository.py` | `OperationsRepository` — tenant-kapsamlı dayanıklı kayıt işlemleri |
| `tasks.py` | Celery task kaydı: `dispatch_outbox`, `media_ingest`, `media_technical_analysis`, `media_scene_speech_analysis` |
| `__init__.py` | Modül paketi |

## Gereksinim, karar, mimari

- [85-orchestration-events.md](../../../../../docs/product/requirements/85-orchestration-events.md) (PRD §26, §27) · [95-observability.md](../../../../../docs/product/requirements/95-observability.md) (§37, §38) · [92-security-privacy.md](../../../../../docs/product/requirements/92-security-privacy.md) (§33.6 audit)
- [ADR-005](../../../../../docs/adr/ADR-005-transactional-outbox.md) transactional outbox · [ADR-003](../../../../../docs/adr/ADR-003-n8n-orchestration-boundary.md) n8n sınırı
- Mimari: [background-jobs.md](../../../../../docs/architecture/background-jobs.md)

## Testler

`tests/unit/test_operations_unit.py` · `tests/integration/test_operations.py` ·
`tests/integration/test_celery_orchestration.py`
