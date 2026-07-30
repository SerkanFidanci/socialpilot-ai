# Phase 2 — İçerik Üretimi Planı

**Status:** Active · **Yazan:** PM/mimar oturumu · **Tarih:** 2026-07-30
**Önkoşul:** Phase 1 kapandı — `main` `28e356a`, Alembic head `0010_brand_catalog`, 392 test.
**Kaynak gereksinim:** PRD §13, §14, §18, §19, §20, §21 → [40a](../../product/requirements/40a-content-planning-scenarios.md), [40b](../../product/requirements/40b-scenario-render-lifecycle.md)

## 1. Amaç

Analiz edilmiş sahne kütüphanesi ve doğrulanmış marka/ürün verisinden **onaylanabilir bir içerik** üretmek. Faz, `content_obligation` veya kullanıcı isteğiyle başlar; ön izlemesi hazır, revize edilebilir ve onaya sunulabilir bir `content_version` ile biter. Yayınlama Phase 4.

Bugüne kadar her şey iskeleydi: medya yükleyip analiz edebiliyoruz ve markayı tanıyoruz, ama **tek bir içerik üretemiyoruz.** Ürünün varlık sebebi bu fazda başlıyor.

## 2. Girişte alınmış kararlar (slice'lar bunları yeniden tartışmaz)

**K4 — Parametrik düzenleme.** Kullanıcı piksel sürüklemez; zaten JSON olan timeline'ın parametrelerini değiştirir: metin içeriği (yasak-kelime doğrulamalı), 9'lu ızgara üzerinde hazır konum çapaları, stil token'ı, marka onaylı sticker kütüphanesinden seçim, segment sınırına snap. **Serbest x/y ve kare kare montaj yok** (PRD §3.3 zaten dışlıyor). Gerekçe: §18.3 doğrulaması (safe-area, yasak kelime, logo kuralı) ancak sınırlı bir düzenleme uzayında zorlanabilir; ve parametrik düzenleme **hiç AI çağrısı yapmaz**, yani ucuzdur. ADR'ı slice 2A yazar.

**Saf yeniden render yeni hak tüketmez.** Revizyon kotasından düşer (§12.8: hak ön izleme başarısında tüketilir; parametrik düzenleme yeni bir üretim değildir).

**RenderPort birinci sınıf kabiliyet portu** (K5 gereği). FFmpeg **bir** adapter'dır; yönetilen render servisi diğeri. Port yoksa dağıtım seçeneği kaybedilir. ADR'ı slice 2A yazar.

**Doğrulanmış alan bindirmesi deterministiktir.** Fiyat, tarih, kampanya metni, CTA **yalnızca** `product_prices` / `campaign_offers` / `approved_ctas`'tan gelir; modelin ürettiği metne bu değerler **kod tarafından** yerleştirilir. Model bu alanları asla yazmaz (PRD §2.2, §11.3). W04 veriyi kurdu; bu faz onu deterministik bir birleştirme adımıyla tüketir.

**AI disclosure alanı ilk günden var.** Gerekçe düzeltmesi: bunu daha önce K3'e (EU pazarı) bağlamıştım — **yanlıştı.** Meta, Temmuz 2026'dan beri FB/IG reklamlarında AI beyanını zorunlu tutuyor ve beyan edilmemiş AI içeriği reklam reddi gerekçesi; bu **Türkiye'de de** geçerli, çünkü platform politikası. Yani alan TR-only kapsamda da gerekli. EU (K3) yalnızca **işaretlemenin katılığını** artırır, alanın varlığını değil.

**C2PA/provenance kancası render çıktısında.** C2PA manifest'i yeniden kodlamada silinir ve bizim hattımız FFmpeg'den geçiyor → render worker'ı manifest'i **yeniden iliştirmek** zorunda. Alan 2A'da açılır; katılığı K3 ile ölçeklenir.

**Gerçek ücretli sağlayıcı bu fazda bağlanmaz.** Phase 1 deseni tekrarlanır: kabiliyet portları + fake adapter. Faz tamamen fake sağlayıcılarla inşa edilip doğrulanabilir; gerçek sağlayıcı W08 benchmark'ı + route politikası ADR'ından sonra takılır. **Sonuç: sağlayıcı kararı Phase 2'yi bloke etmiyor.**

## 3. Slice sırası

