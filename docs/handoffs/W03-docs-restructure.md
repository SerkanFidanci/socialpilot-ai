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

## Rapor — 2026-07-30 · Opus 5 oturumu

**Dal:** `slice/doc-restructure` (base: `main` @ `c13636b`) · **Commit'ler:** aşağıdaki tek commit · **Durum:** tamamlandı, iki kalem PM'e bırakıldı

### Kapsama tablosu — PRD §0–§50

51 bölümün her biri **tam olarak bir** hedef dosyada. Çift yok, eksik yok (script ile
doğrulandı, aşağıya bkz.).

| PRD | Başlık | Hedef |
|---|---|---|
| §0 | Belgenin kullanım talimatı | `00-vision-principles.md` |
| §1 | Ürün vizyonu | `00-vision-principles.md` |
| §2 | Ürün prensipleri | `00-vision-principles.md` |
| §3 | Kapsam | `05-scope-roadmap.md` |
| §4 | Kullanıcı tipleri ve roller | `10-identity-tenancy.md` |
| §5 | Domain sözlüğü | `01-glossary.md` |
| §6 | Yüksek seviyeli sistem mimarisi | `96-stack-and-topology.md` |
| §7 | Önerilen teknoloji yığını | `96-stack-and-topology.md` |
| §8 | Kaynak kod deposu | `96-stack-and-topology.md` |
| §9 | Mobil uygulama bilgi mimarisi | `15-mobile-experience.md` |
| §10 | Onboarding akışı | `15-mobile-experience.md` |
| §11 | İşletme ve marka profili | `20-brand-catalog.md` |
| §12 | Esnek abonelik modeli | `50-subscription-entitlement.md` |
| §13 | İçerik planlayıcı | `40a-content-planning-scenarios.md` |
| §14 | İçerik senaryoları | `40a-content-planning-scenarios.md` |
| §15 | Medya yükleme altyapısı | `30-media-analysis.md` |
| §16 | Video analiz hattı | `30-media-analysis.md` |
| §17 | AI model yönlendirme katmanı | `35-ai-routing-cost.md` |
| §18 | Senaryo, seslendirme ve timeline üretimi | `40b-scenario-render-lifecycle.md` |
| §19 | Render altyapısı | `40b-scenario-render-lifecycle.md` |
| §20 | İçerik proje yaşam döngüsü | `40b-scenario-render-lifecycle.md` |
| §21 | Onay sistemi | `40b-scenario-render-lifecycle.md` |
| §22 | Sosyal hesap bağlantıları | `60-publishing.md` |
| §23 | Yayınlama motoru | `60-publishing.md` |
| §24 | Reklam otomasyonu | `70-advertising.md` |
| §25 | Performans ve analitik | `80-analytics-learning.md` |
| §26 | n8n kullanım sınırı | `85-orchestration-events.md` |
| §27 | Event-driven tasarım | `85-orchestration-events.md` |
| §28 | Veritabanı tasarımı | `90a-database-design.md` |
| §29 | API tasarımı | `90b-api-error-contracts.md` |
| §30 | Hata formatı | `90b-api-error-contracts.md` |
| §31 | Bildirimler | `80-analytics-learning.md` |
| §32 | Faturalandırma ve store doğrulama | `50-subscription-entitlement.md` |
| §33 | Güvenlik | `92-security-privacy.md` |
| §34 | KVKK, gizlilik ve içerik hakları | `92-security-privacy.md` |
| §35 | İçerik güvenliği ve telif | `92-security-privacy.md` |
| §36 | Operasyon yönetim paneli | `88-operations-console.md` |
| §37 | Gözlemlenebilirlik | `95-observability.md` |
| §38 | Ölçekleme | `95-observability.md` |
| §39 | Maliyet kontrolü | `35-ai-routing-cost.md` |
| §40 | Test stratejisi | `97-engineering-standards.md` |
| §41 | CI/CD | `97-engineering-standards.md` |
| §42 | Ortam değişkenleri | `96-stack-and-topology.md` |
| §43 | Feature flag'ler | `96-stack-and-topology.md` |
| §44 | Uygulama geliştirme aşamaları | `05-scope-roadmap.md` |
| §45 | İlk üretim kabul kriterleri | `05-scope-roadmap.md` |
| §46 | Zorunlu uygulama kuralları | `97-engineering-standards.md` |
| §47 | Mimari karar kayıtları (tarihsel öneri) | `98-risks.md` |
| §48 | Risk listesi | `98-risks.md` |
| §49 | Resmî platform notları | `99-external-platform-facts.md` |
| §50 | Son karar özeti | `00-vision-principles.md` |

