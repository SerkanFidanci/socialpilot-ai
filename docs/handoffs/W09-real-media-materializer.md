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
docs/adr/ADR-009-<gercek-medya-materializer>.md            (yeni — numarayı dizini tarayarak doğrula)
```

> **Uyarı:** W02 de `ADR-009` numarasını kullanmayı planlıyor. Dalını açtığında `docs/adr/` dizinini tara ve **kullanılmayan** en küçük numarayı al; çakışırsa sıradakine geç ve raporda hangi numarayı aldığını yaz.

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

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum doldurur)_
