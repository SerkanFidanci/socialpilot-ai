# AI Destekli Otonom Sosyal Medya ve Reklam Yönetim Platformu

**Belge türü:** Ürün Gereksinimleri + Teknik Tasarım + Uygulama Planı  
**Hedef okuyucu:** Codex, Claude Code, yazılım mimarı, backend/mobile geliştirici, DevOps ve ürün ekibi  
**Belge tarihi:** 27 Temmuz 2026  
**Belge sürümü:** 1.0  
**Çalışma adı:** `SocialPilot AI`  
**Ana dil:** Türkçe  
**Hedef istemciler:** iOS ve Android mobil uygulama, operasyon ekibi için web yönetim paneli

---

> ## Bu dosya bütün olarak OKUNMAZ
>
> Bu bir **indekstir**, gereksinim metni değildir. Gereksinim bölümleri
> [`requirements/`](requirements/) altındaki domain dosyalarına **birebir** taşındı; bölüm
> numaraları (`§12.4` gibi) korundu, dolayısıyla mevcut tüm referanslar geçerli.
>
> İhtiyacın olan bölümü aşağıdaki tablodan bul ve **yalnızca o dosyayı** oku. Hangi işte
> hangi dosyaların gerektiğini görev tipine göre [`docs/index.md`](../index.md) söyler.

## Bölüm → dosya

| PRD bölümleri | Dosya | Konu |
|---|---|---|
| §0, §1, §2, §50 | [00-vision-principles.md](requirements/00-vision-principles.md) | Belge talimatı, ürün vizyonu, prensipler, son karar özeti ve kritik mimari sınırlar |
| §5 | [01-glossary.md](requirements/01-glossary.md) | Domain sözlüğü |
| §3, §44, §45 | [05-scope-roadmap.md](requirements/05-scope-roadmap.md) | Kapsam, uygulama aşamaları, ilk üretim kabul kriterleri |
| §4 | [10-identity-tenancy.md](requirements/10-identity-tenancy.md) | Kullanıcı tipleri, roller, yetkiler, iç operasyon rolleri |
| §9, §10 | [15-mobile-experience.md](requirements/15-mobile-experience.md) | Mobil bilgi mimarisi, onboarding, marka sihirbazı |
| §11 | [20-brand-catalog.md](requirements/20-brand-catalog.md) | İşletme/marka profili, ürün-hizmet kaydı, kampanya kaydı |
| §15, §16 | [30-media-analysis.md](requirements/30-media-analysis.md) | Medya yükleme altyapısı, video analiz hattı, ASR, VLM, sahne kütüphanesi |
| §17, §39 | [35-ai-routing-cost.md](requirements/35-ai-routing-cost.md) | AI model yönlendirme katmanı, provider interface, maliyet kontrolü |
| §13, §14 | [40a-content-planning-scenarios.md](requirements/40a-content-planning-scenarios.md) | İçerik planlayıcı, content obligation, 16 içerik senaryosu |
| §18, §19, §20, §21 | [40b-scenario-render-lifecycle.md](requirements/40b-scenario-render-lifecycle.md) | Senaryo/timeline contract, render altyapısı, proje yaşam döngüsü, onay sistemi |
| §12, §32 | [50-subscription-entitlement.md](requirements/50-subscription-entitlement.md) | Esnek abonelik, kredi/hak motoru, store doğrulama ve faturalandırma |
| §22, §23 | [60-publishing.md](requirements/60-publishing.md) | Sosyal hesap bağlantıları, capability matrisi, yayınlama motoru |
| §24 | [70-advertising.md](requirements/70-advertising.md) | Reklam otomasyonu, campaign blueprint, guardrail motoru, harcama defteri |
| §25, §31 | [80-analytics-learning.md](requirements/80-analytics-learning.md) | Organik/reklam metrikleri, öğrenme döngüsü, bildirimler |
| §26, §27 | [85-orchestration-events.md](requirements/85-orchestration-events.md) | n8n kullanım sınırı, domain event'ler, transactional outbox, idempotency |
| §36 | [88-operations-console.md](requirements/88-operations-console.md) | Operasyon yönetim paneli |
| §28 | [90a-database-design.md](requirements/90a-database-design.md) | Veritabanı tasarımı, kritik indexler, row-level güvenlik |
| §29, §30 | [90b-api-error-contracts.md](requirements/90b-api-error-contracts.md) | API tasarımı, endpoint listesi, hata formatı |
| §33, §34, §35 | [92-security-privacy.md](requirements/92-security-privacy.md) | Güvenlik, KVKK/gizlilik, içerik güvenliği ve telif |
| §37, §38 | [95-observability.md](requirements/95-observability.md) | Log, metric, trace, alert, ölçekleme, backpressure |
| §6, §7, §8, §42, §43 | [96-stack-and-topology.md](requirements/96-stack-and-topology.md) | Sistem mimarisi, teknoloji yığını, depo yapısı, env değişkenleri, feature flag'ler |
| §40, §41, §46 | [97-engineering-standards.md](requirements/97-engineering-standards.md) | Test stratejisi, CI/CD, zorunlu uygulama kuralları ve kalite kapıları |
| §48, §47 | [98-risks.md](requirements/98-risks.md) | Risk listesi, PRD'nin tarihsel ADR öneri listesi |
| §49 | [99-external-platform-facts.md](requirements/99-external-platform-facts.md) | Resmî platform referansları — **güncel gerçekler bu dosyanın üst tablolarındadır** |

## Uyarılar

- **Sürüm, fiyat, limit ve mevzuat tarihi hafızadan yazılmaz.** Güncel değerler yalnızca
  [99-external-platform-facts.md](requirements/99-external-platform-facts.md)'te; PRD §49
  27 Temmuz 2026 tarihli **tarihsel** kayıttır.
- **ADR kataloğu bu belge değildir.** §47'deki liste bir öneri listesidir; gerçek ADR
  kimlikleri [`docs/adr/`](../adr/) altındaki dosya adlarıdır. Bkz.
  [ADR kataloğu](../adr/README.md).
- Bir bölüm bir dosyayı 400 satırı aşacak hale getirirse aynı numara korunarak `a`/`b`
  olarak bölünür (`40a`/`40b`, `90a`/`90b`) ve iki dosya da bu tabloya yazılır.
