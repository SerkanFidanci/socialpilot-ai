**Risk listesi ve ADR önerileri** · PRD bölümleri: §48, §47

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 48. Risk listesi

| Risk | Önlem |
|---|---|
| Platform API onayı gecikir | Sandbox/mock adapter, feature flag |
| X Ads erişimi verilmez | Organik X’i ayrı yayınla; reklamı kapalı tut |
| Mobil mağaza dinamik paket kısıtı | Kredi tier + server entitlement |
| Video maliyeti artar | Scene sampling, cache, model routing |
| Render kuyruğu büyür | Ayrı queue, concurrency, autoscaling |
| AI yanlış fiyat yazar | Deterministik verified field overlay |
| Token sızar | Secret manager, encryption, redaction |
| Çift reklam kampanyası | Idempotency + external mapping |
| Bütçe aşımı | Spend ledger + guardrail + emergency stop |
| Kullanıcı medyası yetersiz | Çekim önerisi, görevi beklet |
| Türkçe ses kalitesi düşük | Provider benchmark ve fallback |
| Telif sorunu | Lisans kayıtları ve kullanım kısıtı |
| KVKK/yurt dışı aktarım | Hukuki inceleme, veri minimizasyonu, DPA |
| Provider modeli değişir | Capability routing ve config |
| n8n iş mantığına dönüşür | Domain state yalnızca backend/DB |

---

# 47. Mimari karar kayıtları

Başlangıç ADR’leri:

```text
ADR-001 Modular monolith
ADR-002 Flutter mobile
ADR-003 FastAPI + PostgreSQL
ADR-004 Celery for heavy jobs
ADR-005 n8n only for orchestration
ADR-006 Direct-to-object-storage uploads
ADR-007 Store billing vs RevenueCat adapter
ADR-008 Credit-tier flexible subscription model
ADR-009 Provider-agnostic AI router
ADR-010 Campaigns created paused
ADR-011 Transactional outbox
ADR-012 OAuth token envelope encryption
ADR-013 FFmpeg render worker isolation
ADR-014 pgvector scene retrieval
ADR-015 No regulated ads in MVP
```
