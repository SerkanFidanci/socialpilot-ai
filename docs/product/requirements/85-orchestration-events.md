**n8n sınırı ve event-driven tasarım** · PRD bölümleri: §26, §27

> **Taşıyıcı değişti — [ADR-012](../../adr/ADR-012-remove-n8n-from-mvp.md).** n8n MVP kapsamından çıkarıldı; §26'nın workflow kataloğu iptal değil, Celery Beat + kuyruklar + outbox tüketicileri olarak backend'de karşılığını buluyor. §27'nin event-driven tasarımı (outbox, idempotency, event zarfı) **aynen geçerli**.

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 26. n8n kullanım sınırı

## 26.1 n8n’in yapacağı işler

- Zamanlanmış content obligation tetikleme
- Servisler arası webhook koordinasyonu
- E-posta/push operasyon akışları
- Token health check
- Onay hatırlatması
- Sosyal yayınlama zamanlaması
- Reklam raporu toplama tetikleri
- Günlük/haftalık rapor
- Hata eskalasyonu
- Harici CRM webhook
- Operasyon bildirimleri

## 26.2 n8n’in yapmayacağı işler

- Büyük video binary taşıma
- FFmpeg render
- Domain verisinin tek kaynağı olma
- Abonelik ve kredi hesabı
- Reklam bütçe kararı
- Yetkilendirme
- OAuth tokenını düz metin tutma
- Kritik transaction yönetimi
- Model promptlarının tek kaynağı olma

## 26.3 Workflow kataloğu

### Hesap

```text
ACC-01 Meta Connect Callback
ACC-02 Google Ads Connect Callback
ACC-03 X Connect Callback
ACC-04 Token Health Check
ACC-05 Reconnect Notification
ACC-06 Account Capability Refresh
```

### Abonelik

```text
SUB-01 Store Event Intake
SUB-02 Subscription Activated
SUB-03 Entitlement Window Generator
SUB-04 Upgrade/Downgrade
SUB-05 Grace Period
SUB-06 Billing Failure Notification
SUB-07 Pause/Resume
SUB-08 Refund/Revoke
```

### İçerik

```text
CNT-01 Weekly Plan
CNT-02 Daily Obligation Dispatcher
CNT-03 Media Readiness Check
CNT-04 Content Job Start
CNT-05 Approval Notification
CNT-06 Approval Reminder
CNT-07 Auto Publish Dispatcher
CNT-08 Revision Dispatcher
CNT-09 Failed Job Escalation
```

### Reklam

```text
ADS-01 Campaign Blueprint Approval
ADS-02 Campaign Creation Dispatcher
ADS-03 Hourly Spend Guard
ADS-04 Daily Performance Collection
ADS-05 Optimization Recommendation
ADS-06 Approved Optimization Execution
ADS-07 Landing Page Health
ADS-08 Conversion Tracking Health
ADS-09 Emergency Stop
ADS-10 Ad Rejection Handler
```

### Operasyon

```text
OPS-01 Provider Health
OPS-02 Queue Backlog Alert
OPS-03 Daily Cost Report
OPS-04 Storage Usage Alert
OPS-05 Dead Letter Escalation
OPS-06 Security Event Notification
```

## 26.4 n8n payload standardı

```json
{
  "event_id": "uuid",
  "event_type": "content.preview_ready",
  "occurred_at": "ISO-8601",
  "tenant_id": "uuid",
  "aggregate_id": "uuid",
  "correlation_id": "uuid",
  "idempotency_key": "string",
  "payload": {}
}
```

Tüm n8n girişleri imzalı webhook veya private network üzerinden olmalıdır.

> **Uygulama notu (W12) — zarf bir alan daha taşır: `traceparent`.** Yukarıdaki alan listesine
> W3C [`traceparent`](https://www.w3.org/TR/trace-context/) (ve varsa `tracestate`) eklendi.
> Gerekçe: iş, API'den worker'a doğrudan enqueue ile değil outbox + Beat üzerinden geçtiği için
> süreç içi trace bağlamı worker'a ulaşmıyordu; zarf `correlation_id`'yi zaten taşıdığından
> aynı kategorideki bu bağlam kimliğini de taşıyor. **Sır değildir, şema değişikliği
> gerektirmez:** ayrı bir zarf kolonu olmadığından `outbox_events.payload_json` içinde durur.
> Telemetri kapalıyken alan **hiç yazılmaz**; zarfa `traceparent`/`tracestate` dışında hiçbir
> telemetri verisi (attribute, prompt, URL, baggage) konmaz; okunan değer doğrulanmadan trace
> bağlamına alınmaz. Ayrıntı: [observability.md](../../architecture/observability.md).

---

# 27. Event-driven tasarım

## 27.1 Domain event örnekleri

```text
business.created
brand.updated
media.upload_completed
media.ready
media.analysis_completed
subscription.activated
entitlement.window_created
content.obligation_created
content.job_started
content.preview_ready
content.approved
content.scheduled
content.published
connection.expired
campaign.blueprint_created
campaign.activated
campaign.guardrail_triggered
billing.subscription_changed
```

## 27.2 Transactional outbox

Domain işlemi ile event yayınlama aynı transaction’da güvence altına alınmalıdır.

```text
outbox_events
- id
- event_type
- aggregate_type
- aggregate_id
- payload_json
- occurred_at
- published_at
- retry_count
```

Worker outbox kayıtlarını Redis/n8n webhook’una yollar.

## 27.3 Idempotency

Zorunlu alan:

- Mobil create işlemleri
- Store webhook
- Social publish
- Ad create/update
- Refund
- Usage consume
- n8n webhook

`idempotency_keys` tablosu request hash ve response saklayabilir.
