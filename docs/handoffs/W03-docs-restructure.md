# W03 — Doküman yapısı ve navigasyon katmanı

**Dal:** `slice/doc-restructure` · **Base:** `main` · **Migration slotu:** yok · **Kod dokunuşu:** yok
**Durum:** hazır, tetiklenmedi
**Neden bu iş:** `AGENTS.md` her oturuma `docs/product/product-requirements.md`'yi okutuyor: **81 KB ≈ 22.000 token**. Aktif plan dosyası 30 KB'a (≈8.000 token) şişmiş çünkü doğrulama kayıtları plan içinde birikiyor. `docs/generated/openapi.json` 86 KB (≈23.000 token). Sonuç: tipik bir oturum **tek satır iş yapmadan ~34.000 token** harcıyor ve bu her fazda büyüyor. Ayrıca dizin bazlı `CLAUDE.md` mekanizması hiç kullanılmamış, yani modül sınırlarını öğrenmenin tek yolu dosyaları açmak.

**Hedef:** modül içi bugfix ≈ 2k, mevcut modülde yeni özellik ≈ 6k, yeni modül/mimari ≈ 10k token.

## Değişmezler (ihlal = iş reddedilir)

1. **Gereksinim metni yeniden yazılmaz.** Bölümler **birebir** taşınır. Özetleme, kısaltma, "iyileştirme", terim değiştirme yok.
2. **Bölüm numaraları korunur.** PRD'deki `§12.4`, taşındığı dosyada da `§12.4` olarak kalır; mevcut referanslar kırılmaz.
3. **Her bölüm tam olarak bir hedef dosyaya gider.** Ne kaybolan ne çift yazılan bölüm olur.
4. **Kapsama tablosu üretilir ve doğrulanır:** PRD'nin §0–§50 arası her bölümü → hedef dosya eşlemesi. Eksik/çift varsa iş bitmemiştir.
5. Türkçe kalır.

## Kapsam

### 1. PRD'yi domain dosyalarına böl

`docs/product/requirements/` altına, her dosya **≤400 satır**:

```
00-vision-principles.md          §0 belge talimatı, §1 vizyon, §2 prensipler
01-glossary.md                   §5 domain sözlüğü
05-scope-roadmap.md              §3 kapsam, §44 fazlar, §45 kabul kriterleri
10-identity-tenancy.md           §4 roller/yetkiler
15-mobile-experience.md          §9 mobil bilgi mimarisi, §10 onboarding
20-brand-catalog.md              §11 işletme/marka profili, ürün, kampanya
30-media-analysis.md             §15 yükleme altyapısı, §16 analiz hattı
35-ai-routing-cost.md            §17 model yönlendirme, §39 maliyet kontrolü
40-content-generation.md         §13 planlayıcı, §14 senaryolar, §18 senaryo/timeline, §19 render, §20 yaşam döngüsü, §21 onay
50-subscription-entitlement.md   §12 abonelik, §32 faturalandırma/store
60-publishing.md                 §22 hesap bağlantıları, §23 yayınlama motoru
70-advertising.md                §24 reklam otomasyonu
80-analytics-learning.md         §25 performans ve analitik, §31 bildirimler
85-orchestration-events.md       §26 n8n sınırı, §27 event-driven tasarım
88-operations-console.md         §36 operasyon paneli
90-data-api-contracts.md         §28 veritabanı, §29 API, §30 hata formatı
92-security-privacy.md           §33 güvenlik, §34 KVKK/gizlilik, §35 içerik güvenliği/telif
95-observability.md              §37 gözlemlenebilirlik, §38 ölçekleme
96-stack-and-topology.md         §6 sistem mimarisi, §7 teknoloji yığını, §8 depo yapısı, §42 env, §43 feature flag
97-engineering-standards.md      §40 test stratejisi, §41 CI/CD, §46 uygulama kuralları
98-risks.md                      §48 risk listesi, §47 ADR listesi (statüsü: tarihsel öneri)
99-external-platform-facts.md    ZATEN VAR — dokunma, yalnızca §49'u buraya taşıma NOTU ekle
```

