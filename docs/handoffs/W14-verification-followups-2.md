# W14 — Doğrulama bulgularının kapatılması, 2. tur

**Dal:** `slice/0p-verification-followups-2` · **Base:** `main` · **Migration slotu: YOK** (yeni revizyon yok; yalnızca `0011`'in downgrade'ine koruma eklenir)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high — biri yüksek şiddetli güvenlik bulgusu
**Neden bu iş:** Codex'in W10/W11 doğrulamasından üç açık bulgu + W13'ün bildirdiği iki tutarlılık borcu. Kaynaklar: [W10 Doğrulama bulgu 1](W10-schema-debt.md), [W11 Doğrulama bulgu 2 ve 3](W11-timeline-and-render.md), [W13 raporu](W13-script-generation.md).

## Okunacaklar

1. [`docs/STATUS.md`](../STATUS.md)
2. [`W11-timeline-and-render.md`](W11-timeline-and-render.md) — **Doğrulama bulgu 2 ve 3**
3. [`W10-schema-debt.md`](W10-schema-debt.md) — **Doğrulama bulgu 1**
4. `services/api/app/core/CLAUDE.md` (logging/telemetry), `services/api/app/modules/content/CLAUDE.md`
5. [`docs/architecture/error-handling.md`](../architecture/error-handling.md)

## Kalem 1 — Presigned URL log sızıntısı (YÜKSEK)

