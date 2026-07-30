# İçerik üretim ve render mimarisi

**Kapsam:** senaryo üretimi (`script_generation` portu), timeline dokümanı, render öncesi
doğrulama, parametrik düzenleme ve `RenderPort` arkasındaki render hattı. Slice 2A (W11) render
yolunu, slice 2B (W13) senaryo yolunu getirdi.
**İlgili:** PRD §17, §18, §19 → [35](../product/requirements/35-ai-routing-cost.md) ·
[40a](../product/requirements/40a-content-planning-scenarios.md) ·
[40b](../product/requirements/40b-scenario-render-lifecycle.md) ·
`ADR-016-render-port.md` · `ADR-015-parametric-editing-model.md` ·
[ADR-004](../adr/ADR-004-provider-adapter-pattern.md) · [ADR-007](../adr/ADR-007-media-analysis-provider-routing.md) ·
[ADR-013](../adr/ADR-013-single-server-deployment-topology.md)

## Senaryo üretimi (slice 2B)

```
İstemci ──POST /scripts──► ScriptGenerationService
   (yalnızca kayıt id'leri:      │ yetki (content.generate), aktif işletme
    product/cta/campaign/asset)  │ girdileri doğrula (tenant-kapsamlı, yoksa 404)
                                 │ aktif prompt sürümünü oku (§17.6)
                                 │ maliyet tavanı — AŞILIRSA ÇAĞRI YOK
                                 ├─ T1 COMMIT: content_scripts(pending) + route snapshot
                                 │
                                 │  ScriptGenerationPort.generate(...)   ◄── fake adapter
                                 │  (sistem prompt + talimat + AYRI input_data)
                                 │
                                 └─ T2 COMMIT: provider_usage
                                    │ parse_script_output (katı şema)
                                    │ resolve_script (yasak kelime, uydurma fiyat/tarih,
                                    │   slot çözümü — DEĞERLER YENİDEN OKUNUR)
                                    └─► generated (template + document) | failed (failure_code)
```

**Model fiyatı ve tarihi hiç görmez.** `input_data` yalnızca *slot token'ı* taşır
(`{{price:<product-id>}}`, `{{campaign_end:<offer-id>}}`, `{{cta:<cta-id>}}`); değerin kendisi
prompt'a girmez. Görmediği bir sayıyı kopyalayamaz — savunmanın ilk katmanı bu.

**İkinci katman: slotu kod çözer.** `product_prices` / `campaign_offers` / `approved_ctas`'tan,
tenant-kapsamlı sorguyla. Çözülemeyen referans ile başka tenant'ın kaydı **aynı** kurala düşer:
sorgudan satır dönmez.

**Üçüncü katman: `literal` metinde sayı aramak.** `find_fabrication` deterministik kalıp
eşlemedir ve yalnızca modelden gelen düz metne uygulanır — çözülmüş değere asla, çünkü
doğrulanmış bir fiyatın rakam içermesi *beklenir*. Kalıplar bilerek geniş: `165 TL`, `₺1.650,00`,
`165TL`, `20 dolar`, `yüz altmış beş lira`, `%20`, `31.08.2026`, `1 Ağustos`. `3 dakikada hazır`
geçer; yanlış pozitif kontrolü testte sayılı girdiyle duruyor. Bu katman **sağlayıcıya
güvenmez**: sağlayıcı değişse, ele geçirilse ya da (bugün olduğu gibi) fake olsa da çalışır.

**Prompt injection (§17.5).** Kullanıcı videosundan çıkarılmış transcript/sahne metni
`input_data.untrusted_media_notes` altında **veri** olarak gider; `system_prompt` ve
`instruction` string'lerine hiç birleştirilmez. Savunma modelin itaat etmemesine dayanmıyor:
fake sağlayıcının "itaatkâr" modu enjekte edilen cümleyi senaryoya kopyalıyor ve senaryo yine de
`SCRIPT_FABRICATED_PRICE` ile reddediliyor. Modelin ürettiği URL fetch **edilmiyor** — daha
katısı, saklanmıyor bile (`SCRIPT_LITERAL_URL_REJECTED`); `script.py`/`script_service.py` içinde
HTTP istemcisi olmadığını bir test tokenize ederek doğruluyor.

