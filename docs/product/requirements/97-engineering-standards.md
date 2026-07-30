**Test stratejisi, CI/CD ve zorunlu uygulama kuralları** · PRD bölümleri: §40, §41, §46

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 40. Test stratejisi

## 40.1 Unit

- Entitlement hesaplama
- Frequency expansion
- Credit quote
- Budget guardrail
- Timeline validator
- State transition
- Provider response parser
- Role permission

## 40.2 Integration

- PostgreSQL
- Redis/Celery
- Object storage
- OAuth callback
- Store webhook
- AI provider mock
- Social publish sandbox
- Ads sandbox/test account
- FFmpeg sample render

## 40.3 Contract

- Provider adapter contract
- OpenAPI
- Webhook schema
- Timeline JSON schema
- n8n payload schema

## 40.4 E2E

- Kayıt → işletme → abonelik → medya → Reels → onay → yayın
- Google Ads bağlantı → paused campaign → onay → active
- Ödeme yenileme → entitlement
- Teknik hata → kredi iadesi
- Token expire → reconnect
- Emergency stop

## 40.5 Medya golden testleri

Sabit test medya seti:

- Dikey/yatay
- Gürültülü
- Türkçe konuşma
- Çoklu ürün
- Karanlık
- Titrek
- İnsan yüzü
- Öncesi/sonrası
- Logo
- Küçük metin

Çıktılar kalite eşikleriyle karşılaştırılır.

## 40.6 Güvenlik testleri

- Tenant isolation
- IDOR
- OAuth state attack
- Replay
- Webhook spoof
- File upload
- SSRF
- Prompt injection
- Token leakage
- Budget duplicate request

---

# 41. CI/CD

## 41.1 Pull request

- Lint
- Type check
- Unit tests
- Migration check
- OpenAPI diff
- Security scan
- Docker build
- Contract tests

## 41.2 Deployment

- Dev
- Staging
- Production
- DB migration ayrı job
- Backward-compatible migration
- Feature flag
- Canary
- Rollback
- Mobile API backward compatibility

## 41.3 Migration yöntemi

Expand/contract:

1. Yeni nullable alan
2. Kod iki formatı destekler
3. Backfill
4. Zorunlu hale getir
5. Eski alanı kaldır

---

# 46. Codex ve Claude Code için zorunlu uygulama kuralları

## 46.1 Genel

- Production kalitesinde kod yaz.
- Placeholder TODO bırakma; yapılamayan entegrasyonu feature flag ve mock adapter ile kapat.
- Domain modelini controller içinde yazma.
- Dış servis SDK nesnelerini domain katmanına geçirme.
- Her mutation için yetki ve idempotency düşün.
- Her asenkron iş için state, attempt, timeout ve dead-letter düşün.
- Her tablo için tenant izolasyonu düşün.
- Her dış çağrı için timeout, retry ve circuit breaker düşün.
- API anahtarını asla istemciye dönme.

## 46.2 Her feature için tamamlanma tanımı

1. Migration
2. Domain model
3. Repository
4. Service/use-case
5. API endpoint
6. Authorization
7. Validation
8. Idempotency
9. Event
10. Background task
11. Unit test
12. Integration test
13. OpenAPI
14. Metric/log
15. Admin görünümü gerekiyorsa
16. Mobile UI
17. Hata ve boş durum
18. Dokümantasyon

## 46.3 İlk komut

Kod ajanına aşağıdaki görev verilmelidir:

```text
Bu dokümanı kaynak gereksinim belgesi olarak kabul et.
Önce kod yazma. Aşağıdaki çıktıları üret:
1. Gereksinimlerden çıkarılmış domain modülleri.
2. ADR listesi.
3. Monorepo klasör ağacı.
4. İlk dikey dilimin görev listesi.
5. Veri modeli ERD taslağı.
6. API OpenAPI taslağı.
7. Risk ve belirsizlik listesi.
Ardından yalnızca Aşama 0'ı uygula.
Her aşama sonunda testleri çalıştır, sonuçları raporla ve bir sonraki aşamaya otomatik geçme.
```

## 46.4 Kod kalite kapıları

- Python type checking
- Ruff/format
- Pytest
- Flutter analyze/test
- TypeScript lint/test
- Migration downgrade testi
- No critical security finding
- Minimum kritik domain coverage
- OpenAPI backward compatibility
