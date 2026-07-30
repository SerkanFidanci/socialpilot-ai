# W12 — Doğrulama bulgularının kapatılması

**Dal:** `slice/0n-verification-followups` · **Base:** `main` · **Migration slotu: YOK** (ikisi de migration gerektirmiyor)
**Durum:** tamamlandı (2026-07-30) — rapor aşağıda, merge ve bağımsız doğrulama bekliyor
**Model/effort:** Opus 5 / high — küçük ama ikisi de değişmez (invariant) tarafında
**Neden bu iş:** Bağımsız doğrulamanın (Codex) bıraktığı **iki açık bulgu.** İkisi de "çalışmıyor" değil, "yanlış nedenle çalışıyor" sınıfında — yani sessiz kalırsa ileride pahalı yerde patlar. Kaynak: [W04 raporu, Doğrulama bulgu 2](W04-brand-catalog.md) ve [W05 raporu, Doğrulama bulgu 3](W05-opentelemetry.md).

## Okunacaklar

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/handoffs/W04-brand-catalog.md`](W04-brand-catalog.md) — **Doğrulama bölümü, bulgu 2**
3. [`docs/handoffs/W05-opentelemetry.md`](W05-opentelemetry.md) — **Doğrulama bölümü, bulgu 3**
4. `services/api/app/modules/brands/CLAUDE.md`, `services/api/app/core/CLAUDE.md`
5. [`docs/architecture/observability.md`](../architecture/observability.md) — mevcut correlation/trace bağı
6. [`docs/product/requirements/85-orchestration-events.md`](../product/requirements/85-orchestration-events.md) — **§26.4 olay zarfı standardı**

## Kalem 1 — Parasal alanlarda katı tamsayı

**Bulgu:** `price_minor: 165.0` `201` ile kabul edilip `165`'e çevriliyor; `165.5` ise `400`. Aynısı `discount_amount_minor: 500.0` için. Yani kesirli float reddediliyor, integral float sessizce coerce ediliyor.

**Neden önemli:** para kaybı yok, ama hata modu aralıklı ve teşhisi zor. Bir istemci `fiyat * 100`'ü float'ta hesaplarsa çoğu değer `16500.0` (geçer), bazıları `16499.999999999998` (400 alır). İstemci "çalışıyor" görünür, sonra rastgele kırılır. Ayrıca "parasal alanda float yok" bu projenin sert kuralı ([W04 kabul kriteri 4](W04-brand-catalog.md)) ve reklam bütçesi katmanı (Phase 5) aynı tuzağa hazır.

**Yapılacak:**

- **Tek bir yeniden kullanılabilir katı parasal tip** tanımla (ör. `core/`'da bir `MinorUnits` annotated tipi): JSON `int` kabul eder, JSON `float`'u — integral olsa bile — **reddeder**; negatif ve üst sınır kuralları tek yerde.
- Mevcut **tüm** parasal alanları bu tipe geçir. Bul, tahmin etme: `*_minor` ile biten her alan + para taşıyan diğer alanlar. Bulduğun listeyi rapora yaz.
- **Testin şeklini düzelt.** Mevcut test yalnızca kesirli float'u deniyordu ve açığı gizledi. Yeni test **integral float** (`165.0`), kesirli float (`165.5`), string sayı (`"165"`), `true`, `null` ve çok büyük değeri ayrı ayrı denemeli. Bu, bulgunun tekrar etmemesinin tek garantisi.
- Hata kodu mevcut doğrulama kontratına uymalı; yeni bir kod gerekiyorsa [`error-handling.md`](../architecture/error-handling.md)'ye ekle.
- Kontrat değişikliği: bu bir **sıkılaştırma**. Daha önce kabul edilen `165.0` artık `400` alacak. Mobil istemcinin bu alanları float göndermediğini **doğrula** (`apps/mobile` içinde ilgili yerleri kontrol et) ve sonucu rapora yaz. Gönderiyorsa dur ve bildir — istemci düzeltmesi ayrı slice olur.

## Kalem 2 — Trace zincirinin worker'a taşınması

**Bulgu:** `X-Correlation-ID` server span'ine bağlanıyor ve response header'ı korunuyor, ama API → outbox → Beat → worker arasında `traceparent` saklanmadığı için trace zinciri kopuyor. Sonuç: API trace'leri ve worker trace'leri **iki ayrı ada**.

**Gerekçe düzeltmesi:** W05 bunu "migration gerektiriyor" diyerek takip işine bıraktı ve Codex de öyle kabul etti. **Migration gerekmiyor:** olay zarfı JSON (`payload_json`) ve §26.4 standardı zaten `correlation_id` taşıyor — `traceparent` de zarfın içinde taşınabilir. Yeni kolon yok.

**Yapılacak:**

- W3C `traceparent` (ve varsa `tracestate`) değerini olay **zarfına** yaz; §26.4'ün alan listesine ekle ve dokümana işle.
- Worker tarafında zarftan okunan bağlamı **span üst bağlamı** olarak kur: API'de başlayan isteğin tetiklediği iş aynı trace'te görünsün.
- **Telemetri kapalıyken hiçbir şey değişmemeli:** zarfa `traceparent` yazmak telemetri kapalıyken de zararsız olmalı (ya boş, ya hiç). W05'in "kapalıyken sıfır maliyet" garantisi bozulamaz — bunu test et.
- **Redaksiyon garantisi korunur:** `traceparent` bir kimlik, sır değil; ama zarfa başka hiçbir telemetri verisi (attribute, prompt, URL) yazılmaz.
- Zarftaki `traceparent` **doğrulanmadan** span bağlamına konmaz: bozuk/kötü niyetli değer trace'i kirletmemeli, geçersizse yeni trace başlar. Test var.
- `observability.md`'yi güncelle: artık zincir nerede devam ediyor, nerede kopuyor.

## Kapsam dışı (dokunma)

- **Migration.** İkisi de gerektirmiyor. Gerekiyorsa **dur** ve rapora yaz — slot W10/W11'de.
- Yeni metrik, yeni span, collector/dashboard — W05 kapsamı kapandı.
- Mobil istemci düzeltmesi (kalem 1'in son maddesi çıkarsa ayrı slice).
- Reklam bütçesi alanları — henüz yok; katı tip onların da kullanacağı şekilde yazılır, ama alan eklenmez.
- `compose.yaml` → W06. `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.
- `services/api/app/modules/content/**` ve `migrations/0012*` → **W11'in.** Çakışırsa dur ve bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/core/money.py                    (yeni — katı MinorUnits tipi; ya da uygun mevcut core dosyası, gerekçesini yaz)
services/api/app/api/routes/brands.py             (parasal alanların tipi)
services/api/app/modules/brands/domain.py         (gerekiyorsa)
services/api/app/core/telemetry.py               (zarf bağlamı enjeksiyon/çıkarma)
services/api/app/modules/operations/service.py    (outbox zarfına traceparent)
services/api/app/worker/tasks.py                  (zarftan bağlam kurma)
services/api/tests/unit/ + tests/integration/
docs/architecture/observability.md
docs/product/requirements/85-orchestration-events.md   (§26.4 zarf alanına traceparent notu)
docs/architecture/error-handling.md               (yeni kod gerekiyorsa)
```

## Kabul kriterleri

1. **Integral float reddediliyor:** `price_minor: 165.0` ve `discount_amount_minor: 500.0` artık `400` alıyor; `165` (int) kabul ediliyor. Test integral float, kesirli float, string, bool ve aşırı büyük değeri **ayrı ayrı** deniyor.
2. Katı parasal tip **tek yerde** tanımlı ve tüm mevcut parasal alanlar onu kullanıyor; bulunan alan listesi raporda.
3. Mobil istemcinin bu alanlara float gönderip göndermediği **kontrol edilip** rapora yazıldı.
4. **Trace zinciri devam ediyor:** telemetri açıkken, API isteğinin tetiklediği worker işi **aynı trace ID** altında görünüyor; bunu gösteren bir test var.
5. **Telemetri kapalıyken hiçbir davranış değişmiyor:** span/metric/thread yok, zarf yazımı zararsız; W05'in sıfır-maliyet testi hâlâ geçiyor.
6. Bozuk/kötü niyetli `traceparent` değeri span bağlamına konmuyor; geçersizse yeni trace başlıyor (test var).
7. Zarfa `traceparent` dışında telemetri verisi yazılmıyor; presigned URL/token sızıntısı testleri hâlâ geçiyor.
8. §26.4 zarf standardı ve `observability.md` gerçeği anlatıyor.
9. `make verify` yeşil; test sayısı azalmıyor (şu an 392); Alembic head değişmemiş; kontrat drift'i varsa yeniden üretilip commit'lenmiş.

## ADR numara kuralı

Gerçek bir karar çıkarsa `ADR-XXX-<konu>.md` yaz, raporda bildir; **numarayı PM verir.**

## Rapor — 2026-07-30 · W12 yürütücü oturum (Opus 5)

**Dal:** `slice/0n-verification-followups` (base `main` = `c554184`) · **Commit'ler:** tek slice
commit'i · **Durum:** tamamlandı

### Kalem 1 — katı parasal tamsayı

- **`app/core/money.py` (yeni).** Üç şey: `MAX_MINOR_UNITS` (10¹²), Pydantic annotated tipi
  `MinorUnits = Annotated[int, Field(strict=True, ge=0, le=MAX_MINOR_UNITS)]` ve Pydantic
  dışındaki yarısı `is_minor_units()`. Negatif ve üst sınır artık **tek yerde**;
  `brands/domain.py`'daki `MAX_PRICE_MINOR` bu sabitin katalog tarafındaki adı,
  `normalize_price_minor` ve `Money.__post_init__` aynı yüklemi çağırıyor.
  Neden `core/`: kural sözleşme sınırında geçerli ve reklam bütçesi katmanı (Phase 5) aynı
  tipi kullanacak; `brands` içinde kalsaydı ikinci bir tanım doğardı.
- **Strict'in anlamı:** JSON `integer` kabul edilir; `165.0` (integral float), `165.5`, `"165"`,
  `true` ve `null` **reddedilir**. Yani bu sıkılaştırma sayısal string'i de kapsıyor — WO'nun
  "JSON int kabul eder" ifadesinin doğrudan sonucu, bilinçli.
- **Hata kodu değişmedi.** Ret şema doğrulamasında oluyor → `400 REQUEST_VALIDATION_FAILED`.
  Yeni kod gerekmedi, `error-handling.md` **değişmedi**.

**Bulunan parasal alanların tam listesi** (`*_minor` ile biten her alan + para taşıyan diğerleri):

| Yer | Alan | Durum |
|---|---|---|
| `api/routes/brands.py` `PricePayload` | `price_minor` | → `MinorUnits` |
| `api/routes/brands.py` `ProductResponse` | `price_minor` | → `MinorUnits \| None` |
| `api/routes/brands.py` `CampaignOfferRequest` | `discount_amount_minor` | → `MinorUnits \| None` |
| `api/routes/brands.py` `CampaignOfferResponse` | `discount_amount_minor` | → `MinorUnits \| None` |
| `modules/brands/models.py` | `product_prices.price_minor`, `campaign_offers.discount_amount_minor` | zaten `BigInteger`, değişmedi |
| `modules/brands/domain.py` | `Money.amount_minor`, `normalize_price_minor`, `MAX_PRICE_MINOR` | `core/money.py`'a bağlandı |
| `modules/brands/service.py` | `PriceInput`/`CampaignOfferInput`/… dataclass alanları | tip zaten `int`; JSON sınırı route'tur, orada kapatıldı |
| `app/benchmark/**` | `unit_cost_minor`, `cap_minor`, `spent_minor`, `estimated_cost_minor`, `actual_cost_minor`, `total_cost_minor` | **JSON sınırı yok** — sabit kodlu birim maliyetler + `argparse type=int` olan `--cost-cap-minor`; golden set JSON'u para taşımıyor. Float coercion yolu yok, dokunulmadı |

**Kapsam dışı bıraktığım bitişik alan:** `discount_percent` (`int \| None`, lax) integral
float'ı hâlâ coerce ediyor (`20.0` → `20`). Para taşımıyor ve `10.0` semantik olarak zararsız,
bu yüzden WO'nun "parasal alan" kapsamına almadım. **PM kararı:** aynı katılık yüzdeye de
istenirse tek satır (`Annotated[int, Field(strict=True, ge=0, le=100)]`).

**Testin şekli düzeltildi.** Eski test yalnızca kesirli float'ı deniyor, açığı gizliyordu.

- `tests/unit/test_money.py` (yeni): tip her JSON şeklini **ayrı satırda** reddediyor
  (`validate_python` + `validate_json` ikisi de), `is_minor_units` aynı kuralı veriyor ve
  **keşif testi** `app/api/routes/**` içindeki her Pydantic modelinin `*_minor` biten her
  alanını otomatik bulup float reddettiğini doğruluyor — yeni bir parasal alan eklendiğinde
  test onu kendiliğinden kapsıyor, listeye eklemeyi unutmak mümkün değil.
- `tests/integration/test_brand_catalog.py::test_monetary_fields_refuse_every_non_integer_json_shape`:
  gerçek PostgreSQL üzerinde `165.0`, `165.5`, `"165"`, `true`, `null`, `10¹³`, `-1` — her biri
  ayrı istek, hem `price_minor` hem `discount_amount_minor` için; `16500` hâlâ `201` ve satır
  integer olarak yazılıyor.

**Mobil istemci kontrolü (kabul kriteri 3): temiz, istemci düzeltmesi gerekmiyor.**
`apps/mobile` yalnızca `/v1/businesses` ve `/v1/businesses/{id}/media/*` uçlarını çağırıyor
(`business_repository.dart`, `media_repository.dart`); marka/katalog/kampanya ucu **hiç
kullanılmıyor** ve kaynakta parasal alan yok. `grep -riE "brand|product|campaign|discount|
currency|_minor|price"` tek eşleşme veriyor, o da bir yorumdaki "production cadence".

### Kalem 2 — trace zincirinin worker'a taşınması

- **`core/telemetry.py`:** `current_trace_carrier()` (mevcut span'i W3C alanlarına yazar; kayıt
  yapan span yoksa `{}`), `trace_carrier_from_envelope()` (doğrulama) ve `continue_trace()`
  (bağlamı ekleyip çıkaran context manager). **Global propagator değil, `TraceContextTextMap
  Propagator` doğrudan** kullanılıyor — global propagator baggage de taşır, zarfa yalnızca trace
  bağlamı girmeli.
- **`modules/operations/service.py`:** `event_envelope(**fields)` zarfı kurar ve taşıyıcıyı
  ekler; dört outbox yazımının (`media.ingest`, `technical_analysis`, `scene_speech`,
  `video_understanding`) tamamı bunu kullanıyor. Telemetri kapalıyken payload **birebir eskisi**.
- **`OutboxDispatchService.dispatch_one(publish_scope=...)`:** yeni bir **port**. Domain zarfı
  saklar ve iletir, **yorumlamaz**; kapsamı worker veriyor (`worker/tasks.py` →
  `continue_trace`). Publish o bağlamın içinde olduğu için Celery instrumentation'ı drain
  task'ının mesajına aynı trace'i enjekte ediyor.
- **Zincirin tamamı:** API isteği → outbox zarfı → Beat → dispatch (bağlam geri kuruluyor) →
  drain task'ı **aynı trace'te** → o drain kendi ardıl olayını yazarken taşıyıcıyı yeniden
  damgalıyor, yani ingest → teknik analiz → sahne/konuşma → video understanding hattı tek
  trace'te kalıyor. **Migration yok** (zarf `payload_json`).
- **Güvenlik/dayanıklılık:** doğrulama W3C §3.2.2'ye göre (yanlış versiyon `ff`, sıfır trace/span
  id, büyük harf hex, kısaltılmış, string olmayan → **reddedilir**, yeni trace başlar);
  `tracestate` yalnızca 512 karaktere kadar taşınır; zarfa `traceparent`/`tracestate` dışında
  hiçbir telemetri verisi konmuyor.

**W05'in "domain telemetri bilmez" değişmezi — daraltıldı, gevşetilmedi.** Eski test
`app/modules/**` içinde "telemetry" **kelimesini** arıyordu. `operations/service.py` artık tek
bir core erişimcisini (`current_trace_carrier`) çağırdığı için o testi yerine daha kesin bir
testle değiştirdim (`test_domain_modules_read_the_carrier_but_never_instrument`): domain'de
`opentelemetry` importu, tracer/meter alma, span başlatma, metrik oluşturma, `set_attribute`
**yasak**; `app.core.telemetry`'ye izin verilen **tek satır** `from app.core.telemetry import
current_trace_carrier`, başka her satır bulgu. Gerekçe: `traceparent`, zarfın zaten taşıdığı
`correlation_id` ile aynı kategoride bir **bağlam kimliği**; korunması gereken şey domain'in
enstrümante edilmemesi, ki test artık tam olarak onu ölçüyor. Alternatifi — taşıyıcıyı 5 çağrı
noktasından parametre olarak geçirmek — sahibi olmadığım `modules/media/**` dosyalarına
dokunmayı gerektiriyordu.

### Kapsam dışı bıraktıklarım ve nedeni

- **Migration yok.** İkisi de gerektirmedi; Alembic head `0010_brand_catalog`, tek head, değişmedi.
- **Yeni span/metrik yok** (WO kapsam dışı). `continue_trace` span **başlatmıyor**, yalnızca
  mevcut bağlamı ekliyor; görünen span'ler zaten var olan Celery/httpx/SQLAlchemy span'leri.
- `compose.yaml`, `.env.example`, `docs/index.md`, `docs/adr/README.md` — dokunulmadı.
- `modules/content/**`, `migrations/0012*` — W11'in, dokunulmadı.
- ADR yazmadım: yeni bir mimari karar çıkmadı. W05/ADR-014'ün "takip işi" notu artık
  `observability.md`'de kapatıldı; ADR-014 metnindeki aynı notu **değiştirmedim** (dosya listemde
  yok) — **PM'e bırakıyorum**, aşağıda.

### Doğrulama

Worktree kökünde, `COMPOSE_PROJECT_NAME=sp-w12` ile ayrık stack (host portları
55462/56409/59030/59031/8030). Araç zinciri konteynerde: **Docker 25.0.3 · Docker Compose
v2.24.6-desktop.1 · Python 3.13.14 · pydantic 2.13.4 · pytest 9.1.1 · ruff 0.16.0 · mypy 2.3.0**.

| Kontrol | Sonuç |
|---|---|
| `ruff check` (app tests migrations scripts) | ✅ temiz |
| `ruff format --check` | ✅ 148 dosya temiz |
| `mypy .` (strict) | ✅ 137 dosya, hata yok |
| `pytest` (`RUN_INTEGRATION_TESTS=1`, `STORAGE_ADAPTER=s3`, gerçek PostgreSQL + MinIO) | ✅ **412 passed** (öncesi 392; +20) |
| OpenAPI + `endpoints.md` drift | ✅ yeniden üretildi, ikinci üretim birebir aynı |
| Alembic head | ✅ `0010_brand_catalog` tek head, değişmedi |
| Kabul kriteri 1–9 | ✅ |

Kabul kriteri karşılıkları: (1) `165.0` ve `500.0` artık `400`, `16500` `201`; altı şekil ayrı
ayrı test edilmiş · (2) tek tip `core/money.py`, alan listesi yukarıda, keşif testi bağlıyor ·
(3) mobil kontrol edildi, temiz · (4) `test_worker_continues_the_trace_of_the_request_that_wrote_
the_event` gerçek PostgreSQL'de: API server span'inin trace id'si zarfta, publisher **aynı trace
id** içinde çağrılıyor · (5) `test_telemetry_disabled_writes_the_same_envelope_it_always_wrote`
zarfın `{job_id, asset_id}` olarak kaldığını, W05'in `test_setup_is_noop_without_endpoint` /
`test_app_runs_with_telemetry_disabled` testlerinin hâlâ geçtiğini gösteriyor · (6) sekiz bozuk/
kötü niyetli `traceparent` parametrik testte, hepsi yeni trace başlatıyor · (7) zarf anahtar
kümesi `{job_id, asset_id, traceparent}` olarak **birebir** doğrulanıyor, baggage testi ve W05'in
sentinel presigned URL/token testleri geçiyor · (8) `85-orchestration-events.md` §26.4 notu +
`observability.md` yeniden yazıldı, zincirin **nerede durduğu** da yazılı · (9) yukarıdaki tablo.

### Açıkça belirtmem gerekenler (PM'e)

1. **`main`'de duran bir kontrat drift'ini kapattım.** `generate_endpoints_doc.py`'ye
   `TAG_TITLES["brands"]` W05 merge'ünde (`5addf69`) eklenmiş ama `endpoints.md` yeniden
   üretilmemiş — yani `main` üzerinde `make check-openapi` zaten kırmızıydı. Benim diff'imdeki
   `## brands` → `## brands — marka, katalog, kampanya` satırı bu; W12'nin ürettiği bir değişiklik
   değil.
2. **Sahibi olmadığım dosyalar — DURDUM.** Modül `CLAUDE.md`'leri (`docs/index.md`/`adr/README.md`
   gibi W03 tekelinde) dosya listemde yok; W05 de aynı yerde durmuştu ve **o iş hâlâ yapılmamış**.
   Gereken satırlar:
   - `app/core/CLAUDE.md`: dosya tablosuna `telemetry.py` (W05'ten **eksik**), `pagination.py`
     (W04'ten **eksik**) ve `money.py` (bu WO) satırları; değişmezlere "parasal alan `MinorUnits`
     tipindedir, JSON float reddedilir" ve "log'a trace/span id eklenir".
   - `app/modules/operations/CLAUDE.md`: değişmezlere "outbox zarfı `traceparent` taşır
     (§26.4); domain onu saklar, yorumlamaz" ve `dispatch_one`'ın `publish_scope` portu.
   - `app/worker/CLAUDE.md`: "outbox dispatch, zarftaki trace bağlamını geri kurar" + W05'ten
     kalan "composition worker'da telemetri kurar" notu.
   - `app/modules/brands/CLAUDE.md`: para değişmezi satırına "sınır `core/money.py`'da" eklemesi.
3. **`ADR-014` metni artık güncel değil.** İçindeki "traceparent dayanıklı satırda taşınmalı →
   migration gerekiyor, takip işi" notu bu WO ile kapandı (migration gerekmedi).
   `observability.md` doğruyu anlatıyor; ADR dosyası dosya listemde olmadığı için dokunmadım.
4. **Sıkılaştırmanın kapsamı:** `165.0` gibi integral float'ın yanı sıra `"165"` gibi sayısal
   string de artık `400`. Üretilmiş istemci yoksa etkisi yok (mobil bu uçları çağırmıyor), ama
   kontrat değişikliği olarak kayda geçsin.
5. **`discount_percent` bilinçli olarak kapsam dışı** — gerekçe yukarıda; PM isterse tek satır.

## Doğrulama — 2026-07-31 · Codex test oturumu

Worktree kökünde, `COMPOSE_PROJECT_NAME=sp-codex` ile gerçek PostgreSQL + MinIO üzerinde
sınandı. Araç zinciri: Docker 25.0.3 · Docker Compose v2.24.6-desktop.1 · Python 3.13.14 ·
Pydantic 2.13.4 · PostgreSQL 16.14 · pytest 9.1.1 · ruff 0.16.0 · mypy 2.3.0.

| # | Bulgu | Şiddet | Yeniden üretim | Durum |
|---:|---|---|---|---|
| 1 | Katı `MinorUnits` sınırı hem API keşif testinde hem gerçek marka/kampanya uçlarında integral float, kesirli float, string, bool, null, negatif ve üst-sınır aşımını reddediyor; JSON int kabul ediliyor. Başka bir para alanı kaçmadı. | — | `test_money.py` ve gerçek PostgreSQL entegrasyonu. | geçti |
| 2 | Telemetri açıkken outbox zarfındaki W3C taşıyıcı API `/complete` server span’inin trace ID’sini dispatch publisher’a koruyarak taşıdı. Zarf yalnız `job_id`, `asset_id`, `traceparent` alanlarını içerdi; baggage/signed URL/token taşınmadı. | — | `test_worker_continues_the_trace_of_the_request_that_wrote_the_event`. | geçti |
| 3 | Bozuk/kötü niyetli traceparent varyantları (sıfır ID, yanlış sürüm, uppercase, kesik ve string-dışı dahil) bağlama alınmadı; yeni trace başlatıldı. | — | 8-varyant parametrik telemetry saldırı testi. | geçti |
| 4 | Telemetri kapalıyken zarf eski iki alanlı biçiminde kaldı; exporter/span/metric yan etkisi oluşmadı. | — | Gerçek outbox entegrasyonu ve no-op telemetry testleri. | geçti |

Odaklı W10/W12 takımı **56 passed**; `ruff check`, `ruff format --check` ve `mypy .` temiz.
Tam backend `pytest -q` 300 saniyede sonuç üretmeden zaman aşımına uğradı; bu nedenle
tam-regresyon/`make verify` kanıtı yoktur (Windows hostunda `make` de kurulu değildir).

**Karar:** hedef kabul kriterleri için teslim edilebilir. Not: W11 doğrulamasında saptanan
HTTP istemci presigned-URL log sızıntısı, W12 zarf/spans sınırının dışında kalan ortak log
yüzeyidir ve W11’de açık bulgu olarak kaydedilmiştir.
