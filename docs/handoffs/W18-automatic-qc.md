# W18 — Phase 2D: Otomatik QC (§19.4)

**Dal:** `slice/2d-automatic-qc` · **Base:** `main` · **Migration slotu: SENDE** (`0015`)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Plan:** [Phase 2 planı](../plans/active/phase-2-content-generation.md) — slice 2D
**Neden bu iş:** 2A–2C üretimi kurdu; **güvenilirliği kurmadı.** Bugün render biten her çıktı, gerçekten açılıyor mu, sesi var mı, yazısı kadrajda mı, fiyatı kaynağa uyuyor mu bilinmeden `completed` sayılıyor. QC olmadan preview kullanıcıya gösterilemez — ve gösterilirse hatayı kullanıcı bulur.

## Okunacaklar

Router: [`docs/index.md`](../index.md). Asgari set:

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/plans/active/phase-2-content-generation.md`](../plans/active/phase-2-content-generation.md) — §2 kararlar (özellikle **üretimde fake AI genel kuralı**)
3. [`docs/product/requirements/40b-scenario-render-lifecycle.md`](../product/requirements/40b-scenario-render-lifecycle.md) — **§19.4 QC listesi**, §18.3 doğrulama, §19.2 profiller
4. `services/api/app/modules/content/CLAUDE.md` — değişmezler; `validation.py`, `render.py`, `render_service.py`, `tts.py` (sapma aritmetiği)
5. `services/api/app/infrastructure/render/CLAUDE.md` (varsa) — FFmpeg adapter'ının bugünkü şekli

## PM kararları (slice bunları yeniden tartışmaz)

### 1. QC **fail-closed**: ölçülemeyen kontrol "geçti" değildir

Bir kontrol çalıştırılamadıysa (adapter yok, ölçüm hata verdi, model kabiliyeti üretimde `disabled`), sonucu **`unknown`** olur ve genel karar en az **`needs_review`**'a düşer. Hiçbir kontrol sessizce atlanmaz, hiçbir `unknown` `passed` sayılmaz. Gerekçe: QC'nin tek işi güven üretmek; ölçmediğini onaylayan bir QC, QC'siz olmaktan **daha kötüdür** çünkü sahte güven verir.

### 2. Deterministik kontroller bu slice'ta; model kontrolleri port + fake

- **Bu slice inşa eder (FFmpeg/ffprobe/kod ile, gerçek ölçüm):** video açılıyor mu · süre profil hedefine uyuyor mu · ses akışı var mı · **loudness** (EBU R128, `ebur128`/`loudnorm` ölçümü) · siyah/boş frame oranı · sabit (donuk) görüntü · yazı kadraj/safe-area dışında mı (timeline geometrisi × render çözünürlüğü — deterministik) · seslendirme-süre senkronu (2C'nin `drift_ms`'i, eşik burada) · **fiyat/tarih kaynağa uyuyor mu** (render planındaki çözülmüş değerler ile `product_prices`/`campaign_offers` karşılaştırması).
- **Port olarak tanımlanır, fake adapter ile:** logo görünürlüğü · hassas/uygunsuz içerik · yüz bozulması · üretken sahnede ürün şekli. Bunlar VLM işi; gerçek sağlayıcı W08 benchmark'ı sonrası. **Üretimde `disabled` → sonuç `unknown` → `needs_review`** (kural 1 ile tutarlı; W13 kural onayı 1'in QC'deki karşılığı).

### 3. QC **karar verir, eylem yapmaz**

QC raporu genel kararı (`passed` / `needs_review` / `failed`) ve **önerilen yolu** (`retry_render` · `alternative_scene` · `alternative_provider` · `human_review` · `request_new_media`) deterministik bir tablodan üretir. **Yeniden render tetiklemez, sağlayıcı değiştirmez, döngü saymaz** — otomatik yeniden deneme, deneme sınırı ve yaşam döngüsü geçişleri **2E'nindir**. Gerekçe: eylemi karara aynı slice'ta bağlamak sınırsız render döngüsü riskini QC'nin içine gömer; sınır yaşam döngüsünün işidir.

### 4. Eşikler config'de ve gerekçeli

Her eşik (`QC_*`) `config.py`'de, PRD veya ölçüm gerekçesiyle. Marka/profil bazlı eşik **yok** (erken karmaşıklık). 2C'nin süre sapması eşiği burada ilk kez sayı olur: 2C ölçtü, 2D yargılar.

### 5. Devralınan borç: `forbidden_matcher` birleştirmesi

Timeline metin tarafındaki yasak terim eşleşmesi, W16/W17'nin `normalize_for_matching` + `contains_unsupported_letter` ikilisini **import eder**; ikinci bir katlama uygulaması yazılmaz. Senaryo tarafında kapatılan atlatmalar (görünmez karakter, confusable, aksan, çekim) timeline metninde açık kalmamalı — bu bir düzeltme değil, **aynı savunmanın ikinci kapısı**. Kapatılmış her sınıfın timeline tarafında da kapalı olduğunu gösteren test.

**Yasak terimlerde çekim eşleşmesi YAPILMAZ** (W17'nin sorusuna cevap): `şeker` yasakken `şekerli` serbest kalır. Gerekçe: liste **markanın**, kalıp bizim; kök eşleşmesi `az` yasakken `azalttık`ı da yakalar ve markanın kastetmediğini yasaklar. Ürün tarafı markaya "kökü değil, yasaklamak istediğin biçimleri yaz" der. Mevcut pin (`az` yasakken `lezzetli` serbest) korunur.

## Kapsam

1. **`render_qc_reports` (migration `0015`)** — render output referansı, kontrol başına sonuç ve ölçülen değer (JSONB), genel karar, önerilen yol, QC sürümü (eşik seti sürümlenir — dünkü raporun hangi eşiklerle üretildiği bilinmeden karşılaştırılamaz), varsa route/usage referansı.
2. **QC çalıştırma yolu** — render job'ı tamamlandıktan sonra, aynı dayanıklı job disiplininde (durum, timeout, deneme, correlation ID, dead-letter). Ölçüm worker'da; API katmanında FFmpeg yok.
3. **Rapor okuma ucu** — render output'un QC raporu okunabilir (roller: `business.read`); imzalı URL sızmaz.
4. Dokümantasyon: `content-render.md` QC bölümü, `error-handling.md` yeni kodlar, modül `CLAUDE.md`'leri, `.env.example`.

## Kapsam dışı (dokunma)

- **Otomatik yeniden render / alternatif sahne / sağlayıcı değişimi / deneme sınırı** → 2E.
- **Gerçek VLM sağlayıcısı** → W08 sonrası.
- **Render adapter'ına voiceover miksajı** → 2E (W15'in bıraktığı açık).
- **Senaryo tarafı dedektör** (`script.py`) — kapandı, dokunma; yalnızca import et.
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/modules/content/qc.py + qc_service.py        (yeni — kontrol tanımları, karar tablosu, eşikler)
services/api/app/modules/content/{models,repository}.py       (render_qc_reports)
services/api/app/modules/content/validation.py                (forbidden_matcher birleştirmesi)
services/api/app/infrastructure/render/*                      (deterministik ölçüm adapter'ı)
services/api/app/infrastructure/ai/fake_visual_qc.py + __init__.py
services/api/app/api/routes/content.py                        (rapor okuma ucu)
services/api/app/core/config.py                               (QC_* eşikleri)
services/api/migrations/versions/0015_*.py                    (SLOT SENDE)
services/api/tests/unit/ + tests/integration/
docs/architecture/content-render.md · error-handling.md · .env.example
```

