# content — senaryo, seslendirme, timeline, parametrik düzenleme, render, QC ve yaşam döngüsü

**Sahibi:** senaryo contract'ı (PRD §18.1) ve `script_generation` kabiliyet portu, seslendirme
(§14.8) ve `tts` kabiliyet portu, timeline dokümanı (§18.2), render öncesi doğrulama (§18.3),
parametrik düzenleme (K4), `RenderPort` kabiliyet portu ve dayanıklı render job'ı, otomatik
kalite kontrol (§19.4) — kontrol kümesi, karar tablosu, `MediaQcProbePort` ve `VisualQcPort` —
içerik projesi yaşam döngüsü (§20): kapalı durum makinesi, geçiş kaydı, QC kararının sınırlı
eyleme dönüşü ve senaryodan timeline kurulumu, prompt ve ses profili versiyonlama (§17.6).
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
  aynı açık yeniden açılır.
- **Katlama ASCII'ye kadar iner ve kalıp literalleri de öyle yazılır** (W17). `normalize_for_matching`
  her Latin harfini kurulduğu ASCII harfe indirir (`ṭ`→`t`, `ş`→`s`, `ı`→`i`, `Ł`→`l`, `ß`→`ss`),
  çünkü eksik aksan (`165 turk lirasi` — insanın telefonda yazdığı biçim) ile fazla aksan
  (`165 ṬL` — saldırganın yazdığı biçim) **tek katlamanın iki yönüdür**. Bunun bedeli:
  `script.py`'deki her kalıp literali aksansız yazılır (`turk lirasi`, `yuzde`, `agustos`).
  Türkçe yazımıyla eklenen bir kural hiç eşleşmez.
- **Türkçe sondan eklemelidir; sağ çapa kökün değil ek zincirinin sonundadır** (W17 takip 1).
  `lira` aynı zamanda `lirayla`, `liralarla`, `liranın`, `liraymış`'tır; elle yazılmış bir çekim
  listesi bitirilemez — `165 lirayla` bu yüzden geçiyordu. Kalıplarda para, ay ve oran kökleri
  **kök olarak** yazılır ve `_SUFFIX` ile biter. `_SUFFIX` `\w*` **değil**, Türkçe eklerinin
  kurulduğu alfabedir: ünlü uyumu ekte `o`/`ö` üretmez, `b/f/h/j/p/v` ek ünsüzü değildir — `eur`
  kökünün "Eurovision"a ulaşamamasının tek sebebi budur. Yeni bir kök eklerken çekimini listeye
  yazma, kökü yaz. Tek istisna `T.L.` kısaltması: eki ancak **başka bir ayırıcıdan sonra**
  gelebilir, yoksa "Şef T. Lezzetli" para birimi olurdu.
- **Sayı sözcükleri liste, birleşimleri gramerdir** (W17 takip 2). Türkçenin sayı sözcükleri
  kapalı ve sonlu (`bir`…`dokuz`, `on`…`doksan`, `yuz`, `bin`, `milyon`, `milyar`, `yarim`,
  `ceyrek`, `bucuk`) — dil yeni sayı sözcüğü üretmediği için bu listeyi yazmak güvenlidir.
  **Birleşimleri değil:** ardışık sayı sözcükleri boşluklu, tireli **veya bitişik** tek bir
  tutardır (`yuzbin`, `onbir`, `yuz ellibes`, `beserlira`) ve bileşikleri tek tek yazmak yine
  enumerasyondur. Bölümleme kelimenin tamamını kaplamak zorundadır — çapalar bunu zorlar:
  `onbir` = `on`+`bir` sayıdır, `birey` = `bir`+`ey` değildir.
- **Ondalık bağlaçları sayının yazımının parçasıdır, ama yalnızca sayıdan sonra gelebilir**
  (W17 takip düzeltmesi 3). Türkçe ondalığı kesirle yazar: `bir tam onda bes` = 1,5,
  `iki tam yuzde yirmi bes` = 2,25. `tam`, `onda`, `binde` ve kesir bağlamındaki `yuzde`
  `_FRACTION_CONNECTIVE`'dedir ve küme yine kapalı — dil yeni ondalık bağlacı üretmiyor.
  **Ama bunlar bir tutarı başlatamaz:** `tam` çok yaygın bir sözcük (`tam 5 dakika`,
  `tamamen ucretsiz`) ve ondalık asla onunla başlamaz, önünde her zaman tam kısım vardır.
  Başlatabilseydi `tamamen liraya endeksli` uydurma fiyat olurdu. `yuzde(?!n)` koruması burada
  da geçerli. Birleşme grameri değişmedi; tutar ile birimin arasındaki boşluk da `_JOINED`
  oldu, çünkü `bir-tam-onda-bes-lira` o boşluğu da tireliyor.
