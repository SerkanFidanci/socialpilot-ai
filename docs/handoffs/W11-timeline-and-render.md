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
docs/adr/ADR-015-parametric-editing-model.md        (yeni)
docs/adr/ADR-016-render-port.md                     (yeni)
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

## Rapor — 2026-07-30 · yürüten oturum (Opus 4.8)

**Dal:** `slice/2a-timeline-render` (base `main` @ `e2f3860`) · **Durum:** tamamlandı

### Yapılanlar

- **`modules/content/` yeni modülü.** `timeline.py` (§18.2 kapalı şema, parse + serialize),
  `validation.py` (§18.3 saf kurallar + satır kaydırma), `patch.py` (K4 kapalı operasyon kümesi),
  `render.py` (`RenderPort`, `RenderCapabilities`, `RenderPlan`, §19.2 profilleri, disclosure ve
  provenance durumları), `models.py`, `repository.py`, `service.py`, `render_service.py`,
  `policy.py`, `domain.py`, `CLAUDE.md` (38 satır).
- **`RenderPort` + FFmpeg adapter.** `infrastructure/render/{__init__,ffmpeg,fake}.py`.
  `create_render` fabrikası `create_storage` desenini izliyor, `production`'da `fake` reddediliyor.
- **AI'sız gerçek render.** Analiz edilmiş asset'in iki gerçek kesitinden Türkçe overlay +
  logo + yakılmış altyazılı 1080x1920 MP4 + `preview_540x960` + thumbnail. Çıktı MinIO'ya
  yazılıyor, geri çekilip `ffprobe` ile doğrulanıyor.
- **Migration `0012_content_timeline_render`** — `content_timelines` (revizyon başına satır) +
  `render_outputs`. **Aşağıdaki zincir notuna bakın.**
- **API:** `POST/GET .../content/timelines`, `.../patch`, `POST .../renders`, `GET .../renders/{id}`.
  OpenAPI + `endpoints.md` yeniden üretildi.
- **Worker:** `content.render.drain` task'ı, `WorkerContext.content_render_service`, beat kaydı.
- İki ADR (`ADR-016-render-port.md`, `ADR-015-parametric-editing-model.md`),
  `docs/architecture/content-render.md`.

### Kapsam dışı bıraktıklarım ve nedeni

- **Hiçbir AI çağrısı yok** (WO gereği). `ContentRenderService` yapıcısında model portu yok;
  bir test imza kümesini kilitliyor. Altyazılar mevcut `transcript_segments` satırlarının kesite
  izdüşümü — sağlayıcı çağrısı değil, saklanmış veri.
- **`fade` geçişi ve voiceover/music ses kaynakları** adapter kabiliyetinde bildirilmiyor (2C+).
  Doğrulama bunları dokümante kodla reddediyor — yarı yolda çöken job yerine temiz ret.
- **Gerçek C2PA manifest yazımı** yok; kanca `provenance_state = stripped_pending_reattach`
  olarak bırakıldı (sertifika gerektiriyor).
- **Marka fontu yok** — DejaVu ile çalışıyor. Lisans kararı gerekiyor; ayarlar (`RENDER_FONT_FILE`,
  `RENDER_FONT_FAMILY`) yerinde, karar verildiğinde konfigürasyon değişikliği.
- **Tek video track** (`max_video_tracks=1`). Kompozisyon gerektiğinde kabiliyet artar, port değişmez.
- `docs/index.md` ve `docs/adr/README.md`'ye dokunulmadı (W03 tekeli) — ADR'lar indekse eklenmedi.

### Doğrulama

Araç zinciri: **Python 3.13.14 · mypy 2.3.0 · ruff 0.16.0**, `COMPOSE_PROJECT_NAME=sp-w11`
izole stack (gerçek PostgreSQL + MinIO + FFmpeg 7.1.5).

| Kontrol | Sonuç |
|---|---|
| `ruff check` + `ruff format --check` (app/tests/migrations/scripts) | **yeşil** |
| `mypy .` (strict) | **yeşil** — 155 dosya |
| `pytest` (integration dahil) | **yeşil** — **463 test** (öncesi 392, +71) |
| `check-openapi` (kontrat drift) | **yeşil** — yeniden üretildi, 5 endpoint eklendi |
| migration `upgrade head → downgrade base → upgrade head` | **yeşil**, tek head |

