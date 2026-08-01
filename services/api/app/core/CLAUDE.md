# core — kesişen temel yapılar

**Sahibi:** tipli konfigürasyon, yapılandırılmış log, correlation ID, RFC 9457 hata
sözleşmesi ve uygulama fabrikasının ihtiyaç duyduğu küçük protokoller.
**Sahibi değil:** domain kuralı (→ `../modules/`), sağlayıcı uygulaması
(→ `../infrastructure/`), HTTP route tanımı (→ `../api/`).

## Değişmezler

- **Buradan `../modules/` içine bağımlılık verilmez.** `core` en alt katmandır; domain'i bilmez.
- Konfigürasyon **yalnızca** `Settings` üzerinden okunur; kodda `os.environ` erişimi yoktur ve secret varsayılan değeri yazılmaz.
- Her hata yanıtı `ProblemDetails` şemasındadır; route'lar `ProblemException` fırlatır, elle JSON hata gövdesi kurmaz.
- **Log'a secret, access token veya signed object-storage URL'i yazılmaz.** İki katman: (1) `logging._redact` structlog olaylarında alan adına göre maskeler — yeni hassas alan adı buraya eklenir; (2) `install_signature_redaction()` **süreç genelinde** imza query parametrelerini (`X-Amz-Signature`, `X-Amz-Credential`, `X-Amz-Security-Token`, GCS `Signature`/`GoogleAccessId`, Azure `sig`, `access_token`) hangi logger yazarsa yazsın, **hiçbir handler görmeden** maskeler. İkincisi zorunludur: httpx gerçek MinIO akışında tam imzalı URL'i `INFO` seviyesinde yazıyordu ve structlog işlemcisi o kaydı hiç görmüyordu (W14). Kütüphaneyi susturmak çözüm değildir — sonraki kütüphane aynısını yapar.
- **Redaksiyon üç kancadır ve tek başına record factory yetmez** (W16): `Logger.makeRecord`, `extra={...}`'yı factory **döndükten sonra** kayda yazar, bu yüzden factory o yüzeyi göremez. Kancalar: (a) record factory — `msg` + traceback, kayıt oluşurken; (b) `Logger.callHandlers` — `extra` dahil kaydın tamamı, herhangi bir handler'dan hemen önce (`Logger.handle` değil: 3.12+ bir filtre *başka bir record* döndürebilir); (c) `Handler.handle` — logger'dan geçmemiş, elde kurulmuş kaydın yedeği. Kayıt bir kez taranıp işaretlenir; `str` olmayan değerler (`httpx.URL`, iç içe dict/list) kapsanır ve **çağıranın nesnesi mutasyona uğratılmaz** — record üzerindeki referans redakte edilmiş değerle değişir. Yeni bir imza parametresi eklenirse hızlı yol işaretçilerinden (`sig`/`cred`/`token`/`keyid`/`accessid`) birini içermeli; içermezse `test_the_fast_path_cannot_hide_a_parameter_from_the_scrubber` düşer.
- **Filtre `configure_logging`'e bağlı değildir.** Worker `configure_logging` çağırmaz (handler'lar Celery'nin), bu yüzden `start_worker_process` filtreyi ayrıca kurar. Yeni bir süreç girişi eklenirse `install_signature_redaction()` orada da çağrılır.
- Doğrulama hatası meta'sı `safe_validation_error_meta` ile temizlenir; ham istek gövdesi hata yanıtına sızmaz.
- Her isteğin correlation ID'si vardır ve log kaydına aynı alan adıyla düşer.

## Dosyalar

| Dosya | İş |
|---|---|
| `config.py` | `Settings` (env'den tipli konfigürasyon) ve önbellekli `get_settings()` — **sahiplik: W01** |
| `errors.py` | `ProblemDetails`, `ProblemException`, `problem_response`, `safe_validation_error_meta` |
| `logging.py` | `configure_logging` + özyinelemeli secret maskeleme (`_redact`, `redact_sensitive_values`) + logger-bağımsız imza redaksiyonu (`redact_signature_material`, `install_signature_redaction`, `RedactingFormatter`); `extra` yüzeyi dahil |
| `correlation.py` | `CorrelationIdMiddleware` ve `get_correlation_id()` — istek/log arası izleme |
| `protocols.py` | `DatabaseClient` ve `RedisClient` protokolleri (uygulama fabrikası ve route'lar için) |
| `__init__.py` | Paket |

## Gereksinim, karar, mimari

- [96-stack-and-topology.md](../../../../docs/product/requirements/96-stack-and-topology.md) (PRD §42 env, §43 feature flag) · [90b-api-error-contracts.md](../../../../docs/product/requirements/90b-api-error-contracts.md) (§30 hata formatı) · [92-security-privacy.md](../../../../docs/product/requirements/92-security-privacy.md) (§33.3 secret) · [95-observability.md](../../../../docs/product/requirements/95-observability.md) (§37 log)
- Mimari: [error-handling.md](../../../../docs/architecture/error-handling.md) · [overview.md](../../../../docs/architecture/overview.md)

## Testler

`tests/unit/test_config.py` · `tests/unit/test_health.py` · `tests/unit/test_openapi.py` ·
`tests/unit/test_logging_redaction.py` ·
`tests/integration/test_media_uploads_minio.py` (gerçek MinIO akışında sızıntı testi)
