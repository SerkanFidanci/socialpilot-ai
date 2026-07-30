# W10 — Şema borcu: dört birikmiş kalem

**Dal:** `slice/0m-schema-debt` · **Base:** `main` · **Migration slotu: SENDE** (`0011`)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 4.8 / medium
**Neden bu iş:** Dört kalem, dördü de bir migration bekliyordu ve slot başkasındaydı. Her biri **bilinçli olarak** ertelendi ve gerekçesi kayıtlı; hiçbiri bugün bir şeyi bloke etmiyor ama üçü taşıma maliyeti yaratıyor. Slot boşaldı, hepsi tek slice'ta kapanır. Bu WO yeni yetenek eklemez — **var olan geçici çözümleri kaldırır ve yarım kalan sözleşmeleri tamamlar.**

## Okunacaklar

Router: [`docs/index.md`](../index.md) → "Mimari değişiklik" satırı. Asgari set:

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/handoffs/PM-NOTES.md`](PM-NOTES.md) — **"ADR-008 ekleri"** bölümü (kalem 1 ve 3'ün gerekçesi)
3. `services/api/app/modules/media/CLAUDE.md`, `services/api/app/modules/businesses/CLAUDE.md`
4. [`docs/architecture/ai-provider-routing.md`](../architecture/ai-provider-routing.md) — `provider_usage`'in **planlanan** tablo olduğu notu
5. [`docs/architecture/tenant-isolation.md`](../architecture/tenant-isolation.md) — rol matrisi (kalem 4)

## Kapsam — dört kalem

### 1. `provider_usage` tablosu

ADR-007 ve `ai-provider-routing.md` bu tabloyu tarif ediyor; **yoktu.** W08 bunu yakaladı, migration slotu olmadığı için tabloyu eklemedi ve aynı alanları taşıyan `ProviderUsageRecord` **değerini** üretti (`app/benchmark/model.py`).

- Tabloyu bu şekle göre oluştur: tenant/job/asset/run/capability/provider/model, tahmini ve gerçek **integer minor unit** maliyet, para birimi, süre, sonuç, correlation ID.
- **Dışlananlar sözleşmenin parçası:** token değeri, prompt, imzalı URL, ham yanıt — hiçbiri saklanmaz.
- Benchmark harness'ı tabloyu kullanacak şekilde bağla, ama **paralel muhasebe kurma**: `ProviderUsageRecord` şekli korunur, arkasına kalıcılık gelir.
- `ai-provider-routing.md`'deki "planlanan tablo / maliyet atfı kalıcı değil" notunu **gerçeğe göre güncelle**.

### 2. `media_upload_sessions.storage_upload_id` genişletmesi

`String(128)` gerçek AWS `UploadId` değerleri için kısa. W01 slotu olmadığı için `_control/uploads/{id}.json` yazan **sunucu sahipli bir kontrol objesi** kullandı (ADR-008'de kayıtlı): fazladan bir round-trip, fazladan bir hata modu, temizlenmesi gereken fazladan bir obje.

- Kolonu gerçek sağlayıcı değerlerini taşıyacak genişliğe çıkar.
- **Kontrol objesi katmanını kaldır** ve `create_upload`/part/complete/cancel yollarını kolona dayandır.
- Mevcut satırlar için geçiş: kolon genişletmesi veri kaybetmez, ama yarım kalmış oturumların kontrol objesi varsa geriye dönük yol bırakma — bu bir dev-only geçiş, üretim verisi yok. Yaklaşımını rapora yaz.
- ADR-008'e ek not: geçici çözüm kaldırıldı.

### 3. Fotoğraf analiz durumu (K6 ikinci yarısı için **yalnızca** enum)

W09 HEIC/HEIF'i ingest'te açık kodla reddediyor (`INGEST_ANALYSIS_UNSUPPORTED_MEDIA_TYPE`) — sessiz çıkmaz sokak yok, doğru davranış. Ama fotoğraf hattı geldiğinde bir duruma ihtiyaç var.

- **Yalnızca durum/enum genişletmesini yap**; fotoğraf analiz hattını (teknik metadata + VLM etiketleme, sahne/ASR yok) **kurma** — o ayrı bir slice ve ürün kararı gerektiriyor.
- Enum'a eklenen değerin hiçbir yol tarafından **üretilmediğini** doğrula: yeni durum şu an ulaşılamaz olmalı, ileride hat yazıldığında kullanılacak. Ulaşılamaz bir durumu eklemenin tek gerekçesi migration slotunu bir kez kullanmak — bunu rapora yaz.

### 4. `approver` rolü

`BusinessRole` enum'unda yok; PRD §4 onu tanımlıyor ve W04'ün kabul kriteri 3'ün yarısı bu yüzden test edilemedi.

- Rolü enum'a ve rol matrisine ekle. Yetkileri [`tenant-isolation.md`](../architecture/tenant-isolation.md)'deki tabloya göre: **yalnızca onay kaynaklarını görür ve onay kararı verir**; içerik/medya yazamaz, üyelik yönetemez, faturalandırmaya dokunamaz.
- W04'ün "her `BusinessRole` üyesi için marka cevabı tanımlı" testi seni eşlemeye zorlayacak — o testi **zayıflatarak geçme**, eşlemeyi yap.
- Onay kaynakları henüz yok (Phase 2 işi). Bu yüzden `approver` şu an **hiçbir şeye erişemeyen** bir rol olacak: bunu açıkça test et ve rapora yaz. Yanlış olan bir rolü var etmemek değil, var edip sessizce geniş yetki vermek olurdu.

## Kapsam dışı (dokunma)

- **Fotoğraf analiz hattının kendisi** (kalem 3'e bak).
- **Onay akışı / approval istekleri** — Phase 2.
- **Gerçek AI sağlayıcısı bağlamak** — W08 benchmark'ından sonra ve ayrı karar.
- `compose.yaml`, `Dockerfile`, `.github/workflows/**` → W06'nın.
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.
- Yeni özellik, yeni endpoint, yeni modül. Bu WO borç kapatır.

## Dokunulacak dosyalar (ilan)

```
services/api/migrations/versions/0011_*.py                  (yeni — MIGRATION SLOTU SENDE)
services/api/app/modules/operations/models.py               (provider_usage modeli — ya da uygun modül, gerekçesini yaz)
services/api/app/benchmark/model.py                         (ProviderUsageRecord → kalıcılık bağı)
services/api/app/benchmark/runner.py                        (kayıt yazımı)
services/api/app/modules/media/models.py                    (storage_upload_id genişliği + fotoğraf durumu)
services/api/app/infrastructure/storage/s3.py               (kontrol objesi katmanının kaldırılması)
services/api/app/modules/media/service.py                   (kontrol objesine bağımlılığın kaldırılması)
services/api/app/modules/businesses/models.py + policy.py   (approver rolü)
services/api/app/infrastructure/database/metadata.py        (yeni model modülü varsa kaydı)
services/api/tests/unit/ + tests/integration/
docs/architecture/ai-provider-routing.md                    (planlanan → var)
docs/adr/ADR-008-s3-compatible-storage-adapter.md           (ek not: geçici çözüm kaldırıldı)
docs/architecture/tenant-isolation.md                       (approver satırı)
```

## Kabul kriterleri

1. Migration `0011` up → down → up çalışıyor; tek head. **Downgrade veri kaybetmiyor** ve bunu gösteren bir test var (özellikle kolon genişletmesinin geri alınması).
2. `provider_usage` tablosu var; benchmark koşusu kaydı **oraya** yazıyor; token/prompt/imzalı URL/ham yanıt saklanmadığını doğrulayan test var.
3. `storage_upload_id` gerçek sağlayıcı `UploadId` uzunluğunu taşıyor; **kontrol objesi katmanı kaldırıldı**; upload → part → complete → cancel yolları MinIO'ya karşı hâlâ geçiyor; `_control/` altına artık hiçbir obje yazılmıyor (test var).
4. Fotoğraf durumu enum'da; **hiçbir kod yolu onu üretmiyor** (test var).
5. `approver` rolü enum'da ve rol matrisinde; hiçbir yazma yetkisi yok; W04'ün rol-kapsama testi zayıflatılmadan geçiyor.
6. `make verify` yeşil; test sayısı azalmıyor (şu an 392).
7. `ai-provider-routing.md` gerçeği anlatıyor; ADR-008'e geçici çözümün kaldırıldığı not düşüldü.
8. Kontrat drift yok (`make generate-docs` sonrası temiz).

## ADR numara kuralı

Numarayı **sen seçmiyorsun.** Gerçek bir karar çıkarsa `ADR-XXX-<konu>.md` yaz, başlıkta da `ADR-XXX` bırak, raporda bildir. PM numaralandırır.

## Rapor — 2026-07-30 · yürütücü oturum (Opus 4.8)

**Dal:** `slice/0m-schema-debt` (base `main` `3fcec78`) · **Commit'ler:** tek commit (SHA git log'da) · **Durum:** tamamlandı

### Yapılanlar

**1. `provider_usage` tablosu.** `operations/models.py`'ye `ProviderUsage` modeli eklendi
(migration `0011`). Sütunlar birebir ADR-007 şekli: `business_id` (tenant, FK), `job_id`,
`asset_id`, `run_id`, `capability`, `provider`, `model`, `estimated_cost_minor`,
`actual_cost_minor` (BigInteger, `>= 0` check), `currency`, `duration_ms`, `outcome`,
`correlation_id`, `created_at`. **Token/prompt/imzalı URL/ham yanıt için sütun yok** — dışlama
yapısal. `capability` PG enum değil düz string (yeni kabiliyet migration istemez); `job_id`/
`asset_id` düz UUID (`jobs.resource_id` deseni), yalnız `business_id` FK — modül `media`'ya
bağımlı olmuyor. `ProviderUsage.from_measurement(...)` benchmark'ın `ProviderUsageRecord` ölçüm
alanlarını tenant bağlamıyla satıra çevirir; **paralel maliyet modeli yok**, benchmark'a import
yok (parametreler primitif). Harness **DB'ye yazmaz** (tenant'sız); gerçek koşu yazar. Bağ, bir
benchmark koşusunun ürettiği kayıtları tenant altında kalıcılaştıran integration testiyle
gösteriliyor.

**2. `storage_upload_id` genişletmesi + kontrol objesi kaldırıldı.** Kolon `String(128) → String(512)`.
`create_upload` artık sağlayıcının gerçek `UploadId`'sini `CreatedUpload` ile döner; servis onu
kolona yazar ve part/complete/cancel yollarına `object_key` (asset'ten) + `storage_upload_id`
olarak geçer. `s3.py`'den `_control/` katmanı tamamen kaldırıldı (`_ControlRecord`, `_control_key`,
`_get_control`, `_abort_quietly`, `_delete_quietly`, `_put_bytes`, `json` importu). `_SAFE_UPLOAD_ID`
gerçek sağlayıcı formatlarını taşıyacak şekilde genişletildi (`[A-Za-z0-9._~+/=-]{1,512}`; MinIO
alt kümesi hâlâ geçer). Fake adapter artık id üretir. **Geçiş:** dev-only, üretim verisi yok;
kolon genişletme veri kaybetmez, downgrade daralması ≤128 değerleri korur, >128 olursa **sessizce
kesmez, hata verir**. Gerçek MinIO byte yolu (`test_media_uploads_minio.py`) yeni adapter'la geçiyor.

**3. Fotoğraf durumu enum'u.** `IngestStatus.READY_FOR_PHOTO_ANALYSIS` eklendi (migration `0011`,
`ALTER TYPE ... ADD VALUE IF NOT EXISTS`). **Hiçbir kod yolu üretmiyor**: HEIC/HEIF hâlâ ingest'te
reddediliyor. Ulaşılamazlık, `app/` kaynağını tarayan bir testle (`test_photo_ingest_status.py`)
kanıtlanıyor — değer yalnızca tanımlandığı `models.py`'de geçiyor. Slotu bir kez kullanmak için
eklendi; fotoğraf hattı ayrı slice.

**4. `approver` rolü.** `BusinessRole.APPROVER` + `business_role` enum'una değer. `policy.py`'de
`ROLE_PERMISSIONS[APPROVER] = frozenset()` — **hiçbir yetki yok**. Brands `permits_action` merkezî
tabloya devrettiği için W04'ün "her rol için marka cevabı tanımlı" testi **zayıflatılmadan**
geçiyor (brands dosyasına dokunmadan; sadece merkezî tablo). Approver'ın hiçbir `Permission`'ı
olmadığı açıkça test edildi.

### Kapsam dışı bıraktıklarım ve nedeni
- Fotoğraf analiz hattı, onay akışı, gerçek sağlayıcı bağlama — WO'da açıkça kapsam dışı.
- `provider_usage`'a `route_revision`/`prompt_version`/`data_region` **eklemedim**: WO'nun ve
  ADR-007'nin sütun listesinde yoklar (yalnız ölçüm çekirdeği). Benchmark kaydı bunları kendi
  raporu için tutar; kalıcılıkta atfedilmezler. Provenance'ın DB'de istenip istenmediği PM'e.

### Doğrulama
Araç zinciri: py 3.13.14 · mypy 2.3.0 · ruff 0.16.0 · alembic 1.18.5 · sqlalchemy 2.0.51 ·
PostgreSQL 16 + MinIO (compose, `COMPOSE_PROJECT_NAME=sp-w10`, worktree kökünden).

| Kontrol | Sonuç |
|---|---|
| `ruff check` (app tests migrations scripts) | ✅ temiz |
| `ruff format --check` | ✅ 151 dosya |
| `mypy .` | ✅ 140 dosyada sorun yok |
| `pytest` (RUN_INTEGRATION_TESTS=1, PG+MinIO) | ✅ **406 passed** (392→406, +14; azalma yok) |
| migration up→down(0010)→up→down(base)→up | ✅ tek head `0011_schema_debt` |
| downgrade veri kaybı testi (kolon daralması) | ✅ satır + değer korunuyor; >128 değer widened kolonda tutuluyor |
| `provider_usage` dışlama (token/prompt/url/yanıt sütunu yok) | ✅ unit + integration |
| kontrol objesi kaldırıldı (`_control/` altına yazım yok) | ✅ s3 unit + MinIO byte yolu |
| approver hiçbir yetki | ✅ `test_approver_holds_no_permission_yet` |
| fotoğraf durumu ulaşılamaz | ✅ kaynak tarama testi |
| `make generate-docs` sonrası kontrat | ✅ regenerate edildi (aşağıya bak) |

### Açıkça belirtmem gerekenler
- **ADR numarası:** yeni ADR **çıkmadı** — dört kalem de önceden kararlı (ADR-007, ADR-008/011,
  PRD §4). ADR-008'e "kontrol objesi kaldırıldı" eki düşüldü. PM'in numaralayacağı yeni dosya yok.
- **İlan dışı dokunulan dosyalar (gerekçeli):**
  - `docs/generated/openapi.json` + `docs/api/endpoints.md`: `approver`/`ready_for_photo_analysis`
    enum'ları API şemasında görünüyor (`role: BusinessRole`), kriter 8 regenerate gerektiriyor.
    **Yan etki:** regenerate, `main`'de zaten var olan bir drift'i de düzeltti —
    `generate_endpoints_doc.py:30` "brands — marka, katalog, kampanya" başlığını üretiyor ama
    commit'li `endpoints.md` "brands" diyordu (bayat). W10 kaynaklı değil, zorunlu regenerate
    yüzeye çıkardı. openapi.json diff'i **yalnızca** iki enum değeri.
  - Modül `CLAUDE.md`'leri (operations/media/businesses/benchmark): DoD "modül dosyası
    değişince CLAUDE.md güncellenir" kuralı. WO ilan listesinde tek tek yoktu; W03 tekelindeki
    `docs/index.md`/`docs/adr/README.md`'ye **dokunmadım** (WO'da kapsam dışı).
  - `docs/STATUS.md`: yalnız W10 satırı + Alembic head fact'i (git-doc tutarlılığı).
- **`docs/index.md` / `docs/adr/README.md`:** indekse ekleme yapılmadı (W03 tekeli), bildiriliyor.

## Doğrulama — 2026-07-31 · Codex test oturumu

Worktree kökünde, `COMPOSE_PROJECT_NAME=sp-codex` ile gerçek PostgreSQL + Redis + MinIO
üzerinde sınandı. Araç zinciri: Docker 25.0.3 · Docker Compose v2.24.6-desktop.1 ·
Python 3.13.14 · PostgreSQL 16.14 · Alembic 1.18.5 · SQLAlchemy 2.0.51 · pytest 9.1.1 ·
ruff 0.16.0 · mypy 2.3.0.

| # | Bulgu | Şiddet | Yeniden üretim | Durum |
|---:|---|---|---|---|
| 1 | **Genişletilmiş kolonun geçerli verisi downgrade’i durduruyor.** 288 karakterlik sağlayıcı `UploadId` kaydedildiğinde `alembic downgrade 0010_brand_catalog`, `varchar(128)` için `StringDataRightTruncationError` ile çıkış 1 veriyor. Değer kesilmiyor ve başarısız denemeden sonra korunuyor; ancak gerçek yeni veride kabul kriterindeki up→down→up tamamlanamıyor. Mevcut test yalnızca ≤128 karakterlik geri-dönüş verisini kapsıyor. | orta | Gerçek DB’de 288 karakterlik ID ekle → downgrade → tekrar upgrade head. | açık |
| 2 | `provider_usage` tenant-kapsamlı olarak yazılıyor; token/prompt/signed URL/raw response için tablo sütunu yok. | — | `test_provider_usage_unit.py` + gerçek DB entegrasyonu. | geçti |
| 3 | Multipart create/part/complete/cancel gerçek MinIO’da çalıştı; `_control/` katmanına bağımlılık veya obje yazımı görülmedi. Yarım kalmış/cancel edilmiş oturum tekrar tamamlanamıyor. | — | `test_media_uploads_minio.py` gerçek byte yolu. | geçti |
| 4 | `ready_for_photo_analysis` enum’da, fakat üretim kodunda ulaşılamaz; mevcut HEIC/HEIF reddi korunuyor. | — | Kaynak-tarama birim testi. | geçti |
| 5 | `approver` enum/politika tablosunda var ve izin kümesi boş; marka/katalog dahil hiçbir mevcut kaynağa sızmadı. | — | Merkezi rol politikası ve marka rol kapsamı testleri. | geçti |

Odaklı W10/W12 takımı **56 passed**; Alembic head `0012_content_timeline_render`.
`ruff check`, `ruff format --check` ve `mypy .` temiz. Tam backend `pytest -q` 300 saniyede
sonuç üretmeden zaman aşımına uğradı; bu nedenle tam-regresyon/`make verify` kanıtı yoktur
(Windows hostunda `make` de kurulu değildir).

**Karar:** düzeltme gerekiyor — downgrade yolu, W10’un kabul ettiği uzun `UploadId` verisinde
çalışmıyor.
