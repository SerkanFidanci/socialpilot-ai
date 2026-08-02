# İçerik üretim ve render mimarisi

**Kapsam:** senaryo üretimi (`script_generation` portu), seslendirme (`tts` portu), timeline
dokümanı, render öncesi doğrulama, parametrik düzenleme ve `RenderPort` arkasındaki render hattı.
Slice 2A (W11) render yolunu, slice 2B (W13) senaryo yolunu, slice 2C (W15) seslendirmeyi,
slice 2D (W18) otomatik QC'yi getirdi.
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

## Seslendirme (slice 2C)

PRD §14.8'in sırası: *önce senaryo ve seslendirme oluşturulur, ses segment süreleri çıkarılır,
her cümleye uygun kesit atanır.* Bu slice ilk ikisini yapar; kesit ataması 2E'dir.

```
İstemci ──POST /voiceovers──► VoiceoverService
   (yalnızca script id +          │ yetki (content.generate), aktif işletme
    kayıtlı ses profili kodu)     │ senaryo `generated` mi (tenant-kapsamlı, yoksa 404)
                                  │ maliyet tavanı — HEM ÇAĞRI BAŞI HEM TÜM KOŞU İÇİN
                                  ├─ T1 COMMIT: voiceover_assets(pending) + route snapshot
                                  │
                                  │  her satır için, sırayla:
                                  │    TTSPort.synthesize(...)      ◄── fake adapter (gerçek WAV)
                                  │    AudioProbePort.measure(...)  ◄── ffprobe
                                  │    storage.persist_file(...)    ◄── mevcut depolama adapter'ı
                                  │
                                  └─ T2 COMMIT: çağrı başına provider_usage
                                     └─► generated (segments + ölçülmüş süreler + sapma)
                                         | failed (failure_code + üretilmiş segmentler)
```

**Serbest metin seslendirilemiyor, çünkü isteğin metin alanı yok.** Gövde script id ve ses
profili kodu taşır. Seslendirilen metin senaryonun **çözülmüş** dokümanıdır — `{{price:…}}`'in
kod tarafından `product_prices`'tan basıldığı hali — yani dinleyicinin duyduğu fiyat bir kaydın
tuttuğu fiyattır. 2B'nin üç katmanı buraya kadar taşınıyor; bu slice yeni bir katman eklemiyor
ve eklemesi gerekmiyor, çünkü başka bir yerden metin kabul etmiyor.

**Süre ölçülür, beyan edilmez.** Sağlayıcının kendi çıktısının uzunluğu hakkındaki beyanı
doğrulanmamış bir sayıdır ve aşağı akıştaki her karar ona dayanır. `AudioProbePort` (ffprobe)
dosyadan yeniden türetir; `AudioResult.declared_duration_ms` yine de kaydedilir, böylece
uyuşmazlık kayıtta **görünür** kalır — sessizce düzeltilmiş olmaz. `duration_ms`, `total_duration_ms`
ve §18.3 kontrolü yalnızca ölçümü kullanır.

**Sapma ölçülür, yargılanmaz.** `drift_ms = ölçülen − senaryonun `target_duration_ms`'i`, segment
başına ve toplamda. Hangi sapmanın kabul edilemez olduğu 2D'nin eşiğidir; bu modülde eşik yok.

**Kısmi koşu gerçek bir durumdur.** Satırlar sırayla sentezlenir; üçüncüde düşen bir koşu iki
objeyi zaten depolamıştır. O ikisi `failed` satırın `segments` alanına yazılır — byte'lar
kayıtsız kalmaz — ve gerçekleşen her çağrı kendi `provider_usage` satırını alır (§39.1). Satırlar
isteğin `correlation_id`'siyle gruplanır; `voiceover_assets.provider_usage_id` koşuyu
sonuçlandıran satırı gösterir, dolayısıyla o satırın `outcome`'u ile voiceover'ın `status`'ü
çelişemez.

**Ses profili sürümlüdür (§17.6 deseni).** Kapalı bir registry (`VOICE_PROFILES`) — çağıranın
serbest konuşma hızı veya ham sağlayıcı ses kimliği seçmesi ifade edilemez. Sağlayıcıya verilen
profil dokümanının **tamamı** sesin yanında saklanır, böylece registry yarın düzenlense de
bugün üretilen ses hangi sesle üretildiğini söyleyebilir.

**Ses objeleri mevcut depolama adapter'ıyla yazılır**, ikinci bir yükleme yolu yok:
`tenant/<business>/voiceovers/<voiceover>/segment-NNN.wav`. Depolamanın gözlemlediği boyut,
content-type ve SHA-256, adapter'ın yazdığını söylediğiyle karşılaştırılır; uyuşmazlık
reddedilir. Yanıt gövdesinde ve kayıtta **object key** vardır, imzalı URL değil.