| Slice | Kapsam | Neden bu sırada | Migration |
|---|---|---|---|
| **2A** | Timeline şeması + `RenderPort` + FFmpeg adapter + **AI'sız gerçek render**: mevcut sahnelerden iki kesit + altyazı + logo → oynatılabilir çıktı + preview. Parametrik düzenleme şeklinin veri modeli. Disclosure ve provenance alanları. | **En küçük tam dikey dilim.** Sıfır AI maliyetiyle render yolunu kanıtlar ve iki mimari kararı (K4, RenderPort) somutlaştırır. Sonraki her slice buna oturur. | evet |
| **2B** | Senaryo üretimi: `script_generation` portu, katı JSON şema, doğrulanmış alan bindirmesi, yasak kelime/CTA doğrulaması. Fake sağlayıcı. | Timeline hedefi belli olmadan senaryo üretmek boşa; 2A hedefi tanımlıyor. | olası |
| **2C** | Seslendirme: `tts` portu, `voiceover_assets`, segment süresi çıkarımı, sesin timeline'a hizalanması, müzik ducking. | Senaryo metni olmadan seslendirme olmaz. §14.8: önce senaryo+ses, sonra kesit ataması. | evet |
| **2D** | Otomatik QC (§19.4) + başarısızlık yolları: yeniden render, alternatif sahne, insan incelemesi. Fiyat/tarih kaynağa uyum kontrolü. | Üretim var ama güvenilirliği yok; QC olmadan preview'a güvenilemez. | olası |
| **2E** | `content_projects` / `content_versions` yaşam döngüsü (§20) + preview + **entitlement rezervasyon/tüketim** (§12.8). | Parçalar hazır; durum makinesi onları tek akışa bağlar ve hak tüketimini doğru noktaya koyar. | evet |
| **2F** | Onay + revizyon (§21): onay politikaları, reddetme nedenleri, **parametrik düzenlemenin uygulanması** (timeline patch → yeniden render, yeni hak tüketmeden). | Yaşam döngüsü olmadan onay durumu tutulamaz. | evet |
| **2G** | Planlayıcı: abonelikten `content_obligation` üretimi (§13), içerik karması, sessiz saatler. | Talebi otomatik üretmek en sona kalır; elle tetikleme ile faz zaten çalışır. | evet |

**Migration slotu her seferinde tek slice'ta.** 2A → 2C → 2E → 2F → 2G sırası slot devrini belirler; 2B ve 2D şema gerektirmiyorsa paralel koşabilir.

## 4. Faz çıkış kriteri

*"Çoklu videodan seslendirmeli profesyonel Reels üretilir"* (PRD §44, Aşama 2) — **ve** üretilen içerik: doğrulanmış fiyat/tarih taşıyor, yasak kelime içermiyor, QC'den geçmiş, ön izlemesi açılıyor, en az bir parametrik revizyon uygulanabiliyor, revizyon yeni hak tüketmiyor, disclosure alanı dolu.

Fake sağlayıcılarla karşılanır. "Gerçek içerikle karşılandı" ayrı bir eşik ve W08 sonrasıdır.

## 5. Bu fazın taşımayacağı riskler

- **Yayınlama yok.** Instagram public-URL çelişkisi ve container polling Phase 4'ün işi; ADR'ı PM kuyruğunda.
- **Gerçek sağlayıcı maliyeti yok.** Fake adapter'lar ücretsiz; faz boyunca AI COGS sıfır.
- **Üretim deploy yok.** W07 dayanıklılığı kurdu, deploy ayrı karar.

## 6. Fazı başlatmadan önce

Hiçbir bekleyen karar Phase 2'yi bloke etmiyor. **K3** (pazar kapsamı) yalnızca provenance işaretlemesinin katılığını etkiliyor ve alan zaten açılıyor; **K1** (faturalandırma) Phase 3 konusu. Yani 2A'nın iş emri yazılabilir.

## 7. Referanslar

[STATUS.md](../../STATUS.md) · [handoffs/README.md](../../handoffs/README.md) · [40a](../../product/requirements/40a-content-planning-scenarios.md) · [40b](../../product/requirements/40b-scenario-render-lifecycle.md) · [50-subscription-entitlement.md](../../product/requirements/50-subscription-entitlement.md) (§12.8 hak tüketimi) · [99-external-platform-facts.md](../../product/requirements/99-external-platform-facts.md) (Meta AI etiketi, C2PA kırılganlığı) · [ADR-004](../../adr/ADR-004-provider-adapter-pattern.md) · [ADR-013](../../adr/ADR-013-single-server-deployment-topology.md)
