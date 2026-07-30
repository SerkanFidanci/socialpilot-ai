# W05 — OpenTelemetry: trace + metric

**Dal:** `slice/0i-telemetry` · **Base:** `main` · **Migration slotu:** yok · **W04 ile paralel** (dosya-ayrık)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 4.8 / medium
**Neden bu iş:** PRD §37 OpenTelemetry'yi yığının parçası sayıyor, `AGENTS.md`'nin tamamlanma tanımı "metrics where appropriate" diyor — ama depoda `opentelemetry` kelimesi **hiç geçmiyor.** Var olan tek gözlemlenebilirlik structlog JSON log'u. Şu an 4 modül, 121 dosya ve 313 test varken eklemek, 20 modül varken eklemekten kat kat ucuz. Ayrıca tek sunucu kararı (K5, ADR-013) sonrası "hangi iş CPU'yu yedi" sorusunun cevabı log'dan çıkmıyor.

## Okunacaklar

Router: [`docs/index.md`](../index.md) → "Mimari değişiklik" satırı. Asgari set:

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/product/requirements/95-observability.md`](../product/requirements/95-observability.md) — **PRD §37'nin tamamı**: log alanları, metrik listesi, trace zinciri, alert eşikleri
3. `services/api/app/core/CLAUDE.md` — core teknik kalır, domain servisine dönüşmez
4. `services/api/app/core/logging.py` ve `app/core/correlation.py` — mevcut correlation ID mekanizması
5. [`docs/architecture/error-handling.md`](../architecture/error-handling.md) — korelasyon ve redaksiyon kuralları
6. [`docs/adr/ADR-013-single-server-deployment-topology.md`](../adr/ADR-013-single-server-deployment-topology.md) — kaynak bütçesi; telemetri bu bütçeyi yemeyecek

## Kapsam

### 1. Varsayılan KAPALI, açıkken tam

- OTLP exporter **yalnızca** `OTEL_EXPORTER_OTLP_ENDPOINT` (veya eşdeğer ayar) verildiğinde etkin. Verilmediğinde telemetri **no-op**: span/metric üretilmez, arka planda thread/kuyruk açılmaz, ölçülebilir ek yük olmaz.
- Bu bir kolaylık değil zorunluluk: tek sunucuda (ADR-013) idle maliyeti sıfır olmalı ve CI credential'sız yeşil kalmalı.
- Ayarlar tipli `Settings`'e girer (`app/core/config.py` — bu WO'nun sahipliğinde).

### 2. Trace

- Auto-instrumentation: **FastAPI, SQLAlchemy, httpx, redis, Celery.** PRD §37.3'ün zinciri hedef: `mobile request → API → DB → queue → worker → provider → storage → render`.
- **Correlation ID ↔ trace bağı kurulmalı.** Mevcut `X-Correlation-ID` mekanizması korunur; trace/span kimliği log kayıtlarına eklenir, böylece bir log satırından trace'e, trace'ten log'a gidilebilir. Bu bağ olmadan iki ayrı gözlemlenebilirlik adası oluşur.
- Worker tarafında trace bağlamı **iş mesajıyla taşınır** (Celery instrumentation): API'de başlayan bir isteğin tetiklediği job aynı trace'te görünmeli. Görünmüyorsa neden görünmediğini rapora yaz — sessizce kopuk bırakma.

### 3. Metric

PRD §37.2'nin listesinden bu slice'ta anlamlı olanlar: API latency ve hata oranı · kuyruk derinliği · job süresi · provider latency ve maliyet · upload başarısızlığı · publish başarısı (henüz yok, sonraya) · guardrail tetiklenmesi (henüz yok). **Var olmayan bir şey için metrik uydurma**; kurulan altyapı sonraki modüllerin metrik eklemesini tek satıra indirmeli.

Job süresi ve kuyruk derinliği için mevcut `jobs`/`job_attempts` kayıtları kaynak olabilir; ölçüm noktasını rapora yaz.

### 4. Redaksiyon (en kritik kısım)

Span attribute'ları ve metric label'ları **log'dan daha sızdırıcıdır** çünkü otomatik toplanır. Kesin yasaklar:

- Token, credential, secret → hiçbir span'de.
- **İmzalı object-storage URL'i** → hiçbir span'de. Auto-instrumentation httpx isteklerinin URL'ini varsayılan olarak yazar; presigned URL'ler bu yoldan sızabilir. **Bunu engelleyen açık bir filtre ve onu doğrulayan bir test zorunlu** — W01'in sentinel testi deseni burada da uygulanabilir.
- Ham prompt / ham provider yanıtı / medyadan çıkarılmış metin → hiçbir span'de.
- Metric label'larında yüksek kardinaliteli değer yok (asset ID, iş ID label olarak kullanılmaz; span attribute'u olabilir).
- `user_id` gerekiyorsa maskeli; `business_id` serbest.

### 5. Dokümantasyon

`docs/architecture/observability.md` (yeni): ne toplanıyor, ne toplanmıyor **ve neden**, açma/kapama, redaksiyon garantileri, bir sonraki modülün metrik eklemek için izleyeceği adım. PRD §37 gereksinim, bu doküman uygulama gerçeği.

## Kapsam dışı (dokunma)

- **Collector servisi, Grafana/Prometheus/Loki kurulumu, dashboard, alert.** Exporter bir uca konuşur; ucu ayağa kaldırmak ayrı iş. `compose.yaml`'a servis **ekleme** (dosya W06'ya gidiyor).
- **Sentry.** Ayrı adapter, ayrı slice.
- **Migration.** Şema değişikliği gerekiyorsa dur ve rapora yaz.
- **`app/modules/**` altındaki domain kodu.** Instrumentation core ve infrastructure'dan yapılır; domain servisine telemetri çağrısı gömülmez. Zorunlu bir istisna çıkarsa dur ve rapora yaz.
- **`app/api/routes/__init__.py`** → W04'ün (router kaydı). Sen `main.py`'ın sahibisin.
- `compose.yaml` → W06. `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/core/telemetry.py          (yeni — kurulum, kapatma, redaksiyon filtreleri)
services/api/app/main.py                    (SENİN sahipliğinde — instrumentation kancası)
services/api/app/core/config.py             (SENİN sahipliğinde — telemetri ayarları)
services/api/app/core/logging.py            (trace/span kimliğini log bağlamına ekleme)
services/api/app/worker/composition.py      (worker sürecinde telemetri kurulumu — minimum dokunuş)
services/api/pyproject.toml + uv.lock       (SENİN sahipliğinde — OTel bağımlılıkları)
services/api/tests/unit/                    (kapalıyken no-op, redaksiyon, correlation↔trace bağı)
docs/architecture/observability.md          (yeni)
docs/adr/ADR-014-<telemetri-temeli>.md      (yeni)
```

## Kabul kriterleri

1. **Endpoint verilmediğinde no-op:** telemetri kapalıyken span/metric üretilmiyor, ek thread/kuyruk açılmıyor; test var. `make verify` credential ve collector olmadan yeşil.
2. Endpoint verildiğinde FastAPI, SQLAlchemy, httpx, redis ve Celery instrumentation'ı etkin; bir istek için uçtan uca trace üretiliyor.
3. **Correlation ↔ trace bağı çalışıyor:** log kaydında trace/span kimliği var, `X-Correlation-ID` davranışı bozulmamış; test var.
4. API'de başlayan isteğin tetiklediği worker işi aynı trace'te görünüyor — ya da görünmüyorsa nedeni rapora yazılmış.
5. En az şu metrikler üretiliyor: API latency, API hata oranı, job süresi, kuyruk derinliği. Ölçüm noktaları rapora yazılmış.
6. **Redaksiyon testi:** sentinel içeren bir presigned URL ile istek yapıldığında sentinel hiçbir span attribute'unda, metric label'ında veya exporter payload'ında bulunmuyor. Token/secret için aynısı.
7. Metric label'larında yüksek kardinaliteli değer yok; bunu gösteren bir kontrol var.
8. Domain modüllerine (`app/modules/**`) telemetri çağrısı gömülmemiş (`git diff` ile kanıtla).
9. `app/api/routes/__init__.py` **değişmemiş** (W04'ün dosyası).
10. `make verify` yeşil, 313 test geçiyor (sayı artabilir, azalamaz), Alembic head değişmemiş.
11. `observability.md` yazıldı; "ne toplanmıyor ve neden" bölümü dahil.
12. Bağımlılık ekleme uv üzerinden: `uv.lock` güncellendi ve commit'lendi; sürümler **kurulum anında** doğrulandı.

## ADR numara kuralı

Numarayı **sen seçmiyorsun.** `ADR-014-<konu>.md` adıyla yaz, başlıkta da `ADR-014` bırak, raporda bildir. PM merge sırasında numaralandırır.

## Rapor — 2026-07-30 · W05 yürütücü oturum (Opus 4.8)

**Dal:** `slice/0i-telemetry` (base: `374c02b` = `main` `7b9fd35` + W04/W05 WO doküman commit'i)
**Commit'ler:** _(bu commit)_ · **Durum:** tamamlandı

### Yapılanlar

- **`app/core/telemetry.py` (yeni)** — tüm OTel kurulumu, kapatma ve redaksiyon tek modülde.
  Varsayılan KAPALI: `OTEL_EXPORTER_OTLP_ENDPOINT` boşken kurulum fonksiyonları `None` döner —
  exporter kurulmaz, `BatchSpanProcessor`/`PeriodicExportingMetricReader` thread'i başlamaz,
  global provider kurulmaz, OTel API no-op'ta kalır.
- **Trace auto-instrumentation:** FastAPI (`main.py`), SQLAlchemy (async motorun `sync_engine`'i,
  lifespan + worker init'te), httpx, redis (her iki süreç), Celery (worker, fork sonrası).
  Uçtan uca `mobile→API→DB→queue→worker→provider→storage` zinciri hedefleniyor.
- **Correlation ↔ trace:** `logging.py`'a `add_trace_context` processor'ı eklendi — her log
  satırına `trace_id`/`span_id` (hex) düşüyor, mevcut `correlation_id` ve `X-Correlation-ID`
  davranışı bozulmadı. Ters yön: correlation id server span'ine attribute olarak konuyor.
- **Metric:** `http.server.duration` (API latency + `http.status_code` label'ı → hata oranı),
  `http.client.request.duration` (sağlayıcı/depo istemci latency'si), `job.duration` (Celery
  `task_prerun/postrun` sinyalleriyle, label = task adı + durum), `queue.depth` (broker `LLEN`
  observable gauge). Ölçüm noktaları: job süresi worker composition'daki sinyal bağlantısı,
  kuyruk derinliği gauge callback'i — ikisi de domain'e dokunmadan `telemetry.py`'da.
- **Redaksiyon (iki katman):** (1) httpx kancaları span kaydederken URL'in query/fragment/
  userinfo'sunu düşürür → imzalı URL span'de bir an durmaz; (2) `_RedactingSpanExporter`
  export yolundaki garanti ağı — secret-adlı attribute `[REDACTED]`, URL-değerli attribute
  `scheme://host/path`; temizleyemediği span'i gönder**mez, düşürür.** Sentinel imzalı presigned
  URL + token testleriyle kanıtlandı (W01 deseni).
- **Dokümantasyon:** `docs/architecture/observability.md` (yeni) — ne toplanıyor, **ne
  toplanmıyor ve neden**, açma/kapama, redaksiyon garantileri, sonraki modülün metrik ekleme
  adımı. `docs/adr/ADR-014-opentelemetry-observability-foundation.md` (yeni).
- **Bağımlılıklar (uv):** `opentelemetry-{api,sdk}`, `-exporter-otlp-proto-http` (grpc değil —
  `grpcio`'yu imaja sokmamak için), `-instrumentation-{fastapi,sqlalchemy,httpx,redis,celery}`.
  `uv add` ile eklendi, sürümler kurulum anında PyPI'dan doğrulandı, `uv.lock` güncellendi.
  Lock farkı **yalnızca ek** — mevcut hiçbir paketin sürümü değişmedi (redis 8.1.0 zaten
  lock'taydı, sqlalchemy/fastapi aynı).

### Kapsam dışı bıraktıklarım ve nedeni

- **API→worker aynı trace (kabul kriteri 4): bilinçli olarak KURULMADI, nedeni raporlandı.**
  İşler transactional outbox + Celery Beat-zamanlı drain ile seçiliyor (ADR-005; worker
  invariant "payload ID'sine güvenilmez"). Doğrudan enqueue kenarı yok → taşınacak trace bağlamı
  da yok. Celery instrumentation beat→drain→DB zincirini bağlar ama trace beat tick'inde başlar.
  Tam zincir için `traceparent`'ın dayanıklı iş satırında taşınması gerekir → **şema değişikliği
  (migration), bu slice'ın kapsamı dışı** (migration slotu yok). ADR ve observability.md'de takip
  işi olarak kayıtlı.
- **Collector / Grafana / dashboard / alert:** kapsam dışı (WO). Exporter bir uca konuşur.
- **`compose.yaml` OTLP servisi/değişkenleri (W06), `.env.example` OTEL_* (W01):** sahibi ben
  değilim; değişkenler `observability.md`'de belgelendi, ilgili WO'lara bırakıldı.

### Doğrulama

Araç zinciri (Docker `sp-w05` konteyneri): **python 3.13.14 · mypy 2.3.0 · ruff 0.16.0**.

| Kontrol | Sonuç |
|---|---|
| `ruff check` + `ruff format --check` (app tests migrations scripts) | ✅ temiz |
| `mypy .` (strict) | ✅ 123 dosya, hata yok |
| `pytest` (RUN_INTEGRATION_TESTS=1, STORAGE_ADAPTER=s3, gerçek PostgreSQL+MinIO) | ✅ **327 passed** (313 + 14 yeni telemetri testi) |
| `check-openapi` (generate + git diff) | ✅ drift yok (route eklenmedi) |
| Alembic head | ✅ `0009_video_understanding` tek head, değişmedi |
| Kapalıyken no-op | ✅ endpoint yoksa handle `None`, thread/exporter yok, uygulama çalışır |
| Redaksiyon (sentinel presigned URL + token) | ✅ span attribute / exporter payload'da yok |
| Yüksek kardinalite label kontrolü | ✅ asset/job/upload/correlation/user id label değil |
| Domain'de telemetri yok | ✅ `app/modules/**` .py taraması + git diff temiz |
| `app/api/routes/__init__.py` | ✅ değişmedi (W04'ün dosyası) |

Yeni testler: `tests/unit/test_telemetry.py` (14) — kapalı no-op, redact_url/attribute,
`_RedactingSpanExporter` sentinel, httpx kancası, add_trace_context, enabled server span +
correlation binding, API latency/error metrikleri, job.duration + queue.depth, düşük kardinalite,
domain temizliği.

### Açıkça belirtmem gerekenler (PM'e)

1. **ADR numarası bende değil.** `ADR-014-opentelemetry-observability-foundation.md` adıyla
   yazıldı; başlıkta `ADR-014`. Ayrıca `ADR-014` yer tutucusu şu üç yerde geçiyor, merge'de
   numaralandırılmalı: `pyproject.toml` OTel yorum bloğu, `config.py` OTel ayar yorumu,
   `observability.md` başındaki ADR linki. `docs/adr/README.md` ve `docs/index.md`'ye **eklemedim**
   (W03 tekeli / benim listemde değil).
2. **Modül `CLAUDE.md` güncellemesi bekliyor (sahibi W03, benim dosya listemde yok → dokunmadım).**
   Gerekli: `app/core/CLAUDE.md`'ye yeni `telemetry.py` satırı + "log'a trace/span id eklenir"
   invariantı; `app/worker/CLAUDE.md`'ye composition'ın artık worker'da telemetri kurduğu notu.
   Protokol gereği durup PM'e bıraktım.
3. **`.env.example`'a OTEL_* eklenmeli (sahibi W01).** Dört ayar `observability.md`'de belgeli.
4. **Redis observable-gauge broker okuması best-effort.** Bugün yalnız `default` kuyruğu route
   ediliyor (§38.2 per-queue split gelmedi); gauge o listeyi okur. Split gelince callback kuyruk
   kümesine genişler — hâlâ sınırlı label.
5. **Redaksiyon exporter'ı pinlenmiş SDK'nın `BoundedAttributes._dict` iç deposuna yazıyor**
   (biten span public mapping üzerinden immutable). `uv.lock` sürümü pinliyor, bir test koruyor;
   SDK yükseltmesinde bu test kırmızıya döner ve iç yapı doğrulanır — sızıntı yerine düşürme
   davranışı var.

## Doğrulama — 2026-07-30 · Codex test oturumu

Worktree kökünde, `COMPOSE_PROJECT_NAME=sp-codex` ile sınandı. Araç zinciri: Docker 25.0.3 ·
Docker Compose v2.24.6-desktop.1 · Python 3.13.14 · PostgreSQL 16.14 · pytest 9.1.1 ·
ruff 0.16.0 · mypy 2.3.0.

| # | Bulgu | Şiddet | Yeniden üretim | Durum |
|---:|---|---|---|---|
| 1 | Endpoint ayarlanmamış gerçek API yapılandırmasında telemetri handle’ı `None`; `/health/live` sonrası span/metric sağlayıcısı, exporter kuyruğu veya ek Python thread’i oluşmadı. | — | `OTEL_EXPORTER_OTLP_ENDPOINT` yokken TestClient/lifespan ve thread öncesi-sonrası karşılaştırması; `test_telemetry.py`. | geçti |
| 2 | Sentinel taşıyan presigned URL ve bearer token exporter yolunda sızmadı: URL `https://minio.test/bucket/key` olarak query/userinfo olmadan kaldı, authorization `[REDACTED]` oldu. | — | `_RedactingSpanExporter` ile in-memory exporter saldırısı; 14 telemetri birim testi. | geçti |
| 3 | API isteğindeki `X-Correlation-ID`, server span attribute’una bağlanıyor ve response header korunuyor. Ancak API→outbox→Beat→worker arasında dayanıklı `traceparent` saklanmadığından trace zinciri devam etmiyor; kaynakta `traceparent` kullanımı yok. Bu, iş emrinin raporlanmış/migration gerektiren istisnasıyla uyumlu açık takip işidir. | orta | Etkin telemetry ile `/health/ready` spanı; worker sınırı için kaynak taraması ve yürütücü raporundaki kapsam kararı. | kabul edilmiş takip işi |
| 4 | Kırk farklı saldırgan correlation ID ile metrik toplandı; etiket anahtarları yalnız HTTP düşük-kardinalite alanlarıydı. `asset_id`, `job_id`, `user_id`, `correlation_id`, `upload_id`, `url.full` görülmedi. | — | In-memory metric reader ile tekrarlı istek; `test_metric_labels_are_low_cardinality`. | geçti |

Ek doğrulama: `tests/unit/test_telemetry.py` **14 passed**; tüm backend takımı
`392 passed`; `ruff check`, `ruff format --check` ve `mypy .` temiz. Windows hostunda `make`
kurulu olmadığından `make verify` doğrudan çağrılamadı; eşdeğer konteyner komutları ayrı ayrı
çalıştırıldı.

**Karar:** teslim edilebilir — API içi bağ ve redaksiyon doğrulandı; API→worker trace devamlılığı
belgelenmiş, migration gerektiren takip işidir.
