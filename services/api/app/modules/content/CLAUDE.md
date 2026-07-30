# content — timeline, parametrik düzenleme ve render modülü

**Sahibi:** timeline dokümanı (PRD §18.2), render öncesi doğrulama (§18.3), parametrik
düzenleme (K4), `RenderPort` kabiliyet portu ve dayanıklı render job'ı.
**Sahibi değil:** FFmpeg/render uygulaması (→ `../../infrastructure/render/`), medya byte'ı ve
materializer (→ `../media/`, ADR-002), doğrulanmış kayıtların kendisi (→ `../brands/`), job/outbox
tabloları (→ `../operations/`), HTTP taşıma (→ `../../api/routes/content.py`).

## Değişmezler

- **Doküman kapalıdır.** `parse_timeline` §18.2'nin tanımladığı anahtarları kabul eder, bilinmeyen
  her anahtarı reddeder. Ham `x`/`y` bir parse hatasıdır — K4 böyle *yapısal* olarak zorlanır.
- **Konum 9'lu ızgara çapası, stil kapalı token registry'si.** Serbest font/renk/koordinat yok;
  safe-area kuralı ancak bu sınırlı uzayda deterministik olarak zorlanabilir.
- **Fiyat/tarih/CTA yalnızca `product_prices`/`campaign_offers`/`approved_ctas`'tan çözülür.**
  `verified_*` slotuna serbest metin yazmak parse hatasıdır (PRD §2.2, §11.3).
- **Doğrulama saf fonksiyondur**, `ValidationContext` üzerinde çalışır; okumalar repository'de.
  Doğrulamanın *ürettiği* metin render edilen metindir — plan yeniden çözümleme yapmaz.
- **Worker render'dan hemen önce yeniden doğrular.** İstek ile render arasında kampanya
  bitebilir, fiyat kapanabilir; kareye ancak o an doğru olan değer girer.
- **Her sorgu `business_id` ister.** Başka tenant'ın asset'i sorgudan *dönmez*, bu yüzden
  `TIMELINE_ASSET_NOT_ACCESSIBLE` karşılaştırmadan değil sorgudan doğar.
- **Bu katmanda `ffmpeg`/`subprocess` geçmez**, `app.infrastructure` import edilmez; test
  tokenize ederek zorlar (docstring'de anlatmak serbest, koda sızmak değil).
- **Bu slice hiçbir AI çağrısı yapmaz.** `ContentRenderService` yapıcısında model portu yoktur.

## Dosyalar

| Dosya | İş |
|---|---|
| `timeline.py` | §18.2 dokümanı: kapalı şema, çapa/stil/metin-kaynağı enum'ları, parse + serialize |
| `validation.py` | §18.3 kuralları (saf), `ValidationContext`, satır kaydırma, dokümante hata kodları |
| `patch.py` | K4 parametrik düzenleme: kapalı operasyon kümesi, segment sınırına snap, track yeniden dizilimi |
| `render.py` | `RenderPort`, `RenderCapabilities`, `RenderPlan`, §19.2 profilleri, disclosure/provenance durumları |
| `models.py` | `content_timelines` (revizyon başına satır) + `render_outputs` |
| `repository.py` | `ContentRepository` (tenant-kapsamlı) + `ContentFactsReader` (medya/marka okuma penceresi) + render job claim |
| `service.py` | `ContentTimelineService` — yetki, doğrulama, revizyon, render isteği, idempotency, audit |
| `render_service.py` | `ContentRenderService` — job claim, materialize, render, depolama, dead-letter |
| `policy.py` | `ContentAction` → merkezî `Permission` eşlemesi |
| `domain.py` | `format_money` — doğrulanmış değerin saf gösterimi |

## Gereksinim, karar, mimari

- [40b-scenario-render-lifecycle.md](../../../../../docs/product/requirements/40b-scenario-render-lifecycle.md) (§18, §19) ·
  [99-external-platform-facts.md](../../../../../docs/product/requirements/99-external-platform-facts.md) (Meta AI etiketi, C2PA)
- [ADR-004](../../../../../docs/adr/ADR-004-provider-adapter-pattern.md) · [ADR-013](../../../../../docs/adr/ADR-013-single-server-deployment-topology.md) ·
  `ADR-016-render-port.md` · `ADR-015-parametric-editing-model.md` (numaralandırılmadı)
- Mimari: [content-render.md](../../../../../docs/architecture/content-render.md) · [Phase 2 planı](../../../../../docs/plans/active/phase-2-content-generation.md) §2

## Testler

`tests/unit/test_content_timeline.py` · `tests/unit/test_render_port.py` ·
`tests/unit/test_content_render_worker.py` · `tests/integration/test_content_render.py`