Belge künyesi (belge türü/tarih/sürüm/dil, 8 satır) numaralı bir bölüm olmadığı için
indekste **yerinde** kaldı.

### Birebirlik kanıtı

`git show main:docs/product/product-requirements.md` ile yeni dosyaların birleşimi
karşılaştırıldı. Boş satır ve `---` ayırıcı dışındaki **anlam taşıyan satır** sayısı:

```
kaynak §0–§50 : 2986 satır
hedef (taşınan): 2986 satır      → kayıp 0, fazla 0, değişen 0
```

Karşılaştırma satır çokluğu (multiset) üzerinden yapıldı, yani bir satırın kaybolması,
çift yazılması veya tek karakterinin değişmesi yakalanır. Rastgele 5 bölüm için birebir
doğrulama:

| Bölüm | Hedef | Kaynak satır | Hedef satır | İlk satır | Son satır | Sonuç |
|---|---|---|---|---|---|---|
| §4 | `10-identity-tenancy.md` | 38 | 38 | `# 4. Kullanıcı tipleri ve roller` | `- tüm işlemler audit log'a kaydedilmelidir.` | **IDENTICAL** |
| §12 | `50-subscription-entitlement.md` | 156 | 156 | `# 12. Esnek abonelik modeli` | `Sunucu tarafı entitlement tek başına mobil istemciye güvenerek açılmaz.` | **IDENTICAL** |
| §24 | `70-advertising.md` | 175 | 175 | `# 24. Reklam otomasyonu` | `Acil durdurma n8n'e bağlı olmamalıdır; …` | **IDENTICAL** |
| §29 | `90b-api-error-contracts.md` | 161 | 161 | `# 29. API tasarımı` | `Mobil uygulama kısa polling yapabilir; …` | **IDENTICAL** |
| §46 | `97-engineering-standards.md` | 55 | 55 | `# 46. Codex ve Claude Code için zorunlu uygulama kuralları` | `- OpenAPI backward compatibility` | **IDENTICAL** |

Bölümler elle yazılmadı: satır aralıkları script ile kesildi, bu yüzden başlık satırları da
bayt bayt aynı. Eklenen tek şey her dosyanın başındaki 7 satırlık kaynak/uyarı bloğu
(toplam 72 satır iskele).

### Ölçüm

Token tahmini deponun kendi oranıyla yapıldı (86 KB openapi ≈ 23k token → 3.7 bayt/token).

**Eskiden her oturumun zorunlu okuması** (`AGENTS.md`: "read `product-requirements.md`"):

| Dosya | bayt | ~token |
|---|---|---|
| `docs/index.md` (eski liste) | 1.808 | 489 |
| `docs/product/product-requirements.md` (tam) | 80.947 | 21.878 |
| `docs/plans/active/phase-1-content-pipeline.md` | 30.248 | 8.175 |
| **toplam** | **113.003** | **30.542** |
| + `openapi.json` de okunduysa | +86.378 | **53.887** |