§49 (resmî platform referansları) `99-external-platform-facts.md`'de güncel karşılığıyla zaten kayıtlı; PRD'deki §49 bloğu oraya **taşınır** ve tarih notu düşülür.

Bölüm bir dosyayı 400 satırı aşıracaksa aynı numarayı koruyarak `40a-`, `40b-` şeklinde böl ve indekse ikisini de yaz.

### 2. `docs/product/product-requirements.md` → indeks

Eski dosya **silinmez**, ~60 satırlık indekse dönüşür: bölüm numarası → hedef dosya tablosu + "bu dosya bütün olarak okunmaz" uyarısı. Böylece mevcut tüm referanslar ve `AGENTS.md`'nin zorunlu okuması ucuzlar.

### 3. Router `docs/index.md`

Mevcut liste indeksi, **görev tipi → okunacak dosyalar** tablosuna dönüşür. En az şu satırlar:

| Yapılan iş | Okunacaklar | ~token |
|---|---|---|
| Modül içi bugfix | `STATUS.md` + modül `CLAUDE.md` + hedef dosya | ~2k |
| Mevcut modülde yeni özellik | + ilgili `requirements/` dosyası + 1 mimari doküman | ~6k |
| Yeni modül | + ilgili ADR'ler + aktif plan | ~10k |
| Mimari değişiklik | `STATUS.md` + `architecture/` + ilgili ADR'ler | ~8k |
| Yeni dış sağlayıcı entegrasyonu | + `99-external-platform-facts.md` + `35-ai-routing-cost.md` | ~8k |
| Güvenlik/uyum işi | + `92-security-privacy.md` | ~6k |

Ayrıca "asla bütün olarak okunmayacaklar" listesi: `openapi.json`, `product-requirements.md`, `plans/completed/**`.

### 4. Modül haritaları (`CLAUDE.md`) — en yüksek kazanç

Şu dizinlerin her birine **≤40 satır** `CLAUDE.md`:

```
services/api/app/modules/identity/     businesses/     media/     operations/
services/api/app/core/                 infrastructure/            worker/
apps/mobile/
```

Her biri **tam olarak** şunları içerir, fazlasını değil:
- Modülün sahip olduğu şey (1–2 cümle) ve **sahip olmadığı** şey.
- Değişmezleri (örn. "her sorgu `business_id` ister", "port'a provider SDK tipi geçmez").
- **Her dosya için bir satır** açıklama (media modülünde 11 dosya var: `ingest`, `technical`, `scene_speech`, `video_understanding`, `video_understanding_service`, `processing_summary`, `service`, `repository`, `models`, `storage`).
- Geçerli `requirements/` dosyası + ADR'ler.
- Testlerinin yolu.

`services/api/app/modules/README.md` varsa içeriği bu dosyalara dağıtılır.

### 5. `docs/api/endpoints.md`

Endpoint başına bir satır tablo (metot, yol, amaç, yetki, idempotency gerekir mi). `docs/generated/openapi.json`'dan üretilir; üreten script `services/api/scripts/` altına eklenir ve `make generate-docs`'a bağlanır. `openapi.json`'un başına "bu dosya okunmaz, `docs/api/endpoints.md` kullan" notu düşülemiyorsa (generated), not `docs/index.md`'de yer alır.

### 6. Plan / doğrulama ayrımı

- `docs/plans/active/<slice>.md` **≤150 satır**, yalnızca açık slice.
- Doğrulama kayıtları `docs/plans/completed/<faz>/verification.md`'ye taşınır. `phase-1-content-pipeline.md` (30 KB) ve `phase-0-foundation.md` (21 KB) bu kurala göre ayrıştırılır.
- Kural `docs/handoffs/README.md`'ye zaten yazılı; plan şablonu buna uyar.

### 7. `AGENTS.md` + `CLAUDE.md` güncellemesi