- **`yuzden` + para birimi bilerek reddedilir.** Gerçek bir belirsizlik: bağlaç ("bu yüzden
  liraya geçtik") ile `yuz`+`den` ("yüzden fazla lira") aynı yazılıyor. Ayırt edilemediği için
  kural reddetme tarafında duruyor — bedeli bir üretim tekrarı, tersi ise müşterinin önünde
  uydurma fiyat. Bu davranış W17 takip 3'ten **önce** de vardı; testte pinli.
- **Tek harflik kök gramerle savunulur, alfabeyle değil.** `T L` kısaltmasının eki `_SUFFIX`
  alfabesiyle ayırt edilemez (`ezzetli` de o alfabededir); bu yüzden ayırıcısız biçim **kapalı
  Türkçe ek kümesinin dizisiyle** (`_SUFFIX_SEQUENCE`) eşleşir: `T Lye` yakalanır, "Şef T.
  Lezzetli" yakalanmaz. Kapalı küme **yalnızca burada** kullanılır: alfabe fazla kabul eder
  (bedeli bir üretim tekrarı), unutulmuş bir ek ise az kabul eder (bedeli bir atlatma) — çok
  harfli köklerde fazla kabul etmek doğru taraftır.
- **Katlama yalnızca eşleştirme içindir; saklanan değer `normalize_encoding`'den geçer.** İkinci
  fonksiyon kodlamayı (görünmezler, NFD, fullwidth, confusable, büyük/küçük harf) katlar ama
  harfleri korur. `_scene_tags` saklanan bir değer üretir ve 2C/2E onu video-understanding
  etiketleriyle eşleştirir — `ürün`→`urun` katlaması güvenlik düzeltmesi kılığında bir ürün
  hatasıdır. Saklama yolundan `normalize_for_matching` çağrılmaz.
- **Katlama yetmez, alfabe de sınırlıdır** (W16 2. tur, W17'de katlamayla birleşti). Katlama
  "aynı harfin başka yazımı"na cevap verir; "kimsenin aklına gelmeyen alfabeden bir harf"e
  veremez — 1. tur Kiril ve Yunan'ı katladı, doğrulama turu Coptic `Ⲧ` ile geldi. `parse_text`,
  **ASCII'ye katlanamayan bir harf** taşıyan literali hiçbir kural çalışmadan
  `SCRIPT_UNSUPPORTED_CHARACTER` ile reddeder. Sınırı `contains_unsupported_letter` ile katlamayı
  **aynı fonksiyon** hesaplar (`_ascii_fold`): kuralların okuyabildiği harf kümesi ile parser'ın
  kabul ettiği küme ayrışamaz — `ṬL` tam olarak o ayrışmanın dışarıdan görünüşüydü. Fail-closed:
  haritalanmamış harf (`ᴛ`, `ɐ`) reddedilir, en kötü ihtimalle meşru bir ad reddedilir ve harita
  bir satır büyür. Harf olmayanlar serbesttir — ama Unicode'un **rakam** saydığı her kod noktası
  (`⓵`, `❶`: `\d` eşleşmez) normalizasyonda ASCII rakama indirilir, çünkü "başka sayı sisteminin
  rakamı fiyat kuralının işi" yalnızca `\d`'nin gördüğü rakamlar için doğruydu.
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
- **Yazma yetkisi tek çizgidedir:** timeline yazma, patch, render isteği, senaryo üretimi ve
  seslendirme `content.generate`. PRD §4'te editor içerik üretir; `business.update` yalnızca
  **işletmenin kendisini** değiştirmektir.
- **Seslendirilen metin senaryonun çözülmüş dokümanıdır, isteğin değil.** `VoiceoverRequest`'in
  metin alanı yoktur — ifade edilemeyen prose kontrolden kaçamaz. Şablon (`{{price:…}}`)
  seslendirilmez; dinleyicinin duyduğu değer kaydın tuttuğu değerdir.
- **Ses süresi ölçülür, beyan edilmez.** `AudioProbePort` dosyadan türetir; sağlayıcının
  `declared_duration_ms` beyanı kayda **eklenir** ama hiçbir karar onu okumaz. §18.3'ün
  "seslendirme süresi" kontrolü, sapma kaydı ve toplamlar yalnızca ölçümü kullanır.
- **Sapma 2C'de ölçüldü, 2D'de yargılanır.** `drift_ms` = ölçülen − senaryonun hedefi; `tts.py`
  ve `tts_service.py` hâlâ eşik taşımaz. Eşik `qc.py`'nin `QcThresholds`'ünde ve config'dedir.
- **Kontrol sonucu birleştirmesi sıra-bağımsızdır ve en kötüsü kazanır** (W18 takip 2).
  `merge_check_results` bir kontrol için birden fazla cevabı `failed` > `unknown` > `passed`
  sırasına göre birleştirir; eşitlikte `RemediationPath` sırası, sonra kod ve pointer — yani
  girdileri karıştırmak kararı **ve** saklanan raporu değiştiremez. Son-yazan-kazanır hatasıydı:
  `black_frames=failed` ardından `passed` verildiğinde reddi rapordan düşürüyordu. **Sınırın iki
  yanı farklı muamele görür:** sağlayıcının aynı kontrolü iki kez cevaplaması **veridir**,
  birleştirilir; bizim kodumuzun aynı kontrolü iki kez vermesi **hatadır**,
  `QC_REPORT_DUPLICATE_RESULT` ile reddedilir — ama reddetmeden **önce** birleştirilir, böylece
  fail-closed özelliği hatanın fırlatılmasına bağlı kalmaz.
- **QC fail-closed'dır ve bir kontrolü atlamak ifade edilemez.** `build_results` `QcCheck`'in
  tamamıyla `unknown` başlar, çağıranın cevaplarıyla üzerine yazılır; `decide` eksik kümeyi
  `QC_REPORT_INCOMPLETE` ile reddeder. Tek bir `unknown` kararı `needs_review`'a düşürür.
  Ölçmediğini onaylayan bir QC, QC'siz olmaktan kötüdür.
- **`QcCheck` = §19.4'ün satırları, aynı sırayla.** Sıra iki iş yapar: rapor onu dolaşır, `decide`
  öneriyi ondan seçer. `CHECK_POLICIES` her üyeyi kapsamak zorunda — politikasız bir kontrol
  eklemek testi düşürür, çünkü rapordaki delik tam olarak bu modülün önlediği şeydir.
- **QC karar verir, eylem yapmaz.** `ContentQcService` yapıcısında render/senaryo/tts portu
  **yoktur**; yeniden render, sağlayıcı değişimi ve deneme sınırı 2E'nindir. Döngüyü sınırı
  gelmeden kurmak, sınırsız render döngüsünü kontrol edicinin içine gömmek olurdu.
- **Proje sıralayıcıdır, sahip değildir.** `ContentProjectAdvanceService` hiçbir sağlayıcı portu
  taşımaz; her adım o işi zaten yapan servisi çağırır (`ScriptGenerationService`,
  `VoiceoverService`, `ContentTimelineService`), kendi yetkisi ve idempotency'siyle. Yapıcının
  şekli iddianın kendisidir. Mevcut tekil uçlar değişmedi; proje bağlamı bir katmandır.
- **Geçiş tablosu kapalı ve total.** `next_state` `(durum, olay)` çarpımının tamamı için cevap
  verir; §20'nin çizmediği çift `None` döner ve `require_next_state` onu hataya çevirir. Tanımsız
  geçiş kod hatasıdır, veri hatası değil. Tek ekleme `STEP_FAILED` — §20 `FAILED`'a yalnızca
  `QUALITY_CHECK`/`PUBLISHING`'den geliyor, oysa senaryosu düşen projenin gidecek yeri yok.
- **Döngü sınırı `lifecycle.py`'de, servis katmanında değil.** `decide_after_qc` tavana ulaşınca
  hiçbir girdi için "retry" dönmez ve `render_attempts` render **istenmeden önce** okunur;
  sınırsız döngü ifade edilemez. Sayaç QC başarısızlığıyla render başarısızlığı arasında
  paylaşılır — ikisi arasında gidip gelmek fazladan render satın alamaz.
- **Proje satırının kendisi dayanıklı job'dır.** Ayrı `jobs` satırı yok: sıralayıcının durumu
  zaten sonucudur, ikisini iki tabloya yazmak çökme sonrası iki cevap üretirdi. `next_check_at`
  hem sıralama anahtarı hem lease'tir; claim onu ileri iter, ölen worker'ın projesi lease dolunca
  serbest kalır ve adım baştan koşar — her alt çağrının idempotency anahtarı deterministik.
- **Her adım iki transaction'dır: claim + settle.** Aradaki iş açık transaction olmadan koşar,
  çünkü alt servisler kendilerininkini açar. Adımların *okumaları* da kendi `begin()`'i içindedir
  ve dışarıya düz değer çıkar: hem iç içe transaction olmasın diye, hem de kapanmış bir session'a
  karşı lazy-load olmasın diye.
- **QC olayı render'ı başarılı yapan transaction'da yazılır.** `content.qc.requested` +
  `render_outputs.qc_claimed_at`; tarama seyrek bir süpürmeye düştü. W18'in "index tek başına
  çözmüyor" ölçümünün cevabı sorgunun yeniden şekillenmesiydi, index'in eklenmesi değil.
- **Sahne etiketi iki tarafta aynı yazımla karşılaştırılır.** `normalize_scene_tag`
  `script._scene_tags`'in uyguladığı iki adımın aynısıdır (`normalize_encoding` + ayırıcı → alt
  çizgi) ve **eşleştirme katlaması değildir** — `ürün`ü `urun` yapmak eşitliğin bir tarafını
  bozardı. Yalnızca karşılaştırmada bir ek adım var (`_match_key`): Türkçe küçük harf `I`'yi `ı`
  yapıyor, bu yüzden `PREPARATION` yazan sağlayıcı ile `preparation` isteyen senaryo asla
  buluşamazdı. Saklanan değer değişmez.
- **Koşunun başarısızlığı ile videonun başarısızlığı ayrı sütunlardır.** `RenderQcReport.status`
  koşuyu, `verdict` çıktıyı anlatır. Ölçüm alınamadan denemeler tükenirse satır
  `failed` + `needs_review` + `failure_code` ile kapanır — `pending`'de bırakmak, kimsenin
  kontrol etmediği ve kontrol edilmediği görülemeyen bir render demek olurdu.
- **"Uygulanamaz" ile "ölçülmedi" aynı şey değildir.** Seslendirmesi olmayan timeline'ın senkron
  kontrolü `passed` + `applicable: false`'tır: dokümandan okunan bir gerçek. `unknown` yalnızca
  kimsenin bakmadığı durumdur.
- **Rapor değer taşımaz.** Kontrol sonuçları pointer ve kod tutar; çözülmüş fiyat, çizilen metin
  ve object key rapora girmez. QC raporu süresiz saklanıyor; bir fiyatın yazıldığı ikinci yer
  olamaz.
- **Fiyat/tarih uyumu kaydın kendi tarihinden okunur.** Render çözdüğü değeri saklamaz, bu yüzden
  karşılaştırma "değer ↔ değer" değil "kayıt ne zaman değişti ↔ render ne zaman bitti"dir.
  `product_prices` append-only olduğu için bu kesin. `approved_ctas`'ta değişiklik damgası yok:
  yerinde düzenlenmiş bir CTA görülmüyor, yalnızca kaybolması yakalanıyor — `changed_at=None`
  bunu gizlemek yerine söylüyor.
- **Timeline yasak terim eşleşmesi `script.forbidden_matcher`'ı import eder.** İkinci bir katlama
  uygulaması yazılmaz; import `_forbidden_matcher` içinde geç bağlanır çünkü `script.py` bu
  modülden `VerifiedValue` alıyor (döngü). Literal metin `contains_unsupported_letter` ile de
  sınırlanır (`TIMELINE_UNSUPPORTED_CHARACTER`). **Çekim eşleşmesi yoktur** (PM, W18): `şeker`
  yasakken `şekerli`, `az` yasakken `lezzetli` serbest.
- **`voiceover` ses track'i `voiceover_assets`'i gösterir**, `media_assets`'i değil. Bu yüzden
  `Timeline.asset_ids` voiceover kimliğini içermez (`voiceover_ids` ayrı): worker onu kaynak
  video sanıp materialize etmeye çalışırdı.

## Dosyalar

| Dosya | İş |
|---|---|
| `script.py` | §18.1 contract'ı: katı parse, slot/literal ayrımı, uydurma fiyat-tarih ve URL tespiti (kalıp literalleri **katlanmış alfabede**), `T.L.`/`T L` kısaltma grameri, yasak terim eşleyici, `ScriptGenerationPort`, `ProviderDescriptor`, `RouteSnapshot` (her kabiliyet aynı route kaydını kullanır), prompt payload kurucusu |
| `text_normalization.py` | `normalize_for_matching` — eşleştirme katlaması (süslü rakam açma → `Cf/Cn/Co/Cs` çıkarma → NFKC → kalan görünmez/birleşen işaretler → confusable → Türkçe küçük harf → **Latin harflerin ASCII'ye katlanması**) · `normalize_encoding` — saklanan değerler için aynısının Latin adımı olmayan hâli · `contains_unsupported_letter` — alfabe kısıtı, katlamanın kendisiyle ifade edilir. Kural içermez; `script.py` ve `validation.py` (2D) aynı fonksiyonları çağırır, üçüncü bir çağıran testle yasaktır |
| `qc.py` | §19.4 kontrol kümesi (`QcCheck`), `CHECK_POLICIES`, saf karar tablosu (`decide`), `build_results` bütünlük garantisi, `QcThresholds` anlık görüntüsü, deterministik değerlendiriciler, doğrulanmış kayıt denetimi (`audit_verified_sources`), `MediaQcProbePort` + `QcMeasurement`, `VisualQcPort` + `VisualQcDisabledError` |
| `qc_service.py` | `ContentQcService` — QC açılmamış `succeeded` render'ı claim, dayanıklı job (durum/timeout/deneme/correlation/dead-letter), materialize + ölçüm + VLM çağrısı, `provider_usage`, iki transaction · `ContentQcReportService` — yalnızca okuma (yetki + rapor), ölçüm portu taşımaz |
| `lifecycle.py` | §20'nin **saf** yarısı: `ProjectState`/`ProjectEvent`, kapalı ve total geçiş tablosu (`next_state`, `require_next_state`), `decide_after_qc`/`decide_after_render_failure` karar tablosu ve döngü sınırı, `compose_timeline` (senaryo + sahneler → §18.2 dokümanı), `normalize_scene_tag`, dokümante hata kodları. Session/saat/sağlayıcı yok |
| `project_service.py` | `ContentProjectService` — proje açma, medya bağlama, okuma (yetki, idempotency, audit, outbox uyandırma) · `ContentProjectAdvanceService` — claim + adım + settle; her adım alt servisi çağırır, sağlayıcı portu taşımaz · `AbandonedRunSweeper` — `pending`de kalmış senaryo/seslendirme satırlarını yaş eşiğine göre `failed`e düşürür |
| `script_service.py` | `ScriptGenerationService` — yetki, girdi doğrulama, route snapshot + ücretli çağrı + kullanım kaydı, iki transaction, idempotency, liste |
| `tts.py` | §17.3 `TTSPort` + `AudioProbePort`, kapalı `VOICE_PROFILES` registry'si (§17.6 deseni), çözülmüş senaryodan satır çıkarma (`script_lines`), `VoiceoverSegment` ve sapma aritmetiği, obje anahtarı |
| `tts_service.py` | `VoiceoverService` — yetki, senaryo durumu, ses profili çözümü, route snapshot + satır başına çağrı + ffprobe ölçümü + depolama, çağrı başına `provider_usage`, kısmi koşu kaydı, idempotency, liste |
| `timeline.py` | §18.2 dokümanı: kapalı şema, çapa/stil/metin-kaynağı enum'ları, parse + serialize |
| `validation.py` | §18.3 kuralları (saf), `ValidationContext`, `layout_text_in_frame` (2D ölçülen kareyle de çağırır), `resolve_overlay_text`, `script.forbidden_matcher` + alfabe kısıtı üzerinden yasak terim kapısı, dokümante hata kodları |
| `patch.py` | K4 parametrik düzenleme: kapalı operasyon kümesi, segment sınırına snap, track yeniden dizilimi, `serialize_patch` (idempotency fingerprint'inin alındığı kanonik biçim) |
| `render.py` | `RenderPort`, `RenderCapabilities`, `RenderPlan`, §19.2 profilleri, disclosure/provenance durumları |
| `models.py` | `content_timelines` (revizyon başına satır) + `render_outputs` (+ `qc_claimed_at` ve "QC bekleyenler" kısmi index'i) + `content_scripts` + `prompt_templates` + `voiceover_assets` (segmentler JSONB, ölçülmüş toplam ve sapma gerçek sütun) + `render_qc_reports` (kontroller ve eşik anlık görüntüsü JSONB; `verdict`/`recommended_path` `pending` satırında bile NOT NULL ve karamsar) + `content_projects` (durum, sayaçlar, üretilen artefakt referansları, lease) + `content_project_transitions` (proje başına sıralı, `from_state` yalnız girişte NULL) |
| `repository.py` | `ContentRepository` (tenant-kapsamlı; senaryo, prompt sürümü, seslendirme, QC raporu ve proje okumaları dahil) + `ContentFactsReader` (`voiceover_facts`, `voiceover_drift`, `voiceover_object_keys`, `scene_candidates`, `verified_record_states` dahil) + `ScriptFactsReader` (marka/katalog/medya okuma penceresi) + render job claim + QC claim (`qc_claimed_at` damgasını da o basar) + proje claim + terk edilmiş `pending` satır claim'leri |
| `service.py` | `ContentTimelineService` — yetki, doğrulama, revizyon, render isteği, idempotency, audit |
| `render_service.py` | `ContentRenderService` — job claim, materialize, render, depolama, dead-letter |
| `policy.py` | `ContentAction` → merkezî `Permission` eşlemesi (**her yazma** `content.generate`, her okuma `business.read`) |
| `domain.py` | `format_money` — doğrulanmış değerin saf gösterimi |

## Gereksinim, karar, mimari

- [40a-content-planning-scenarios.md](../../../../../docs/product/requirements/40a-content-planning-scenarios.md) (§14, §14.8 seslendirmeli reklam) ·
  [40b-scenario-render-lifecycle.md](../../../../../docs/product/requirements/40b-scenario-render-lifecycle.md) (§18, §19) ·
  [35-ai-routing-cost.md](../../../../../docs/product/requirements/35-ai-routing-cost.md) (§17.5 çıktı güvenliği, §17.6 prompt versiyonlama) ·
  [99-external-platform-facts.md](../../../../../docs/product/requirements/99-external-platform-facts.md) (Meta AI etiketi, C2PA)
- [ADR-004](../../../../../docs/adr/ADR-004-provider-adapter-pattern.md) · [ADR-007](../../../../../docs/adr/ADR-007-media-analysis-provider-routing.md) ·
  [ADR-013](../../../../../docs/adr/ADR-013-single-server-deployment-topology.md) ·
  [ADR-015](../../../../../docs/adr/ADR-015-parametric-editing-model.md) · `ADR-016-render-port.md`
- Mimari: [content-render.md](../../../../../docs/architecture/content-render.md) ·
  [error-handling.md](../../../../../docs/architecture/error-handling.md) (SCRIPT_* / TTS_* /
  VOICEOVER_* katalogları) ·
  [Phase 2 planı](../../../../../docs/plans/active/phase-2-content-generation.md) §2

## Testler

`tests/unit/test_content_timeline.py` · `tests/unit/test_render_port.py` ·
`tests/unit/test_content_render_worker.py` · `tests/unit/test_content_script_unit.py` ·
`tests/unit/test_voiceover_unit.py` · `tests/unit/test_content_qc_unit.py` ·
`tests/unit/test_content_lifecycle_unit.py` · `tests/unit/test_qc_probe.py` ·
`tests/unit/test_visual_qc_port.py` ·
`tests/unit/test_timeline_forbidden_terms.py` · `tests/integration/test_content_render.py` ·
`tests/integration/test_content_script.py` · `tests/integration/test_content_voiceover.py` ·
`tests/integration/test_content_qc.py` · `tests/integration/test_content_lifecycle.py`
