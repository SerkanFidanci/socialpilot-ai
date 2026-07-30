# Teknoloji ve Metodoloji İncelemesi

**Tarih:** 2026-07-30 · **İnceleyen:** PM oturumu · **İncelenen commit:** `648c516` (Phase 0 sonu; Phase 1 o an merge edilmemişti)
**Kapsam:** ürün amacı, mimari yön, teknoloji seçimlerinin güncelliği, çalışma metodolojisi.

Dış dünya gerçekleri (sürümler, fiyatlar, mevzuat) bu dosyada **tekrarlanmaz** — canlı kayıt: [99-external-platform-facts.md](../product/requirements/99-external-platform-facts.md).

## Yargı

| Soru | Cevap |
|---|---|
| Mimari yön doğru mu? | **Evet.** 2026 pratiğiyle uyumlu, birkaç yerde ondan iyi. |
| En güncel teknolojiler mi? | **Hayır.** Sürüm seçimleri sistematik olarak ~18–20 ay geride; tespit edilebilir bir kök nedeni var. |
| Doğru şekilde mi ilerliyoruz? | Mühendislik metodolojisi olarak evet; ama planın hesaba katmadığı dış gerçekler vardı. |

## Korunacak kararlar (değiştirme)

Modüler monolit + ayrı worker süreçleri · PostgreSQL tek gerçek kaynak, Redis yalnızca broker · doğrudan object-storage upload (byte API'den geçmez) · transactional outbox + at-least-once + idempotent consumer · kabiliyet tabanlı AI routing (model kimliği config'te) · sağlayıcı adapter/port sınırları · sunucu taraflı entitlement ledger · reklam kampanyalarının PAUSED açılması + deterministik guardrail + spend ledger · mobil yığın (Flutter + Riverpod 3 + go_router + Dio + Drift) · admin yığını (Next.js + TS + TanStack Query + RHF + Zod).

**Kabiliyet routing'inin değeri kanıtlandı:** PRD yazıldığından bu yana Qwen3.5-Omni, Gemini 3.1, Seedance 2.0, Kling 3.0, Sora 2 çıktı. Model isimlerini koda gömen bir tasarım bugün çöp olurdu. Sağlayıcı adaylarınız hâlâ maliyet-optimal.

## Kök neden: sürüm sapması

Bağımlılık pinlerinin **tamamı Eylül–Kasım 2024** sürümlerine denk düşüyordu. Bu rastgele değil — bir dil modelinin hafızasından yazdığı sürüm setidir, canlı paket deposundan doğrulanmış değil. Üstüne tüm pinler **üst sınırlı** (`<0.116`, `<0.31`, `<1.15`, `<5.5`, `<3.13`): güncellemeyi aktif olarak bloke eden bir yapı. Lockfile yok → CI ile prod aynı byte'ları kurmuyor. Dependabot/Renovate yok.

| Bileşen | Projede | 2026-07 güncel |
|---|---|---|
| Python | `>=3.12,<3.13` | 3.14 kararlı (3.15 → 1 Eki 2026) |
| FastAPI | 0.115.x | ~0.13x |
| uvicorn | 0.30.x | 0.51.0 |
| Alembic | 1.14.x | 1.18.5 |
| Celery | 5.4.x | 5.6.2 |
| redis-py | 5.2 | 6.x |
| structlog | 24.4 | 25.x |
| mypy / ruff | 1.13 / 0.8 | ikisi de çok ileride |
| SQLAlchemy | `>=2.0,<2.1` | ✅ **doğru** (2.1 beta) |
| PostgreSQL | 16-alpine | 18 |
| Redis | 7-alpine | Redis 8 (AGPL) / Valkey 9.1 (BSD) |
| Paket yönetimi | pip + requirements.txt | `uv` fiili standart |

**Kalıcı önlem:** `AGENTS.md`'ye "bağımlılık sürümleri yazıldığı anda paket deposundan doğrulanır, lockfile zorunludur" kuralı eklenir → **W03** kapsamında.

## Metodolojik boşluklar → iş emri eşlemesi

