# W09 — Gerçek medya materializer + `.mov`/HEVC analiz kapısı

**Dal:** `slice/1g-real-materializer` · **Base:** `main` · **Migration slotu:** yok · **W02 ile paralel çalışabilir** (dosya-ayrık)
**Durum:** hazır, tetiklenmedi
**Neden bu iş:** **Phase 1 çıkış kriterinin kalan yarısı.** W01 yükleme yolunu gerçek yaptı (multipart PUT → MinIO → complete → `uploaded` + ingest job). Ama worker medyayı hâlâ `FakeMediaMaterializer` ile "materialize" ediyor ve diske `b"test-only-media"` yazıyor — yani gerçek MinIO'ya yüklenen bir video ingest'i geçiyor, sonra **ffprobe'a çöp veriliyor**. Ayrıca `.mov`/HEIC allowlist'te ama analiz hattına girmiyor. Bu ikisi kapanmadan *"10 video yüklenir; sahneler, transcript ve etiketler görünür"* karşılanamaz.

## Okunacaklar

Router: [`docs/index.md`](../index.md) → "Mevcut modülde yeni özellik" satırı. Asgari set:

1. [`docs/STATUS.md`](../STATUS.md) — özellikle **B1** ve **K6**
2. [`docs/handoffs/W01-object-storage-adapter.md`](W01-object-storage-adapter.md) — **Rapor bölümü, madde 3 ve 4** (bu WO'nun doğrudan girdisi)
3. [`docs/adr/ADR-008-s3-compatible-storage-adapter.md`](../adr/ADR-008-s3-compatible-storage-adapter.md)
4. [`docs/adr/ADR-006-media-ingest-security-gate.md`](../adr/ADR-006-media-ingest-security-gate.md)
5. `services/api/app/modules/media/CLAUDE.md` (modül haritası — dosyaları tek tek açarak keşfetme)
6. [`docs/product/requirements/30-media-analysis.md`](../product/requirements/30-media-analysis.md) — §15.2 doğrulama gereksinimleri, §16.1 yerel analiz
7. `docs/architecture/media-ingest-pipeline.md`

## Kapsam

### 1. Gerçek `MediaMaterializerPort` implementasyonu

- S3-uyumlu depodan worker'ın yerel scratch alanına **akışlı** indirme. Tüm dosyayı belleğe alma.
- W01'in `S3MultipartStorage`'ının imzalama/hata çevirme mantığını **yeniden yazma** — mevcut adapter'ı veya onun paylaşılan parçasını kullan. İki yerde SigV4 istemiyoruz.
- Boyut ve tür sınırları indirme **öncesinde** doğrulanır (`HeadObject`), sonra değil. Beklenmedik boyut → indirme başlamaz.
- Scratch alanı zorunlu temizlik: başarıda, hatada ve timeout'ta. Kısmi dosya bırakılmaz (PRD §19.3).
- Tenant kapsamı: worker hedefi tenant-kapsamlı repository üzerinden yeniden yükler ve durumu doğrular (mevcut kural).
- `fake` materializer testler için **kalır**; seçim `STORAGE_ADAPTER` ile aynı mantıkta yapılır ve `production`'da `fake` reddedilir.

### 2. `.mov` / HEVC analiz kapısı (K6)

- `ingest.py::_complete_clean` teknik analizi yalnızca `video/mp4` için kuyruğa alıyor; bu kapı **desteklenen video türleri kümesine** genişletilir (`video/mp4`, `video/quicktime`).
- Kapı `content_type` eşitliğine değil, **ffprobe'un gerçekte çözdüğü codec'e** dayanmalı: container kabul edilse de codec desteklenmiyorsa iş `rejected` olur ve kullanıcıya anlaşılır bir hata koduyla döner — sessizce durmaz.
- Proxy üretimi HEVC girdiden H.264 çıktı verebiliyor mu doğrulanır (§15.5 proxy profili).
- **HEIC/HEIF fotoğraflar bu WO'da analiz hattına alınmaz.** Mevcut hat video odaklı (sahne, transcript). Fotoğraf için "analiz"in ne olduğu (teknik metadata + VLM etiketleme; sahne/ASR yok) tanımlanmamış — K6'nın ikinci yarısı, ayrı slice. Bu WO yalnızca **HEIC'in sessizce ölmediğinden** emin olur: ya açık bir "fotoğraf analizi henüz desteklenmiyor" durumu, ya da allowlist'ten geçici çıkarma. Hangisini seçtiğini raporda gerekçelendir.

### 3. Uçtan uca doğrulama

Gerçek bir video dosyasıyla (fixture değil) tam zincir: multipart upload → complete → ingest → teknik analiz → sahne tespiti → ASR → video understanding → processing-summary `coverage`. En az bir `.mp4` ve bir `.mov` ile.

## Kapsam dışı (dokunma)

- **Migration.** Şema değişikliği gerekiyorsa dur ve rapora yaz. `storage_upload_id` genişletme işi W04'ün slotunda.
- **Gerçek `ContentInspectionPort` / `MalwareScanPort`** — hâlâ fake, ayrı slice. Bu WO onları değiştirmez ama **atlamaz** da: ingest güvenlik geçidi (ADR-006) sırası korunur.
- **Gerçek AI sağlayıcıları.** ASR ve VLM fake kalır. Ücretli sağlayıcı bağlanması W08 benchmark'ından sonra.
- `pyproject.toml`, `Dockerfile`, `Makefile`, `.github/workflows/**`, `docs/runbooks/local-development.md` → **W02'nin**. Bağımlılık eklemen gerekiyorsa dur ve rapora yaz.
- `docs/index.md`, `docs/adr/README.md` → indekse ADR eklemezsin, raporda bildirirsin (PM bağlar).
- HEIC fotoğraf analiz hattı (yukarıya bakın).

## Dokunulacak dosyalar (ilan)

```
services/api/app/infrastructure/media/s3_materializer.py   (yeni)
services/api/app/infrastructure/media/fake_ingest.py       (imza uyumu gerekiyorsa)
services/api/app/infrastructure/storage/s3.py              (paylaşılan indirme/imzalama parçası)
services/api/app/modules/media/ingest.py                   (analiz kapısı)
services/api/app/worker/composition.py                     (materializer seçimi)
services/api/app/core/config.py                            (materializer ayarı)
services/api/tests/integration/test_media_ingest.py
services/api/tests/integration/ (yeni uctan uca test)
services/api/tests/unit/
docs/architecture/media-ingest-pipeline.md
docs/adr/ADR-011-<gercek-medya-materializer>.md            (yeni — numarayı dizini tarayarak doğrula)
```

> **Uyarı:** W02 de `ADR-011` numarasını kullanmayı planlıyor. Dalını açtığında `docs/adr/` dizinini tara ve **kullanılmayan** en küçük numarayı al; çakışırsa sıradakine geç ve raporda hangi numarayı aldığını yaz.

## Kabul kriterleri

1. Gerçek bir `.mp4` MinIO'ya yüklenip zincirin sonunda processing-summary'de sahneler, transcript ve scene understanding görünüyor. Fixture byte'ı hiçbir adımda kullanılmıyor.
2. Aynısı bir `.mov`/HEVC dosyasıyla çalışıyor.
3. Desteklenmeyen codec → iş `rejected`, dokümante edilmiş hata kodu, **sessiz durma yok**.
4. Materializer akışlı: `MEDIA_MAX_BYTES` boyutunda bir dosyada süreç bellek kullanımı dosya boyutuyla doğru orantılı **artmıyor**.
5. Scratch alanı başarıda, hatada ve timeout'ta temizleniyor; kısmi dosya kalmıyor (test var).
6. `production` + `fake` materializer → başlangıçta reddediliyor.
7. HEIC yükleme sessizce ölmüyor (açık durum veya allowlist kararı, raporda gerekçeli).
8. Tenant izolasyonu: başka tenant'ın asset'i için materializer çağrısı yapılmıyor.
9. İmzalı URL / credential log'a, audit'e, hata gövdesine sızmıyor (W01'in sentinel testi deseni).
10. `make verify` yeşil, Alembic head değişmemiş (`0009_video_understanding`).
11. **Phase 1 çıkış kriteri karşılandı:** en az 3 farklı gerçek video (biri `.mov`, biri dikey, biri sesli) yüklenip sahne + transcript + etiket üretiyor. Raporda çıktı özeti.

## Rapor — 2026-07-30 · yürüten oturum (Opus 4.8 / high)

**Dal:** `slice/1g-real-materializer` · **Base:** `main` (`82eb4dc`) · **Durum:** tamamlandı

### Yapılanlar

- **Gerçek `MediaMaterializerPort`** (`infrastructure/media/s3_materializer.py`, yeni).
  `S3MediaMaterializer` depodaki objeyi worker scratch'ine **1 MiB parçalarla akıtır**. W01'in
  `S3MultipartStorage`'ına eklenen `download_to_path` üzerinden çalışır — **ikinci bir SigV4
  yok**. Boyut indirmeden önce `HeadObject` ile doğrulanır (sistem geneli tavan =
  `max(media_max_bytes, media_max_derivative_bytes, media_max_extracted_audio_bytes)`); indirme
  sırasında head boyutu koşan bir tavan olarak uygulanır (obje büyürse reddedilir). Hata,
  iptal ve timeout'ta kısmi dosya **silinir** (§19.3). Port imzası değişmedi, üç çağrı yeri
  (`technical`, `scene_speech`, `video_understanding`) elden geçmedi. Gerekçe/alternatifler:
  **ADR-011**.
- **Materializer seçimi** `MATERIALIZER_ADAPTER` (`fake|s3`) + `create_materializer` fabrikası
  (`infrastructure/media/__init__.py`), `create_storage` deseninin aynısı. `production`'da
  `fake` reddedilir (`reject_non_production_adapters`'a eklendi); `s3` materializer aynı `S3_*`
  konfigürasyonunu gerektirir (`require_complete_s3_configuration` genişletildi).
- **`.mov`/HEVC analiz kapısı.** `ingest.py::_complete_clean` artık teknik analizi
  `media_analyzable_video_types` (`video/mp4`, `video/quicktime`) için kuyruğa alır. Codec kararı
  ffprobe çıktısından verilir: `technical.py::validate_technical_metadata`
  `media_supported_video_codecs` (`h264`, `hevc`) dışını `TechnicalUnsupportedMediaError`
  (`TECHNICAL_VIDEO_CODEC_UNSUPPORTED`) ile reddeder; yeni `_reject` yolu asset'i `rejected`
  yapar (mimari dokümandaki "unsupported media → rejected" kuralı) — **sessiz durma yok**.
  HEVC → H.264 proxy mevcut derivative adapter'ıyla üretiliyor (E2E'de doğrulandı).
- **HEIC/HEIF açık ret.** Güvenlik geçidinden sonra ingest bunları
  `INGEST_ANALYSIS_UNSUPPORTED_MEDIA_TYPE` ile reddeder (asset `rejected`). Gerekçe: HEIC/HEIF
  web-uyumlu değil, kullanılabilmesi için henüz olmayan bir transcode gerekir; K6'nın "kabul
  edip analiz etmemek reddetmekten kötü" duruşuyla uyumlu. JPEG/PNG/ses mevcut kabul-analiz-yok
  sözleşmesini korur (test 717 bozulmadı). Fotoğraf analiz hattı (teknik metadata + VLM) K6'nın
  ikinci yarısı, ayrı slice; o slice gelince ret kümesi boşalır.
- **Uçtan uca doğrulama** (`tests/integration/test_real_media_pipeline.py`, yeni): gerçek
  MinIO'ya yüklenen 3 gerçek video (biri `.mov`/HEVC, biri dikey, biri sesli) tam zinciri
  geçiyor; processing-summary `current_step=completed`, `coverage` dolu, teknik metadata
  gerçek dosya boyutuyla eşleşiyor. Fixture byte'ı hiçbir adımda yok.

### Kapsam dışı bıraktıklarım ve nedeni

- **Migration yok.** Alembic head `0009_video_understanding` (up/down/up doğrulandı). HEIC için
  kalıcı bir "analiz beklemede/desteklenmiyor" durumu enum'a eklemek migration ister → W04
  slotu; şimdilik mevcut `rejected` + dokümante kod kullanıldı.
- **Gerçek ContentInspection/Malware/ASR/VLM** fake kaldı (WO gereği); ingest güvenlik geçidi
  sırası korundu.
- **Video-understanding frame çıkarma** E2E'de `FakeFrameExtractionAdapter` ile sürülüyor
  (materializer gerçek proxy'yi yine de S3'ten indiriyor). Gerçek frame çıkarma zaten
  `test_video_understanding_flow` içinde kapsanıyor ve WO AI-girdi hazırlığını (VLM) fake
  bırakıyor; E2E'yi deterministik tutmak için fake seçildi. Gerçek-byte kanıtı ingest+teknik+
  sahne/konuşma aşamalarında zaten var.
- **`docs/index.md` / `docs/adr/README.md`** — sahibi W03. **ADR-011 indekslere eklenmedi.**
  Bağlanacak satır: `ADR-011 — Gerçek medya materializer + .mov/HEVC analiz kapısı` →
  `docs/adr/ADR-011-real-media-materializer.md`. PM bağlar.

### Doğrulama

Kanonik ortam yeniden üretildiği için (aşağıya bakın), tüm kontroller taze imajda çalıştırıldı.

| Kontrol | Sonuç |
|---|---|
| `ruff check app tests migrations` | ✅ temiz |
| `ruff format --check app tests migrations` | ✅ 101 dosya |
| `mypy .` (strict) | ✅ 105 dosyada sorun yok |
| `pytest` (`RUN_INTEGRATION_TESTS=1`, gerçek PostgreSQL + MinIO) | ✅ **264 geçti** (öncesi 244) |
| check-openapi (semantik) | ✅ contract değişmedi |
| migration up → down → up | ✅ head `0009_video_understanding` (değişmedi) |
| Kabul 1 — gerçek `.mp4` tam zincir | ✅ E2E (dikey + sesli mp4) sahne + transcript + understanding + coverage |
| Kabul 2 — `.mov`/HEVC | ✅ E2E `.mov`/HEVC (libx265 yoksa H.264-in-MOV'a düşer, raporda) tam zincir |
| Kabul 3 — desteklenmeyen codec → rejected | ✅ `test_unsupported_codec_rejects_the_asset_with_a_documented_code`: mpeg4 → asset `rejected`, job FAILED, `TECHNICAL_VIDEO_CODEC_UNSUPPORTED`, `next_attempt_at=None` |
| Kabul 4 — akışlı (bellek dosya boyutuyla artmıyor) | ✅ 1 MiB parça; unit test çok-chunk payload'la indiriyor |
| Kabul 5 — scratch temizliği (başarı/hata/timeout) | ✅ `test_transient_outage_leaves_no_partial_file`, `test_missing_object_is_permanent_and_leaves_no_file`, `test_size_disagreement...` |
| Kabul 6 — `production` + `fake` materializer reddi | ✅ `test_settings_reject_the_fake_materializer_in_production` |
| Kabul 7 — HEIC sessizce ölmüyor | ✅ `test_heic_ingest_is_declined_explicitly_without_silent_death` (açık ret, dokümante kod) |
| Kabul 8 — tenant izolasyonu | ✅ materializer yalnızca servisin tenant-kapsamlı yüklediği asset'in object key'iyle çağrılıyor; obje-key doğrulaması sağlayıcıya ulaşmadan reddediyor (`test_unusable_object_key_never_reaches_the_provider`) |
| Kabul 9 — imzalı URL / credential sızmıyor | ✅ `test_materializer_never_logs_urls_signatures_or_keys` (log + exception gövdesi taranıyor) |
| Kabul 10 — `make verify` yeşil, head değişmemiş | ✅ (aşağıdaki not 2 hariç tamamı temiz) |
| Kabul 11 — Phase 1 çıkış kriteri (≥3 gerçek video) | ✅ E2E: `.mov`/HEVC + dikey + sesli, hepsi sahne/transcript/etiket üretiyor |

### Açıkça belirtmem gerekenler

**1. İlan edilen dosya listesi dışına çıktım — hepsi aktif bir WO'nun sahipliğinde değil, her
biri bir kabul kriteri için zorunlu (W01 precedenti):**

| Dosya | Neden zorunlu |
|---|---|
| `services/api/app/modules/media/technical.py` | Kabul 3. Codec kararı yalnızca ffprobe çıktısının olduğu yerde verilebilir; `validate_technical_metadata`'ya codec allowlist'i ve unsupported-media reddi (`_reject` → asset `rejected`) eklendi. |
| `services/api/app/infrastructure/media/__init__.py` | `create_materializer` fabrikası (`create_storage` deseni). Alt paket, kimse sahibi değil. |
| `services/api/app/infrastructure/CLAUDE.md` | Definition of Done: yeni dosya eklendiğinde modül `CLAUDE.md` güncellenir (`s3_materializer.py`, `__init__` fabrikası). Modül `CLAUDE.md`'leri tabloda W03'te; W03 merge edildiği için tekel serbest. Diğer modül `CLAUDE.md`'leri değişmedi (dosya açıklamaları hâlâ doğru). |
| `services/api/tests/unit/test_config.py` | Kabul 6 + s3-config gerekliliği testleri (config.py W01'indi, merge edildi). |
| `services/api/scripts/generate_openapi.py` | **Kabul 10 için zorunlu, ama W09'un değil — bkz. not 2.** |

**2. `mypy .` `make verify` kapsamında `scripts/generate_openapi.py`'de ÖNCEDEN VAR OLAN bir
hata veriyordu** (`from generate_endpoints_doc import write` — runtime'da satır 17'deki
`sys.path.insert` ile çözülüyor, mypy statik olarak takip edemiyor). Dosya `main`'le birebir
aynı (git `scripts/` altında değişiklik göstermiyor), yani bu W09'dan bağımsız, main'de zaten
kırık bir durum. `make typecheck` `mypy .` çalıştırdığı için Kabul 10'u bloke ediyordu. Tek
satırlık, davranış değiştirmeyen doğru anotasyonu ekledim (`# type: ignore[import-not-found]`).
**PM'e not:** kalıcı çözüm (mutlak import ve/veya W02'nin lockfile'ıyla dev-araç sürüm
sabitlemesi) W02'ye aittir; ben yalnızca gerçek kapıyı açtım.

**3. Toolchain sürüm kayması gözlemlendi.** Uzun süredir çalışan `socialpilot-ai-api` imajının
`ruff`/`mypy` sürümleri (dev extras'ta pinlenmemiş) taze bir build'den farklı davranıyor;
çalışan konteyner ayrıca eski (82eb4dc öncesi) kodu mount ediyordu. Kanonik doğrulama için
imajı worktree'den `--build-arg INSTALL_DEV=true` ile **yeniden derledim** ve tüm kontrolleri
orada çalıştırdım. Bu, W02'nin lockfile işini doğrudan motive ediyor.

**4. Compose ağ notu (doğrulama ortamı):** `minio` yalnızca `edge` ağında; `postgres`/`redis`
`backend`'de. Gerçek-S3 entegrasyon testleri her iki ağa da bağlı bir konteyner ister (kanonik
`api` servisinin yaptığı gibi). Kod değişikliği değil, doğrulama kurulumu notu.

**5. E2E `.mov` HEVC kodlaması libx265'e bağlı.** ffmpeg build'inde libx265 yoksa E2E `.mov`'u
H.264-in-MOV'a düşürür (QuickTime *container* yolu yine kanıtlanır); HEVC *decode* + H.264
proxy yolu, libx265 mevcutsa gerçek HEVC ile de doğrulanır. Doğrulama imajında libx265 vardı.

## Doğrulama

_(test eden oturum doldurur)_
