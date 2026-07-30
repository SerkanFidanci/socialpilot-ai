# media — medya alım ve analiz modülü

**Sahibi:** doğrudan yükleme control-plane'i, ingest güvenlik geçidi, teknik analiz, sahne/konuşma çözümleme, video understanding ve bunların özet okuması.
**Sahibi değil:** medya byte'ları (asla FastAPI'den geçmez), FFmpeg/sağlayıcı uygulamaları (→ `../../infrastructure/media/`), job/outbox kayıtları (→ `../operations/`), Celery kayıt ve drain (→ `../../worker/`).

## Değişmezler

- **Byte bu modülden geçmez.** API yalnızca signed multipart URL üretir; yükleme istemciden doğrudan object storage'a gider (ADR-002).
- **Her sorgu `business_id` ister.** `MediaRepository`'de tenant filtresiz okuma/yazma yok.
- `storage.py`, `technical.py`, `scene_speech.py`, `video_understanding.py` içindeki port protokollerine **provider SDK tipi geçmez**; servis yalnızca normalize edilmiş dataclass görür.
- Hata sınıflandırması `*TransientError` / `*PermanentError` ikilisiyle yapılır — retry kararı buna bağlıdır, sağlayıcı hata metnine değil.
- Yükleme içeriği **uzantıyla değil içerik denetimiyle** doğrulanır; medya içindeki metin güvenilmez veridir, talimat olarak okunmaz (ADR-006).
- Durum geçişleri `service.py`/`video_understanding_service.py` içinde tek transaction'da yazılır; kısmi durum bırakılmaz.

## Dosyalar

| Dosya | İş |
|---|---|
| `service.py` | `MediaService` — doğrudan yükleme control-plane'i için yetkilendirme, durum kuralları, transaction |
| `ingest.py` | `MediaIngestService` + içerik denetim/malware port'ları ve `IngestValidationError` (güvenlik geçidi) |
| `technical.py` | FFprobe/FFmpeg teknik analiz kontratları, `FFprobeAdapter`, `FFmpegDerivativeAdapter`, `TechnicalAnalysisService` |
| `scene_speech.py` | Sahne tespiti + konuşma çözümleme kontratları, `FFmpegAudioExtractionAdapter`, normalize fonksiyonları |
| `video_understanding.py` | Sağlayıcıdan bağımsız kontratlar ve **saf** güvenlik/kapsama kuralları (`SceneAnalysisMode`, `SceneCoverageReport`, `normalize_provider_output`) |
| `video_understanding_service.py` | `VideoUnderstandingSchedulingService` / `VideoUnderstandingService` — dayanıklı, tenant-güvenli orkestrasyon |
| `processing_summary.py` | `ProcessingSummaryService` — bir asset'in dayanıklı işleme durumunun salt-okunur özeti (istemci checklist'i) |
| `storage.py` | `MultipartStoragePort` ve part/metadata dataclass'ları — sağlayıcıdan bağımsız depolama kontratı |
| `repository.py` | `MediaRepository` — tenant-kapsamlı kalıcılık işlemleri |
| `models.py` | Asset/session/inspection/scan/teknik metadata modelleri ve tüm durum enum'ları |
| `__init__.py` | Modül paketi |

## Gereksinim, karar, mimari

- [30-media-analysis.md](../../../../../docs/product/requirements/30-media-analysis.md) (PRD §15, §16) · [92-security-privacy.md](../../../../../docs/product/requirements/92-security-privacy.md) (§33, §35) · [35-ai-routing-cost.md](../../../../../docs/product/requirements/35-ai-routing-cost.md) (§17, §39)
- [ADR-002](../../../../../docs/adr/ADR-002-direct-object-storage-upload.md) · [ADR-004](../../../../../docs/adr/ADR-004-provider-adapter-pattern.md) · [ADR-006](../../../../../docs/adr/ADR-006-media-ingest-security-gate.md) · [ADR-007](../../../../../docs/adr/ADR-007-media-analysis-provider-routing.md)
- [media-upload.md](../../../../../docs/architecture/media-upload.md) · [media-ingest-pipeline.md](../../../../../docs/architecture/media-ingest-pipeline.md) · [media-analysis.md](../../../../../docs/architecture/media-analysis.md) · [media-security.md](../../../../../docs/architecture/media-security.md) · açık bloke edici **B1** (gerçek PUT yolu yok): [docs/STATUS.md](../../../../../docs/STATUS.md)

## Testler

- Unit: `tests/unit/test_media_ingest_unit.py`, `test_technical_media.py`, `test_scene_speech.py`, `test_video_understanding.py`, `test_frame_extraction.py`
- Integration: `tests/integration/test_media_uploads.py`, `test_media_ingest.py`, `test_video_understanding_flow.py`, `test_processing_summary.py`
