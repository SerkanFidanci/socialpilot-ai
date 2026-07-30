# API Endpoint Envanteri

<!-- ÜRETİLMİŞ DOSYA — elle düzenlenmez. Kaynak: docs/generated/openapi.json
     Üreten: services/api/scripts/generate_endpoints_doc.py (`make generate-docs`) -->

**Kontrat:** SocialPilot AI API `0.1.0` · **OpenAPI** `3.1.0` · **32 endpoint**

> Bu dosya [`../generated/openapi.json`](../generated/openapi.json) yerine okunur:
> aynı kontrat, ~%98 daha az token. Şema/alan detayı gerekiyorsa tek endpoint'i
> `jq '.paths["/v1/..."]'` ile çek — dosyanın tamamını **okuma**.

Tüm sütunlar kontrattan türetilir; elle yazılmaz, dolayısıyla koddan sapamaz.
**Amaç** sütunu route fonksiyonunun docstring'inden, yoksa özet adından gelir —
boş görünen satırların çözümü ilgili route fonksiyonuna docstring eklemektir.

## Sütunların anlamı

| Sütun | Nereden gelir |
|---|---|
| Yetki | Operasyonun `security` şeması; yolda `{business_id}` varsa tenant kapsamı eklenir |
| Idempotency | `Idempotency-Key` header parametresinin varlığı. Mutasyon olup header'ı olmayan endpoint `değerlendirilmeli` işaretlenir — [AGENTS.md](../../AGENTS.md) her dışa görünür mutasyonun idempotency'yi değerlendirmesini ister |
| Başarı | Kontrattaki `2xx` yanıt kodları |

Hata gövdeleri RFC 9457 Problem Details formatındadır; her operasyon `400/401/403/404/409/422/500` tanımlar. Bkz. [error-handling.md](../architecture/error-handling.md) ve PRD §30 ([90b-api-error-contracts.md](../product/requirements/90b-api-error-contracts.md)).

## health — canlılık ve bağımlılık hazırlığı

| Metot | Yol | Amaç | Yetki | Idempotency | Başarı |
|---|---|---|---|---|---|
| `GET` | `/health/live` | Return process liveness without contacting dependencies | genel (kimlik gerekmez) | — | `200` |
| `GET` | `/health/ready` | Check PostgreSQL and Redis independently without exposing connection data | genel (kimlik gerekmez) | — | `200` |

## brands — marka, katalog, kampanya

| Metot | Yol | Amaç | Yetki | Idempotency | Başarı |
|---|---|---|---|---|---|
| `GET` | `/v1/businesses/{business_id}/brand` | Get Brand | `HTTPBearer` + tenant (`business_id`) | — | `200` |
| `PUT` | `/v1/businesses/{business_id}/brand` | Replace Brand | `HTTPBearer` + tenant (`business_id`) | yok — **değerlendirilmeli** | `200` |
| `GET` | `/v1/businesses/{business_id}/brand/health` | Brand Health | `HTTPBearer` + tenant (`business_id`) | — | `200` |
| `GET` | `/v1/businesses/{business_id}/campaign-offers` | List Campaign Offers | `HTTPBearer` + tenant (`business_id`) | — | `200` |
| `POST` | `/v1/businesses/{business_id}/campaign-offers` | Create Campaign Offer | `HTTPBearer` + tenant (`business_id`) | **var** — `Idempotency-Key` | `201` |
| `GET` | `/v1/businesses/{business_id}/products` | List Products | `HTTPBearer` + tenant (`business_id`) | — | `200` |
| `POST` | `/v1/businesses/{business_id}/products` | Create Product | `HTTPBearer` + tenant (`business_id`) | **var** — `Idempotency-Key` | `201` |
| `PATCH` | `/v1/businesses/{business_id}/products/{product_id}` | Update Product | `HTTPBearer` + tenant (`business_id`) | yok — **değerlendirilmeli** | `200` |

## businesses — işletme ve üyelik