| # | Kabul kriteri | Sonuç |
|---|---|---|
| 1 | Migration up/down/up, tek head | ✅ `0012_content_timeline_render (head)` |
| 2 | Gerçek render, ffprobe doğrulaması, `render_outputs`, ön izleme, AI çağrısı yok | ✅ `test_real_render_of_two_scenes_with_turkish_overlay_and_logo` — 1080x1920 h264+aac, ~5 sn, preview 540x960 |
| 3 | Türkçe metin doğru render | ✅ `test_turkish_glyphs_resolve_against_the_bundled_font` — exit 0 **ve** glif uyarısı yok |
| 4 | §18.3 reddi (süre, safe-area, yasak kelime, duplicate) render başlamadan | ✅ `test_validation_rejects_before_any_render_is_scheduled` — ayrıca 0 render kaydı, 0 job |
| 5 | `verified_field` uydurulamıyor | ✅ parse hatası (`TIMELINE_VERIFIED_FIELD_NOT_LITERAL`) + `TIMELINE_VERIFIED_FIELD_NOT_FOUND` |
| 6 | Yalnızca 9'lu ızgara çapası; ham koordinat reddi | ✅ `TIMELINE_UNKNOWN_FIELD` |
| 7 | Patch → yeniden doğrula → yeniden render, yeni hak tüketmiyor | ✅ `trigger=revision`, `consumes_entitlement=false`; revizyon 1 hâlâ okunabilir |
| 8 | Tek adapter; domain'de `ffmpeg`/`subprocess` yok | ✅ tokenize eden test + `app.infrastructure` import yasağı; fabrika `production`'da `fake`'i reddediyor |
| 9 | Worker izolasyonu: timeout, kısmi çıktı, scratch guard, dead-letter | ✅ `test_content_render_worker.py` (9 test) |
| 10 | Disclosure + provenance alanları, bu slice `none`/boş | ✅ `ai_disclosure_state=none`, `provenance_state=stripped_pending_reattach`, `provenance_manifest_key=NULL` |
| 11 | Tenant izolasyonu | ✅ başka tenant'ın asset'i `TIMELINE_ASSET_NOT_ACCESSIBLE`; başka işletme `404` |
| 12 | İmzalı URL / credential sızmıyor | ✅ yanıt gövdesi object key taşıyor; audit ve yanıtta imza materyali yok; FFmpeg stderr hiç okunmuyor |
| 13 | `make verify` yeşil, test sayısı azalmıyor, drift yok | ✅ 392 → 463 |
| 14 | İki ADR (`ADR-XXX`), `content` `CLAUDE.md` ≤40 satır | ✅ 38 satır |

### Açıkça belirtmem gerekenler

1. **Migration zinciri — PM merge'de düzeltmeli.** W10 merge edilmemişti (Alembic head `0010`),
   ama W10 worktree'sinde `0011_schema_debt.py` zaten oluşturulmuş. Kullanıcı kararıyla `0011`
   alınmadı: dosya `0012_content_timeline_render` olarak yazıldı ve **dalında**
   `down_revision = "0010_brand_catalog"`. **Merge sırasında `down_revision`
   `0011_schema_debt`'e çevrilmeli.** Tablolar ayrık (W10: `provider_usage`/media/businesses —
   W11: `content_timelines`/`render_outputs`), zincir notu migration dosyasının docstring'inde de var.

2. **İlan edilen listenin dışında 4 dosyaya dokundum.** Hepsi yeni bir modül/job tipi için
   yapısal olarak zorunluydu ve mevcut guard testleri bunları zaten talep ediyordu:
   - `app/infrastructure/database/metadata.py` — yeni modül modellerinin kaydı (1 import + 1 satır).
   - `app/infrastructure/celery_publisher.py` — `content.render.requested` → `content.render.drain`
     (1 satır). `test_every_emitted_outbox_event_type_is_classified` sınıflandırılmamış event'i reddediyor.
   - `app/infrastructure/celery_app.py` — beat kaydı (4 satır).
     `test_beat_schedule_wakes_every_drain_task_the_publisher_can_route` bunu talep ediyor.
   - `tests/unit/test_celery_publisher.py` — yukarıdaki iki eklemenin beklentileri (2 satır).
     (`tests/` zaten ilan listemde.)
   Hiçbiri W10'un kapsamıyla çakışmıyor. Sahiplik tablosuna girmeleri gerekip gerekmediği PM kararı.

3. **Render job'ının claim sorgusu `modules/content/repository.py`'de**, `modules/operations/`'da
   değil — o dosyalar ilan listemde yok. `job_type` düz `String(128)` olduğu için şema
   değişikliği gerekmedi. Sorgu media drain'lerinin SKIP LOCKED şeklinin aynısı.
   **PM'e öneri:** ileride `claim_next_job(job_type)` olarak `OperationsRepository`'ye
   yükseltilebilir; şu an üç yerde tekrar eden bir desen.

4. **§18.3 hata kodları PRD §30 kataloğuna eklenmedi.**
   `docs/product/requirements/90b-api-error-contracts.md` W03 tekelinde. Kodların tam listesi
   [content-render.md](../architecture/content-render.md)'de ve modül `CLAUDE.md`'sinde;
   katalog güncellemesi PM kuyruğunda.

5. **Safe-area marjları bizim ürün kararımız, platform verisi değil.** `render.py`'de bu
   açıkça yazılı. Instagram'ın yayımladığı bir geometri olarak sunulmadı — öyle sunmak bu
   deponun hafızadan yapmasına izin verilmeyen bir iddia olurdu.

6. **Metin artık satır kaydırılıyor (en fazla 3 satır).** Tek satır varsayımı gerçek Türkçe
   içerikle ilk denemede çöktü. Kaydırma tanımı doğrulama ile renderer arasında tek yerde
   (`validation.wrap_text`); doğrulamanın ölçtüğü blok ile çizilen blok ayrışamaz.

7. **`.env.w11`** worktree'ye eklendi (izole port bloğu: API 8031, PG 55531, Redis 56531,
   MinIO 59031/59032). `.gitignore`'a girmesi ya da silinmesi PM kararı — commit'e dahil edilmedi.

## Doğrulama

_(test eden oturum doldurur — özellikle: kaynak süresini aşan kesit, safe-area sınırı, `verified_field` yerine serbest metin sokma denemesi, başka tenant'ın asset'i, patch sonrası doğrulamanın atlanması)_
