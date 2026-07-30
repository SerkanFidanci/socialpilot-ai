# Doküman Router

Bu dosya bir liste değil, **yönlendiricidir** ve kasıtlı olarak kısadır. Her şeyi okumak
yerine **yaptığın işin tipine** karşılık gelen satırı bul, yalnızca oradaki dosyaları oku.

## Her oturum, her zaman

- [STATUS.md](STATUS.md) — nerede kaldık, bloke ediciler, bekleyen kararlar, açık work
  order'lar. **İlk okunan dosya.**
- [handoffs/README.md](handoffs/README.md) — work order protokolü, çakışma ve dal kuralları,
  bağlam bütçesi.

PM oturumu ayrıca [handoffs/PM-NOTES.md](handoffs/PM-NOTES.md) (rol, tetikleme promptları,
öğrenilen dersler) ve [reviews/](reviews/README.md) (teknoloji ve metodoloji
değerlendirmeleri) okur.

## Görev tipi → okunacaklar

Modül içinde çalışıyorsan o dizinin `CLAUDE.md`'si otomatik yüklenir; aşağıdaki
"modül `CLAUDE.md`" onu sayar. `+` işareti üstteki satıra eklendiğini gösterir.

| Yapılan iş | Okunacaklar | ~token |
|---|---|---|
| Modül içi bugfix | [STATUS.md](STATUS.md) + modül `CLAUDE.md` + hedef dosya | ~3k |
| Mevcut modülde yeni özellik | + ilgili `requirements/` dosyası + 1 mimari doküman | ~7k |
| Yeni modül | + ilgili ADR'ler + [aktif plan](plans/active/) | ~10k |
| Mimari değişiklik | [STATUS.md](STATUS.md) + ilgili `architecture/` + ilgili ADR'ler | ~8k |
| Yeni dış sağlayıcı entegrasyonu | + [99-external-platform-facts.md](product/requirements/99-external-platform-facts.md) + [35-ai-routing-cost.md](product/requirements/35-ai-routing-cost.md) | ~9k |
| Güvenlik/uyum işi | + [92-security-privacy.md](product/requirements/92-security-privacy.md) | ~6k |
| API endpoint ekleme/değiştirme | + [api/endpoints.md](api/endpoints.md) + [90b-api-error-contracts.md](product/requirements/90b-api-error-contracts.md) | ~8k |
| Doküman/işlem işi | [STATUS.md](STATUS.md) + [handoffs/README.md](handoffs/README.md) + bu dosya | ~3k |

## Asla bütün olarak okunmayacaklar

| Dosya | Boyut | Yerine |
|---|---|---|
| [generated/openapi.json](generated/openapi.json) | 86 KB / ~23k token | [api/endpoints.md](api/endpoints.md) (~1.2k token). Tek operasyonun şeması için `jq '.paths["/v1/..."]'`. |
| [product/product-requirements.md](product/product-requirements.md) | artık yalnızca indeks | Bölüm → dosya tablosundan hedefi bul, **yalnızca** o dosyayı oku. |
| [plans/completed/](plans/completed/) | ~52 KB | Tarihsel kayıt. Yalnızca "bu neden böyle yapıldı" için, tek dosya. |

`generated/openapi.json` üretilmiş bir çıktı olduğu için içine not düşülemez; kural
buradadır ve [AGENTS.md](../AGENTS.md)'de tekrarlanır.

## Ürün gereksinimleri

PRD §0–§50 **birebir** olarak [`product/requirements/`](product/requirements/) altındaki 24
domain dosyasına taşındı; bölüm numaraları korundu, her dosya ≤400 satır.

**Bölüm numarasından dosyayı bulmak için:**
[product/product-requirements.md](product/product-requirements.md) — 60 satırlık indeks.

En sık gerekenler: [99-external-platform-facts.md](product/requirements/99-external-platform-facts.md)
(sürüm/fiyat/limit/mevzuat — **hafızadan yazılmaz**) ·
[00-vision-principles.md](product/requirements/00-vision-principles.md) (kritik mimari
sınırlar) · [90b-api-error-contracts.md](product/requirements/90b-api-error-contracts.md) ·
[92-security-privacy.md](product/requirements/92-security-privacy.md) ·
[05-scope-roadmap.md](product/requirements/05-scope-roadmap.md) (fazlar ve kabul kriterleri).