## Kabul kriterleri

Sayılı girdiler + düşman gözü:

1. Migration `0015` up → down → up; tek head.
2. **Her deterministik kontrol gerçek bozuk medyayla test edilir** — FFmpeg ile üretilmiş fixture'lar: tamamen siyah video, sessiz video, aşırı sessiz/aşırı yüksek ses, donuk tek kare, hedeften sapan süre, bozuk konteyner. "Kontrol var" değil, **kontrol gerçekten yakalıyor**.
3. **Fail-closed üç yoldan da kanıtlanır:** ölçüm hata verirse `unknown` → `needs_review` · model kabiliyeti üretimde `disabled` ise `unknown` → `needs_review` · hiçbir kontrol sonucu eksik bırakılamaz (rapor kontrol kümesinin tamamını taşır, testle sabitlenir).
4. Fiyat/tarih uyum kontrolü: render planındaki çözülmüş değer kayıttaki değerden **farklıysa** yakalanır (kampanya bitmiş/fiyat değişmiş senaryosu gerçek DB ile).
5. Karar tablosu saf ve testli: aynı kontrol sonucu kümesi → aynı karar + aynı öneri; hiçbir kombinasyon tanımsız değil.
6. **`forbidden_matcher` birleştirmesi:** senaryo tarafında kapatılan atlatma sınıflarının (görünmez karakter, confusable, Latin katlama, çekim, süslü rakam) timeline metninde de kapalı olduğunu gösteren test; ikinci bir katlama uygulaması yok (import zorlanır).
7. QC yolu dayanıklı job disiplinine uyuyor; imzalı URL hiçbir log/rapor/span'e sızmıyor (sentinel testi).
8. Roller + idempotency: rapor okuma `business.read`; başka tenant'ın raporu `404`.
9. `make verify` yeşil; test sayısı **947** tabanının altına düşmez; kontrat yeniden üretilip commit'li; modül `CLAUDE.md`'leri güncel.
10. Rapor + araç zinciri sürümleri. **Merge etme, dalda bırak.**