**Neden iki transaction?** Route snapshot çağrıdan **önce** commit edilir (ADR-007). Tek
transaction daha derli görünürdü ve iki kez yanlış olurdu: ağ turu boyunca bir PostgreSQL
bağlantısını ve snapshot'ı tutardı, ve çağrı sırasında düşen süreç faturalanmış çağrının tek
kaydını geri alırdı. Sonucu bilinçli: `pending`'de takılı satır "çağrı yapılmış olabilir,
sonuçlanmadı" demektir — görülebilir olması gereken bir gerçek.

**Değerler sonuçlanma anında yeniden okunur.** Sağlayıcı düşünürken fiyat satırı kapanabilir,
kampanya bitebilir. Saklanan senaryoda ancak saklandığı anda doğru olan değer bulunur — worker'ın
render öncesi yeniden doğrulamasıyla aynı disiplin.

**CTA yalnızca `approved_ctas`'tan.** Serbest CTA metni ifade **edilemiyor**: §18.1'in
`cta.text` alanını kod dolduruyor, modelin yazabileceği bir alan şemada yok.

**Kampanya bitiş tarihi kapsayıcı son gündür.** Pencere yarı açık `[starts_at, ends_at)`, yani
`ends_at` kampanyanın bittiği ilk andır. Doğrudan basılsaydı reklamda bir gün fazla vaat
edilirdi; bu yüzden son kapsayıcı an işletme saat diliminde `31.08.2026` olarak biçimlendirilir.

**Prompt versiyonlama (§17.6).** `prompt_templates` platform konfigürasyonudur (`business_id`
yok), append-only, ve kısmi unique index kod başına tek aktif sürüm garantiler. `0013` ilk
sürümü seed eder. Hangi prompt'la üretildiği bilinmeyen senaryo **var olamaz**: sütun `NOT NULL`
ve tabloya referans veriyor.

**Sağlayıcı fake, yol gerçek.** Ücretli çağrı disiplini bugünden yerinde: route snapshot, maliyet
tavanı (çağrıdan önce), `provider_usage` satırı (başarısızlıkta da — zaman aşımına uğrayan çağrı
da faturalanmış olabilir). Politika hatasında **fallback yok**: "model fiyat uydurdu" geçici bir
hata değil, ve ikinci sağlayıcı ikinci bir görüş değil.

**Üretimde fake yok, ama boot da çökmüyor.** Diğer fake adapter'lar üretimde `Settings`
doğrulamasında reddediliyor. Bu kabiliyet bir yönüyle farklı: **fake senaryo yayınlanabilir.**
Fake render açıkça yer tutucu bir dosya yazar; fake senaryo bir insanın onaylayıp
paylaşabileceği akıcı Türkçe reklam metni yazar. Bu yüzden üretim, uygulamayı düşürmek yerine
`DisabledScriptGenerationAdapter` alıyor: `503 SCRIPT_GENERATION_NOT_CONFIGURED`, diğer tüm
endpoint'ler çalışmaya devam ediyor.

## Render akışı

```
İstemci ──POST /content/timelines──► ContentTimelineService
                                       │ parse (kapalı şema)
                                       │ doğrula (§18.3, deterministik)
                                       └─► content_timelines (revizyon 1)

İstemci ──POST .../patch────────────► apply_patch → yeniden doğrula
                                       └─► content_timelines (revizyon N+1)

İstemci ──POST .../renders──────────► doğrula → render_outputs (pending)
                                       + jobs (content.render) + outbox
                                                    │
Celery beat ──content.render.drain──► ContentRenderService
                                       │ claim (SKIP LOCKED)
                                       │ ► TEK transaction: timeline oku,
                                       │   YENİDEN doğrula, asset/altyazı oku
                                       │ materialize (W09, asset başına alt dizin)
                                       │ RenderPort.render(plan)  ◄── FFmpeg adapter
                                       │ storage.persist_file × 3
                                       └─► render_outputs (succeeded)
```

## Neden bu şekilde

**Doküman kapalı.** `parse_timeline` §18.2'nin anahtarlarını kabul eder, gerisini reddeder.
K4'ün "serbest x/y yok" kararı böyle *yapısal* olarak zorlanır: ham koordinat yok sayılan bir
alan değil, parse hatasıdır.

**Doğrulama saf, okumalar repository'de.** `validate_timeline` bir `ValidationContext` üzerinde
çalışır. Aynı kurallar API sınırında, patch sonrasında ve worker'da render'dan hemen önce
koşar; üçü de aynı fonksiyon olduğu için ayrışamazlar.

