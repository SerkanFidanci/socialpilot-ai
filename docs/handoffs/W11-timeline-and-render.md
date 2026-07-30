# W11 — Phase 2A: Timeline şeması + `RenderPort` + AI'sız gerçek render

**Dal:** `slice/2a-timeline-render` · **Base:** `main` · **Migration slotu:** W10'dan sonra (`0012`)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Plan:** [Phase 2 planı](../plans/active/phase-2-content-generation.md) — bu, fazın ilk slice'ı
**Neden bu iş:** Ürün bugüne kadar medya yükleyip analiz edebiliyor ve markayı tanıyor ama **tek bir içerik üretemiyor.** Bu slice o eşiği geçer ve bunu **sıfır AI maliyetiyle** yapar: mevcut analiz edilmiş sahnelerden iki kesit + altyazı + logo → oynatılabilir çıktı + ön izleme. AI olmadığı için render yolu izole doğrulanır; Phase 2'nin geri kalanı bunun üstüne oturur.

Ayrıca fazın iki mimari kararını **somutlaştırır** (kararlar verildi, yeniden tartışılmaz — bkz. plan §2).

## Okunacaklar

Router: [`docs/index.md`](../index.md) → "Yeni modül" satırı. Asgari set:

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/plans/active/phase-2-content-generation.md`](../plans/active/phase-2-content-generation.md) — **§2 girişte alınmış kararlar**
3. [`docs/product/requirements/40b-scenario-render-lifecycle.md`](../product/requirements/40b-scenario-render-lifecycle.md) — **§18.2 timeline şeması, §18.3 doğrulama, §19 render altyapısı**
4. [`docs/adr/ADR-004-provider-adapter-pattern.md`](../adr/ADR-004-provider-adapter-pattern.md) — port deseni
5. [`docs/adr/ADR-013-single-server-deployment-topology.md`](../adr/ADR-013-single-server-deployment-topology.md) — kaynak bütçesi; render bunu aşmayacak
6. `services/api/app/modules/media/CLAUDE.md`, `services/api/app/modules/brands/CLAUDE.md`, `services/api/app/worker/CLAUDE.md`
7. [`99-external-platform-facts.md`](../product/requirements/99-external-platform-facts.md) — **Meta AI etiketi** ve **C2PA kırılganlığı** satırları

## Kapsam

### 1. Timeline şeması (PRD §18.2) — kalıcı ve doğrulanabilir

`content_timelines` kaydı: canvas (genişlik/yükseklik/fps/süre), video track'leri ve clip'ler (`asset_id`, `source_start_ms`, `source_end_ms`, `timeline_start_ms`, `crop_mode`, geçiş), audio track'leri (kazanç, ducking), overlay'ler, caption ayarı. PRD §18.2 şemasına **sadık kal**; alan adı uydurmadan önce oraya bak.

**Render öncesi doğrulama (§18.3) bu slice'ta zorunlu:** süre taşması, asset erişimi, kesit zaman aralığının kaynak süreye sığması, aspect ratio, minimum çözünürlük, metin safe-area, yasak kelime, duplicate clip. Doğrulama **deterministik koddur**; başarısızlıkta dokümante hata kodu döner ve render başlamaz.

### 2. Parametrik düzenleme veri modeli (K4 kararı)

Kullanıcı serbest x/y vermez. Bu slice **şekli** kurar, UI'ı kurmaz:

- Overlay konumu **9'lu ızgara çapası** (enum) + safe-area farkındalığı; ham koordinat alanı **yok**.
- Metin içeriği kaynağı ayrımı: `literal` (yasak-kelime doğrulamalı) vs **`verified_field`** (yalnızca `product_prices` / `campaign_offers` / `approved_ctas`'tan çözülür).
- Stil `style_id` token'ı; serbest font/renk değeri değil.
- Zamanlama segment sınırına snap.
- Timeline'a **patch** uygulayan bir yol: patch → yeniden doğrula → yeniden render. Bu slice patch uygulamasını **veri seviyesinde** kurar; onay/revizyon akışı 2F'nin.

**ADR yaz:** parametrik düzenleme modeli — neden serbest düzenleme değil, hangi doğrulamalar bu sayede zorlanabiliyor, hangi kaçış kapısı ileride açılabilir.

### 3. `RenderPort` (K5 gereği) + FFmpeg adapter

- Kabiliyet portu: girdi doğrulanmış timeline + hedef profil (§19.2: `instagram_reels_1080x1920` vb.), çıktı render edilmiş obje + teknik özet.
- **FFmpeg bir adapter'dır**, port'un kendisi değil. Yönetilen render servisi ikinci adapter olarak eklenebilecek şekilde bırak (plan §2). FFmpeg çağrıları port'un arkasında kalır; domain'e `ffmpeg` sözcüğü sızmaz.
- Kaynak referansları **kısa ömürlü** — W09'un materializer'ını kullan, ikinci bir indirme yolu yazma.
- Worker izolasyonu (§19.3): ayrı süreç, timeout, **W07'nin scratch guard'ı ve `os.nice` düşük önceliği korunur**, kısmi çıktı silinir, dead-letter yolu var.
- Konteynerde FFmpeg **7.1.5**, `libass`/`freetype`/`harfbuzz`/`fribidi` derli; `drawtext`/`subtitles`/`ass`/`overlay` mevcut. **Türkçe glif render'ı doğrulandı** (DejaVu). Marka fontu **yok** — bu slice DejaVu ile çalışır ve marka fontu ihtiyacını rapora yazar (lisans gerektirir).

**ADR yaz:** `RenderPort` ve render adapter sınırı.

### 4. AI'sız gerçek render (bu slice'ın kanıtı)

Seed'lenmiş/analiz edilmiş bir asset'in **gerçek** sahnelerinden iki kesit + altyazı + logo overlay → oynatılabilir MP4 + ön izleme (§15.5 proxy profili mantığı) + thumbnail. **Hiçbir AI çağrısı yok.** Çıktı object storage'a yazılır, `render_outputs` kaydı oluşur.

### 5. Disclosure ve provenance alanları (plan §2)

- **AI disclosure state** alanı: render çıktısının AI üretimi/manipülasyonu içerip içermediği. Gerekçe: Meta Temmuz 2026'dan beri FB/IG reklamlarında beyan zorunlu ve beyan edilmemiş AI içeriği **reklam reddi gerekçesi** — TR'de de geçerli. Bu slice'ın çıktısı AI içermiyor (`none`), ama alan ve akış **şimdi** kurulur.
- **Provenance kancası:** C2PA manifest'i yeniden kodlamada **silinir**. Bu slice manifest yazmaz ama render çıktısında provenance alanını ve "render sonrası yeniden iliştirme" kancasının yerini bırakır. Katılık K3 ile ölçeklenir.

## Kapsam dışı (dokunma)

- **AI'ın her türü:** senaryo (2B), TTS (2C), VLM. Hiçbir sağlayıcı çağrısı yok.
- **QC (2D), yaşam döngüsü/entitlement (2E), onay/revizyon akışı (2F), planlayıcı (2G).** Timeline patch'i veri seviyesinde kurulur; onay akışı 2F'nin.
- **Yayınlama.** Public URL / container polling Phase 4.
- **Gerçek C2PA manifest yazımı** — kanca bırakılır, imzalama ayrı iş (sertifika gerektirir).
- **Marka fontu ekleme** (lisans kararı gerekiyor) — DejaVu ile çalış, ihtiyacı bildir.
- `compose.yaml` → W06. `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.
- **Migration:** slot W10'da. W10 merge edilmeden migration ekleme; edilmişse `0012` senin.