## Enumerasyon kuralı (bu hattın dört turluk dersi)

Kalıp, liste veya küme yazan her yerde sor: **bu bir enumerasyon mu?** Elle sayılmış her küme bu projede bir sonraki doğrulama turunda delindi (confusable tablosu → Coptic; görünmez listesi → atanmamış kod noktası; çekim listesi → `lirayla`). Tutan çözümlerin hepsi kategori kuralı, üretilmiş veri veya fail-closed sınır oldu. QC eşik ve kontrol kümesi de bu soruya tabidir.

## ADR numara kuralı

Gerçek karar çıkarsa `ADR-XXX-<konu>.md`; numarayı PM verir. (QC'nin fail-closed duruşu ADR'lık olabilir — gerekçeni yaz, numarayı isteme.)

## Rapor — 2026-08-02 · Opus 5 / high

**Dal:** `slice/2d-automatic-qc` (base `855bd7a`) · **Durum:** tamamlandı, **merge edilmedi**

### Yapılanlar

- **`modules/content/qc.py` — QC contract'ı.** `QcCheck` = §19.4'ün 13 satırı, aynı sırayla;
  `CHECK_POLICIES` her üyeyi kapsar; `build_results` kontrol kümesinin tamamıyla `unknown`
  başlar ve çağıranın cevaplarıyla üzerine yazılır — **bir kontrolü atlamak ifade edilemiyor**;
  `decide` saf, total ve eksik kümeyi `QC_REPORT_INCOMPLETE` ile reddediyor. Deterministik
  değerlendiriciler, `QcThresholds` anlık görüntüsü, `MediaQcProbePort`, `VisualQcPort`,
  `audit_verified_sources` burada.
- **`migrations/0015_render_qc_reports.py`** — kontroller ve eşik anlık görüntüsü JSONB;
  `verdict`/`recommended_path` `pending` satırında bile NOT NULL ve karamsar
  (`needs_review`/`human_review`), ayrıca `status <> 'pending' OR verdict <> 'passed'` check
  constraint'i aynı kuralı şemada tekrar ediyor. `render_id` üstünde unique **yok**, yalnızca
  `pending` koşular üzerinde kısmi unique index.
