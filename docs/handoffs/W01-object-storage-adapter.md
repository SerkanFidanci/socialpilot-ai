# W01 — Object storage adapter (MinIO/S3) + MIME düzeltmesi

**Dal:** `slice/1e-object-storage` · **Base:** `main` · **Migration slotu:** yok (migration eklemeyeceksin)
**Durum:** hazır, tetiklenmedi
**Neden bu iş:** Bloke edici **B1**. `FakeMultipartStorage` byte kabul etmiyor, part URL'leri erişilemez `fake-storage.invalid` host'una gidiyor, `complete_upload` yalnızca in-process test hook'uyla çalışıyor. Bu yüzden mobil demonun 3. adımı (gerçek PUT) çalışmıyor ve **Phase 1 çıkış kriteri** ("10 video yüklenir; sahneler, transcript ve etiketler görünür") kapanamıyor. Bu WO, byte yolunu gerçek yapar.

## Okunacaklar (bunlar, bu sırayla — fazlası yok)

1. `docs/STATUS.md`
2. `docs/adr/ADR-002-direct-object-storage-upload.md`
3. `docs/architecture/media-upload.md`
4. `docs/architecture/media-security.md`
5. `services/api/app/modules/media/storage.py` (port tanımı) ve `services/api/app/infrastructure/storage/fake.py`
6. `services/api/app/modules/media/service.py` içinde yalnızca upload session oluşturma ve completion yolları
7. PRD §15 (`docs/product/product-requirements.md`, "Medya yükleme altyapısı" bölümü) — **yalnızca bu bölüm**

## Kapsam

- `MultipartStoragePort`'un S3-uyumlu gerçek implementasyonu. MinIO ile yerelde, S3/R2 ile üretimde aynı adapter çalışsın.
- Presigned multipart part URL'leri: kısa ömürlü, en az yetkili, sunucu tarafında üretilmiş opak object key ile.
- Completion doğrulaması: provider tarafında multipart finalize + object metadata/ETag okuma + **istemci beyanına güvenmeden** SHA-256 / boyut doğrulaması.
- Adapter seçimi konfigürasyondan (`fake` | `s3`). `fake` unit testlerde kalmaya devam eder; ağ/credential gerektiren testler entegrasyon işareti altında.
- MinIO servisi Compose'a eklenir; yalnızca `backend` ağında, host'a yalnızca geliştirme için publish edilir.
- Entegrasyon testi: gerçek MinIO'ya çok parçalı gerçek PUT → complete → asset `uploaded` + ingest job kuyruğa girer.
- **MIME allowlist düzeltmesi:** `media_allowed_mime_types` şu an yalnızca `image/jpeg, image/png, video/mp4, audio/mpeg`. iOS'un varsayılan çıktıları **HEIC/HEIF fotoğraf** ve **`.mov`/HEVC video (`video/quicktime`)** — mobil öncelikli üründe ana akış bu haliyle kırık. `image/heic`, `image/heif`, `video/quicktime` eklenir; her yeni tür için ffprobe/inspection tarafındaki varsayımlar gözden geçirilir.

## Kapsam dışı (dokunma)

- Üretim sağlayıcısı seçimi, credential provisioning, bucket/IAM kurulumu, CDN.
- İçerik inspection, malware taraması, proxy üretimi, ffprobe genişletmesi — ayrı slice'lar.
- Yayın (publish) için gereken **public URL** yüzeyi — ayrı ADR gerektiriyor, PM'de.
- Migration. Şema değişikliği gerekiyorsa **dur** ve rapora yaz.
- `app/core/config.py` dışındaki hiçbir W02/W03 dosyası.

## Dokunulacak dosyalar (ilan)

```
services/api/app/infrastructure/storage/s3.py            (yeni)
services/api/app/infrastructure/storage/__init__.py
services/api/app/modules/media/storage.py                (port yalnızca gerekiyorsa)
services/api/app/core/config.py                          (storage + MIME ayarları — bu dosyanın sahibi W01)
services/api/pyproject.toml                              (yalnızca storage istemcisi bağımlılığı)
compose.yaml                                             (minio servisi)
.env.example
services/api/tests/integration/test_media_uploads*.py
services/api/tests/unit/ (fake adapter sözleşme testleri)
docs/architecture/media-upload.md                        (adapter bölümü)
docs/adr/ADR-008-<s3-uyumlu-storage-adapter>.md          (yeni ADR)
```

> `pyproject.toml`'a **yalnızca** storage istemcisi eklenir; sürüm tazeleme ve lockfile W02'nin işi. `requirements.txt`'e dokunma.

## Kabul kriterleri

1. Yerel MinIO'ya karşı: upload session oluştur → parçaları **doğrudan** MinIO'ya PUT et → complete → asset `uploaded`, `ingest_status=pending`, ingest job kuyrukta. Byte'lar FastAPI'den geçmez.
2. Checksum uyuşmazlığı `UPLOAD_CHECKSUM_MISMATCH` döner; eksik/fazla part `UPLOAD_METADATA_INVALID`; süresi geçmiş session yeni part URL'i döndürmez.
3. Aynı idempotency key ile tekrar completion → ilk sonuç; farklı payload → `409`.
4. Yetkisiz veya başka tenant'ın session'ı → adapter çağrısı **yapılmadan** `403`/`404`.
5. Presigned URL'ler log'a, audit detayına, hata gövdesine hiçbir koşulda yazılmaz. Bunu doğrulayan bir test var.
6. `image/heic`, `image/heif`, `video/quicktime` kabul edilir; reddedilen tür için hata mesajı tür listesini sızdırmaz.
7. Adapter seçimi konfigürasyonla; `production` ortamında `fake` adapter reddedilir (mevcut identity adapter'ındaki desenin aynısı).
8. `make verify` yeşil. Alembic head değişmemiş: `0009_video_understanding`.
9. ADR-008 yazıldı, `docs/index.md` ve `docs/adr/README.md`'ye eklendi.

## Bilinmesi gerekenler

- Mobil istemci zaten doğru şekilde davranıyor: SHA-256'yı 1 MiB'lık stream ile hesaplıyor, 8 MiB'lık parçaları stream ederek PUT ediyor, part URL'ine bearer token eklemiyor, her denemeye ayrı idempotency key veriyor. Adapter'ın bu davranışı bozmaması gerekir — özellikle part boyutu ve part sayısı sınırları (`media_max_parts`) uyumlu kalmalı.
- Dev veritabanında hazır bir "Demo Isletme" + analiz edilmiş asset var; sonuç ekranı upload olmadan da görülebiliyor. Bu seed'i bozma.

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum doldurur)_
