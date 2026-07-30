**Gözlemlenebilirlik ve ölçekleme** · PRD bölümleri: §37, §38

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 37. Gözlemlenebilirlik

## 37.1 Log

Her log:

- timestamp
- level
- service
- environment
- correlation_id
- user_id; gerekirse maskeli
- business_id
- job_id
- provider
- event
- duration
- error_code

Token ve medya URL’leri loglanmaz.

## 37.2 Metric

- API latency/error
- Queue depth
- Job duration
- Render duration
- Provider latency
- Provider cost
- Token usage
- Upload failure
- Publish success
- OAuth refresh failure
- Ad API failure
- Guardrail trigger
- Subscription mismatch
- Notification delivery

## 37.3 Trace

OpenTelemetry ile:

```text
mobile request
→ API
→ DB
→ queue
→ worker
→ AI provider
→ storage
→ render
```

## 37.4 Alert

- Publish failure spike
- OAuth refresh spike
- Render queue backlog
- Provider error > threshold
- Spend guard mismatch
- Store notification lag
- DB connection saturation
- Storage quota
- Security event
- n8n workflow failure

---

# 38. Ölçekleme

## 38.1 Başlangıç

- Tek PostgreSQL
- Tek Redis
- API yatay ölçeklenebilir
- Worker queue’ları ayrılmış
- Object storage
- n8n tek main + worker ihtiyaca göre
- CDN

## 38.2 Queue’lar

```text
media_ingest
media_analysis
asr
vlm
script
tts
render_standard
render_premium
publishing
ads
analytics
notifications
dead_letter
```

Her queue farklı concurrency ve resource limiti kullanır.

## 38.3 Backpressure

- Tenant eşzamanlı iş limiti
- Plan bazlı öncelik
- Premium queue
- Provider rate limit semaphore
- Render kapasite limiti
- İş başlamadan maliyet bütçesi
- Queue tahmini UI’da gösterilebilir; kesin süre sözü verilmez

## 38.4 Database partitioning

İleride yüksek hacimli tablolar:

- metric observations
- audit logs
- webhook events
- provider usage
- job attempts

tarih bazlı partition edilebilir.