**Bu uç senkron ve dayanıklı bir job değil.** Bir koşu birkaç çağrıdır, bu yüzden çağrı başına
timeout'un üstüne koşunun tamamı için `TTS_TOTAL_TIMEOUT_SECONDS` konuldu. Gerçek bir sağlayıcı
takıldığında bu dayanıklı bir job'a taşınır (2E) — senaryo üretimiyle aynı borç.

**Üretimde fake yok, boot da çökmüyor.** W13'ün kuralı, PM'in genelleştirdiği haliyle: çıktısı
insan-onaylanabilir olan kabiliyet üretimde `disabled` adapter'a düşer (`503
TTS_NOT_CONFIGURED`). Seslendirme iki kez niteliyor — zaten onaylanmış metni okuyor ve dinleyici
fixture sesi ile satın alınmış sesi kulakla ayırt edemiyor. `TTS_ADAPTER` bu yüzden
`reject_non_production_adapters` listesinde **yok**; storage/identity/materializer/render orada
kalmaya devam ediyor.

**Timeline hizalaması.** Üretilen ses `audio_tracks`'e `voiceover` olarak bağlanır; `asset_id`
bir `voiceover_assets` satırını gösterir, yüklenmiş bir medya asset'ini değil — iki farklı
tablo, iki farklı tenant-kapsamlı sorgu. `Timeline.asset_ids` bu yüzden voiceover kimliğini
**içermez** (worker onu kaynak video sanıp indirmeye çalışırdı); `Timeline.voiceover_ids` ayrı.
§18.3'ün "seslendirme süresi" kontrolü artık gerçeğe bağlı: seslendirme süresi canvas süresini
aşamaz (`TIMELINE_VOICEOVER_DURATION_OVERFLOW`). Bu slice **yeni ses işleme yazmaz** — müzik
ducking (`duck_under_voice`) 2A şemasında zaten var, miksaj filtresi yok.

> **Açık, PM'e:** hiçbir render adapter'ı `voiceover` ses kaynağını kabiliyetinde bildirmiyor
> (`audio_sources = {original}`), çünkü ses miksajı bu iş emrinin kapsamı dışında. Doğrulama
> bunu temiz biçimde `TIMELINE_UNSUPPORTED_AUDIO_SOURCE` ile reddediyor, yani seslendirmeli bir
> timeline bugün **kaydedilemiyor**. Süre kuralı yine de koşuyor ve iki bulgu birlikte
> dönüyor — kural "bir gün bir adapter özellik kazanınca var olmaya başlayan" bir şey olmasın
> diye. FFmpeg adapter'ına voiceover miksajı eklemek ayrı bir slice (2E).

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
| `TIMELINE_VOICEOVER_NOT_ACCESSIBLE` | olmayan, başka tenant'ın ya da medya asset'ine işaret eden seslendirme referansı (2C) |
| `TIMELINE_VOICEOVER_NOT_READY` | sonuçlanmamış (`pending`) veya kısmi (`failed`) seslendirme — ölçülmüş süresi yok (2C) |
| `TIMELINE_VOICEOVER_DURATION_OVERFLOW` | §18.3 "seslendirme süresi": ffprobe ile ölçülen ses canvas'ı aşıyor (2C) |
| `TIMELINE_UNSUPPORTED_CHARACTER` | ASCII'ye katlanamayan harf taşıyan overlay metni — hiçbir kural okuyamadığı için hiçbir kural çalışmadan reddedilir (2D, `parse_text`'in timeline karşılığı) |

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

## Otomatik QC (slice 2D)

§19.4'ün listesi. 2A–2C üretimi kurdu ama **güvenilirliği** kurmadı: render biten her çıktı,
gerçekten açılıyor mu, sesi var mı, yazısı kadrajda mı, fiyatı hâlâ kayda uyuyor mu bilinmeden
`succeeded` sayılıyordu.

```
Celery beat ──content.qc.drain──► ContentQcService
                                   │ claim: QC raporu OLMAYAN `succeeded` render (SKIP LOCKED)
                                   ├─ T1 COMMIT: render_qc_reports(pending,
                                   │             verdict=needs_review, path=human_review)
                                   │             + jobs(content.qc) + attempt + route snapshot
                                   │
                                   │  materialize(master)   ◄── W09 materializer
                                   │  MediaQcProbePort      ◄── FFmpeg/ffprobe (fake'i YOK)
                                   │  VisualQcPort          ◄── fake / disabled
                                   │
                                   └─ T2 COMMIT: 13 kontrol + karar + eşik anlık görüntüsü
                                      └─► completed (passed | needs_review | failed)
                                          | failed (ölçüm alınamadı, checks=unknown)
```

**QC fail-closed'dır.** Çalıştırılamayan kontrol `unknown` olur ve genel karar en az
`needs_review`'a düşer. Bu bir üslup değil yapı: `build_results` kontrol kümesinin tamamıyla
`unknown` olarak başlar ve çağıranın verdiği cevaplarla üzerine yazılır — **bir kontrolü atlamak
ifade edilemiyor.** Gerekçe: ölçmediğini onaylayan bir QC, QC'siz olmaktan kötüdür; sahte güven
üretir.

**QC karar verir, eylem yapmaz.** `decide` bir karar ve bir *öneri* döner (`retry_render` ·
`alternative_scene` · `alternative_provider` · `human_review` · `request_new_media`). Yeniden
render tetiklemez, sağlayıcı değiştirmez, deneme saymaz — sınırsız render döngüsünün sınırı
yaşam döngüsünündür (2E). `ContentQcService` yapıcısında **render portu yoktur**; bir test
imzayı zorluyor.

**Kontrol kümesi bizim değil, gereksinimin.** `QcCheck`'in her üyesi §19.4'ün bir satırı, aynı
sırayla. Bu hattın dört turluk dersi (elle sayılmış her küme delindi) burada "daha uzun liste"
ile değil **bütünlükle** karşılanıyor: `CHECK_POLICIES` her üyeyi kapsar, `build_results` her
üyeyi yazar, `decide` eksik kümeyi reddeder. Politikasız bir kontrol eklemek testi düşürür.

| Kontrol | Nasıl ölçülür | Bloke eder mi |
|---|---|---|
| `container_readable` | ffprobe açabiliyor mu | evet |
| `duration_matches_plan` | ölçülen süre ↔ timeline kesitlerinin toplamı | evet |
| `audio_present` | ses akışı var **ve** loudness sessizlik tabanının üstünde | evet |
| `loudness` | EBU R128 integrated, config penceresinde | hayır |
| `black_frames` | `blackdetect` oranı | evet |
| `static_frames` | `freezedetect` oranı | hayır |
| `text_within_safe_area` | timeline geometrisi × **ölçülen** çözünürlük | hayır |
| `logo_visible` | VLM (port + fake) | hayır |
| `speech_sync` | 2C'nin `drift_ms`'i, eşik burada | hayır |
| `verified_values_current` | referansın kaydı hâlâ aynı mı | **evet** |
| `sensitive_content` | VLM (port + fake) | evet |
| `face_integrity` | VLM (port + fake) | hayır |
| `product_shape` | VLM (port + fake) | hayır |

§19.4'ün "Altyazı senkronu" satırı burada **seslendirme sapması** olarak ölçülüyor: bu hatta
altyazılar saklanmış transcript satırlarının kesit geometrisine deterministik izdüşümü — kayamaz;
sağlayıcının ürettiği konuşma kayabilir ve kayıyor. Yanlış olabilecek şeyi ölçmek bu satırın
dürüst okuması.

**"Fiyat ve tarih kaynağa uyuyor mu" kopya tutmadan cevaplanıyor.** Render çözdüğü değeri
saklamaz (bir fiyatın kopyası, fiyatın yaşadığı ikinci yerdir). Bunun yerine kaydın **kendi
tarihi** okunuyor: referans artık çözülmüyorsa, penceresinin dışına düştüyse, ya da şu anki
değeri render **bittikten sonra** yürürlüğe girdiyse bayat sayılır. Üçüncüsü kesin, çünkü
`product_prices` append-only: açık satırın `effective_from`'u render'dan sonraysa karede kapanmış
satır yazıyor demektir. Rapora yalnızca **pointer ve kod** girer, değer asla — QC raporu süresiz
saklanıyor ve bir fiyatın yazıldığı ikinci yer olamaz. **Bilinen sınır:** `approved_ctas`
değişiklik damgası taşımıyor, dolayısıyla yerinde düzenlenmiş bir CTA görülemiyor; yalnızca
kaybolması yakalanıyor.

**Eşikler config'de ve rapora basılıyor.** Sürüm numarası hangi kural setinin koştuğunu söyler;
**neye karşı karşılaştırdığını** yalnızca anlık görüntü söyler. İkisi olmadan bir ay arayla
yazılmış iki rapor karşılaştırılamaz ve yanlışlıkla değiştirilmiş bir eşik kayıtta iz bırakmaz.
Loudness penceresi **bizim ürün kararımız**, platform gerçeği değil: yayınlanmış bir Instagram
loudness sözleşmesi [99-external-platform-facts.md](../product/requirements/99-external-platform-facts.md)'de
kayıtlı değil ve bu depo platform gerçeğini hafızadan yazmaz.

**Ölçüm portunun fake'i yok.** `create_audio_probe`'un 2C'de verdiği kararla aynı: bu port *tam
olarak* "kimsenin çıktı hakkındaki beyanı olduğu gibi alınmaz" kontrolüdür, dolayısıyla fake bir
probe fixture'ı doğrulayan bir fixture olurdu. Görünür sonucu doğru olan: yer tutucu render
adapter'ının yazdığı dosya video değil, ölçüm başarısız oluyor ve rapor bunu söylüyor.

**VLM adapter'ı üretimde `disabled`.** Kural W13'ün, PM'in genelleştirdiği hâliyle — ama en keskin
hâli bu: fake senaryo yayınlanabilir bir *metin* yazar, fake denetim ise bir insanın üzerine
işlem yapacağı bir **onay** yazar. "Bu karede hassas içerik yok" hiçbir şeyin bakmadığı bir
iddiadır. Sonuç bilinçli ve görünür: gerçek sağlayıcı bağlanana kadar (W08 sonrası) dört model
kontrolü `unknown`, her rapor `needs_review`, hiçbir render otomatik `passed` olmuyor.

**Ölçümün başarısızlığı videonun başarısızlığı değildir.** ffprobe'un ayrıştıramadığı dosya
"video açılıyor mu"yu `failed` ile cevaplar; koşamayan bir probe `unknown` ile. Biri karar,
öbürü kesinti — ve aynı biçimde yazılamazlar. Denemeler tükendiğinde satır `failed` **koşu**
durumuyla, `needs_review` kararıyla ve bir `failure_code` ile kapanır: `pending`'de bırakmak,
kimsenin kontrol etmediği ve kontrol edilmediği görülemeyen bir render demek olurdu.

**QC işi mevcut render yolunu değiştirmeden var oluyor:** talep, QC raporu **olmayan**
`succeeded` render'ı tarayarak alınıyor. Worker düşmüşken biten render, worker döndüğünde
alınıyor; kaybolmuş olabilecek bir kuyruk kaydı yok. Rapor satırı aynı transaction'da yazıldığı
için ikinci bir tur aynı render'ı görmüyor — otomatik QC render başına bir kez koşuyor.

**Timeline `forbidden_matcher` birleştirmesi (devralınan borç).** Timeline metin tarafı kendi
`re.IGNORECASE` eşleyicisini çalıştırıyordu; yani senaryo tarafında kapatılan her atlatma
(görünmez karakter, confusable, NFD, Latin katlaması, süslü rakam) timeline metninde **açıktı**.
Artık `script.forbidden_matcher` + `normalize_for_matching` import ediliyor ve
`contains_unsupported_letter` ASCII'ye katlanamayan harfi `TIMELINE_UNSUPPORTED_CHARACTER` ile
reddediyor — aynı iki fonksiyon, ikinci bir uygulama değil. **Çekim eşleşmesi yok** (PM, W18):
`şeker` yasakken `şekerli` serbest, `az` yasakken `lezzetli` serbest. Liste markanın, kalıp
bizim; kök eşleşmesi markanın kastetmediğini yasaklardı.

> **PM'e, açık:** QC servisinin uçtan uca koşması için üç ek satır gerekiyor ve üçü de bu iş
> emrinin dosya listesinin **dışında**: `worker/tasks.py`'de bir `content.qc.drain` task'ı,
> `worker/composition.py`'de fabrika satırı, `infrastructure/celery_app.py`'de beat girdisi.
> Servis, testleri ve raporu tamamlandı; yalnızca beat tetiklemesi bağlanmadı.

## Bu üç slice'ın taşımadıkları

Yaşam döngüsü ve entitlement tüketimi (2E), onay/revizyon akışı (2F),
planlayıcı (2G). Yayınlama Phase 4. Gerçek C2PA manifest yazımı ayrı iş. `fade` geçişi ve
voiceover/music ses kaynakları adapter kabiliyetinde **bildirilmiyor**, dolayısıyla doğrulama
onları temiz biçimde reddediyor — seslendirmeli timeline'ın bugün kaydedilememesinin sebebi bu
(yukarıdaki açık).

Senaryo ve seslendirme tarafında ayrıca: **gerçek AI sağlayıcısı yok** (W08 benchmark'ı + route
politikası ADR'ından sonra), senaryodan timeline **otomatik kurulmuyor** (senaryo
`required_scene_tags` ve seslendirme segment süreleri taşır ama sahne ataması yapmaz — 2E), ve
ikisi de **dayanıklı bir job değil**: istek-yanıt döngüsünde, sınırlı timeout ile koşuyorlar.
`pending`'de takılı kalan satırları süpüren bir kurtarma taraması 2E'nin işi.