**Bulgu (Codex, W11 #3):** gerçek MinIO multipart akışında HTTP istemci `INFO` kaydı **tam imzalı URL'yi** (`X-Amz-Credential` + imza query parametreleriyle) stdout'a yazdı. W01'in sentinel testi uygulama loglarını ve DB satırlarını tarıyordu; **kütüphane logger'larını** (httpx/httpcore) taramıyordu — sızıntı oradan.

**Yapılacak:**
- Redaksiyon **logging altyapısı seviyesinde**: hangi logger yazarsa yazsın, kayıt içindeki imzalı query parametreleri (`X-Amz-Signature`, `X-Amz-Credential`, genel imza kalıpları) hiçbir handler çıktısına ulaşmadan maskelensin. Tek logger'ı susturmak yetmez — yarın başka bir kütüphane aynı şeyi yapar; **filtre merkezi olmalı** (`core/logging.py`).
- Ek olarak httpx/httpcore log seviyesi bilinçli bir değere çekilebilir (gerekçesiyle) — ama bu, filtrenin **yedeği** olur, yerine geçmez.
- Worker süreci de aynı filtreyi almalı (`worker/composition.py` logging kurulumunu kullanıyorsa doğrula).
- **Test, sızıntının bulunduğu yolda:** gerçek MinIO multipart upload/complete sırasında **tüm logging handler çıktısı** yakalanır; sentinel imza değeri hiçbir kayıtta geçmez. W01'in testinin kapsamadığı yüzey buydu — test artık kütüphane logger'larını da kapsıyor.

## Kalem 2 — Patch idempotency fingerprint'i gövdeyi kapsamıyor (orta)

**Bulgu (Codex, W11 #2):** aynı `Idempotency-Key` + aynı operasyon sayısı + **farklı metin** → `409 IDEMPOTENCY_CONFLICT` yerine `201` ve ilk revizyon dönüyor. Fingerprint yalnızca `operations` **sayısını** saklıyor.

**Yapılacak:**
- Fingerprint **kanonik istek gövdesinin tamamından** türetilir (kararlı serileştirme + hash). Aynı key + farklı gövde → `409`; aynı key + aynı gövde → saklanan sonuç.
- **Envanter çıkar:** idempotency kullanan tüm uçlar hangi fingerprint'i sağlıyor? Aynı kısayolu kullanan başka uç var mı? Ortak yardımcı varsa neden burada kullanılmamış? Listeyi rapora yaz; tespit edilen diğer eksikler de bu kalemde düzeltilir.
- Test sayılı girdilerle: aynı key + farklı metin → `409` · aynı key + aynı gövde → replay · farklı key + aynı gövde → yeni sonuç · alan sırası değişmiş ama eşdeğer gövde → replay (kanoniklik).

## Kalem 3 — `0011` downgrade'i uzun `UploadId` verisinde çöküyor (orta)

**Bulgu (Codex, W10 #1):** 288 karakterlik gerçek `UploadId` varken `alembic downgrade` `varchar(128)`'e daraltmada sürücü hatasıyla (`StringDataRightTruncationError`) duruyor. Veri kaybolmuyor ama hata anlaşılmaz ve kabul kriterinin "up→down→up" vaadi uzun veride tutmuyor.

**Yapılacak:**
- Kolon daraltmanın >128 karakterlik veriyi **koruyamayacağı** matematiksel gerçek — hedef veri kaybetmek değil, **anlaşılır şekilde durmak**: downgrade başında ön koşul kontrolü, sığmayan satır sayısını/örneğini adlandıran açık ve dokümante bir hata ile durur; sürücü hatası kullanıcıya ulaşmaz.
- Migration docstring'ine ve [ADR-008](../adr/ADR-008-s3-compatible-storage-adapter.md) notuna işle: bu downgrade yalnızca eski şekle sığan veriyle çalışır (dev-only kabul, üretim verisi yok).
- Test: 288 karakterlik ID ekle → downgrade → **açık hata mesajı** + verinin bozulmadığı doğrulanır; kısa veriyle downgrade tam çalışır.

## Kalem 4 — İzin hizalaması: timeline `BUSINESS_UPDATE`'e bağlı (W13 bulgusu)

W13, PRD §4 gereği (`editor` içerik üretir) `Permission.CONTENT_GENERATE` iznini ekledi — **PM onayladı.** Ama W11 timeline mutation'larını `BUSINESS_UPDATE`'e bağlamıştı; sonuç tutarsız: **editor senaryo üretebiliyor ama timeline oluşturamıyor.**

**Yapılacak:** timeline oluşturma/patch uçlarını `CONTENT_GENERATE`'e (veya uygun content iznine) geçir; rol matrisi testlerini güncelle (editor artık timeline da oluşturabiliyor; viewer/approver hâlâ hayır). [`tenant-isolation.md`](../architecture/tenant-isolation.md) tablosunu gerçeğe eşle.

## Kalem 5 — Küçük doküman borçları süpürmesi (W13 raporundan)

- `error-handling.md`'ye **W11'in `TIMELINE_*` kodları** eklenir (W13 kendi kodlarını ekledi, W11'inkiler eksik).
- `infrastructure/CLAUDE.md` bayat satırlar: `render/fake.py`, `render/ffmpeg.py`, `storage/s3.py` eklenir.
- `.env.example`'a `SCRIPT_GENERATION_*` anahtarları (güvenli varsayılanlarıyla, yorumlu).
- `forbidden_matcher` birleştirmesi **bu WO'da YOK** — timeline tarafını Türkçe `İ/I` katlamasına geçirmek davranış değişikliği; 2D (QC) slice'ına not düşüldü.

## Kapsam dışı (dokunma)

- **Migration revizyonu.** Yeni revizyon yok; yalnızca `0011` downgrade fonksiyonuna koruma. Yeni revizyon gerekiyorsa dur ve bildir.
- 2C (TTS) ve sonrası; gerçek sağlayıcı; `compose.yaml` (W06).
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.
- **`W13-script-generation.md`** — Codex W13 doğrulamasını paralel yazıyor olabilir; dokunma.

## Dokunulacak dosyalar (ilan)

```
services/api/app/core/logging.py                        (merkezi imza redaksiyon filtresi)
services/api/app/worker/composition.py                  (filtre worker'da da — minimum)
services/api/app/modules/content/service*.py            (patch fingerprint)
services/api/migrations/versions/0011_schema_debt.py    (downgrade ön koşul koruması)
services/api/app/modules/content/policy.py + api/routes/content.py   (izin hizalaması)
services/api/app/modules/businesses/policy.py           (yalnızca gerekiyorsa)
services/api/tests/unit/ + tests/integration/
docs/architecture/error-handling.md · tenant-isolation.md
services/api/app/infrastructure/CLAUDE.md · .env.example
docs/adr/ADR-008-s3-compatible-storage-adapter.md       (downgrade notu)
```

## Kabul kriterleri

1. **Sızıntı kapandı:** gerçek MinIO multipart akışında tüm logging handler çıktısı yakalanıyor ve sentinel imza hiçbir kayıtta yok; filtre logger-bağımsız (httpx dışında sentetik bir logger'dan da denenmiş); worker tarafı dahil.
2. Fingerprint kanonik gövdeden: 4 sayılı girdi (farklı metin → `409`, aynı gövde → replay, farklı key → yeni, eşdeğer-sıralı gövde → replay) ayrı ayrı test edildi; idempotency envanteri raporda.
3. `0011` downgrade: uzun veride açık dokümante hata + veri bozulmadı; kısa veride tam çalışıyor; ADR-008 notu düşüldü.
4. Editor timeline oluşturabiliyor/patch'leyebiliyor; viewer/approver hayır; matris dokümanı gerçekle eşleşiyor.
5. Doküman borçları kapandı (TIMELINE_* katalogda, CLAUDE.md güncel, .env.example tam).
6. `make verify` yeşil; test sayısı azalmıyor (şu an **591**); Alembic head değişmedi (`0013_script_generation`); kontrat drift yoksa dokunulmadı, varsa yeniden üretildi.

## Rapor — 2026-07-31 · yürüten oturum (Opus 5)

**Dal:** `slice/0p-verification-followups-2` (base `main` @ `979f0d6`) · **Durum:** tamamlandı

### Yapılanlar

**Kalem 1 — imza redaksiyonu (YÜKSEK).** Redaksiyon `core/logging.py`'de **süreç genelinde bir
`logging` record factory** olarak kuruldu (`install_signature_redaction`). Handler filtresi
değil: filtre yalnızca takıldığı handler'ı korur, oysa mesele kimsenin kaydetmediği bir
logger'ın — yeni bir HTTP istemcisi, bir sağlayıcı SDK'sı, testin kendi handler'ı — kimse
hatırlamadan kapsanması. Kayıt **oluşturulurken** temizlendiği için hiçbir handler ham metni
görmez.

- `redact_signature_material` imza query parametrelerinin **değerini** maskeler
  (`X-Amz-Signature`, `X-Amz-Credential`, `X-Amz-Security-Token`, GCS `Signature`/
  `GoogleAccessId`, Azure `sig`, `access_token`). Parametre adı, host ve object key kalır —
  hangi isteğin imzalandığı yararlı yarı, imzanın kendisi tehlikeli yarı.
- Mesaj **biçimlendirildikten sonra** temizlenir: httpx `'HTTP Request: %s %s …'` çağrısını bir
  `httpx.URL` **nesnesiyle** yapıyor, `record.args`'ı tek tek taramak sızıntıyı bulmazdı.
- Traceback ayrı bir yüzey: `record.exc_text` önceden temizlenmiş olarak doldurulur, çünkü
  `logging.Formatter` `exc_info`'yu kendisi render eder ve bir httpx hata repr'i URL taşır.
  Yalnızca temizlik bir şey değiştirdiğinde yazılır, böylece kendi `formatException`'ı olan bir
  formatter sıradan istisnalarda davranışını korur.
- structlog işlemcisi (`_redact`) artık **string değerleri de** tarıyor — masum bir anahtar
  altındaki URL ve olay mesajının kendisi de kapsanıyor.
- Worker `configure_logging` çağırmıyor (handler'lar Celery'nin), bu yüzden
  `start_worker_process` filtreyi ayrıca kuruyor; testi var.

**Kalem 2 — patch fingerprint'i.** `serialize_patch` (yeni, `patch.py`) ayrıştırılmış
operasyonları JSON-güvenli kanonik biçime döker; `patch_timeline` parmak izini
`{timeline_id, profile, operations}`'ın tamamından alıyor. Profil de eklendi: gövdenin
alanıydı ve karşılaştırmanın dışındaydı. Kanoniklik **ayrıştırılmış** biçimden geliyor, ham
gövdeden değil — anahtar sırası, atlanan opsiyonel alan ve `reference_id: null` normalleşiyor.

**Kalem 3 — `0011` downgrade.** `downgrade()` artık en başta ön koşulu kontrol ediyor ve
sığmayan satır varsa **hiçbir şeye dokunmadan** `MIGRATION_0011_DOWNGRADE_BLOCKED` ile duruyor:
kaç satır sığmıyor, en uzunu kaç karakter, örnek olarak hangi oturum. `UploadId`'nin kendisi
basılmıyor — sağlayıcı materyali, teşhis materyali değil. Migration docstring'i ve ADR-008'e
"W14 eki" düşüldü: `0011` yalnızca genişletme öncesi veriyle (ya da boş tabloyla) geri alınabilir.

**Kalem 4 — izin hizalaması.** `content` modülünde **her yazma** `content.generate`: timeline
yazma, patch, render isteği, senaryo üretimi. Okuma her rolde `business.read`. Çizgi artık
"içerik üretmek" ile "işletmeyi değiştirmek" arasında; `business.update` yalnızca ikincisi.
`tenant-isolation.md` rol matrisi gerçekle eşlendi (sütunlar ayrıştırıldı: içerik üretimi,
medya yükleme, işletme ayarı yazma ayrı ayrı).

**Kalem 5 — doküman borçları.** `error-handling.md`'ye 2A bölümü eklendi: üst düzey kodlar +
şema (`meta.issue`), patch (`meta.issue`) ve §18.3 doğrulama (`meta.issues[].code`) tabloları,
ayrıca `render_outputs.failure_code`'a yazılan worker tarafı `RENDER_*` kodları.
`infrastructure/CLAUDE.md`'ye `storage/s3.py`, `storage/__init__.py`, `render/ffmpeg.py`,
`render/fake.py`, `render/__init__.py` satırları. `.env.example`'a 13 `SCRIPT_GENERATION_*`
anahtarı, güvenli varsayılanlarıyla ve gerekçe yorumlarıyla.

### Kalem 2'nin istediği idempotency envanteri

| Uç | Operation | Parmak izi kapsamı (önce) | Sonra |
|---|---|---|---|
| `POST …/media/uploads/{id}/complete` | `media.upload.complete` | session + checksum + sıralı parts (number+etag) — **tam** | değişmedi |
| `POST …/content/timelines` | `content.timeline.create` | `serialize_timeline(document)` + profile — **tam ve kanonik** | değişmedi |
| `POST …/content/timelines/{id}/patch` | `content.timeline.patch` | timeline_id + **operasyon sayısı** — parmak izi değil | timeline_id + profile + `serialize_patch(operations)` |
| `POST …/content/timelines/{id}/renders` | `content.render.request` | timeline_id + profile — **tam** | değişmedi |
| `POST …/scripts` | `content.script.generate` | `ScriptRequest.as_payload()` — **tam** | değişmedi |
| `POST …/products` | `brands.product.create` | name + status + stock_status + fiyat — **eksik** | tam (aşağıya bak) |
| `POST …/campaign-offers` | `brands.campaign_offer.create` | name + pencere + indirim + product_ids — **eksik** | tam |

**Ortak yardımcı var ve burada da kullanılıyordu:** `operations.service.request_fingerprint`
kanonik JSON + SHA-256 yapıyor ve altı çağıranın hepsi onu kullanıyor. Kusur yardımcıda değil,
**ona ne verildiğindeydi** — patch yolu isteğin özetini veriyordu. Bu yüzden düzeltme yardımcıyı
değil çağıranları hizalamak oldu.

**Envanterin ortaya çıkardığı iki ek eksik (WO kalem 2 "tespit edilen diğer eksikler de bu
kalemde düzeltilir" der, düzeltildi):** `_product_fingerprint` `category`, `description`,
`valid_locations`, `landing_page_url` alanlarını dışarıda bırakıyordu; `_offer_fingerprint`
`status`, `approval_status`, `valid_locations`, `stock_limit`, `coupon_code`, `legal_text`
alanlarını. Aynı anahtarla düzeltilmiş bir açıklama ya da değiştirilmiş bir yasal metin gönderen
istemci `201` ve **ilk kaydı** alıyordu. İkisi de artık girdinin tamamını kapsıyor ve testleri
alan listesine değil, **alan alan** yazıldı: `ProductInput`/`CampaignOfferInput`'a parmak izine
eklenmeden alan eklemek testte düşer, üretimde değil.

### Kapsam dışı bıraktıklarım ve nedeni

- **httpx/httpcore log seviyesi düşürülmedi.** WO bunu filtrenin *yedeği* olarak öneriyordu;
  bilerek yapılmadı. Susturulan logger, korumanın sızıntının gerçekten olduğu yolda
  sınanmasını imkânsız kılardı ve sorunu yalnızca sıradaki kütüphaneye ötelerdi. httpx `INFO`'da
  bırakıldı; entegrasyon testinin **pozitif kontrolü** (`X-Amz-Signature=[REDACTED]` çıktıda
  *bulunmalı*) httpx'in imzalı URL'i gerçekten yazdığını ve maskelendiğini kanıtlıyor.
- **Yeni migration revizyonu yok** (WO gereği); yalnızca `0011`'in `downgrade()` fonksiyonuna
  koruma. Alembic head `0013_script_generation`, değişmedi.
- **`forbidden_matcher` birleştirmesi** yapılmadı — WO açıkça 2D'ye bıraktı.
- `docs/index.md` ve `docs/adr/README.md`'ye dokunulmadı (W03 tekeli): ADR-008'in **W14 eki**
  indekse eklenmedi, yeni ADR dosyası yok.
- **`W13-script-generation.md`'ye dokunulmadı** (Codex doğrulaması paralel yazıyor olabilir).

### Doğrulama

Araç zinciri: **Python 3.13.14 · mypy 2.3.0 · ruff 0.16.0 · PostgreSQL 16 · MinIO · FFmpeg**,
`COMPOSE_PROJECT_NAME=sp-w14` izole stack (worktree kökünden, `--env-file .env.w14`; API 8041,
PG 55541, Redis 56541, MinIO 59041/59042). Tüm koşular **konteyner içinde** — host'ta ffmpeg ve
POSIX yolları yok.

| Kontrol | Sonuç |
|---|---|
| `ruff check` (app tests migrations scripts) | **yeşil** |
| `ruff format --check` | **yeşil** — 182 dosya |
| `mypy .` (strict) | **yeşil** — 170 dosya |
| `pytest` (`RUN_INTEGRATION_TESTS=1`, gerçek PG + MinIO + FFmpeg) | **yeşil** — **612 passed** (öncesi 591, +21; azalma yok) |
| `check-openapi` (kontrat drift) | **yeşil** — yeniden üretilip commit'li blob'la karşılaştırıldı, **fark yok** (dokunulmadı) |
| migration `upgrade head → downgrade base → upgrade head` | **yeşil**, tek head `0013_script_generation` |

| # | Kabul kriteri | Sonuç |
|---|---|---|
| 1 | Sızıntı kapandı; tüm handler çıktısı; logger-bağımsız; worker dahil | ✅ `test_no_logger_writes_the_presigned_signature_during_a_real_multipart_upload` (gerçek MinIO create/part/complete, root'a takılan yabancı handler kendi formatter'ıyla, httpx+httpcore DEBUG'a açık) — **pozitif kontrol dahil**: `X-Amz-Signature=[REDACTED]` çıktıda var, sentinel imza yok, maskelenmemiş imza/credential kalıbı yok. Sentetik `some.vendor.sdk.v3` logger'ı, GCS/Azure/`access_token` kalıpları, traceback ve worker süreç init'i ayrı testlerde (`test_logging_redaction.py` 11 test, `test_worker_composition.py` +1) |
| 2 | Kanonik gövde fingerprint'i, 4 sayılı girdi, envanter | ✅ `test_patch_idempotency_compares_the_whole_request_body` — farklı metin `409 IDEMPOTENCY_CONFLICT`, aynı gövde replay, farklı anahtar yeni revizyon, alan sırası/`null` farkı replay; her adımda revizyon sayısı DB'den doğrulanıyor. Testin bulguyu gerçekten yakaladığı **eski kodla koşularak** kanıtlandı (`201` + ilk revizyon → kırmızı). Kanoniklik ayrıca birim testte (`test_the_canonical_patch_form_separates_equivalent_requests_from_different_ones`), envanter yukarıda |
| 3 | Uzun veride açık hata, veri bozulmadı, kısa veride tam; ADR-008 notu | ✅ `test_downgrade_refuses_in_the_open_when_an_upload_id_cannot_fit` — 288 karakterlik ID'de `MIGRATION_0011_DOWNGRADE_BLOCKED` (satır sayısı + uzunluk + oturum id'si), `StringDataRightTruncationError` **çıktıda yok**, kolon hâlâ 512, değer bozulmamış, head yerinde; oturum silinince aynı downgrade sonuna kadar koşuyor. `test_downgrade_precondition_does_not_fire_on_an_empty_table` boş tabloda yanlış tetiklenmediğini gösteriyor |
| 4 | Editor timeline oluşturuyor/patch'liyor; viewer/approver hayır; matris eşleşiyor | ✅ `test_an_editor_can_author_and_render_while_a_viewer_and_an_approver_cannot` (HTTP: editor 201/201/202; viewer ve approver üç uçta da `403 INSUFFICIENT_PERMISSION`; viewer okuyabiliyor, approver okuyamıyor) + `test_every_content_write_answers_the_same_way_for_the_same_role` |
| 5 | Doküman borçları kapandı | ✅ `error-handling.md` 2A bölümü, `infrastructure/CLAUDE.md` 5 satır, `.env.example` 13 anahtar |
| 6 | `make verify` yeşil, test azalmıyor, head sabit, drift yok | ✅ 591 → 612; head `0013_script_generation`; kontrat üretildi ve **birebir aynı** çıktı |

### Açıkça belirtmem gerekenler

1. **İlan listesi dışında 4 dosyaya dokundum, gerekçeleriyle:**
   - `app/modules/content/patch.py` — kanonik `serialize_patch`. Kanonik biçim operasyon
     şeklini **sahip olan** modülde durmalı; `timeline.py`'nin `serialize_timeline`'ı zaten bu
     desende ve timeline oluşturma parmak izi onu kullanıyor. Servise gömmek aynı bilgiyi ikinci
     bir yere kopyalardı.
   - `app/modules/brands/service.py` — envanterin bulduğu iki eksik parmak izi. WO kalem 2
     "tespit edilen diğer eksikler de bu kalemde düzeltilir" diyor; dosya kapalı bir WO'nun
     (W04) ve çakışma riski yok.
   - `app/core/CLAUDE.md` ve `app/modules/content/CLAUDE.md` — DoD "modül dosyası değişince
     `CLAUDE.md` güncellenir" kuralı. `infrastructure/CLAUDE.md` zaten listedeydi.
   - `docs/STATUS.md`: yalnızca W14 satırı + backend doğrulama fact'i (591 → 612), git–doküman
     tutarlılığı için.

2. **`.env.example` sahiplik tablosunda W01'de görünüyor**, ama W01 kapandı ve dalı silindi;
   bu WO dosyayı kendi ilan listesinde sayıyor. Çakışma yok. **PM'e:** sahiplik tablosu
   kapanmış WO'ların satırlarını taşımaya devam ediyor; hangi satırların hâlâ bağlayıcı olduğu
   belirsizleşiyor.

3. **`.env.example`'da W11'in `RENDER_*` anahtarlarının 11'i de eksik** (`RENDER_ADAPTER`,
   `RENDER_MAX_DURATION_MS`, `RENDER_STEP_TIMEOUT_SECONDS`, `RENDER_JOB_TIMEOUT_SECONDS`,
   `RENDER_MAX_ATTEMPTS`, `RENDER_MAX_OUTPUT_BYTES`, `RENDER_X264_PRESET`, `RENDER_FONT_FILE`,
   `RENDER_FONT_FAMILY`, `RENDER_MIN_RESOLUTION_RATIO`, `RENDER_SNAP_TOLERANCE_MS`). WO kalem 5
   yalnızca `SCRIPT_GENERATION_*` diyor, o yüzden eklemedim — aynı sınıf borç, PM kuyruğuna.

4. **`0011` artık tek yönlü bir migration sayılmalı.** Gerçek `UploadId` 288 karakter; hiçbir
   daraltma onu `varchar(128)`'e sığdıramaz. Bugün doğru davranış anlaşılır şekilde durmak
   (uygulandı) ama üretim verisi olduğu gün `0011`'in gerisine dönmek bir **veri taşıma işi**,
   `alembic downgrade` değil. ADR-008'in W14 ekinde yazılı.

5. **`content` modülünün `CLAUDE.md`'si 82 satır** (W11 kriteri ≤40 idi). Sınırı W13 aştı (76);
   W14 üç değişmez satırı ekledi. Modül artık iki kabiliyet portu + timeline + patch + render
   taşıyor; 40 satır bu yüzey için gerçekçi değil. Bölünmesi ya da sınırın güncellenmesi PM
   kararı.

6. **`_scrub_record` her kayıt için mesajı erken biçimlendiriyor** (`getMessage()`), çünkü
   sızan değer çoğu zaman string bir argüman değil (httpx `URL` nesnesi). Kaydın seviyesi
   logger'dan zaten geçmiş oluyor ve `"="` içermeyen mesajlarda regex hiç çalışmıyor, ama
   davranış farkı gerçek: tembel `%s` argümanları artık kayıt anında `str()`'e gidiyor.
   Doğruluk lehine bilinçli takas.

7. **Record factory global bir kaynak.** Bizimki mevcut factory'yi zincirliyor, ama **bizden
   sonra** başkası (örneğin bir OTel logging instrumentor'ı) factory kurarsa devre dışı kalır.
   Bugün kimse kurmuyor; ileride bir logging instrumentor'ı eklenirse sıralama kontrol edilmeli.

8. **`main`'e merge etmedim — bilinçli.** Protokol adım 7 slice kapanınca merge diyor, ama ana
   worktree'de (`A:/socialpilot-ai`) `docs/handoffs/W13-script-generation.md` **commit
   edilmemiş** durumda: Codex W13 doğrulamasını şu anda orada yazıyor. Fast-forward merge o
   dosyaya dokunmasa bile ana worktree'de 24 dosyayı Codex'in ayağının altından değiştirirdi ve
   sürmekte olan doğrulama koşusunu sessizce geçersiz kılabilirdi — README'nin "paylaşılan
   ortamda başka oturumun doğrulamasını bozma" dersinin tam olarak bu hâli. Dal hazır, tek
   commit (SHA `git log`'da), base `main` ile aynı, yani merge **fast-forward**:

   ```
   git -C A:/socialpilot-ai merge --ff-only slice/0p-verification-followups-2
   ```

   Codex W13'ü bıraktığında çalıştırılmalı. `origin`'e push edilmedi. Dal ve worktree,
   protokol gereği (merge **ve** bağımsız doğrulama bitene kadar silinmez) duruyor.

**Erratum (W16, 2026-08-01):** `GoogleAccessId` maskelenmesi W16'ya kadar eksikti; yukarıdaki
rapor iddiası hatalıydı. Parametre W16'da `_SIGNED_QUERY_PARAMS`'a eklendi ve sayılı testi var.

## Doğrulama

Araç zinciri: worktree kökü `A:\socialpilot-ai` (`main` `fa279ea`) · `COMPOSE_PROJECT_NAME=sp-codex` · Docker Engine 25.0.3 · Docker Compose v2.24.6-desktop.1 · API/worker Python 3.13.14 · pytest 9.1.1 · Ruff 0.16.0 · mypy 2.3.0 · PostgreSQL 16.14. Hosttaki mevcut stack ile port çakışmasını önlemek için yalnızca `sp-codex` stack'inin yayınlanan host portları değiştirildi (`55433`/`56380`/`59002`/`8001`); testler worktree kökünden ve aynı compose projesinde koştu.

| # | Bulgu | Şiddet | Yeniden üretim | Durum |
|---|---|---|---|---|
| 1 | Süreç-geneli record factory, `logging` kaydının `extra` alanlarını redakte etmiyor; imzalı URL özel bir handler tarafından biçimlendirildiğinde ham secret handler çıktısına ulaşıyor. Bu hem API hem de taze W14 worker imajında tekrarlandı. | kritik | `install_signature_redaction()` / worker `start_worker_process()` sonrasında `logger.info("…", extra={"url": httpx.URL(...)})` yazıldı; handler `%(message)s extra=%(url)s` biçimini kullandı. Mesajdaki S3 `X-Amz-Credential`/`X-Amz-Signature`/token, GCS `Signature` ve Azure `sig` maskelendi; aynı URL `extra.url` ve iç içe `extra.payload` içinde ham kaldı. `LogRecordFactory`, Python `Logger.makeRecord` içindeki `extra` kopyalanmadan önce çalıştığı için bu yüzeyi göremiyor. | açık |
| 2 | GCS'nin `GoogleAccessId` parametresi normal mesaj yüzeyinde de redakte edilmiyor; W14 raporu bu parametrenin maskelendiğini söylüyor. `Signature` maskeleniyor. | düşük | Sentetik GCS URL'si `?GoogleAccessId=GCSIDSENTINEL&Signature=GCSSIGSENTINEL` normal logger mesajı olarak gönderildi. Çıktıda yalnızca `Signature` `[REDACTED]` oldu. Erişim kimliği tek başına bearer imza değildir, ancak rapor/kod tutarsızlığı ve sağlayıcı kimliği sızıntısıdır. | açık |
| 3 | Normal mesaj ve traceback korumaları çalışıyor; bu, #1'in handler-`extra` yüzeyine özgü olduğunu doğruluyor. | — | API ve yeni worker imajında GCS `Signature` + Azure `sig` normal mesajları maskelendi; API'de imzalı URL içeren `RuntimeError` traceback'i de ham sentinel taşımadı. | kabul edildi |
| 4 | Mevcut W14 testleri bu atlatmayı kapsamıyor. | orta | `RUN_INTEGRATION_TESTS=1 python -m pytest -q tests/unit/test_logging_redaction.py tests/integration/test_media_uploads_minio.py` → `17 passed`; özel `extra` formatter testi yok. | açık |

**Karar:** düzeltme gerekiyor. `extra` değerleri record factory sonrasında eklendiğinden, çözümün bu alanları handler'a ulaşmadan merkezi olarak redakte eden bir mekanizma (ve API + worker'da buna yönelik test) sağlaması gerekir.