| Metot | Yol | Amaç | Yetki | Idempotency | Başarı |
|---|---|---|---|---|---|
| `GET` | `/v1/businesses` | List Businesses | `HTTPBearer` | — | `200` |
| `POST` | `/v1/businesses` | Create Business | `HTTPBearer` | yok — **değerlendirilmeli** | `201` |
| `GET` | `/v1/businesses/{business_id}` | Get Business | `HTTPBearer` + tenant (`business_id`) | — | `200` |
| `PATCH` | `/v1/businesses/{business_id}` | Update Business | `HTTPBearer` + tenant (`business_id`) | yok — **değerlendirilmeli** | `200` |
| `GET` | `/v1/businesses/{business_id}/members` | List Members | `HTTPBearer` + tenant (`business_id`) | — | `200` |
| `POST` | `/v1/businesses/{business_id}/members` | Add Member | `HTTPBearer` + tenant (`business_id`) | yok — **değerlendirilmeli** | `201` |
| `PATCH` | `/v1/businesses/{business_id}/members/{member_id}` | Update Member | `HTTPBearer` + tenant (`business_id`) | yok — **değerlendirilmeli** | `200` |

## content

| Metot | Yol | Amaç | Yetki | Idempotency | Başarı |
|---|---|---|---|---|---|
| `GET` | `/v1/businesses/{business_id}/content/renders/{render_id}` | Get Render | `HTTPBearer` + tenant (`business_id`) | — | `200` |
| `POST` | `/v1/businesses/{business_id}/content/timelines` | Create Timeline | `HTTPBearer` + tenant (`business_id`) | **var** — `Idempotency-Key` | `201` |
| `GET` | `/v1/businesses/{business_id}/content/timelines/{timeline_id}` | Get Timeline | `HTTPBearer` + tenant (`business_id`) | — | `200` |
| `POST` | `/v1/businesses/{business_id}/content/timelines/{timeline_id}/patch` | Apply a parametric patch, producing a new revision rather than editing in place | `HTTPBearer` + tenant (`business_id`) | **var** — `Idempotency-Key` | `201` |
| `POST` | `/v1/businesses/{business_id}/content/timelines/{timeline_id}/renders` | Validate and enqueue a render. The response is the record, not the video | `HTTPBearer` + tenant (`business_id`) | **var** — `Idempotency-Key` | `202` |
| `GET` | `/v1/businesses/{business_id}/scripts` | List this business's scripts newest first, with an opaque cursor | `HTTPBearer` + tenant (`business_id`) | — | `200` |
| `POST` | `/v1/businesses/{business_id}/scripts` | Generate one script (PRD §18.1) from verified records, synchronously | `HTTPBearer` + tenant (`business_id`) | **var** — `Idempotency-Key` | `201` |
| `GET` | `/v1/businesses/{business_id}/scripts/{script_id}` | Read one script: the resolved contract, the slot template, and its provenance | `HTTPBearer` + tenant (`business_id`) | — | `200` |

## identity — kimlik

| Metot | Yol | Amaç | Yetki | Idempotency | Başarı |
|---|---|---|---|---|---|
| `GET` | `/v1/me` | Me | `HTTPBearer` | — | `200` |

## media — yükleme control-plane'i ve analiz okuması

| Metot | Yol | Amaç | Yetki | Idempotency | Başarı |
|---|---|---|---|---|---|
| `POST` | `/v1/businesses/{business_id}/media/uploads` | Create | `HTTPBearer` + tenant (`business_id`) | yok — **değerlendirilmeli** | `201` |
| `POST` | `/v1/businesses/{business_id}/media/uploads/{upload_session_id}/cancel` | Cancel | `HTTPBearer` + tenant (`business_id`) | yok — **değerlendirilmeli** | `204` |
| `POST` | `/v1/businesses/{business_id}/media/uploads/{upload_session_id}/complete` | Complete | `HTTPBearer` + tenant (`business_id`) | **var** — `Idempotency-Key` | `200` |
| `POST` | `/v1/businesses/{business_id}/media/uploads/{upload_session_id}/parts` | Parts | `HTTPBearer` + tenant (`business_id`) | yok — **değerlendirilmeli** | `200` |
| `GET` | `/v1/businesses/{business_id}/media/{asset_id}` | Asset | `HTTPBearer` + tenant (`business_id`) | — | `200` |
| `GET` | `/v1/businesses/{business_id}/media/{asset_id}/processing-summary` | Return one tenant-scoped read of the whole analysis pipeline for a client screen | `HTTPBearer` + tenant (`business_id`) | — | `200` |

## Kapsam notu

Bu envanter yalnızca **uygulanmış** endpoint'leri listeler. PRD §29'un tasarladığı tam API yüzeyi (içerik, abonelik, bağlantılar, reklam, iş durumu) için [90b-api-error-contracts.md](../product/requirements/90b-api-error-contracts.md) okunur; oradaki bir endpoint burada yoksa henüz yazılmamıştır.
