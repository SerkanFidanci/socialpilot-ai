**API tasarımı ve hata formatı** · PRD bölümleri: §29, §30

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 29. API tasarımı

Base:

```text
/api/v1
```

## 29.1 Ortak kurallar

- JSON
- ISO-8601
- UUID
- Cursor pagination
- Problem Details hata formatı
- `Idempotency-Key`
- `X-Correlation-ID`
- ETag/optimistic locking
- OpenAPI
- API versioning
- Request size limits

## 29.2 Auth ve kullanıcı

```text
GET    /me
PATCH  /me
GET    /me/devices
DELETE /me/devices/{id}
POST   /auth/session/exchange
POST   /auth/logout
DELETE /me
```

## 29.3 İşletmeler

```text
GET    /businesses
POST   /businesses
GET    /businesses/{id}
PATCH  /businesses/{id}
DELETE /businesses/{id}
GET    /businesses/{id}/members
POST   /businesses/{id}/members
PATCH  /businesses/{id}/members/{member_id}
DELETE /businesses/{id}/members/{member_id}
```

## 29.4 Marka

```text
GET    /businesses/{id}/brand
PUT    /businesses/{id}/brand
GET    /businesses/{id}/brand/health
GET    /businesses/{id}/products
POST   /businesses/{id}/products
PATCH  /businesses/{id}/products/{product_id}
GET    /businesses/{id}/campaign-offers
POST   /businesses/{id}/campaign-offers
```

## 29.5 Medya

```text
POST   /businesses/{id}/media/uploads
POST   /businesses/{id}/media/uploads/{session_id}/complete
GET    /businesses/{id}/media
GET    /businesses/{id}/media/{asset_id}
DELETE /businesses/{id}/media/{asset_id}
POST   /businesses/{id}/media/{asset_id}/reanalyze
GET    /businesses/{id}/media/{asset_id}/scenes
PATCH  /businesses/{id}/media/{asset_id}/scenes/{scene_id}
```

## 29.6 İçerik

```text
GET    /businesses/{id}/calendar
GET    /businesses/{id}/content-obligations
POST   /businesses/{id}/content-projects
GET    /businesses/{id}/content-projects/{project_id}
POST   /businesses/{id}/content-projects/{project_id}/generate
POST   /businesses/{id}/content-projects/{project_id}/approve
POST   /businesses/{id}/content-projects/{project_id}/reject
POST   /businesses/{id}/content-projects/{project_id}/revisions
POST   /businesses/{id}/content-projects/{project_id}/schedule
POST   /businesses/{id}/content-projects/{project_id}/publish-now
```

Create örneği:

```json
{
  "scenario": "voiceover_ad",
  "platforms": ["instagram"],
  "product_ids": ["uuid"],
  "media_asset_ids": ["uuid", "uuid"],
  "target_duration_seconds": 20,
  "quality_tier": "professional",
  "use_entitlement": true
}
```

## 29.7 Abonelik

```text
GET    /businesses/{id}/subscription
POST   /businesses/{id}/subscription/quotes
POST   /businesses/{id}/subscription/activate
PATCH  /businesses/{id}/subscription/configuration
POST   /businesses/{id}/subscription/pause
POST   /businesses/{id}/subscription/resume
GET    /businesses/{id}/entitlements
GET    /businesses/{id}/usage
POST   /billing/store/verify
POST   /billing/webhooks/apple
POST   /billing/webhooks/google
```

Quote örneği:

```json
{
  "items": [
    {
      "content_type": "instagram_reels",
      "frequency": {"unit": "day", "count": 1},
      "quality_tier": "professional"
    },
    {
      "content_type": "premium_video",
      "frequency": {"unit": "week", "count": 1},
      "quality_tier": "premium_ad"
    }
  ],
  "automation_mode": "semi_automatic"
}
```

Yanıt:

```json
{
  "monthly_points": 480,
  "recommended_store_tier": "flex_500",
  "included_flexible_points": 20,
  "display_price": "store-provided",
  "warnings": []
}
```

## 29.8 Bağlantılar

```text
GET    /businesses/{id}/connections
POST   /businesses/{id}/connections/{provider}/authorize
GET    /connections/{provider}/callback
POST   /businesses/{id}/connections/{connection_id}/refresh
DELETE /businesses/{id}/connections/{connection_id}
GET    /businesses/{id}/connections/{connection_id}/capabilities
```

## 29.9 Reklam

```text
GET    /businesses/{id}/advertising/settings
PUT    /businesses/{id}/advertising/settings
POST   /businesses/{id}/campaign-blueprints
GET    /businesses/{id}/campaign-blueprints/{id}
POST   /businesses/{id}/campaign-blueprints/{id}/validate
POST   /businesses/{id}/campaign-blueprints/{id}/create-paused
POST   /businesses/{id}/campaigns/{id}/approve
POST   /businesses/{id}/campaigns/{id}/activate
POST   /businesses/{id}/campaigns/{id}/pause
POST   /businesses/{id}/campaigns/{id}/emergency-stop
GET    /businesses/{id}/campaigns/{id}/metrics
GET    /businesses/{id}/optimization-recommendations
POST   /businesses/{id}/optimization-recommendations/{id}/approve
```

## 29.10 İş durumu

```text
GET /jobs/{job_id}
GET /jobs/{job_id}/events
POST /jobs/{job_id}/cancel
POST /jobs/{job_id}/retry
```

Mobil uygulama kısa polling yapabilir; üretimde WebSocket veya SSE eklenebilir.

---

# 30. Hata formatı

```json
{
  "type": "https://errors.example.com/entitlement-insufficient",
  "title": "Yetersiz kullanım hakkı",
  "status": 409,
  "code": "ENTITLEMENT_INSUFFICIENT",
  "detail": "Professional Reels hakkı bulunamadı.",
  "correlation_id": "uuid",
  "meta": {
    "required_points": 8,
    "available_points": 3
  }
}
```

Hata kodları dokümante edilmelidir.
