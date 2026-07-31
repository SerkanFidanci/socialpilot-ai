# infrastructure — adapter katmanı

**Sahibi:** `../modules/` içindeki port protokollerinin somut uygulamaları — PostgreSQL,
Redis, Celery, object storage, kimlik doğrulama ve medya işleme adapter'ları.
**Sahibi değil:** iş kuralı, durum geçişi, yetkilendirme. Bunlar modül servislerindedir;
buradaki hiçbir sınıf domain kararı vermez.

## Değişmezler

- **Yön tek taraflıdır:** adapter port'u uygular, port adapter'ı bilmez. Dışa `../modules/` içinde tanımlı tipler döner, sağlayıcı SDK tipi sızmaz (ADR-004). Yeni sağlayıcı eklemek **yeni bir dosya** eklemektir; mevcut adapter'a `if provider == ...` dalı yazılmaz.
- **Her dış çağrının timeout'u vardır**; timeout'suz istemci eklenmez. `fake_*` ve `local.py` adapter'ları **üretimde çalışmaz**: `_reject_production` benzeri kapı bunu zorlar ve test kolaylığı için gevşetilmez.
- FFmpeg işleri izole ve sınırlı çalışır: `frame_extraction.py` frame bütçesini, dizin/dosya kısıtını ve sınırlı diagnostic çıktısını korur.

## Dosyalar

| Dosya | İş |
|---|---|
| `database/session.py` | `Database`, `create_database`, istek-kapsamlı `get_session` (async SQLAlchemy) |
| `database/metadata.py` | Tüm süreçler için eksiksiz SQLAlchemy metadata + `verify_mapping_is_complete()` |
| `redis/client.py` | `create_redis_client` — sınırlı bağlantı timeout'larıyla Redis istemcisi |
| `celery_app.py` | `create_celery_app` — domain task'ı kaydetmeyen Celery konfigürasyonu |
| `celery_publisher.py` | `CeleryOutboxPublisher` — dayanıklı PostgreSQL drain task'larını uyandıran outbox yayıncısı |
| `storage/fake.py` | `FakeMultipartStorage` — byte kabul etmeyen bellek içi multipart fake (bkz. bloke edici **B1**) |
| `storage/s3.py` | `S3MultipartStorage` — SigV4'ü `httpx` üzerinde kendi imzalayan S3/MinIO multipart adapter'ı; sağlayıcı `UploadId`'sini döner (kontrol objesi yok, W10), imzalı URL'i döndürmez/loglamaz/hataya koymaz |
| `storage/__init__.py` | `create_storage` — `STORAGE_ADAPTER`'a göre fake/s3 seçimi, üretimde `fake` reddedilir |
| `render/ffmpeg.py` | `FFmpegRenderAdapter` — `RenderPort`'un FFmpeg uygulaması: filtre grafiği, concat demuxer, altyazı yakma, timeout ve kısmi çıktı temizliği; `ffmpeg` sözcüğü bu çizginin altında kalır |
| `render/fake.py` | `FakeRenderAdapter` — yer tutucu dosya yazan render fake'i (birim testler FFmpeg olmadan render *servisini* sınar); üretimde reddedilir |
| `render/__init__.py` | `create_render` — `RENDER_ADAPTER`'a göre fake/ffmpeg seçimi (`create_storage` deseni), üretimde `fake` reddedilir |
| `identity/local.py` | `LocalIdentityVerifier` — yalnızca geliştirme/test için imzalı yerel token |
| `media/frame_extraction.py` | `FFmpegFrameExtractionAdapter` + `select_frame_timestamps` — sınırlı gerçek frame çıkarma |
| `media/s3_materializer.py` | `S3MediaMaterializer` — depodan worker scratch'ine akışlı indirme; W01 adapter'ının imzalamasını yeniden kullanır, kısmi dosya bırakmaz (ADR-009) |
| `media/__init__.py` | `create_materializer` — `MATERIALIZER_ADAPTER`'a göre fake/s3 seçimi (`create_storage` deseni), üretimde `fake` reddedilir |
| `media/fake_ingest.py` | Byte'sız içerik denetimi / malware tarama / materialize fake'leri |
| `media/fake_scene_speech.py` | Deterministik sahne tespiti, ses çıkarma ve ASR fake'leri |
| `media/fake_video_understanding.py` | Deterministik frame çıkarma ve video-understanding fake'leri |
| `ai/__init__.py` | `create_script_generator` — `SCRIPT_GENERATION_ADAPTER`'a göre fake/disabled seçimi; **üretim `fake` yerine disabled alır**, boot düşürülmez (gerekçe dosyanın içinde) |
| `ai/fake_script.py` | `FakeScriptGenerationAdapter` (fixture senaryo yazarı; düşman çıktıları için `output_json`/`failure`/`echo_untrusted_notes`) + `DisabledScriptGenerationAdapter` |
| `__init__.py` (ve alt paket `__init__`'leri) | Paket sınırları |

## Gereksinim, karar, mimari

- [96-stack-and-topology.md](../../../../docs/product/requirements/96-stack-and-topology.md) (PRD §6, §7) · [35-ai-routing-cost.md](../../../../docs/product/requirements/35-ai-routing-cost.md) (§17.3 provider interface) · [30-media-analysis.md](../../../../docs/product/requirements/30-media-analysis.md) (§15, §16)
- [ADR-004](../../../../docs/adr/ADR-004-provider-adapter-pattern.md) · [ADR-002](../../../../docs/adr/ADR-002-direct-object-storage-upload.md) · [ADR-007](../../../../docs/adr/ADR-007-media-analysis-provider-routing.md)
- Mimari: [ai-provider-routing.md](../../../../docs/architecture/ai-provider-routing.md) · [media-upload.md](../../../../docs/architecture/media-upload.md)

## Testler

`tests/unit/test_frame_extraction.py` · `tests/unit/test_celery_publisher.py` ·
`tests/unit/test_content_script_unit.py` · `tests/integration/test_alembic.py` ·
ilgili modül testleri
