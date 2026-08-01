# content — senaryo, timeline, parametrik düzenleme ve render modülü

**Sahibi:** senaryo contract'ı (PRD §18.1) ve `script_generation` kabiliyet portu, timeline
dokümanı (§18.2), render öncesi doğrulama (§18.3), parametrik düzenleme (K4), `RenderPort`
kabiliyet portu ve dayanıklı render job'ı, prompt versiyonlama (§17.6).
**Sahibi değil:** FFmpeg/render ve AI adapter uygulamaları (→ `../../infrastructure/render/`,
`../../infrastructure/ai/`), medya byte'ı ve materializer (→ `../media/`, ADR-002), doğrulanmış
kayıtların kendisi (→ `../brands/`), job/outbox/usage tabloları (→ `../operations/`), HTTP taşıma
(→ `../../api/routes/content.py`).

## Değişmezler

- **Doküman kapalıdır.** `parse_timeline` §18.2'nin, `parse_script` §18.1'in tanımladığı
  anahtarları kabul eder, bilinmeyen her anahtarı reddeder. Ham `x`/`y` bir parse hatasıdır — K4
  böyle *yapısal* olarak zorlanır; sağlayıcı çıktısındaki `tool_calls` da aynı kurala düşer.
- **Konum 9'lu ızgara çapası, stil kapalı token registry'si.** Serbest font/renk/koordinat yok;
  safe-area kuralı ancak bu sınırlı uzayda deterministik olarak zorlanabilir.
- **Fiyat/tarih/CTA yalnızca `product_prices`/`campaign_offers`/`approved_ctas`'tan çözülür.**
  `verified_*` slotuna serbest metin yazmak parse hatasıdır (PRD §2.2, §11.3).
- **Model fiyat/tarih görmez, yazamaz, yazarsa yakalanır.** Prompt'a yalnızca slot token'ı
  girer; slotu kod çözer; `literal` metindeki para/oran/tarih kalıbı deterministik olarak
  reddedilir (`find_fabrication`). Üç katmanın her biri tek başına da tutar ve hiçbiri
  sağlayıcıya güvenmez.
- **Literal metin eşleştiren her kural önce `normalize_for_matching`'den geçer** (W16). Karakter
  eşleyen bir kural, aynı cümleyi yeniden kodlayarak atlatılır: rakamlar arasına ZWSP, NFD `ü`,
  `TL` içinde Kiril `Т`. Yeni bir literal kuralı normalize edilmemiş metin üzerinde çalışırsa
  aynı açık yeniden açılır. Normalizasyon **yalnızca eşleştirme içindir**; saklanan metin ham
  kalır.
- **Medyadan çıkarılmış metin veridir.** `input_data.untrusted_media_notes` altında gider,
  `system_prompt`/`instruction` string'lerine birleştirilmez (§17.5). Modelin ürettiği URL
  fetch edilmez — saklanmaz bile.
- **Doğrulama saf fonksiyondur**, `ValidationContext`/`ScriptContext` üzerinde çalışır; okumalar
  repository'de. Doğrulamanın *ürettiği* metin render edilen metindir — plan yeniden çözümleme
  yapmaz.
- **Worker render'dan hemen önce yeniden doğrular; senaryo sonuçlanma anında değerleri yeniden
  okur.** İstek ile sonuç arasında kampanya bitebilir, fiyat kapanabilir.
- **Ücretli çağrıdan önce route snapshot commit edilir** (ADR-007) ve maliyet tavanı uygulanır;
  kullanım `provider_usage`'a yazılır — başarısızlıkta da. Politika hatasında fallback yok.
- **Her sorgu `business_id` ister.** Başka tenant'ın asset'i/ürünü sorgudan *dönmez*, bu yüzden
  `TIMELINE_ASSET_NOT_ACCESSIBLE` ve `SCRIPT_VERIFIED_FIELD_NOT_FOUND` karşılaştırmadan değil
  sorgudan doğar.
