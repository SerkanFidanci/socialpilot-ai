# brands — marka profili, katalog ve kampanya modülü

**Sahibi:** marka kimliği, ürün/hizmet kataloğu ve fiyat geçmişi, kampanya kaydı, içerik
güvenlik listeleri (onaylı/yasak iddia, onaylı CTA), hedef kitle ve marka sağlık skoru.
İçerik üretiminin **doğrulanmış kayıt** kaynağı budur (PRD §11.3).
**Sahibi değil:** yetki tablosu (→ `../businesses/policy.py`), medya byte'ı ve yükleme yolu
(→ `../media/`, ADR-002), içerik/senaryo üretimi (Phase 2), HTTP taşıma
(→ `../../api/routes/brands.py`), cursor primitifi (→ `../../core/pagination.py`).

## Değişmezler

- **Para her yerde integer minor unit + ISO-4217 kodu.** Modülde `float`/`Decimal`/`Numeric`
  yok; testi bunu sütunlar, kaynak kod ve OpenAPI şeması üzerinden doğrular.
- **Fiyat yerinde güncellenmez.** Yeni fiyat açık satırı kapatır (`effective_to`) ve yeni satır
  ekler; kısmi unique index ürün başına tek açık fiyat garantiler.
- **Tek para birimi.** `brand_profiles.default_currency` işletmenin kayıt para birimidir;
  uyuşmazlık `CURRENCY_MISMATCH` ile reddedilir.
- **Zaman UTC.** Kampanya penceresi yarı açık `[starts_at, ends_at)`; offset'siz zaman reddedilir.
- **Her sorgu `business_id` ister.** `list_all()` yok; erişilemeyen kayıt `404`, yetkisiz üye `403`.
- **Yetki yeniden yazılmaz.** `policy.py` yalnızca marka eylemini mevcut `Permission`'a eşler:
  yazma `business.update` (owner/admin), okuma `business.read` (tüm roller).
- **Kampanya aktifliği deterministik** ve iki yerde tanımlı (saf fonksiyon + SQL yüklemi);
  integration testi ikisinin sınır satırlarında aynı cevabı verdiğini doğrular.
- **Sağlık skoru tavsiyedir**, hiçbir mutation'ı bloke etmez; ölçülemeyen bileşen `unavailable`.
- İş kuralı servis katmanındadır, controller'da değildir.

## Dosyalar

| Dosya | İş |
|---|---|
| `models.py` | 10 tablo + durum enum'ları (`ProductStatus`, `StockStatus`, `CampaignOfferStatus`, `CampaignApprovalStatus`, `DiscountType`, `BrandAssetRole`) |
| `domain.py` | Saf kurallar: normalizasyon/limitler, `Money`, `evaluate_campaign_activity`, `evaluate_brand_health`, `MediaAssetPort` |
| `policy.py` | `BrandAction` → merkezî `Permission` eşlemesi (`permits_action`) |
| `repository.py` | `BrandRepository` (tenant-kapsamlı kalıcılık) + `MediaAssetReader` (medyaya salt-okunur pencere) |
| `service.py` | `BrandService` — yetki, validation, idempotency, audit, transaction; giriş/çıkış dataclass'ları |
| `__init__.py` | Modül paketi |

## Gereksinim, karar, mimari

- [20-brand-catalog.md](../../../../../docs/product/requirements/20-brand-catalog.md) (PRD §11) ·
  [15-mobile-experience.md](../../../../../docs/product/requirements/15-mobile-experience.md) (§10.3–10.4) ·
  [90a-database-design.md](../../../../../docs/product/requirements/90a-database-design.md) (§28.2) ·
  [90b-api-error-contracts.md](../../../../../docs/product/requirements/90b-api-error-contracts.md) (§29.1, §29.4) ·
  [10-identity-tenancy.md](../../../../../docs/product/requirements/10-identity-tenancy.md) (§4)
- [ADR-001](../../../../../docs/adr/ADR-001-modular-monolith.md) · [ADR-002](../../../../../docs/adr/ADR-002-direct-object-storage-upload.md)
- Mimari: [brand-catalog.md](../../../../../docs/architecture/brand-catalog.md) ·
  [tenant-isolation.md](../../../../../docs/architecture/tenant-isolation.md) ·
  [error-handling.md](../../../../../docs/architecture/error-handling.md)

## Testler

`tests/unit/test_brand_catalog_unit.py` · `tests/unit/test_pagination.py` ·
`tests/integration/test_brand_catalog.py`
