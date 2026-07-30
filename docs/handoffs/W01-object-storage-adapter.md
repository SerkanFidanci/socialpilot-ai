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
- **Tekrar üretilebilir dev seed:** `services/api/scripts/seed_dev.py`. Gerekçe: 2026-07-30'da Docker sıfırlandı ve elle oluşturulmuş "Demo Isletme" + analiz edilmiş asset kalıcı olarak kayboldu — seed script'i olmadığı için yeniden üretilemedi. Script idempotent olmalı (iki kez çalışınca çift kayıt yaratmaz), yalnızca `development` ortamında çalışmalı, üretimde çalıştırılmayı reddetmeli, ve gerçek üretim credential'ı istememeli. Kapsam: bir işletme + üye, bir `ready` medya asset'i, sahneler, transcript + segmentler, scene understandings — yani processing-summary ekranının dolu görüneceği asgari veri.

## Kapsam dışı (dokunma)

- Üretim sağlayıcısı seçimi, credential provisioning, bucket/IAM kurulumu, CDN.
- İçerik inspection, malware taraması, proxy üretimi, ffprobe genişletmesi — ayrı slice'lar.
- Yayın (publish) için gereken **public URL** yüzeyi — ayrı ADR gerektiriyor, PM'de.
- Migration. Şema değişikliği gerekiyorsa **dur** ve rapora yaz.
- **`docs/index.md` ve `docs/adr/README.md` — sahibi W03.** ADR-008 dosyasını yaz, ama bu indekslere **ekleme**; raporunda bildir, PM bağlar.
- Bağımlılık sürümü tazeleme, lockfile, `requirements.txt` — W02'nin. `pyproject.toml`'a yalnızca storage istemcisini ekle.
- PostgreSQL/Redis imaj sürümleri — W06. `compose.yaml`'a yalnızca minio servisini ekle.

## Dokunulacak dosyalar (ilan)