| # | Boşluk | Nereye bağlandı |
|---|---|---|
| 1 | Lockfile yok, pinler üst sınırlı, otomatik güncelleme yok | **W02** |
| 2 | CI'da güvenlik taraması yok — PRD §41.1 ve §33.5 zorunlu kılıyor | **W02** |
| 3 | Gözlemlenebilirlik iddia ediliyor, yalnızca structlog var; OTel/metrik/trace sıfır | **W05** |
| 4 | MIME allowlist iOS gerçeğiyle çakışıyor (HEIC/HEIF/`.mov` yok) — mobil ana akış kırık | **W01** |
| 5 | Celery ↔ async uyumsuzluğu (Celery 5.6'da native asyncio yok, uygulama tamamen async) | ADR gerekiyor — PM kuyruğunda |
| 6 | İçerik hattı bir task queue işi değil, durable workflow işi; Postgres üstünde elle mini motor yazılıyor | Phase 2 kapısında karar (DBOS/Temporal değerlendirmesi) |
| 7 | Dış API sürüm yaşam döngüsü politikası yok (Google Ads yılda 4 major, ~1 yıl ömür) | ADR gerekiyor — PM kuyruğunda |
| 8 | Instagram yayını **public URL** istiyor; "yalnızca kısa ömürlü signed URL" duruşuyla çelişiyor | ADR gerekiyor — PM kuyruğunda |
| 9 | n8n lisansı + değer sorusu | Karar **K2** |
| 10 | Terraform BUSL yerine OpenTofu (greenfield) | W06 sonrası, düşük öncelik |
| 11 | AI router'ı sıfırdan yazmak yerine LiteLLM'i kabiliyet portlarının **altına** koymak | Phase 2 öncesi değerlendirme |
| 12 | Tenant listelerinde cursor pagination (kendi kaydettikleri teknik borç) | W04 ile birlikte |
| 13 | Bağlam maliyeti: zorunlu okuma ~34k token/oturum | **W03** |
| 14 | `main` 16 commit geride, 5 worktree / 9 dal, çift iş (`c43ccad`) | ✅ Sprint 0'da kapatıldı |

## Ürün ekonomisi ve uyum uyarıları

Ayrıntı ve kaynak: [99-external-platform-facts.md](../product/requirements/99-external-platform-facts.md). Özet:

- **Mağaza komisyonu (K1):** Türkiye alternatif faturalandırma programlarında yok → IAP'ta %15–30 kaçınılmaz. Yüksek AI COGS'lu B2B SaaS'ta marjı yiyebilir. Öneri: web-first satış, mobilde satın alma yok (Apple 3.1.3(a)).
- **X API:** link içeren gönderi $0.20 → "günlük X gönderisi + UTM" tenant başına ayda ~$6. Kredi puan tablosu revize edilmeli.
- **EU AI Act Md. 50 (K3):** 2 Ağu 2026'dan itibaren makine-okunur işaretleme. C2PA **FFmpeg yeniden kodlamada silinir** → render worker'ı manifest'i yeniden iliştirmeli.
- **Meta AI etiketi:** Tem 2026'dan beri otomatik; beyan edilmemiş AI içeriği reklam reddi gerekçesi. `ad_creatives`/`campaign_blueprint`/`publishing_jobs`'a disclosure alanı gerekiyor.
- **KVKK:** yüz/ses içeren medya + yurt dışı AI sağlayıcısı → standart sözleşme + 5 iş günü Kurul bildirimi. Sağlayıcı ekleme süreci hukuki checklist'e bağlanmalı.

## Yöntem notu

Bulgular 2026-07-30'da canlı web araştırmasıyla doğrulandı (paket depoları, resmî platform dokümanları, mevzuat kaynakları). Ticari karara esas alınacak fiyat/limit satırları resmî sağlayıcı dokümanından teyit edilmelidir; ikincil kaynaklardan gelenler [99-external-platform-facts.md](../product/requirements/99-external-platform-facts.md) içinde bu notla işaretlidir.