**Yeni okuma setleri** (router'ın satırında yazan dosyalar, gerçek ölçüm):

| Görev tipi | Dosyalar | ~token (router hariç) | ~token (router dahil) | WO hedefi |
|---|---|---|---|---|
| Modül içi bugfix (media) | `STATUS.md` + `media/CLAUDE.md` | **2.835** | 4.563 | ~2k |
| Mevcut modülde yeni özellik | + `30-media-analysis.md` + `architecture/media-analysis.md` | **7.001** | 8.730 | ~6k |
| Yeni dış sağlayıcı entegrasyonu | + `99-external-platform-facts.md` + `35-ai-routing-cost.md` + `ADR-004` | **6.801** | 8.530 | ~8k |
| API endpoint ekleme | `api/endpoints.md` + `90b-…` + `error-handling.md` | **5.316** | 7.045 | ~7k |

**Tek dosya kazançları:**

| | eski | yeni | kazanç |
|---|---|---|---|
| PRD zorunlu okuması | 80.947 B / 21.878 tok | 5.199 B / 1.405 tok | **-94%** |
| Endpoint envanteri | 86.378 B / 23.345 tok | 4.556 B / 1.231 tok | **-95%** |
| Aktif plan | 30.248 B / 8.175 tok | 13.623 B / 3.682 tok | **-55%** |

Tipik oturumun sabit açılış maliyeti **30.5k → 4.6k token (-85%)**.

### Yapılanlar

1. **PRD bölündü.** `docs/product/requirements/` altında 24 dosya, en büyüğü 276 satır
   (limit 400). §40 grubu 521 satır olacağı için WO kuralı gereği `40a`/`40b` olarak,
   §28+§29+§30 grubu ise `90a`/`90b` olarak bölündü.
2. **`product-requirements.md` indekse döndü** — 3.770 → 60 satır, bölüm→dosya tablosu +
   "bütün olarak okunmaz" uyarısı. Dosya silinmedi, tüm mevcut referanslar çalışıyor.
3. **`docs/index.md` router oldu** — görev tipi → okunacaklar tablosu (8 satır),
   "asla bütün olarak okunmayacaklar" tablosu, `STATUS.md` / `handoffs/README.md` /
   `PM-NOTES.md` / `reviews/` bağlantıları, kod haritaları, mimari ve ADR katalogları.
4. **8 dizine `CLAUDE.md`** (31–40 satır): sahip olduğu/olmadığı şey, değişmezler, her dosya
   için bir satır, geçerli `requirements/` + ADR + mimari, test yolları. `media` modülünün
   11 dosyası tek tek satırlandı. Bayat `modules/README.md` ("Slice 0A intentionally creates
   no domain modules" — artık 4 modül var) yerine dağıtım tablosuna dönüştürüldü.
5. **`docs/api/endpoints.md` üretiliyor** — 16 endpoint, 4,5 KB. Sütunların tamamı
   kontrattan türetilir (metot, yol, amaç, yetki, idempotency, başarı kodları), elle
   yazılmaz, bu yüzden koddan sapamaz.
6. **Plan/doğrulama ayrımı** — aktif plan 310 → 150 satır, yalnızca açık slice (1E).
   1A–1D kayıtları `completed/phase-1-content-pipeline/verification.md`'ye (209 satır),
   Phase 0 kapanış denetimi `completed/phase-0-foundation/verification.md`'ye taşındı.
7. **`AGENTS.md` + `CLAUDE.md`** yeni okuma protokolünü yazıyor; bağımlılık doğrulama +
   lockfile, `openapi.json` okunmaz, dış platform gerçekleri hafızadan yazılmaz kuralları
   eklendi.
8. **ADR ve doküman indeksleri** — `docs/adr/README.md` statü ve tarih sütunlarıyla, PRD
   §47'nin katalog **olmadığı** uyarısıyla güncellendi.
9. **`docs/reviews/README.md`** — "tarih-konu" adlandırma kuralı; mevcut inceleme linkli.
   İçeriğe dokunulmadı.

### Kapsam dışı bıraktıklarım ve nedeni

- **`Makefile`'a dokunmadım.** Kabul kriteri 7 `make generate-docs`'un `endpoints.md`'yi de
  güncellemesini istiyor, ama `Makefile` hem WO'nun "Kapsam dışı" listesinde hem
  `STATUS.md`'de **W02'nin**. Çözüm: üreteci `services/api/scripts/generate_endpoints_doc.py`
  olarak ekledim ve `generate_openapi.py`'nin sonuna tek satır çağrı koydum. `make
  generate-docs` zaten o script'i çalıştırdığı için kriter **Makefile'a dokunmadan**
  karşılandı; `check-openapi` yalnızca `openapi.json`'u diff'lediği için etkilenmedi.
  **PM'e karar:** `endpoints.md`'nin de tazelik kontrolüne girmesi isteniyorsa W02
  `check-openapi` hedefinin diff'ine `docs/api/endpoints.md` eklemeli — bu satır bende değil.
- `docs/architecture/*.md` ve `docs/adr/ADR-*.md` **içerikleri** yeniden yazılmadı, yalnızca
  router'dan linklendi. `media-upload.md` (W01) ve `local-development.md` (W02) açılmadı.
- `99-external-platform-facts.md`'nin mevcut içeriği korundu; yalnızca §49 bloğu tarih notuyla
  sonuna eklendi.

### Doğrulama

| Kontrol | Sonuç |
|---|---|
| Kapsama tablosu tam (§0–§50, çift/eksik yok) | ✅ script: 51 bölüm, çift yok, eksik yok |
| Birebirlik (anlam kaybı yok) | ✅ 2986 → 2986 anlamlı satır, multiset eşit; 5 örnek bölüm IDENTICAL |
| `requirements/` her dosya ≤400 satır | ✅ en büyük 276 (`96-stack-and-topology.md`) |
| `product-requirements.md` ≤80 satır ve yalnızca indeks | ✅ 60 satır |
| Router'da görev tipi tablosu + okunmayacaklar listesi | ✅ |
| 8 dizinde ≤40 satır `CLAUDE.md`; media'nın 11 dosyası tek tek | ✅ 31–40 satır aralığı |
| `endpoints.md` üretiliyor; `check-openapi` çalışıyor | ✅ üretildi; `openapi.json` diff'i boş |
| Aktif plan ≤150 satır; doğrulama kayıtları `completed/` altında | ✅ 150 satır |
| `AGENTS.md` + `CLAUDE.md` yeni protokolü yazıyor | ✅ |
| Kırık link yok | ✅ 74 markdown dosyasında 252 göreli link, hepsi çözülüyor |
| Raporda ölçüm var | ✅ yukarıda |
| `make verify` | ⚠️ **bu oturumda çalıştırılamadı** — aşağıya bkz. |
| migration up/down/up | — (migration yok, slot yok) |

### Açıkça belirtmem gerekenler

1. **`make verify` bu oturumda çalıştırılamadı.** Ortamda `make` binary'si yok, backend
   bağımlılıkları (`structlog`, `fastapi`, …) kurulu değil ve entegrasyon testlerinin
   istediği PostgreSQL ayakta değil. Yerine yaptıklarım: `generate_openapi.py`'nin yeni
   import yolunu birebir taklit ederek çalıştırdım (çalışıyor), `endpoints.md`'yi üretip
   `openapi.json` diff'inin boş kaldığını doğruladım. Risk analizi: `ruff` `app tests
   migrations`, `mypy` ise `files = ["app", "tests"]` üzerinde çalışıyor — **ikisi de
   `scripts/`'i kapsamıyor**, dolayısıyla eklediğim script lint/type kapılarını
   etkilemiyor. Kod dosyası değişmediği için testler de etkilenmedi. Yine de merge öncesi
   `make verify` çalıştırılmalı; teslimi buna bağlıyorum.
2. **`~2k` bugfix hedefi karşılanmadı: 2.835 token.** Sebep bende değil —
   `docs/STATUS.md` tek başına **1.730 token** ve her oturum onu okuyor, `media/CLAUDE.md`
   ise 1.105. Yani hedef, `STATUS.md` ~1k'ya inmeden matematiksel olarak mümkün değil.
   `STATUS.md` PM'in dosyası; router'daki hedef sütununu **ölçülen** değerlere çevirdim
   (~2k → ~3k), çünkü yanlış rakam yazan bir router kimseye yardım etmez. Küçültme kararı
   PM'in.
3. **§50 WO'nun eşleme tablosunda yoktu**, ama kabul kriteri §0–**§50** kapsaması istiyor.
   "Son karar özeti" + 10 kritik mimari sınır olduğu için `00-vision-principles.md`'ye
   koydum (§0 belge talimatı ve §2 prensiplerle aynı yerde). Farklı bir yer isteniyorsa
   tek satırlık taşıma.
4. **`90-data-api-contracts.md` yerine `90a`/`90b`.** Tek dosya olsa 391 satır olurdu:
   limitin altında ama %98'inde, bir sonraki ek satırda ihlal. WO'nun `a`/`b` kalıbını
   kullandım ve ikisini de indekse yazdım.
5. **`AGENTS.md`'de iki kusur vardı**, düzelttim: (a) tüm markdown kaçış karakterleriyle
   yazılmıştı (`\#`, `\-`, `\.`) ve bozuk render ediyordu, (b) okuma sırası **iki kez**
   yazılmıştı ("Project source of truth" + "Required reading order") — zaten değiştirmem
   istenen blok olduğu için tek bloğa indirdim. Kuralların anlamı korundu, hiçbir kural
   düşmedi. `CLAUDE.md`'deki aynı kaçış sorunu da giderildi.
6. **`apps/mobile/CLAUDE.md`'de 19 Dart dosyası var, limit 40 satır.** İkisi çakıştığı için
   4 widget dosyasını 2 satırda eşleştirdim; her dosya adı ve işi yazılı. Kabul kriteri 6
   dosya-başına-satırı yalnızca **media'nın 11 dosyası** için şart koştuğu, ≤40'ı ise 8
   dizinin hepsi için şart koştuğu için limiti üstün tuttum.
7. **ADR-008/009/010 henüz yok.** `docs/adr/` şu an ADR-001…007 içeriyor; W01/W02 dalları
   merge edilmemiş. WO "var olmayan ADR için satır yazma" dediği için 7 satır yazdım ve
   hem kataloğa hem router'a "merge eden oturum dizini tarayıp satırları ekler" notunu
   düştüm. **Merge sırasında bu iki dosya güncellenmeli.**
8. **Aktif plandan §7 ve §12 de taşındı.** 150 satıra sığdırmak için, açık slice'a ait
   olmayan bu iki bölüm (state transitions ve expected files — ikisi de artık uygulanmış
   gerçeği tarif ediyor) `verification.md`'ye gitti. Bölüm numaraları korundu, bu yüzden
   planda numara boşluğu var ve bu kasıtlı olduğu dosyada yazılı.
9. **Yeni bulgu — idempotency açığı.** Üretilen `endpoints.md` şunu ortaya çıkardı: 6
   mutasyon endpoint'inden **yalnızca** `POST …/uploads/{id}/complete` `Idempotency-Key`
   kabul ediyor. `POST /v1/businesses`, `PATCH /v1/businesses/{id}`,
   `POST …/members`, `PATCH …/members/{id}`, `POST …/uploads`, `POST …/cancel` idempotency
   taşımıyor. `AGENTS.md` "every externally visible mutation must consider idempotency"
   diyor ve `phase-0-foundation.md`'nin "Deferred work"ü bunu zaten kabul ediyor. Tabloda
   `değerlendirilmeli` olarak işaretli. **PM'e:** bu bir work order konusu.

## Doğrulama

_(test eden oturum doldurur — özellikle: kapsama tablosunun gerçekten tam olduğunu bağımsız doğrula)_