```
services/api/app/infrastructure/storage/s3.py            (yeni)
services/api/app/infrastructure/storage/__init__.py
services/api/app/modules/media/storage.py                (port yalnızca gerekiyorsa)
services/api/app/core/config.py                          (storage + MIME ayarları — bu dosyanın sahibi W01)
services/api/pyproject.toml                              (yalnızca storage istemcisi bağımlılığı)
compose.yaml                                             (minio servisi)
.env.example
services/api/scripts/seed_dev.py                         (yeni)
services/api/tests/integration/test_media_uploads*.py
services/api/tests/unit/ (fake adapter sözleşme testleri, seed idempotency testi)
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
9. ADR-008 yazıldı (indekslere **eklenmedi** — W03'ün sahipliğinde, raporda bildirildi).
10. `pyproject.toml`'daki tek değişiklik storage istemcisi bağımlılığı; sürüm tazeleme yapılmadı.
11. `seed_dev.py` sıfırdan bir veritabanında çalışıp processing-summary ekranını dolduruyor; iki kez çalıştırıldığında çift kayıt yaratmıyor; `production` ortamında reddediyor. Çalıştırma komutu raporda yazılı.

## Bilinmesi gerekenler

- Mobil istemci zaten doğru şekilde davranıyor: SHA-256'yı 1 MiB'lık stream ile hesaplıyor, 8 MiB'lık parçaları stream ederek PUT ediyor, part URL'ine bearer token eklemiyor, her denemeye ayrı idempotency key veriyor. Adapter'ın bu davranışı bozmaması gerekir — özellikle part boyutu ve part sayısı sınırları (`media_max_parts`) uyumlu kalmalı.
- Dev veritabanında hazır bir "Demo Isletme" + analiz edilmiş asset var; sonuç ekranı upload olmadan da görülebiliyor. Bu seed'i bozma.

## Rapor — 2026-07-30 · yürüten oturum (Opus 5 / high)

**Dal:** `slice/1e-object-storage` · **Commit:** `8d055b7` (base `11aefee`) · **Durum:** tamamlandı

### Yapılanlar

- **`S3MultipartStorage`** (`app/infrastructure/storage/s3.py`, yeni). SigV4 imzalama `httpx` üzerinden elle yapılıyor; async istek yoluna senkron vendor SDK'sı sokulmadı. Gerekçe, reddedilen alternatifler ve takasların tamamı **ADR-008**'de.
- **Completion doğrulaması istemciye hiç güvenmiyor:** `ListParts` → `CompleteMultipartUpload` → `HeadObject` → tek seferlik akışlı `GetObject`. İstemcinin bildirdiği part/ETag listesi sağlayıcının envanteriyle karşılaştırılıyor, finalize isteği **gözlenen envanterden** kuruluyor (uydurma ETag finalize edilemiyor), SHA-256 depodaki byte'lardan hesaplanıyor.
- **Multipart durum eşlemesi kontrol objesiyle.** `media_upload_sessions.storage_upload_id` `String(128)`; gerçek AWS `UploadId` değerleri bunu aşıyor ve kolonu genişletmek migration ister — bu slice'ın slotu yok. Bu yüzden `create_upload`, `_control/uploads/{storage_upload_id}.json` altına sunucu sahipli küçük bir JSON yazıyor; part/complete/cancel buradan çözümlüyor, finalize veya iptalde siliniyor. Kontrol yazımı başarısızsa sağlayıcı tarafındaki multipart upload abort ediliyor (orphan bırakılmıyor).
- **Presign endpoint ayrımı.** SigV4 imzayı `Host` başlığına bağladığı için part URL'leri `S3_PRESIGN_ENDPOINT_URL` (istemcinin ulaştığı adres) için imzalanıyor; sunucu tarafı `S3_ENDPOINT_URL` kullanıyor. Telefon Compose servis adını çözemeyeceği için bu ayrım mobil akış için zorunluydu.
- **Adapter seçimi** `STORAGE_ADAPTER` (`fake` | `s3`); `production` ortamında `fake` reddediliyor. Mevcut identity guard'ıyla **tek validator'da birleştirildi**: artık üretim yanlış yapılandırmasının tamamı tek seferde bildiriliyor, her yeniden başlatmada bir tanesi değil.
- **MIME allowlist:** `image/heic`, `image/heif`, `video/quicktime` eklendi; `service.py`'deki uzantı haritası `.heic`, `.heif`, `.mov` ile eşleştirildi (aksi hâlde HEIC yükleme uzantı kontrolünde reddediliyordu).
- **Compose:** `minio` (pinlenmiş imaj, `backend` + host'a yalnızca loopback publish) + tek seferlik `minio-init` bucket provisioning. Adapter kendi bucket'ını **oluşturmuyor**.
- **`scripts/seed_dev.py`** (yeni): işletme + üye, `uploaded` asset, teknik metadata, 3 sahne, transcript + 3 segment, 3 scene understanding, 4 tamamlanmış job. Idempotent, yalnızca `development`, üretimde `SystemExit`.

### Kapsam dışı bıraktıklarım ve nedeni

- **`docs/index.md` / `docs/adr/README.md`'ye ADR-008 eklenmedi** — sahibi W03. Bağlanacak satır: `ADR-008 — S3-uyumlu object storage adapter` → `docs/adr/ADR-008-s3-compatible-storage-adapter.md`.
- **Migration yok.** Alembic head değişmedi. `storage_upload_id` kolonunu genişletmek gerekiyordu; kontrol objesi bu ihtiyacı ortadan kaldırdı (ADR-008'de kayıtlı, slot açıldığında sadeleştirilebilir).
- **`requirements.txt`, sürüm tazeleme, lockfile** — W02. `pyproject.toml`'daki tek değişiklik `httpx`'in dev'den runtime bağımlılığına taşınması (storage istemcisi); sürüm aralığı `>=0.27,<0.29` aynen korundu.
- **PostgreSQL/Redis imajları** — W06. `compose.yaml`'a yalnızca minio/minio-init eklendi.
- **Gerçek materializer, content inspection, malware taraması, ffprobe genişletmesi** — ayrı slice'lar (aşağıya bakın).

### Doğrulama

Kanonik yol (runbook): `docker compose exec -T -e RUN_INTEGRATION_TESTS=1 api pytest`.

| Kontrol | Sonuç |
|---|---|
| `ruff check` + `ruff format --check` (app, tests, migrations, scripts) | ✅ temiz, 101 dosya |
| `mypy .` (strict) | ✅ 101 dosyada sorun yok |
| pytest (integration kapalı, `make test-backend` yolu) | ✅ 244 geçti |
| pytest (`RUN_INTEGRATION_TESTS=1`, gerçek PostgreSQL + MinIO) | ✅ 244 geçti (öncesi 180) |
| pytest, `STORAGE_ADAPTER=s3` ortam değişkeni açıkken | ✅ 244 geçti (aşağıdaki düzeltmeden sonra) |
| Alembic head | ✅ `0009_video_understanding` (tek head, değişmedi) |
| migration up → down → up | ✅ `0009_video_understanding` |
| `docker compose config` | ✅ geçerli |
| OpenAPI kontratı | ✅ değişmedi (`docs/generated/openapi.json` ile birebir) |
| Kabul 1 — gerçek MinIO'ya multipart PUT → complete | ✅ `test_real_multipart_upload_reaches_storage_and_queues_ingest`: 5 MiB + 2 KiB iki parça doğrudan MinIO'ya PUT, asset `uploaded`, `ingest_status=pending`, `media.ingest` job `queued`, `media.ingest.requested` outbox kaydı. Part URL host'u depo; byte FastAPI'den geçmiyor. |
| Kabul 2 — checksum / part / süre | ✅ Gerçek MinIO'ya karşı: yanlış checksum → `409 UPLOAD_CHECKSUM_MISMATCH` (asset `uploading` kalıyor, job yaratılmıyor); eksik ve fazla part → `422 UPLOAD_METADATA_INVALID`; uydurma ETag → `409 UPLOAD_CHECKSUM_MISMATCH`; süresi geçmiş session → `UPLOAD_SESSION_EXPIRED`, imza üretilmiyor. |
| Kabul 3 — idempotency | ✅ `test_completion_is_idempotent_per_key_and_rejects_a_changed_payload`: aynı key → aynı `id`/`uploaded_at`; değişmiş payload → `409`. |
| Kabul 4 — yetki öncesi adapter çağrısı yok | ✅ `test_unauthorized_and_foreign_tenant_requests_never_reach_storage`: her çağrıda `AssertionError` atan adapter enjekte edildi; viewer `403`, yabancı tenant `404`, adapter çağrı sayısı `0`. |
| Kabul 5 — imzalı URL sızmıyor | ✅ `test_signed_part_urls_stay_out_of_logs_audit_rows_and_error_bodies`: sentinel'li URL üreten adapter; süreçten çıkan stdout ve `audit_logs`/`idempotency_keys`/`outbox_events`/`jobs`/`job_attempts`/`media_assets`/`media_upload_sessions` satırlarının tamamı taranıyor. Ayrıca adapter'ın kendi log'ları için birim testi var. |
| Kabul 6 — iOS türleri | ✅ `IMG.HEIC`/`.heif`/`.MOV` → `201`; reddedilen türde gövde `UPLOAD_METADATA_INVALID` ve tür listesinden hiçbir parça sızmıyor. |
| Kabul 7 — konfigürasyonla seçim | ✅ `production` + `fake` → `ValidationError`; eksik S3 ayarı → başlangıçta hata; `create_storage` composition root'ta ikinci kez koruyor. |
| Kabul 9 — ADR-008 | ✅ yazıldı, indekslere eklenmedi. |
| Kabul 10 — `pyproject.toml` | ✅ tek değişiklik storage istemcisi. |
| Kabul 11 — seed | ✅ Sıfırdan DB'de processing-summary `current_step=completed`, 4 aşama, 3 sahne / 3 segment / 3 understanding, teknik metadata 1080×1920, `coverage=full`. Üst üste 3 çalıştırma sonrası satır sayıları birebir aynı (`1/1/1/3/3/3/4`). `APP_ENV=test` ve `production` → `SystemExit`. |

**Seed çalıştırma komutu:**

```
docker compose exec -T api python -m scripts.seed_dev
```

Ek olarak, presign endpoint ayrımı gerçek MinIO'ya karşı ayrıca doğrulandı: sunucu `http://minio:9000`, part URL'leri farklı bir host adı için imzalandı ve o host'a yapılan PUT'lar 200 döndü — yani imza gerçekten istemcinin adresine bağlanıyor.