- **`infrastructure/render/qc_probe.py`** — gerçek ölçüm: ffprobe konteyner okuması,
  `blackdetect` + `freezedetect`, `ebur128` integrated loudness, sınırlı frame örneklemesi.
  Ölçümler stderr'den **okunmuyor**: `metadata=mode=print:file=` ile kendi yazdığımız özel
  dosyaya gidiyor, böylece "stderr'in yalnızca boyutu denetlenir" kuralı bozulmadan kalıyor.
  **Fake'i yok** (`create_qc_probe`), gerekçe `create_audio_probe` ile aynı.
- **`infrastructure/ai/fake_visual_qc.py`** — `VisualQcPort` fake + disabled adapter'ı,
  `_reject_production` kapısıyla. Üretimde `disabled`, dolayısıyla dört model kontrolü `unknown`
  ve **hiçbir render otomatik `passed` olmuyor**.
- **`modules/content/qc_service.py`** — dayanıklı QC job'ı (claim, timeout, deneme, correlation,
  dead-letter, iki transaction) + yalnızca-okuma `ContentQcReportService`. Yapıcıda render/AI
  üretim portu **yok**: yeniden render 2E'nin.
- **`validation.py` `forbidden_matcher` birleştirmesi** — `script.forbidden_matcher` +
  `normalize_for_matching` import ediliyor, `contains_unsupported_letter` ile
  `TIMELINE_UNSUPPORTED_CHARACTER` eklendi. `re.IGNORECASE` eşleyicisi kaldırıldı. Çekim
  eşleşmesi yok (PM kararı korunuyor). Ayrıca `layout_text_in_frame` ve `resolve_overlay_text`
  public oldu — QC ölçülen kareyle aynı fonksiyonu çağırıyor, ikinci bir uygulama yok.