**Worker yeniden doğrular.** İstek ile render arasında kampanya bitebilir, fiyat satırı
kapanabilir, asset karantinaya alınabilir. Kareye ancak piksellerin çizildiği anda doğru olan
değer girer. Bu aynı zamanda "patch uygula, doğrulamayı atla" yolunu kapatır.

**Tüm DB okuması render'dan önce, tek transaction'da.** Encode dakikalar sürebilir; araya
yayılmış bir session PostgreSQL bağlantısını ve snapshot'ı render boyunca tutardı. `_begin`
her şeyi okur, sonra servis yalnızca dosyalara ve object storage'a dokunur.

**Doğrulamanın ürettiği metin render edilen metindir.** `resolved_texts` satır kaydırması dahil
tam dizgedir. Plan kurucusu yeniden çözümleseydi, doğrulanmış bir fiyat denetimden geçip kareye
başka bir değer ulaşabilirdi.

## Doğrulama hata kodları (§18.3)

Hepsi render **başlamadan** döner; hiçbiri sağlayıcıya danışmaz. Yanıt `422
TIMELINE_VALIDATION_FAILED` ve `meta.issues[]` altında tüm ihlaller birden listelenir — tek tek
keşfettirmek yerine.

| Kod | Ne yakalar |
|---|---|
| `TIMELINE_DURATION_OVERFLOW` | canvas süresini aşan kesit, kabiliyet tavanını aşan canvas |
| `TIMELINE_ASSET_NOT_ACCESSIBLE` | olmayan **veya başka tenant'ın** asset'i (sorgudan dönmez) |
| `TIMELINE_ASSET_NOT_RENDERABLE` | ingest/teknik analiz tamamlanmamış asset |
| `TIMELINE_CLIP_RANGE_INVALID` | kaynak süresini aşan kesit aralığı |
| `TIMELINE_CLIP_OVERLAP` | aynı track'te çakışan kesitler |
| `TIMELINE_DUPLICATE_CLIP` | aynı (asset, başlangıç, bitiş) üçlüsü iki kez |
| `TIMELINE_ASPECT_RATIO_MISMATCH` | canvas ile hedef profil oranı uyuşmuyor |
| `TIMELINE_RESOLUTION_TOO_LOW` | kaynak, hedefi gözle görülür şekilde büyütmeden dolduramıyor |
| `TIMELINE_TEXT_OUTSIDE_SAFE_AREA` | kaydırıldıktan sonra bile güvenli alana sığmayan metin |
| `TIMELINE_FORBIDDEN_TERM` | markanın yasak iddia/konu listesindeki terim (kelime sınırında) |
| `TIMELINE_VERIFIED_FIELD_NOT_FOUND` | çözülemeyen doğrulanmış referans |
| `TIMELINE_CAMPAIGN_WINDOW_INVALID` | penceresi dışındaki kampanya |
| `TIMELINE_LOGO_ASSET_INVALID` | markanın logo olarak kaydetmediği görsel |
| `TIMELINE_UNSUPPORTED_TRANSITION` / `_CROP_MODE` / `_AUDIO_SOURCE` / `_CAPTION_SOURCE` | adapter kabiliyeti dışında |
| `TIMELINE_TOO_MANY_VIDEO_TRACKS` | bu adapter tek track destekliyor |

Şema (parse) hataları ayrı: `422 TIMELINE_SCHEMA_INVALID` + `meta.issue`/`meta.pointer`.
Başlıcaları `TIMELINE_UNKNOWN_FIELD` (ham koordinat buraya düşer),
`TIMELINE_VERIFIED_FIELD_NOT_LITERAL`, `TIMELINE_VERIFIED_REFERENCE_MISSING`,
`TIMELINE_STYLE_TOKEN_UNKNOWN`.

> **PM'e not:** bu kodlar henüz PRD §30 hata kataloğuna
> ([90b-api-error-contracts.md](../product/requirements/90b-api-error-contracts.md)) eklenmedi —
> o dosya W03 tekelinde. Katalog güncellemesi PM kuyruğunda.

## Render hattı (FFmpeg adapter)

Dört sınırlı aşama, her biri kendi timeout'uyla:

1. **Normalize** — her kesit ayrı ayrı kırpılır/ölçeklenir, aynı parametrelere getirilir
   (`smart_cover` / `blur_pad` / `contain`), sesi olmayan kaynağa sessizlik eklenir.