### Açıkça belirtmem gerekenler

**1. İlan edilen dosya listesinin dışına çıktım — 5 dosya, hepsi başka bir WO'nun sahipliğinde değil.** Her biri bir kabul kriterini karşılamak için zorunluydu:

| Dosya | Neden zorunlu |
|---|---|
| `app/main.py` | Kabul 7. `storage_factory` varsayılanı `FakeMultipartStorage`'a sabitlenmişti; konfigürasyondan seçim buraya dokunmadan çalışmıyor. Değişiklik: fabrika artık `Settings` alıyor ve varsayılanı `create_storage`. |
| `app/modules/media/service.py` | Kabul 6. `_EXTENSIONS` haritası olmadan `image/heic` allowlist'te olsa da `photo.heic` uzantı kontrolünde reddediliyordu. Ayrıca porta eklenen `content_type` geçirildi ve `StoragePermanentError` eşlemesi yapıldı. |
| `app/infrastructure/storage/fake.py` | Port'a `content_type` eklendiği için imza uyumu. Davranış değişmedi. |
| `app/worker/composition.py` | Worker `FakeMultipartStorage`'a sabitlenmişti; API `s3` iken worker `fake` kalırsa ingest `get_object_metadata`'da düşer. Tek satır: `create_storage(settings)`. |
| `tests/conftest.py` | **Gerçek bir kırılma.** `STORAGE_ADAPTER=s3` ile çalışan bir geliştirici test paketini çalıştıramıyordu: `test_media_ingest.py` ve `test_operations.py` içindeki `config()` yardımcıları adapter'ı sabitlemiyor, `Settings` ortamdan `s3` okuyup fake'in test hook'larını yok ediyordu (**14 test kırıldı**). Tek satırla merkezî çözüldü: conftest `STORAGE_ADAPTER=fake` olarak **override** ediyor; gerçek sağlayıcıyı kullanan testler kendi `Settings` nesnesinde açıkça `s3` seçiyor. Alternatif, iki test dosyasını ayrı ayrı düzeltmekti; merkezî çözüm gelecekte eklenen testleri de koruyor. |