"Required reading order" bloğu router'a işaret edecek şekilde değiştirilir: artık PRD'nin tamamı değil, **görev tipine göre** okuma. Ayrıca şu kurallar eklenir:
- Bağımlılık sürümleri yazıldığı anda paket deposundan doğrulanır; lockfile zorunludur.
- `openapi.json` bütün olarak okunmaz.
- Dış platform sürümleri/fiyatları `99-external-platform-facts.md`'den okunur, hafızadan yazılmaz.

### 8. ADR ve doküman indeksleri (bu WO'nun tekel sahipliği)

`docs/index.md` ve `docs/adr/README.md`'nin **tek sahibi sensin**. W01 ve W02 paralel çalışırken yeni ADR dosyaları (ADR-008, ADR-009, ADR-010) üretiyor ama indekslere dokunmuyorlar. Merge sırasında `docs/adr/` dizinini tara ve **o an var olan** tüm ADR'leri (ADR-001…ADR-010) hem router'a hem ADR kataloğuna bağla. Var olmayan ADR için satır yazma.

### 9. `docs/reviews/` ve `docs/handoffs/` router'a bağlanır

`docs/reviews/` zaten var ve içinde `2026-07-30-tech-methodology.md` bulunuyor; `README.md`'sini ekle ("tarih-konu" adlandırma kuralı). İçeriğini PM doldurur, sen yeniden yazmazsın.

Router'da (`docs/index.md`) şu üçü mutlaka linkli olsun: `STATUS.md`, `handoffs/README.md` + `handoffs/PM-NOTES.md`, `reviews/`. Bir oturumun "proje nerede kaldı" ve "nasıl çalışıyoruz" sorularına tek tıkla ulaşması gerekiyor.

## Kapsam dışı (dokunma)

- **Hiçbir `.py`, `.dart`, `.yaml`, `Dockerfile`, `Makefile`** — `CLAUDE.md` dosyaları ve `scripts/` altındaki endpoint üreteci istisna.
- `docs/architecture/media-upload.md` (W01'in), `docs/runbooks/local-development.md` (W02'nin).
- `docs/STATUS.md` (PM'in) — yalnızca WO durum satırını güncellersin.
- `docs/product/requirements/99-external-platform-facts.md` içeriği (PM'in) — yalnızca §49 taşıması.
- Mevcut `docs/architecture/*.md` ve `docs/adr/*.md` içerikleri yeniden yazılmaz; yalnızca router'dan linklenir.

## Kabul kriterleri

1. Kapsama tablosu var: PRD §0–§50 arası her bölüm tam olarak bir hedef dosyada. Eksik/çift **yok**.
2. `git show main:docs/product/product-requirements.md` ile yeni `requirements/**` dosyalarının birleşimi arasında **anlam kaybı yok**; taşınan bloklar birebir. Rastgele seçilmiş 5 bölüm için bunu raporda kanıtla (satır sayısı + ilk/son satır karşılaştırması).
3. `requirements/` altındaki her dosya ≤400 satır; hiçbiri 400'ü aşmıyor.
4. `docs/product/product-requirements.md` ≤80 satır ve yalnızca indeks.
5. `docs/index.md` görev tipi → okuma tablosu içeriyor ve "okunmayacaklar" listesi var.
6. Listelenen 8 dizinin her birinde ≤40 satırlık `CLAUDE.md` var; media modülünün 11 dosyası tek tek satırlanmış.
7. `docs/api/endpoints.md` üretiliyor ve `make generate-docs` onu da güncelliyor; `make check-openapi` hâlâ çalışıyor.
8. `docs/plans/active/` altındaki dosya ≤150 satır; doğrulama kayıtları `completed/` altına taşınmış.
9. `AGENTS.md` ve `CLAUDE.md` yeni okuma protokolünü yazıyor.
10. Tüm iç linkler çalışıyor (kırık link yok). `make verify` etkilenmemiş (kod değişmedi, yeşil kalır).
11. Raporda **ölçüm** var: yeni okuma setleriyle 3 örnek görev tipi için gerçek token/byte hesabı.

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum doldurur — özellikle: kapsama tablosunun gerçekten tam olduğunu bağımsız doğrula)_