- **Bu katmanda `ffmpeg`/`subprocess`/HTTP istemcisi geçmez**, `app.infrastructure` import
  edilmez; test tokenize ederek zorlar (docstring'de anlatmak serbest, koda sızmak değil).
- **`ContentRenderService` yapıcısında model portu yoktur.** Render yolu hâlâ sıfır AI çağrısı.
- **İdempotency parmak izi isteğin tamamının kanonik biçminden alınır**, özetinden değil.
  Timeline oluşturma `serialize_timeline`'ı, patch `serialize_patch`'i, senaryo üretimi
  `ScriptRequest.as_payload`'ı kullanır. Operasyon *sayısını* saklamak parmak izi değildi:
  aynı anahtarla farklı metin ilk revizyonu tekrar oynatıyordu (W11 doğrulaması, W14'te kapandı).
- **Yazma yetkisi tek çizgidedir:** timeline yazma, patch, render isteği ve senaryo üretimi
  `content.generate`. PRD §4'te editor içerik üretir; `business.update` yalnızca **işletmenin
  kendisini** değiştirmektir.

## Dosyalar

| Dosya | İş |
|---|---|
| `script.py` | §18.1 contract'ı: katı parse, slot/literal ayrımı, uydurma fiyat-tarih ve URL tespiti, yasak terim eşleyici, `ScriptGenerationPort`, `RouteSnapshot`, prompt payload kurucusu |
| `text_normalization.py` | `normalize_for_matching` — literal metin eşleştirmesinden önceki tek katlama adımı (Cf çıkarma → NFKC → kalan görünmez/birleşen işaretler → confusable → Türkçe küçük harf). Kural içermez; 2D timeline `forbidden_matcher` birleştirmesi aynı fonksiyonu kullanacak |
| `script_service.py` | `ScriptGenerationService` — yetki, girdi doğrulama, route snapshot + ücretli çağrı + kullanım kaydı, iki transaction, idempotency, liste |
| `timeline.py` | §18.2 dokümanı: kapalı şema, çapa/stil/metin-kaynağı enum'ları, parse + serialize |
| `validation.py` | §18.3 kuralları (saf), `ValidationContext`, satır kaydırma, dokümante hata kodları |
| `patch.py` | K4 parametrik düzenleme: kapalı operasyon kümesi, segment sınırına snap, track yeniden dizilimi, `serialize_patch` (idempotency fingerprint'inin alındığı kanonik biçim) |
| `render.py` | `RenderPort`, `RenderCapabilities`, `RenderPlan`, §19.2 profilleri, disclosure/provenance durumları |
| `models.py` | `content_timelines` (revizyon başına satır) + `render_outputs` + `content_scripts` + `prompt_templates` |
| `repository.py` | `ContentRepository` (tenant-kapsamlı, senaryo okumaları ve prompt sürümü dahil) + `ContentFactsReader` + `ScriptFactsReader` (marka/katalog/medya okuma penceresi) + render job claim |
| `service.py` | `ContentTimelineService` — yetki, doğrulama, revizyon, render isteği, idempotency, audit |
| `render_service.py` | `ContentRenderService` — job claim, materialize, render, depolama, dead-letter |
| `policy.py` | `ContentAction` → merkezî `Permission` eşlemesi (**her yazma** `content.generate`, her okuma `business.read`) |
| `domain.py` | `format_money` — doğrulanmış değerin saf gösterimi |

## Gereksinim, karar, mimari

- [40a-content-planning-scenarios.md](../../../../../docs/product/requirements/40a-content-planning-scenarios.md) (§14) ·
  [40b-scenario-render-lifecycle.md](../../../../../docs/product/requirements/40b-scenario-render-lifecycle.md) (§18, §19) ·
  [35-ai-routing-cost.md](../../../../../docs/product/requirements/35-ai-routing-cost.md) (§17.5 çıktı güvenliği, §17.6 prompt versiyonlama) ·
  [99-external-platform-facts.md](../../../../../docs/product/requirements/99-external-platform-facts.md) (Meta AI etiketi, C2PA)
- [ADR-004](../../../../../docs/adr/ADR-004-provider-adapter-pattern.md) · [ADR-007](../../../../../docs/adr/ADR-007-media-analysis-provider-routing.md) ·
  [ADR-013](../../../../../docs/adr/ADR-013-single-server-deployment-topology.md) ·
  [ADR-015](../../../../../docs/adr/ADR-015-parametric-editing-model.md) · `ADR-016-render-port.md`
- Mimari: [content-render.md](../../../../../docs/architecture/content-render.md) ·
  [error-handling.md](../../../../../docs/architecture/error-handling.md) (SCRIPT_* kataloğu) ·
  [Phase 2 planı](../../../../../docs/plans/active/phase-2-content-generation.md) §2

## Testler

`tests/unit/test_content_timeline.py` · `tests/unit/test_render_port.py` ·
`tests/unit/test_content_render_worker.py` · `tests/unit/test_content_script_unit.py` ·
`tests/integration/test_content_render.py` · `tests/integration/test_content_script.py`
