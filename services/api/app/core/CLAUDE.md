# core — kesişen temel yapılar

**Sahibi:** tipli konfigürasyon, yapılandırılmış log, correlation ID, RFC 9457 hata
sözleşmesi ve uygulama fabrikasının ihtiyaç duyduğu küçük protokoller.
**Sahibi değil:** domain kuralı (→ `../modules/`), sağlayıcı uygulaması
(→ `../infrastructure/`), HTTP route tanımı (→ `../api/`).

## Değişmezler

- **Buradan `../modules/` içine bağımlılık verilmez.** `core` en alt katmandır; domain'i bilmez.
- Konfigürasyon **yalnızca** `Settings` üzerinden okunur; kodda `os.environ` erişimi yoktur ve secret varsayılan değeri yazılmaz.
- Her hata yanıtı `ProblemDetails` şemasındadır; route'lar `ProblemException` fırlatır, elle JSON hata gövdesi kurmaz.
- **Log'a secret, access token veya signed object-storage URL'i yazılmaz.** `logging._redact` özyinelemeli maskeleme yapar; yeni hassas alan adı buraya eklenir.
- Doğrulama hatası meta'sı `safe_validation_error_meta` ile temizlenir; ham istek gövdesi hata yanıtına sızmaz.
- Her isteğin correlation ID'si vardır ve log kaydına aynı alan adıyla düşer.

## Dosyalar

| Dosya | İş |
|---|---|
| `config.py` | `Settings` (env'den tipli konfigürasyon) ve önbellekli `get_settings()` — **sahiplik: W01** |
| `errors.py` | `ProblemDetails`, `ProblemException`, `problem_response`, `safe_validation_error_meta` |
| `logging.py` | `configure_logging` + özyinelemeli secret maskeleme (`_redact`, `redact_sensitive_values`) |
| `correlation.py` | `CorrelationIdMiddleware` ve `get_correlation_id()` — istek/log arası izleme |
| `protocols.py` | `DatabaseClient` ve `RedisClient` protokolleri (uygulama fabrikası ve route'lar için) |
| `__init__.py` | Paket |

## Gereksinim, karar, mimari

- [96-stack-and-topology.md](../../../../docs/product/requirements/96-stack-and-topology.md) (PRD §42 env, §43 feature flag) · [90b-api-error-contracts.md](../../../../docs/product/requirements/90b-api-error-contracts.md) (§30 hata formatı) · [92-security-privacy.md](../../../../docs/product/requirements/92-security-privacy.md) (§33.3 secret) · [95-observability.md](../../../../docs/product/requirements/95-observability.md) (§37 log)
- Mimari: [error-handling.md](../../../../docs/architecture/error-handling.md) · [overview.md](../../../../docs/architecture/overview.md)

## Testler

`tests/unit/test_config.py` · `tests/unit/test_health.py` · `tests/unit/test_openapi.py`
