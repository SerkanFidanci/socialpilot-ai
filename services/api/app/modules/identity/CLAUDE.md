# identity — kimlik modülü

**Sahibi:** global kullanıcı kimliği ve dış kimlik sağlayıcı eşlemesi. Bir kişinin
sistemdeki tek kaydını çözer.
**Sahibi değil:** tenant üyeliği, rol ve yetki (→ `../businesses/`), token doğrulama
uygulaması (→ `../../infrastructure/identity/`), HTTP taşıma (→ `../../api/routes/`).

## Değişmezler

- Bu modül **tenant-farkında değildir.** `IdentityRepository` kasıtlı olarak hiç tenant
  operasyonu içermez; `business_id` filtresi burada aranmaz, `businesses` modülünde aranır.
- `domain.py`'deki `IdentityVerifier` protokolüne **provider SDK tipi geçmez.** Servis
  yalnızca `VerifiedIdentity` görür; hangi sağlayıcının doğruladığını bilmez.
- E-posta karşılaştırması `service.normalize_email` üzerinden yapılır; ham string ile
  kullanıcı aranmaz.
- Kullanıcı kimlikleri UUID'dir, zaman damgaları UTC'dir.

## Dosyalar

| Dosya | İş |
|---|---|
| `domain.py` | Sağlayıcıdan bağımsız sınır kontratları: `VerifiedIdentity`, `IdentityVerifier` protokolü |
| `models.py` | Kalıcılık modelleri: SQLAlchemy `Base`, `UserStatus`, `User`, `ExternalIdentity` |
| `repository.py` | `IdentityRepository` — global kullanıcı/dış kimlik sorguları, tenant operasyonu yok |
| `service.py` | `IdentityService` kimlik çözümleme + `normalize_email` |
| `__init__.py` | Modül paketi |

## Geçerli gereksinim ve kararlar

- [10-identity-tenancy.md](../../../../../docs/product/requirements/10-identity-tenancy.md) — roller ve yetkiler (PRD §4)
- [92-security-privacy.md](../../../../../docs/product/requirements/92-security-privacy.md) — kimlik doğrulama, secret yönetimi (PRD §33)
- [ADR-001](../../../../../docs/adr/ADR-001-modular-monolith.md) modüler monolit · [ADR-004](../../../../../docs/adr/ADR-004-provider-adapter-pattern.md) provider adapter
- Mimari: [tenant-isolation.md](../../../../../docs/architecture/tenant-isolation.md)

## Testler

`services/api/tests/unit/test_identity_and_business_policy.py` ·
`services/api/tests/integration/test_identity_businesses.py`