## Kod haritaları

Dosyaları tek tek açarak keşfetme; dizinin `CLAUDE.md`'si sınırı, değişmezleri, her
dosyanın ne yaptığını ve testlerin yolunu söyler.

Backend: [modules/identity](../services/api/app/modules/identity/CLAUDE.md) ·
[modules/businesses](../services/api/app/modules/businesses/CLAUDE.md) ·
[modules/media](../services/api/app/modules/media/CLAUDE.md) ·
[modules/operations](../services/api/app/modules/operations/CLAUDE.md) ·
[core](../services/api/app/core/CLAUDE.md) ·
[infrastructure](../services/api/app/infrastructure/CLAUDE.md) ·
[worker](../services/api/app/worker/CLAUDE.md)
Mobil: [apps/mobile](../apps/mobile/CLAUDE.md)

## Mimari

| Doküman | Konu |
|---|---|
| [overview.md](architecture/overview.md) | Sistem genel görünümü |
| [backend-modules.md](architecture/backend-modules.md) | Modüler monolit sınırları |
| [tenant-isolation.md](architecture/tenant-isolation.md) | Tenant izolasyonu |
| [media-upload.md](architecture/media-upload.md) | Doğrudan object storage yükleme |
| [media-ingest-pipeline.md](architecture/media-ingest-pipeline.md) | Ingest hattı |
| [media-analysis.md](architecture/media-analysis.md) | Analiz hattı |
| [media-security.md](architecture/media-security.md) | Medya güvenlik geçidi |
| [ai-provider-routing.md](architecture/ai-provider-routing.md) | AI sağlayıcı yönlendirme |
| [background-jobs.md](architecture/background-jobs.md) | Arka plan işleri ve dayanıklı event'ler |
| [error-handling.md](architecture/error-handling.md) | RFC 9457 hata kontratı |

## Mimari karar kayıtları

Kimliklerin tek kaynağı [`adr/`](adr/) altındaki **dosya adlarıdır** — PRD §47'deki liste
değil. Katalog, statüler ve numaralandırma kuralı: [ADR kataloğu](adr/README.md).

[ADR-001](adr/ADR-001-modular-monolith.md) modüler monolit ·
[ADR-002](adr/ADR-002-direct-object-storage-upload.md) doğrudan object storage yükleme ·
[ADR-003](adr/ADR-003-n8n-orchestration-boundary.md) n8n sınırı ·
[ADR-004](adr/ADR-004-provider-adapter-pattern.md) provider adapter ·
[ADR-005](adr/ADR-005-transactional-outbox.md) transactional outbox ·
[ADR-006](adr/ADR-006-media-ingest-security-gate.md) ingest güvenlik geçidi ·
[ADR-007](adr/ADR-007-media-analysis-provider-routing.md) analiz sağlayıcı yönlendirme ·
[ADR-008](adr/ADR-008-s3-compatible-storage-adapter.md) S3-uyumlu storage adapter ·
[ADR-009](adr/ADR-009-dependency-and-runtime-baseline.md) bağımlılık ve runtime temeli ·
[ADR-010](adr/ADR-010-valkey-runtime-evaluation.md) Valkey değerlendirmesi (önerildi) ·
[ADR-011](adr/ADR-011-real-media-materializer.md) gerçek medya materializer

## Kontratlar, planlar, runbook'lar

| Dosya | Not |
|---|---|
| [api/endpoints.md](api/endpoints.md) | Endpoint envanteri — üretilmiş, `make generate-docs` günceller |
| [plans/active/](plans/active/) | Açık slice planı, ≤150 satır |
| [plans/completed/](plans/completed/) | Kapanmış planlar ve faz doğrulama kayıtları |
| [runbooks/local-development.md](runbooks/local-development.md) | Yerel geliştirme |
| [reviews/README.md](reviews/README.md) | İnceleme adlandırma kuralı ve mevcut incelemeler |
| [../apps/mobile/README.md](../apps/mobile/README.md) | Mobil analiz demosu |