Ek olarak `services/api/scripts/__init__.py` eklendi: `mypy .`, `scripts/seed_dev.py`'yi aynı anda `seed_dev` ve `scripts.seed_dev` olarak görüp hata veriyordu.

**2. Dev ortamı varsayılan olarak `fake` kalıyor — mobil demo için tek adım gerekiyor.** `compose.yaml` `STORAGE_ADAPTER: ${STORAGE_ADAPTER:-fake}` kullanıyor. Gerçek byte yolu için depo kökünde `.env` içine `STORAGE_ADAPTER=s3` yazıp `docker compose up -d api`. Bilinçli tercih: varsayılanı `s3` yapmak, mevcut kontrol düzlemi testlerinin dayandığı adapter'ı sessizce değiştirirdi. Kendi doğrulamam için oluşturduğum `.env`'i sildim — geride yerel yapılandırma bırakmadım. `.env.example` anahtarların tamamını içeriyor. **Runbook'a yazılması gereken bu adım W02'nin sahipliğinde** (`docs/runbooks/local-development.md`).

**3. Phase 1 çıkış kriteri hâlâ kapanmıyor — B1'in yalnızca yükleme yarısı çözüldü.** Worker medyayı hâlâ fixture tabanlı `FakeMediaMaterializer` ile "materialize" ediyor (`b"test-only-media"` yazıyor). Yani gerçek MinIO'ya yüklenen bir video ingest'i geçiyor ama teknik analiz ffprobe'a çöp veriyor. *"10 video yüklenir; sahneler, transcript ve etiketler görünür"* için **gerçek bir materializer adapter'ı** (`MediaMaterializerPort`'un S3 implementasyonu) gerekiyor — ayrı WO. `ContentInspectionPort` ve `MalwareScanPort` de hâlâ fake.

