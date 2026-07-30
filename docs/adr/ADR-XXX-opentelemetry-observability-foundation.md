# ADR-XXX: OpenTelemetry Observability Foundation (Trace + Metric, Default OFF)

**Status:** Accepted
**Date:** 2026-07-30
**Karar veren:** W05 (yürüten oturum) · PRD §37 doğrultusunda
**İlgili:** [ADR-013](ADR-013-single-server-deployment-topology.md) (kaynak bütçesi) ·
[ADR-005](ADR-005-transactional-outbox.md) (outbox + drain) ·
mimari: [observability.md](../architecture/observability.md)

> Numarayı PM merge sırasında verir; dosya adında ve başlıkta `ADR-XXX` bilinçli bırakıldı.

## Context

PRD §37 OpenTelemetry'yi yığının parçası sayıyor ve `AGENTS.md`'nin tamamlanma tanımı "metrics
where appropriate" diyor; ama depoda `opentelemetry` kelimesi hiç geçmiyordu. Tek
gözlemlenebilirlik structlog JSON log'uydu. Tek sunucu kararı (ADR-013) sonrası "hangi iş CPU'yu
yedi / kuyruk ne kadar derin / sağlayıcı ne kadar sürdü" sorularının cevabı log'dan çıkmıyor.

İki kısıt tasarımı belirledi:

1. **Tek sunucuda idle maliyeti sıfır olmalı** (ADR-013 kaynak bütçesi). Kimse toplamıyorken
   telemetri hiçbir thread/kuyruk açmamalı.
2. **CI credential ve collector olmadan yeşil kalmalı.** `make verify` bir uca ihtiyaç duymamalı.

Ayrıca span attribute'ları ve metric label'ları otomatik toplandığı için log'dan daha
sızdırıcı: auto-instrumentation httpx isteğinin **tam URL'ini** yazar ve object storage'da bu
*imzalı* (presigned) bir URL'dir — sorgu dizesi geçerli bir credential'dır.

## Decision

### 1. Varsayılan KAPALI, açıkken tam

`OTEL_EXPORTER_OTLP_ENDPOINT` verilmedikçe telemetri no-op. Kurulum fonksiyonları `None` döner:
exporter kurulmaz, `BatchSpanProcessor`/`PeriodicExportingMetricReader` thread'i başlamaz, global
provider kurulmaz, OTel API kendi no-op provider'ında kalır. Ayarlar tipli `Settings`'e girer.

### 2. OTLP http/protobuf, gRPC değil

Exporter **http/protobuf** taşımasını kullanır (`opentelemetry-exporter-otlp-proto-http`).
Gerekçe: `grpcio`'yu imaja sokmamak. Uç tek bir collector'a konuşur.

### 3. Instrumentation core ve composition'dan, domain'e değil

Kanca yalnızca `app/core/telemetry.py` + iki composition kökünde (`app/main.py`,
`app/worker/composition.py`). Auto-instrumentation: FastAPI, SQLAlchemy, httpx, redis, Celery.
Hiçbir `app/modules/**` dosyası telemetri import etmez; bir test bunu zorlar. Domain servisleri
taşınabilir kalır.

### 4. Redaksiyon iki katmanlı ve garantili

- **httpx kancaları** span daha kaydederken URL'in query/fragment/userinfo'sunu düşürür —
  imzalı URL span'de bir an bile durmaz.
- **`_RedactingSpanExporter`** export yolundaki garanti ağı: her instrumentation'dan gelen her
  span'i OTLP'ye verilmeden hemen önce temizler (secret-adlı attribute → `[REDACTED]`,
  URL-değerli attribute → `scheme://host/path`). Temizleyemediği span'i **gönder
  mez, düşürür**. Sentinel imzalı presigned URL ve token ile test edilir (W01 deseni).
- Metric label'ları düşük kardinaliteli tutulur; asset/job/upload/correlation/user id label
  olmaz (span attribute olabilir). `user_id` maskeli, `business_id` serbest.

### 5. Metrikler

Bu slice'ta anlamlı olanlar: API latency + hata oranı (`http.server.duration`), sağlayıcı/depo
istemci latency'si (`http.client.request.duration`), job süresi (`job.duration` — Celery
prerun/postrun sinyalleriyle, label = task adı + durum) ve kuyruk derinliği (`queue.depth` —
broker `LLEN`, observable gauge). Var olmayan metrik uydurulmadı; sonraki modüller tek satırla
ekler.

### 6. Correlation ID ↔ trace

Mevcut `X-Correlation-ID` korunur. `trace_id`/`span_id` her log satırına eklenir (structlog
processor); correlation id server span'ine attribute olarak konur. İki yönlü köprü.

## Consequences

- Tek sunucuda telemetri kapalıyken ölçülebilir ek yük yok; açıkken tek collector'a http/protobuf.
- İmzalı URL / token / secret hiçbir span'e veya metric label'ına düşmez; bu bir test invaryantı.
- **API'de başlayan isteğin tetiklediği worker işi aynı trace'te GÖRÜNMEZ** — ve bu bilinçli.
  İşler outbox + beat-zamanlı drain ile seçilir (ADR-005); doğrudan enqueue kenarı yok, dolayısıyla
  taşınacak trace bağlamı da yok. Celery instrumentation beat→drain→DB zincirini bağlar ama trace
  beat tick'inde başlar. Tam zincir için `traceparent`'ın dayanıklı iş satırında taşınması gerekir
  → **şema değişikliği (migration), bu slice'ın kapsamı dışı.** Takip işi olarak kaydedildi.
- SQLAlchemy async motoru `sync_engine` üzerinden instrument edilir; motor yaşam döngüsünde
  (lifespan/worker init) bağlanır çünkü ancak orada mevcuttur.

## Rejected alternatives

- **gRPC OTLP exporter:** reddedildi — `grpcio` imaj/derleme yükü; http/protobuf yeterli.
- **Exporter'da span attribute'unu public mapping ile düzenlemek:** mümkün değil — biten span'in
  `BoundedAttributes`'ı immutable. Pinlenmiş SDK'nın iç deposu üzerinden yazılır (uv.lock
  pinliyor, test koruyor); iç yapı değişirse span sızmak yerine düşürülür.
- **Domain servislerine telemetri gömmek:** reddedildi — ADR-004 port sınırını ve domain
  taşınabilirliğini bozar; instrumentation kenarlardan uygulanır.
- **Collector/dashboard/alert'i bu slice'a katmak:** ertelendi — exporter bir uca konuşur, ucu
  ayağa kaldırmak operasyon işi (§37.4). `compose.yaml` W06'ya ait.