## Dokunulacak dosyalar (ilan)

```
services/api/app/modules/content/                  (yeni modül: domain, models, repository, service, timeline doğrulama, CLAUDE.md)
services/api/app/modules/content/render.py          (RenderPort tanımı — port domain'de, adapter değil)
services/api/app/infrastructure/render/ffmpeg.py    (yeni — FFmpeg adapter)
services/api/app/infrastructure/render/__init__.py  (create_render fabrikası, create_storage deseni)
services/api/app/api/routes/content.py              (yeni)
services/api/app/api/routes/__init__.py             (bir import + bir satır)
services/api/app/core/config.py                     (render ayarları — sahibi artık serbest)
services/api/app/worker/tasks.py + composition.py   (render job'ı; W07'nin scratch guard'ı korunur)
services/api/migrations/versions/0012_*.py          (W10 sonrası)
services/api/tests/unit/ + tests/integration/
docs/architecture/content-render.md                 (yeni)
docs/adr/ADR-XXX-parametric-editing-model.md        (yeni)
docs/adr/ADR-XXX-render-port.md                     (yeni)
```

## Kabul kriterleri

1. Migration `0012` up → down → up; tek head.
2. **Gerçek render:** analiz edilmiş bir asset'in iki gerçek sahnesinden altyazılı + logolu oynatılabilir MP4 üretiliyor; `ffprobe` ile süre/çözünürlük/codec doğrulanıyor; `render_outputs` kaydı ve ön izleme var. **Hiçbir AI çağrısı yapılmıyor** (test bunu kanıtlıyor).
3. **Türkçe metin doğru render ediliyor:** `ığşçöüİĞŞÇÖÜ` içeren overlay çıktıda bozulmadan görünüyor (kutu/soru işareti yok) — piksel değil, en azından render'ın hata vermediği + font çözümlemesinin başarılı olduğu doğrulanıyor.
4. **Timeline doğrulaması (§18.3) reddediyor:** kaynak süreyi aşan kesit, safe-area dışına düşen metin, yasak kelime içeren `literal` metin, duplicate clip — her biri dokümante hata koduyla ve **render başlamadan** reddediliyor.
5. **`verified_field` uydurulamıyor:** fiyat/tarih/CTA yalnızca `product_prices`/`campaign_offers`/`approved_ctas`'tan çözülüyor; olmayan referans hata veriyor; serbest metin bu alanlara yazılamıyor (test var).
6. Overlay konumu yalnızca 9'lu ızgara çapası kabul ediyor; ham koordinat reddediliyor.
7. **Timeline patch'i:** bir metin ve bir çapa değişikliği uygulanıp yeniden render ediliyor; doğrulama yeniden koşuyor; **yeni bir hak tüketilmiyor** (entitlement 2E'de bağlanacak, bu slice'ta patch'in hak tüketmediğini yapısal olarak gösterir).
8. `RenderPort` arkasında FFmpeg **tek adapter**; domain katmanında `ffmpeg`/`subprocess` geçmiyor (`git diff` ile kanıtla). Fabrika `production`'da fake render'ı reddediyor.
9. Worker izolasyonu: timeout, kısmi çıktı silme, W07 scratch guard'ının hâlâ etkili olduğu, dead-letter yolu — testli.
10. Disclosure state ve provenance alanları şemada var; bu slice `none`/boş üretiyor ve bunu test ediyor.
11. Tenant izolasyonu: başka tenant'ın asset'i timeline'a konamıyor; render çağrısı tenant doğrulaması olmadan yapılmıyor.
12. İmzalı URL / credential log'a, audit'e, hata gövdesine, **span'lere** sızmıyor (W01 sentinel deseni + W05 telemetri redaksiyonu).
13. `make verify` yeşil; test sayısı azalmıyor (şu an 392); kontrat drift yok.
14. İki ADR yazıldı (`ADR-XXX`, indekse eklenmedi, raporda bildirildi); `content` modülünün `CLAUDE.md`'si ≤40 satır.

## ADR numara kuralı

Numarayı **sen seçmiyorsun.** İki dosyayı da `ADR-XXX-<konu>.md` adıyla yaz, başlıklarda `ADR-XXX` bırak, raporda bildir. PM merge sırasında numaralandırır.

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum doldurur — özellikle: kaynak süresini aşan kesit, safe-area sınırı, `verified_field` yerine serbest metin sokma denemesi, başka tenant'ın asset'i, patch sonrası doğrulamanın atlanması)_