**4. `video/quicktime` ve HEIC kabul ediliyor ama analiz edilmiyor.** `ingest.py::_complete_clean` teknik analizi yalnızca `asset.content_type == "video/mp4"` iken kuyruğa alıyor. iOS'un varsayılan `.mov` çıktısı ingest'ten sonra duruyor: sahne/transcript üretilmiyor. WO ffprobe genişletmesini açıkça kapsam dışı bıraktığı için `ingest.py`'ye dokunmadım. Karar gerekiyor: (a) `.mov`/HEVC'yi analiz hattına alan slice, ya da (b) mobil istemcinin yüklemeden önce transcode etmesi. **Bu, kabul 6'nın MIME düzeltmesini yarım bırakan gerçek boşluk.**

**5. Entegrasyon test paketi geliştirme veritabanını `TRUNCATE` ediyor** (runbook bunu zaten not ediyor). Pratik sonuç: test paketinden sonra demo tenant siliniyor, `seed_dev` yeniden çalıştırılmalı. Seed artık bunu tolere ediyor — demo e-postasına sahip bir kullanıcı zaten varsa (demo token'ıyla giriş yapmak birini otomatik oluşturuyor) sabit UUID'yi zorlamak yerine mevcut satırı **benimsiyor**. Bunu doğrulama sırasında gerçek bir `UniqueViolationError` olarak yaşadım ve düzelttim.

**6. `docs/STATUS.md`'de bir tutarsızlık gördüm ve düzelttim** (protokol: "çelişkiyi gören oturum bu dosyayı aynı commit'te düzeltir"). Başlık tablosundaki `main` satırı `7d78c6e` diyordu; gerçek `main` çoktan ilerlemişti. Yalnızca o satırı ve W01 durum satırını güncelledim; dosyanın kalanına dokunmadım.

**7. Sağlayıcı tarafı checksum kullanılmadı — bilinçli.** S3'ün `ChecksumType=FULL_OBJECT` SHA-256'sı completion'ı bedava yapardı ama S3-uyumlu sağlayıcılar ve MinIO sürümleri arasında desteği değişken; iki yollu bir çözümde asıl doğruluk garantisi olan fallback neredeyse hiç çalışmayacaktı. Tek, her zaman doğru yol seçildi. Maliyet: completion `MEDIA_MAX_BYTES` ile doğru orantılı bir okuma yapıyor. Yükseltme yolu ADR-008'de.

**8. Compose'da bir ortam tuzağı bulundu:** `minio-init` yalnızca `internal: true` olan `backend` ağındayken Docker'ın gömülü DNS'i bu imaj için SERVFAIL döndürüyor ve bucket hiç oluşmuyor. `edge` ağı eklendi (postgres/redis/minio'nun host publish için yaptığının aynısı) ve alias adımına sınırlı retry konuldu. `networks: [backend, edge]` satırı kosmetik değil.

## Doğrulama

### Doğrulama — 2026-07-30 · Codex test oturumu