2. **Compose** — concat demuxer + logo overlay + `drawtext` + altyazı yakma (ASS), master çıkar.
3. **Preview** — master'dan `preview_540x960` (§15.5 proxy mantığının çıktı karşılığı).
4. **Thumbnail** — master'dan tek kare.

Sonra `ffprobe` master'ı okur; teknik özet **istenen değil gözlemlenen** değerlerdir.

**Metin güvenliği:** overlay metni dosyaya yazılır, `textfile=` + `expansion=none` ile
referans verilir. Byte'lar çizilir, hiç ayrıştırılmaz — iki nokta, tırnak ve `%{...}` zararsız.
Altyazı aynı yoldan üretilen ASS dosyasıyla gider.

**Süreç hijyeni:** `shell=False`, timeout, diagnostic'ler özel geçici dosyaya — yalnızca boyutu
denetlenir, içeriği hiç okunmaz. Başarısızlıkta koşunun oluşturduğu her dosya silinir.

**Tek sunucu (ADR-013):** render worker süreci `os.nice(+10)` ile renice edilmiştir, FFmpeg alt
süreçleri bunu miras alır. Drain her işten önce `WorkerScratchGuard.ensure_within_budget()`
çağırır ve her işten sonra artıkları süpürür.

## Font ve marka fontu açığı

Konteynerde FFmpeg 7.1.5, `libass`/`freetype`/`harfbuzz`/`fribidi` derli. Metin **DejaVu Sans**
ile render edilir (`/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf`); Türkçe alfabenin tamamı
(`ığşçöüİĞŞÇÖÜ`) doğru çözümleniyor ve bir test bunu glif uyarısı yokluğuyla birlikte doğruluyor.

**Marka fontu yok.** Bir marka fontu paketlemek lisans kararı gerektiriyor ve bu slice'ın
kapsamında değil; `RENDER_FONT_FILE`/`RENDER_FONT_FAMILY` ayarları yerinde duruyor, karar
verildiğinde konfigürasyon değişikliğidir.

## Disclosure ve provenance

`render_outputs` ilk satırından itibaren iki alan taşır:

- **`ai_disclosure_state`** — bu slice `none` yazar, çünkü hattın hiçbir yerinde model çağrısı
  yok. Alan şimdi var, çünkü render anında yazılmış kayıt güvenilir, sonradan doldurulmuş sütun
  değil. Gerekçe Meta'nın Temmuz 2026'dan beri FB/IG reklamlarında zorunlu tuttuğu AI beyanı;
  platform politikası olduğu için TR kapsamında da bağlayıcı
  ([99-external-platform-facts.md](../product/requirements/99-external-platform-facts.md)).
- **`provenance_state`** — FFmpeg yeniden kodlama C2PA manifest'ini siler ve bu adapter yerine
  yenisini imzalayamaz. Bu yüzden `stripped_pending_reattach` yazar: imzalama adımı geldiğinde
  üzerinde çalışacağı sorgulanabilir bir küme bırakır. `provenance_manifest_key` bu slice'ta
  daima `NULL`. Manifest yazımı ve imzalama sertifika gerektiriyor, ayrı iş.

Katılık K3 (pazar kapsamı) ile ölçeklenir; alanların varlığı K3'e bağlı değil.

## Bu iki slice'ın taşımadıkları

TTS ve ses hizalama (2C), otomatik QC (2D), yaşam döngüsü ve entitlement tüketimi (2E),
onay/revizyon akışı (2F), planlayıcı (2G). Yayınlama Phase 4. Gerçek C2PA manifest yazımı ayrı
iş. `fade` geçişi ve voiceover/music ses kaynakları adapter kabiliyetinde **bildirilmiyor**,
dolayısıyla doğrulama onları temiz biçimde reddediyor.

Senaryo tarafında ayrıca: **gerçek AI sağlayıcısı yok** (W08 benchmark'ı + route politikası
ADR'ından sonra), senaryodan timeline **otomatik kurulmuyor** (senaryo `required_scene_tags`
taşır ama sahne ataması yapmaz — 2C/2E), ve senaryo üretimi **dayanıklı bir job değil**:
istek-yanıt döngüsünde, sınırlı timeout ile koşuyor. `pending`'de takılı kalan satırları
süpüren bir kurtarma taraması 2E'nin işi.