- **Rapor okuma ucu:** `GET /v1/businesses/{id}/content/renders/{render_id}/qc`
  (`business.read`, mevcut `RENDER_READ` aksiyonu üzerinden — `policy.py`'ye dokunulmadı).
- Dokümantasyon: `content-render.md` QC bölümü + kontrol tablosu, `error-handling.md` QC kod
  kataloğu, `.env.example` (`VISUAL_QC_*` + `QC_*`), modül `CLAUDE.md`'leri, yeniden üretilmiş
  OpenAPI kontratı (35 → 36 endpoint).

### Kapsam dışı bıraktıklarım ve nedeni

- **Celery bağlantısı yapılmadı — tek gerçek eksik.** `content.qc.drain` task'ı
  (`worker/tasks.py`), fabrika satırı (`worker/composition.py`) ve beat girdisi
  (`infrastructure/celery_app.py`) **iş emrinin dosya listesinde yok**. Protokol "listede olmayan
  dosyaya dokunma, dur ve raporla" diyor; iş emrinin kapsam maddesi ise "ölçüm worker'da"
  diyor — **iki kaynak çelişiyor** ve AGENTS.md gereği sessizce birini seçmek yerine bildiriyorum.
  Servis bu yüzden **kendi kendine kuyruğa giriyor**: `process_next()` QC raporu olmayan
  `succeeded` render'ı tarayıp job'ı kendisi açıyor, yani bağlantı gerçekten üç ek satır.
  Gereken yama aşağıda, "Açıkça belirtmem gerekenler"de.
- Gerçek VLM sağlayıcısı (W08 sonrası), otomatik yeniden render / alternatif sahne / deneme
  sınırı (2E), render adapter'ına voiceover miksajı (2E), `script.py` — hepsi iş emrinde kapsam
  dışı.
- `docs/index.md` ve `docs/adr/README.md`'ye ekleme yapılmadı (iş emri gereği). **ADR yazılmadı:**
  fail-closed duruşu gerçek bir karar ama iş emrinin verdiği kararın uygulanması; gerekçe
  `qc.py`'nin modül docstring'inde ve `content-render.md`'de. PM ADR'lık görürse numarayı verir.
- `docs/plans/active/` altına ayrı bir plan dosyası açılmadı: bu slice'ın planı iş emrinin
  kendisi ve faz planının 2D satırı; dosya listesinde `docs/plans/` yok.

### Doğrulama

Araç zinciri: Python 3.13.14 · mypy 2.3.0 · ruff 0.16.0 · pytest 9.1.1 · FFmpeg 7.1.5.
Tamamı `COMPOSE_PROJECT_NAME=sp-w18` ile izole compose projesinde (host portları 8022/55463/
56389/59040/59041 ile ayrıldı — 55432 ve 59000 başka worktree'lerde doluydu).

| Kontrol | Sonuç |
|---|---|
| `ruff check` | ✅ temiz |
| `ruff format --check` | ✅ 200 dosya biçimli |
| `mypy .` (strict) | ✅ 188 dosya, hata yok |
| `pytest` (gerçek PostgreSQL + MinIO + FFmpeg, `RUN_INTEGRATION_TESTS=1`) | ✅ **1071 passed** (taban 947, +124) |
| migration `0015` up → down → base → up | ✅ tek head (`0015_render_qc_reports`) |
| OpenAPI yeniden üretildi | ✅ 35 → 36 endpoint, commit'li |

| Kabul kriteri | Sonuç |
|---|---|
| 1 · migration up/down/up, tek head | ✅ |
| 2 · her deterministik kontrol **gerçek bozuk medyayla** | ✅ `test_qc_probe.py` (13) + `test_content_qc.py` (19): tamamen siyah, sessiz, donuk kare, sesi olmayan, hedeften sapan süre, bozuk konteyner — hepsi FFmpeg ile üretiliyor, hiçbiri stub değil |
| 3 · fail-closed üç yoldan | ✅ (a) probe yok → 13 kontrolün ölçüme bağlı olanları `unknown`, koşu `failed`, karar `needs_review`; (b) VLM `disabled` → 4 model kontrolü `unknown`, kusursuz çıktı yine `needs_review`; (c) rapor daima 13 kontrolü taşıyor (unit + integration) |
| 4 · fiyat/tarih uyumu gerçek DB ile | ✅ fiyat satırı render'dan sonra açıldı → `QC_VERIFIED_VALUE_SUPERSEDED`; kampanya bitti → `QC_VERIFIED_VALUE_OUT_OF_WINDOW`; değişmemiş fiyat temiz |
| 5 · karar tablosu saf ve testli | ✅ tüm tek ve **çift** kontrol permütasyonları tüketiliyor + tohumlanmış 3.000 rastgele atama; her atama tanımlı, tekrarlanabilir ve `passed` yalnızca her şey `passed` iken |
| 6 · `forbidden_matcher` birleştirmesi | ✅ görünmez karakter, atanmamış kod noktası, NFD, büyük/küçük, aksan düşmesi, fazla aksan → hepsi yakalanıyor; Coptic `Ⲧ` → `TIMELINE_UNSUPPORTED_CHARACTER`; ikinci uygulama yok (tokenize + AST + `script.forbidden_matcher`'ı monkeypatch edip davranışın değiştiğini gösteren test) |
| 7 · dayanıklı job disiplini + imzalı URL sızmıyor | ✅ job tipi/kaynağı/timeout/deneme/correlation/dead-letter testli; rapor gövdesinde `X-Amz-Signature`, `X-Amz-Credential`, `https://` ve object key sentinel'leri yok |
| 8 · roller + izolasyon | ✅ `business.read`; başka tenant'ın raporu **404** (iki yönde) |
| 9 · `make verify` yeşil, ≥947 test, kontrat commit'li, `CLAUDE.md` güncel | ✅ 1071 test |
| 10 · rapor + araç zinciri sürümleri, merge yok | ✅ dalda bırakıldı |

### Açıkça belirtmem gerekenler

1. **PM'e bırakılan tek iş — Celery bağlantısı (3 dosya, iş emrinin listesi dışında).**
   Gereken tam yama:
   - `worker/composition.py`: `WorkerContext`'e `qc_probe` + `visual_qc` alanları,
     `build_worker_context`'te `create_qc_probe(settings)` / `create_visual_qc(settings)`, ve
     `content_qc_service(session) -> ContentQcService(session, settings, materializer, qc_probe, visual_qc)`.
   - `worker/tasks.py`: `@celery_app.task(name="content.qc.drain")` → `_drain(context, context.content_qc_service, needs_workdir=True)`.
   - `infrastructure/celery_app.py`: `beat_schedule`'a `"drain-content-qc"` girdisi.
   Bunlar olmadan servis, testleri ve raporu tam ama beat tetiklemesi bağlı değil.
2. **Üretimde hiçbir render otomatik `passed` olmayacak** ve bu bilinçli. VLM adapter'ı üretimde
   `disabled` → dört model kontrolü `unknown` → kural 1 gereği `needs_review`. Gerçek sağlayıcı
   W08 sonrası bağlanana kadar QC "insan baksın" diyor. Ürün tarafında bunun anlatılması gerek.
3. **Bilinen ölçüm sınırı: `approved_ctas` değişiklik damgası taşımıyor.** Yerinde düzenlenmiş bir
   CTA metni QC tarafından görülemiyor; yalnızca kaydın kaybolması yakalanıyor. Kayıtta
   `changed_at=None` olarak açıkça duruyor. Kapatmak `brands` şemasına `updated_at` eklemek
   demek — o modül bu iş emrinin dışında.
4. **`TIMELINE_UNSUPPORTED_CHARACTER` yeni bir reddetme sınıfı.** Latin dışı alfabeden harf
   taşıyan overlay metni artık reddediliyor — senaryo tarafındaki kuralın timeline karşılığı.
   Fail-closed: en kötü ihtimalle meşru bir ad reddedilir ve ortak katlama tablosu bir satır büyür.
   PRD §30 hata kataloğu (`90b-api-error-contracts.md`, W03 tekelinde) hâlâ bu kodları taşımıyor;
   W11'den beri süren PM kuyruğu.
5. **Loudness penceresi platform gerçeği değil, ürün varsayılanımız.**
   `99-external-platform-facts.md`'de yayınlanmış bir Instagram loudness sözleşmesi yok, bu yüzden
   `-14 ±3 LUFS` config'de ve gerekçesi hem `config.py`'de hem `.env.example`'da yazılı. Bir
   sağlayıcıdan doğrulanırsa fact dosyasına girmeli.
6. **`prompt_templates` TRUNCATE tuzağı.** QC entegrasyon testi ilk yazımında bu tabloyu da
   temizliyordu ve `0013`'ün seed'ettiği aktif prompt sürümünü silerek 79 senaryo/seslendirme
   testini düşürdü. Tablo artık listede değil ve nedeni yorumda. Yeni entegrasyon testi yazan
   oturumlar için: `prompt_templates` platform konfigürasyonudur, tenant verisi değil.
7. **Test dosyası adı çakışması** (`tests/unit/test_content_qc.py` ↔
   `tests/integration/test_content_qc.py`) pytest'in toplama hatasına yol açtı; unit dosyası
   depo geleneğine uyularak `test_content_qc_unit.py` oldu.

## Doğrulama

_(test eden oturum: bozuk medyayı QC'ye "geçti" dedirtmeye çalış — ölçüm hatasını sessiz geçirme, eksik kontrol kümesi, karar tablosunda tanımsız kombinasyon, timeline metninde senaryo tarafında kapalı bir atlatmanın açık kalması)_