| # | Bulgu | Şiddet | Yeniden üretim | Durum |
|---|---|---|---|---|
| 1 | Kabul 8 karşılanmıyor: Windows hostta `make` kurulu değil; Makefile’daki eşdeğer kapılar API konteynerinde çalıştırıldığında `ruff check` temiz olsa da `ruff format --check` `tests/integration/test_media_ingest.py:377` için başarısız, `mypy .` 7 dosyada 21 hata veriyor. Çalışma ağacı temizdi; bu test oturumu kod değiştirmedi. | orta | `docker compose exec -T api python -m ruff format --check app tests migrations`; `docker compose exec -T api python -m mypy .` | açık |
| 2 | Gerçek MinIO completion sınırları saldırıya dayanıklı: uydurma ETag `409 UPLOAD_CHECKSUM_MISMATCH`; eksik/fazla part `422 UPLOAD_METADATA_INVALID`; süresi geçmiş session yeni URL üretmiyor; checksum uyuşmazlığında asset `uploading` kalıyor ve ingest işi oluşmuyor. | — | `docker compose exec -T -e RUN_INTEGRATION_TESTS=1 api pytest tests/integration/test_media_uploads_minio.py -vv` → 5 geçti | kabul edildi |
| 3 | SigV4 sınırları: sunucunun ürettiği güvenli key alanı dışındaki `+`, `%`, boşluk ve Unicode içeren key adapter tarafından imzalanmadan `StoragePermanentError` ile reddediliyor; 1.000 part URL’si üretildi; decoded credential scope `eu-west-1/s3/aws4_request`; 20 dakika eski imza MinIO’dan `403` aldı. | — | `S3MultipartStorage` ile doğrudan key/part/scope/expired-signature probu (MinIO) | kabul edildi |

**Karar:** düzeltme gerekiyor — W01 davranış saldırıları kabul edildi, ancak zorunlu `make verify` kapısı şu an yeşil değil.

### PM değerlendirmesi — 2026-07-30

**Bulgu 2 ve 3 kabul edildi ve değerlidir.** SigV4 sınır saldırıları (özel karakterli key, 1.000 part, credential scope, süresi geçmiş imza) ve completion sınır saldırıları (uydurma ETag, eksik/fazla part, checksum uyuşmazlığında asset durumu) W01'in en riskli iki yüzeyini bağımsız olarak doğruladı. Bunlar W01'i kabul etme kararının dayanağıdır.

**Bulgu 1, W01'e ait değil — ortam kirlenmesi.** Kırmızı kapı gerçekti ama nedeni W01'in kodu değil, testin **yanlış araç zincirinde** koşmasıydı. Kanıt: doğrulama sırasında çalışan API konteynerinde mypy 2.3.0, ruff 0.16.0, celery 5.6.3, fastapi 0.141.1, Python 3.13.14 vardı — oysa o commit'teki `pyproject.toml` mypy `<1.14`, ruff `<0.9`, celery `<5.5`, fastapi `<0.116`, Python `<3.13` istiyordu. Yani `main`'in kaynağı **W02'nin yükseltilmiş araç zincirinden** geçirilmiş. Bildirilen 21 mypy hatası ve format hatası W02'nin sürüm yükseltmesinin beklenen sonucudur ve W02 kendi dalında zaten düzeltmişti.

**Kök neden ve kalıcı düzeltme:** `compose.yaml` sabit `name: socialpilot-ai` kullanıyordu; Docker Compose proje adını worktree'den türetmediği için **herhangi bir worktree'de `docker compose up --build` çalıştırmak paylaşılan konteynerleri ele geçiriyordu**. W02 kendi dalında doğrulama yaparken `main`'in konteynerini kendi imajıyla değiştirdi. Proje adı `${COMPOSE_PROJECT_NAME:-socialpilot-ai}` yapıldı (`5ee03d4`) ve kural [handoffs/README.md](README.md)'ye yazıldı.

**Bu yüzden bulgu 1 kapatılıyor** — W01'de düzeltilecek bir şey yok. Ama Codex'in raporu bunu bulmamızı sağladı: paralel doğrulamayı sessizce geçersiz kılan gerçek bir altyapı hatasıydı ve tek bir oturum bunu göremezdi. Yanlış nedene bağlanmış doğru bir gözlem, değersiz bir rapor değildir.

**Birleşik durumun gerçek sonucu** (`5ee03d4`, W01+W02+W03+W09 hep birlikte, W02'nin araç zinciriyle): lint yeşil · format yeşil · mypy 105 dosya yeşil · **264 pytest** geçti · migration up/down/up head değişmedi · kontrat drift'i yok.
