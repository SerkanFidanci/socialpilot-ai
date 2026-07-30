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
docs/adr/ADR-XXX-<telemetri-temeli>.md      (yeni)
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

Numarayı **sen seçmiyorsun.** `ADR-XXX-<konu>.md` adıyla yaz, başlıkta da `ADR-XXX` bırak, raporda bildir. PM merge sırasında numaralandırır.

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum doldurur — özellikle: kapalıyken gerçekten sıfır maliyet mi, presigned URL sızıntısı, correlation↔trace bağının kopma senaryoları)_
