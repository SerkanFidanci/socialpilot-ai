# apps/mobile — Flutter demo istemcisi

**Sahibi:** medya analiz hattının uçtan uca mobil demosu — işletme seçimi/oluşturma, video seçme, upload progress, 6 adımlı işleme checklist'i, sonuç detayı. Material 3, Türkçe.
**Sahibi değil:** iş kuralı, hak/entitlement kararı, sağlayıcı seçimi. Bunlar backend'dedir; uygulama yalnızca API'nin verdiği durumu gösterir.

## Değişmezler

- **Uygulamada API anahtarı, AI sağlayıcı anahtarı veya secret bulunmaz.** Konfigürasyon yalnızca `--dart-define` ile gelir; kaynak kodda token yok.
- **Medya byte'ı backend'e gönderilmez.** Yükleme, backend'in verdiği signed part URL'lerine doğrudan yapılır.
- Ekranlar durum uydurmaz: adım durumu `processing_summary` yanıtından gelir; istemcide "tahmini tamamlandı" hesaplanmaz.
- Ağ hatası `ApiException`/`NetworkException` olarak sınıflandırılır ve `ErrorBanner` ile gösterilir; sessiz yutma yok. Tüm metinler Türkçedir.

## Dosyalar (`lib/`)

| Dosya | İş |
|---|---|
| `main.dart` | `SocialPilotDemoApp` uygulama kökü + `ConfigurationErrorScreen` (eksik `--dart-define` durumu) |
| `config/app_config.dart` | `AppConfig` — yalnızca `--dart-define`'dan okunan çalışma zamanı konfigürasyonu |
| `api/api_client.dart` | `ApiClient` — timeout'lu HTTP taşıma katmanı |
| `api/api_exception.dart` | `ApiException`, `NetworkException` — hata sınıflandırması |
| `models/business.dart` | `Business` modeli |
| `models/upload_session.dart` | `UploadPart`, `UploadSession`, `CompletedPart` |
| `models/processing_summary.dart` | `ProcessingStep`, `PipelineStepState`, `StageState`, `MediaAssetInfo` |
| `repositories/business_repository.dart` | `BusinessRepository` — işletme listeleme ve oluşturma |
| `repositories/media_repository.dart` | `MediaRepository`, `UploadProgress`, `UploadPhase` — hash → session → part transfer → complete |
| `repositories/processing_poller.dart` | `ProcessingPoller`, `PollResult` — işleme durumu polling'i |
| `screens/business_list_screen.dart` | İşletme seçimi ve boş durum |
| `screens/business_create_screen.dart` | İşletme oluşturma formu |
| `screens/upload_screen.dart` | Video seçme ve yükleme progress'i |
| `screens/processing_screen.dart` | 6 adımlı işleme checklist ekranı |
| `screens/result_screen.dart` | Sonuç detayı: video bilgileri, sahneler, transcript |
| `widgets/step_checklist.dart` · `widgets/step_labels.dart` | `StepChecklist` adım listesi ve adım etiketlerinin Türkçe metinleri |
| `widgets/coverage_card.dart` · `widgets/error_banner.dart` | `CoverageCard` sahne kapsama özeti ve `ErrorBanner` hata gösterimi |

## Gereksinim, karar, testler

- [15-mobile-experience.md](../../docs/product/requirements/15-mobile-experience.md) (PRD §9, §10) · [30-media-analysis.md](../../docs/product/requirements/30-media-analysis.md) (§15) · [92-security-privacy.md](../../docs/product/requirements/92-security-privacy.md) (§33) · [50-subscription-entitlement.md](../../docs/product/requirements/50-subscription-entitlement.md) (§12.5 mağaza sınırı)
- [ADR-002](../../docs/adr/ADR-002-direct-object-storage-upload.md) · [media-upload.md](../../docs/architecture/media-upload.md) · kurulum: [README.md](README.md) · bloke edici **B2** (`JAVA_HOME`/Android cmdline-tools yok): [docs/STATUS.md](../../docs/STATUS.md)
- Testler: `test/api_client_test.dart`, `test/models_test.dart`, `test/polling_test.dart`, `test/widgets_test.dart`, fixture'lar `test/support/fixtures.dart`
