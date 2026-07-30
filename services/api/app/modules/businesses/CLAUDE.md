# businesses — tenant ve üyelik modülü

**Sahibi:** işletme (tenant) kaydı, üyelik, rol ve rol→yetki politikası. Sistemdeki
yetkilendirme kararlarının merkezi.
**Sahibi değil:** kullanıcı kimliği (→ `../identity/`), marka profili / ürün-hizmet
kataloğu (PRD §11, henüz yok — W04), HTTP taşıma (→ `../../api/routes/businesses.py`).

## Değişmezler

- **Her sorgu `business_id` ister.** `BusinessRepository`'deki hiçbir okuma veya yazma
  tenant filtresi olmadan çalışmaz; tenant sızması burada başlar.
- Yetki kararı **yalnızca** `policy.permits(role, permission)` üzerinden verilir. Route
  veya servis içinde elle rol karşılaştırması (`role == "owner"` gibi) yazılmaz.
- Yeni bir yetki eklemek `Permission` enum'una satır eklemektir; politika tablosu tek yerde.
- Slug üretimi `service.create_slug` ile deterministiktir; istemciden gelen slug'a güvenilmez.
- İş kuralı servis katmanındadır, controller'da değildir.

## Dosyalar

| Dosya | İş |
|---|---|
| `models.py` | `BusinessStatus`, `BusinessRole`, `MembershipStatus`, `Business`, `BusinessMember` |
| `policy.py` | `Permission` enum'u ve `permits()` — merkezî rol→yetki tablosu |
| `repository.py` | `BusinessRepository` — tenant-kapsamlı kalıcılık işlemleri |
| `service.py` | `BusinessService` (tenant zorlamalı işletme/üyelik servisleri) + `create_slug` |
| `__init__.py` | Modül paketi |

## Geçerli gereksinim ve kararlar

- [10-identity-tenancy.md](../../../../../docs/product/requirements/10-identity-tenancy.md) — roller, yetkiler, operasyon rolleri (PRD §4)
- [20-brand-catalog.md](../../../../../docs/product/requirements/20-brand-catalog.md) — işletme/marka profili, ürün, kampanya (PRD §11)
- [ADR-001](../../../../../docs/adr/ADR-001-modular-monolith.md) modüler monolit
- Mimari: [tenant-isolation.md](../../../../../docs/architecture/tenant-isolation.md) · [backend-modules.md](../../../../../docs/architecture/backend-modules.md)

## Testler

`services/api/tests/unit/test_identity_and_business_policy.py` ·
`services/api/tests/integration/test_identity_businesses.py`
