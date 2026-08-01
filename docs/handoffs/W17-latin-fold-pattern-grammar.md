# W17 — Latin harf katlaması (iki yön) + kalıp grameri

**Dal:** `fix/w17-latin-fold` · **Base:** `main` · **Migration slotu: YOK** (migration dosyalarına dokunma)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Neden bu iş:** Dedektörde bilinen ve bilinçli bırakılmış son açık sınıfları kapatır. W16 2. tur raporunun "PM'e somut istek" bölümü aynen kabul edildi: eksik aksan (`165 turk lirasi`) ve fazla/farklı aksan (`165 ṬL`, `165 ŦL`) **tek bir katlamanın iki yönüdür** ve birlikte kapatılır; ayrı turlara bölünürse ikinci yön bir sonraki doğrulama turunda kritik olarak geri gelir. Üstüne 1. turdan beri bekleyen üç kalıp-grameri açığı eklenir (`T.L.` / `T L`, `⑴⑸`).

## Okunacaklar

1. [`docs/STATUS.md`](../STATUS.md)
2. [W16](W16-verification-followups-3.md) — **iki rapor ve iki doğrulama bölümünün tamamı**; özellikle "Rapor — düzeltme turu 2"nin 1. ve 5. maddeleri (alfabe kısıtı ve `ṬL` analizi)
3. `services/api/app/modules/content/text_normalization.py` ve `script.py`
4. `services/api/app/modules/content/CLAUDE.md` — değişmezler (özellikle "Katlama yetmez, alfabe de sınırlıdır")

## Kapsam ve PM kararları

### 1. Aksan katlaması — yalnızca eşleştirme için

- `normalize_for_matching`'e yeni adım: Latin harflerde NFD ayrıştırması sonrası birleşen işaretleri (`Mn`) at → `ṭ`→`t`, `é`→`e`, `ş`→`s`, `ğ`→`g`, `ü`→`u`, `ö`→`o`, `ç`→`c`, `ı`/`İ`→`i`. Türkçe katlamayla etkileşimi sen tasarla; sonuç alfabesi **ASCII a–z + rakam + noktalama** olmalı.
- **Kalıp literalleri katlanmış alfabeyle yeniden yazılır** (`türk lirası`→`turk lirasi`, `yüzde`→`yuzde`, `ağustos`→`agustos`, …). Böylece `165 turk lirasi` de `165 ṬL` de aynı kalıba düşer.
- **Yasak terim eşleşmesi de katlanır** (PM ONAYI: marka `şeker`'i yasakladıysa `seker` de yasak — güvenli taraf).
- **Saklanan hiçbir değere uygulanmaz:** `_scene_tags`'in ürettiği/sakladığı değer bugünkü gibi kalır (W16 raporunun uyarısı — video-understanding etiketleriyle eşleşme bozulmasın). Katlama yalnızca `find_fabrication`/`contains_url`/yasak-terim yolunda.

### 2. Ayrıştırılamayan Latin genişletmeleri — sınıf, fail-closed

NFD tabanı ASCII olmayan ve Türkçe kümesinde de olmayan Latin harfleri (`Ŧ`, `Ⱦ`, `Ƭ`, `Đ`, `Ł`, `Ø`, `Æ`, `Œ`, `ß`, `Þ`, `Ð`, …) için kural:

- **Küçük, kapalı bir standart katlama haritası** (Avrupa Latin genişletmeleri sonlu ve kararlıdır: `Ł`→`l`, `Đ`→`d`, `Ø`→`o`, `Æ`→`ae`, `Œ`→`oe`, `ß`→`ss`, …) — haritadaysa katlanır;
- **haritada yoksa `SCRIPT_UNSUPPORTED_CHARACTER` ile reddedilir** (mevcut alfabe kısıtı deseni). Fail-closed olduğu için bu bir "bilinen-kötü listesi" değil: haritalanmamış hiçbir şey *geçemez*, en kötü ihtimalle meşru bir işletme adı reddedilir ve harita bir satır büyür.
- Sınır davranışı testle pinlenir: `165 ṬL` ve `165 ŦL` reddedilir; "Café Nero", "Łukasz Kebap" gibi meşru adlar geçer.

### 3. Kalıp grameri

- **`T.L.` / `T L`:** para birimi kısaltması olarak `T` ve `L` arasında nokta/boşluk kabul edilir — ama yalnızca **tek harf token** olarak (kelime öneki değil): `165 T.L.` ve `165 T L` yakalanır, `165 tatlı lezzet` **yakalanmaz**.
- **`⑴⑸ TL`:** NFKC'nin parantezli rakamı `(1)(5)`'e açması kalıbın bitişiklik şartını bozuyor — rakam dizisi içindeki NFKC-kaynaklı noktalamayı kalıp veya normalizasyon düzeyinde ele al (tasarım senin; iki seçeneğin de yanlış pozitif maliyetini raporda karşılaştır: `(1) madde (5) fıkra` gibi meşru metin etkilenmemeli).

## Kapsam dışı (dokunma)

- Timeline `forbidden_matcher` birleştirmesi → **2D** (senin katlamanı import edecek; fonksiyonları genel amaçlı bırak).
- Redaksiyon (`core/logging.py`) — bu turda dokunma.
- Bilinçli politika pinleri (Ağustos böceği, yüzde yüz pamuk) değişmez; katlama sonrası da reddedilmeye devam ettikleri testle korunur.
- `docs/index.md`, `docs/adr/README.md`, `migrations/` tamamı.

## Dokunulacak dosyalar (ilan)

```
services/api/app/modules/content/text_normalization.py     (katlama adımları + harita)
services/api/app/modules/content/script.py                 (kalıp literalleri + T.L./⑴⑸ grameri)
services/api/tests/unit/test_content_script_unit.py
services/api/tests/integration/test_content_script.py
services/api/app/modules/content/CLAUDE.md                 (değişmez güncellemesi: "bilinen kalan açık" satırı kapanır)
docs/architecture/error-handling.md                        (yalnızca davranış açıklaması değişirse)
docs/handoffs/W17-latin-fold-pattern-grammar.md            (rapor)
```

## Kabul kriterleri

1. **Reddedilen sayılı girdiler:** `165 turk lirasi` · `yuzde yirmi indirim` · `1 agustos` · `yuz altmis bes lira` · `165 ṬL` · `165 ŦL` · `165 ⱦl` · `165 T.L.` · `165 T L` · `⑴⑸ TL` — hepsi hem birim testte hem HTTP'de (`document IS NULL`).
2. **Yanlış pozitif pinleri:** tam Türkçe alfabeli sıradan pazarlama kopyası · `165 tatlı lezzet` · `(1) madde (5) fıkra` benzeri meşru noktalama · "Café Nero" / "Łukasz Kebap" geçer; mevcut iki bilinçli-politika pini reddedilmeye devam eder.
3. Yasak terim: `şeker` yasakken `seker`, `sekER`, `ṣeker` yakalanır (katlama testi).
4. `_scene_tags`'in sakladığı değerlerin **değişmediğini** sabitleyen test (katlama saklamaya sızmadı).
5. **Kendi düzeltmene düşman gözle saldır** ve tabloyu rapora yaz: farklı aksan kombinasyonları, harita dışı Latin genişletmeleri (fail-closed reddi doğrula), katlama + görünmez + confusable kombinasyonları, `T`+`L` çevresinde boşluk/noktalama varyantları.
6. `make verify` yeşil; taban **792**'nin altına düşmez; migration yok; kontrat farksız (yeni `meta.issue` değeri şema değildir).
7. Rapor + araç zinciri sürümleri. **Merge etme, dalda bırak.**

## ADR numara kuralı

Gerçek karar çıkarsa `ADR-XXX-<konu>.md`; numarayı PM verir.

## Rapor — 2026-08-02 · yürüten oturum (Opus 5)

**Dal:** `fix/w17-latin-fold` (base `main` @ `3d4e4de`) · **Commit'ler:** `3bef588` ·
**Durum:** tamamlandı, dalda bırakıldı

### 1 — Katlama iki yönde, tek adımda

`normalize_for_matching` artık **ASCII'ye kadar iniyor**: `_ascii_fold` her harfi kurulduğu ASCII
harfe indiriyor. İki basamak, sırayla:

1. **Ayrıştırma** (NFD → birleşen işaretleri at). Türkçenin tamamını ve Avrupa'nın çoğunu tek
   kuralla kapatır: `ş` = `s`+sedil, `ṭ` = `t`+alt nokta, `é` = `e`+aksan.
2. **Unicode adı.** Ayrıştırması olmayan tek kod noktaları için — `Ŧ`, `Đ`, `Ł`, `Ø`, `ı`, `ß` —
   karakterin **kendi adı** neyden kurulduğunu söylüyor: `LATIN CAPITAL LETTER T WITH STROKE`.
   Ad `LATIN (CAPITAL|SMALL)? (LETTER|LIGATURE) <taban>( WITH …)?` olarak ayrıştırılıyor, taban
   `_NAMED_BASES`'ten çözülüyor.

**Neden harita değil de ad okuması:** WO "küçük, kapalı bir standart harita" diyordu; elle yazılmış
bir harita `Ŧ`'yi kapatır, saldırgan `Ⱦ`'yi dener, o da kapatılır, sonraki tur `Ƭ`/`Ț`/`Ʈ` ile
gelir — W16'nın Coptic'te kaybettiği savunmanın aynı şekli. Ad okuması `LATIN … LETTER T WITH
<herhangi bir şey>` sınıfının **tamamını** tek kuralla kapatıyor ve yeni bir Unicode sürümüyle
bayatlamıyor, çünkü ad yorumlayıcıyla birlikte geliyor, bu dosyayla değil.

`_NAMED_BASES` yine de bir **allowlist**, çünkü ad kendi katlamasını yazmıyor: `THORN` → `th`
(`thorn` değil), `SCHWA` → `e`. Tek harfli tabanlar (A–Z) üretiliyor, kelime olanlar sayılı.
Haritada olmayan taban → `None` → **fail-closed**: `ᴛ` (`SMALL CAPITAL T`), `ɐ` (`TURNED A`),
`ƻ` (`TWO WITH STROKE`) `SCRIPT_UNSUPPORTED_CHARACTER` ile reddediliyor. Yanlış tahmin edip
sessizce yanlış katlamak yerine reddetmek doğru yön: maliyeti bir üretim tekrarı ve bir satır.

**Alfabe kısıtı ile katlama artık aynı fonksiyon.** `contains_unsupported_letter` "Latin mi"
sormayı bıraktı, "katlama bu harfi ASCII'de yazabiliyor mu" diye soruyor — ikisi de `_ascii_fold`.
`ṬL`'nin ta kendisi bu iki sorunun ayrı ayrı cevaplanmasının sonucuydu: `Ṭ` kabul kuralı için
yeterince Latin, eşleştirme kuralları için yeterince yabancıydı ve aradan yürüdü. Tek fonksiyon
olduğu için bir daha ayrışamazlar; testi var.

**Kalıp literalleri katlanmış alfabede yeniden yazıldı** (`turk lirasi`, `yuzde`, `agustos`,
`subat`, `mayis`, `kurus`, `ceyrek`, `altmis`, `dolarlik`, …). Bu, kodu okurken yanlış görünüyor
ve bilinçli: eşleştiği metinde de aksan yok. `script.py`'de bunu söyleyen bir blok yorumu var,
çünkü Türkçe yazımıyla eklenecek bir sonraki kural hiç eşleşmezdi.

**Yasak terim eşleşmesi de katlanıyor** (PM onayı): `şeker` yasaklıysa `seker`, `sekER`, `ṣeker`
de yasak.

### 2 — Saklanan değer kımıldamadı

`normalize_encoding` = katlamanın Latin adımı olmayan hâli, yani **W16'nın
`normalize_for_matching`'i birebir**. `_scene_tags` ve `_slot_kind` buna geçti.

Bunu iddia etmek yerine ölçtüm: W16'nın modülü `git show main:` ile alınıp yanına yüklendi ve
**atanmış her Unicode kod noktası tek tek** iki fonksiyondan geçirildi — `0 fark`. Yani sahne
etiketi üreten yol bu turda hiç oynamadı; `ürün` `ürün` kaldı, `urun` olmadı (video-understanding
etiketleriyle eşleşme bozulmadı).

Adlandırma bilinçli: `normalize_for_matching`'in docstring'i "asla saklanmaz" diyor ve artık bunu
hak ediyor. Alternatif — eşleştirme yolunun ayrı bir isim alması — yanlış yön: unutulduğunda
sessizce **güvenlik** kaybettirirdi; bu yönde unutulduğunda gürültülü bir **ürün** hatası verir.

### 3 — Kalıp grameri

**`T.L.` / `T L`.** `_TL_ABBREVIATION = t[\W_]+l[\W_]{0,2}`. Ayırıcı, WO'nun saydığı iki karakter
değil, **kelime karakteri olmayan herhangi bir dizi** — `T·L`, `T-L`, `T/L`, `T , L` aynı
kısaltmayı okutur ve ayırıcı listelemek bir sonraki turun bulgusudur. Sınırsız olması güvenli,
çünkü dizi hiçbir kelime karakteri taşıyamaz: iki harfin arasına giren *herhangi bir kelime*
eşleşmeyi bitirir ("1 t. tuz, 2 l. su" bir tarif, para birimi değil). Prose'a bulaşmamasını
sağlayan şey her iki harfin de **tek harflik token** olması: grubun her kullanımı sonuna `(?!\w)`
taşıyor ve solunu çapalıyor, o yüzden `165 tatlı lezzet` yakalanmıyor — `t`'yi ayırıcı değil harf
izliyor.

**`⑴⑸ TL`.** Normalizasyon düzeyinde çözüldü, kalıp düzeyinde değil. NFKC `⑴`'i `(1)` yapıp
**rakam dizisinin içine noktalama sokuyor**; tek kod noktasının NFKC açılımı süslü bir sayıysa
süs atılıyor. İki seçeneğin yanlış pozitif maliyeti WO'nun istediği gibi karşılaştırıldı:

| Seçenek | Neyi kaçırır | Yanlış pozitif maliyeti |
|---|---|---|
| **Normalizasyon (seçilen)** | — | **Sıfır.** Yalnızca *zaten rakam taklidi yapan tek bir karakter* üzerinde çalışır; `(1) madde (5) fıkra` ASCII parantezle yazılır ve bu adımın menzilinde değildir |
| Kalıpta rakam arası noktalamaya izin | — | `(1) madde (5) fıkra`, `(1) ve (5) numaralı şubeler` gibi meşru metin fiyat/tarih sayılmaya başlar; ayrıca `_NUMBER` her kalıpta kullanıldığı için maliyet tarih kurallarına da yayılır |

İkisi de HTTP testinde pinli: süslü rakam reddediliyor, madde/fıkra metni `201` alıyor.

### 4 — Kendi düzeltmeme saldırı (kabul kriteri 5)

`sp-w17` konteynerinde, gerçek kodda, **136 girdi**. Ölçüt tek bir literalin bütün hattan geçirilmesi
(`parse_text` şema kapısı → `find_fabrication` → `contains_url`).

| Sınıf | Denenen | Sonuç |
|---|---|---|
| Aksan **eksik** (insan yazımı) | `turk lirasi` (+caps), `lirasi`, `yuzde`, `yuzde yuz`, `agustos`, `subat`, `mayis`, `aralik`, `eylul`, `otuz bir aralik`, `yuz altmis bes lira`, `uc yuz lira`, `dolarlik`, `kurus`, `ceyrek milyon` | **16/16 yakalandı** |
| Aksan **fazla/farklı** | `T`/`t` üzerinde U+1E6C · U+1E6D · U+0166 · U+0167 · U+023E · U+2C66 · U+01AC · U+021A · U+0162 · U+0164; `L` üzerinde U+0139 · U+0141 · U+013D · U+013F; ikisi birden; `lïrâ`, `dölâr`, `aǧüstõs`, `yûzdë`, `tûrk lïrasi` | **20/20 yakalandı** |
| Harita dışı Latin (fail-closed) | `ᴛ`, `ʟ`, `ᴀ`, `ɐ`, `ƻ`, `Ƅ`, `ʒ`, `ɩ`, `ʔ`, `ʼ` | **9/10 `SCRIPT_UNSUPPORTED_CHARACTER`**; `ʈ` (`T WITH RETROFLEX HOOK`) adından çözülüp `t`'ye katlandı ve **fiyat olarak** yakalandı — ikisi de ret |
| Diğer alfabeler (W16 kuralı) | Coptic (karışık ve tamamen), Cherokee, Kiril, Yunan, Ermeni, Deseret | **7/7 reddedildi** |
| Katlama + görünmez + confusable | stroke+ZWSP, alt nokta+U+2065, stroke+birleşen işaret, aksansız+ZWNJ, aksansız+soft hyphen, aksansız+fullwidth, süslü+fullwidth, Kiril+stroke, BOM+alt nokta+ZWJ, Hangul dolgu+aksansız, NFD `t`+combining stroke, braille boşluk+aksansız | **12/12 engellendi** |
| `T`/`L` çevresinde boşluk ve noktalama | nokta (tek/çift/boşluklu), boşluk (tek/çift/NBSP), orta nokta, tire, en dash, eğik çizgi, alt çizgi, virgül, iki nokta, üç nokta, ZWSP, önek biçimleri, süslü harfle birlikte, yazılı sayıyla | **21/21 yakalandı** (ikisi ilk sürümde geçti, bkz. aşağısı) |
| Yeniden gruplanmış rakam | `⑴⑸`, `⒈⒌`, `⑤`, `⓵⓹`, `❶❺`, süslü rakam + süslü harf, süslü rakam + `yuzde` | **7/7 yakalandı** (biri ilk sürümde geçti) |
| **Yanlış pozitif kontrolü** | Türkçe alfabesinin tamamı, sıradan pazarlama kopyası (aksanlı ve aksansız), `165 tatlı lezzet`, `Şef T. Lezzetli tarifler`, `(1) madde (5) fıkra`, `(1) ve (5) numaralı şubeler`, `3 tabak, 2 limon`, `Menu: 4 tost, 2 limonata`, `Café Nero`, `Łukasz Kebap`, `Straße Burger`, `Smørrebrød ve Æblekage`, `Lezzet.Çok beğenildi`, emoji, `₺` tek başına, `5 t limon`, `2 l süt`, `üç dakikada`, `Dolar gibi değerli`, `Ağustos esintisiyle` | **25/25 doğru geçti** |
| Bilinçli politika pinleri | `1 Ağustos böceğiyle tanışın`, `Yüzde yüz pamuk dokusuyla` ve **aksansız hâli** | **3/3 reddedilmeye devam** |
| URL | aksansız alan adı, katlamayla oluşan `.com`, süslü `www` | **3/3 yakalandı** |

**Kendi düzeltmemde bulduğum ve düzelttiğim iki açık** (ikisi de testle pinlendi):

| Bulgu | İlk sürüm | Neden geçti | Düzeltme |
|---|---|---|---|
| `165 T....L`, `165 T ... L` | `PASS` | Ayırıcı `{1,3}` ile sınırlıydı — "kimin aklına geldiyse o kadar" sınırı | Ayırıcı sınırsız, ama kelime karakteri taşıyamaz |
| `⓵⓹ TL`, `❶❺ TL` | `PASS` | W16 "harf olmayan zaten fiyat kuralının işi" diyordu; bu **`\d`'nin gördüğü** rakamlar için doğru (`١٦٥` yakalanıyor), `No` kategorisindeki `⓵` için değil — NFKC de dokunmuyor | `unicodedata.digit()` değeri olan her kod noktası ASCII rakama iniyor (sınıf, örnek değil) |

**Geçemediğim ve bilinçli bıraktığım:** yok. Kalan tek sınır, aşağıdaki "belirtmem gerekenler"in
2. maddesindeki fail-closed ret — bir açık değil, ürün maliyeti.

### Kapsam dışı bıraktıklarım ve nedeni

- **Timeline `forbidden_matcher` birleştirmesi** yapılmadı (WO 2D'ye bıraktı). Fonksiyonlar genel
  amaçlı ve bağımsız test edilebilir; 2D `normalize_for_matching` + `contains_unsupported_letter`
  ikilisini olduğu gibi import edebilir.
- **Redaksiyon (`core/logging.py`)** — bu turda dokunulmadı.
- **`docs/index.md`, `docs/adr/README.md`, `migrations/`** — dokunulmadı; yeni ADR dosyası yok.
- **ADR yazılmadı.** Gerçek bir karar çıktı ("alfabe kısıtı = katlamanın kendisi" ve "saklanan
  değer ayrı fonksiyon"), ama ikisi de mevcut değişmez listesine sığdı ve
  `modules/content/CLAUDE.md` ile `error-handling.md`'ye işlendi. PM aksini düşünüyorsa numara
  verilmeli.

### Doğrulama

Araç zinciri: **Python 3.13.14 · pytest 9.1.1 · mypy 2.3.0 · ruff 0.16.0 · unicodedata 15.1.0 ·
PostgreSQL 16.14 · MinIO · FFmpeg · Docker Engine 25.0.3 / Compose v2.24.6-desktop.1**. İzole
stack `COMPOSE_PROJECT_NAME=sp-w17` (worktree kökünden, `--env-file .env.w17`; API 8021, PG 55453,
Redis 56400, MinIO 59030/59031). Tüm koşular **konteyner içinde**.

| Kontrol | Sonuç |
|---|---|
| `ruff check` (app tests migrations scripts) | **yeşil** |
| `ruff format --check` | **yeşil** — 190 dosya |
| `mypy .` (strict) | **yeşil** — 178 dosya |
| `pytest` (`RUN_INTEGRATION_TESTS=1`, gerçek PG + MinIO + FFmpeg) | **yeşil** — **864 passed** (taban 792, +72) |
| Kontrat drift | **fark yok** — şema konteynerde yeniden üretilip `docs/generated/openapi.json` ile karşılaştırıldı, birebir aynı |
| Alembic head | `0014_voiceover_assets` — **değişmedi**; `migrations/` altında değişiklik yok |

| # | Kabul kriteri | Sonuç |
|---|---|---|
| 1 | 10 girdi hem birim hem HTTP'de reddediliyor, `document IS NULL` | ✅ Birim: `test_a_missing_or_an_unexpected_diacritic_is_the_same_figure` (10 girdi) + `test_none_of_those_can_be_resolved_into_a_document`. HTTP: `test_a_folded_or_regrouped_figure_never_reaches_a_stored_script` — 10 girdi × `422` + satır `("failed", <kod>, NULL)` |
| 2 | Yanlış pozitif pinleri | ✅ `test_the_pattern_grammar_stops_at_ordinary_punctuation` (7 girdi), `test_latin_copy_is_not_collateral_damage` (11 girdi, 4'ü yeni), HTTP'de `test_ordinary_copy_still_produces_a_script` (`201`, metin dokümanda). Politika pinleri `test_a_known_false_positive_is_pinned_rather_than_narrowed` ile duruyor |
| 3 | Yasak terim katlaması | ✅ `test_a_forbidden_term_survives_its_diacritics_being_dropped` — `şeker`/`seker`/`sekER`/`ṣeker`; `şekerli` hâlâ serbest (kelime sınırı korundu) |
| 4 | `_scene_tags` saklanan değerleri değişmedi | ✅ `test_the_matching_fold_never_reaches_a_stored_scene_tag` + `test_the_stored_fold_keeps_the_letters_and_drops_only_the_encoding`; ayrıca W16'nın fonksiyonuna karşı **atanmış her Unicode kod noktasında 0 fark** ölçüldü |
| 5 | Düşman turu + tablo | ✅ 136 girdi; bulunan iki açık düzeltildi ve pinlendi (`test_the_bypasses_this_slice_found_against_itself_stay_closed`) |
| 6 | `make verify` yeşil, taban 792'nin altına düşmüyor, migration yok, kontrat farksız | ✅ 792 → **864** |
| 7 | Rapor + araç zinciri, merge yok | ✅ |

### Açıkça belirtmem gerekenler

1. **İlan listesi dışında 1 dosyaya dokundum:** `docs/architecture/error-handling.md` — WO'nun
   koşullu izni ("yalnızca davranış açıklaması değişirse"). `SCRIPT_UNSUPPORTED_CHARACTER`'ın
   *anlamı* değişti: artık "Latin dışı harf" değil, "katlamanın ASCII'de yazamadığı harf". Yeni
   hata kodu yok, OpenAPI değişmedi.

2. **Fail-closed reddin ürün maliyeti kayda geçsin.** Haritalanmamış bir Latin harfi taşıyan
   *meşru* bir metin reddedilir ve **yeniden üretim bunu çözmez** (aynı işletme adı yine gelir).
   Bugün bunun menzilindekiler IPA/fonetik harfler ve `SMALL CAPITAL`/`TURNED` biçimleri — gerçek
   bir işletme adında beklenmez — ve `Café`, `Łukasz`, `Straße`, `Smørrebrød`, `Æblekage`
   testlerle korunuyor. Yine de bir tenant buna takılırsa çözüm `_NAMED_BASES`'e bir satırdır;
   bunu bir ürün destek yolu olarak biliniyor sayın.

3. **`ı`/`i` ayrımı eşleştirme yolunda kayboldu.** `normalize_for_matching` ikisini de `i` yapıyor
   (WO'nun istediği ASCII alfabesi). Sonucu: yasak terim listesi bu ayrımı ayırt edemez —
   `açık` yasaklıysa `acik` de yasak. Güvenli yön ve WO'nun kararı, ama bir markanın
   listesinde dar bir kelime varsa beklenenden geniş yasaklayabilir. Saklanan değerlerde ayrım
   duruyor.

4. **`SCRIPT_UNSUPPORTED_CHARACTER` hâlâ `parse_text`'te**, yani `resolve_script`'in "tüm
   sorunları birlikte topla" davranışı bu kural için geçerli değil (W16 2. turun 3. maddesi;
   değişmedi).

5. **`main`'e merge etmedim** (talimat gereği). Dal `main` (`3d4e4de`) üzerinde tek commit, yani
   ileri sarma:

   ```
   git -C A:/socialpilot-ai merge --ff-only fix/w17-latin-fold
   ```

   `origin`'e push edilmedi. Dal ve worktree, protokol gereği birleşik Codex turu bitene kadar
   duruyor.

## Doğrulama

Araç zinciri: worktree kökü `A:\socialpilot-ai` (`main` `282155c`) ·
`COMPOSE_PROJECT_NAME=sp-codex` · Docker Engine 25.0.3 · Docker Compose
v2.24.6-desktop.1 · Python 3.13.14 · pytest 9.1.1 · Ruff 0.16.0 · mypy 2.3.0 ·
unicodedata 15.1.0 · PostgreSQL 16.14 · MinIO · FFmpeg. İzole host portları
`55433`/`56380`/`59002`/`8001`; Alembic head `0014_voiceover_assets`.

| # | Bulgu | Şiddet | Yeniden üretim | Durum |
|---|---|---|---|---|
| 1 | `lirayla` para birimi çekimi kalıp dışında; dolayısıyla aksansız temel biçim ve onun Latin katlama varyantları literal fiyat olarak kalıcı dokümana ulaşabiliyor. | kritik | `find_fabrication("165 lirayla")` → `None`; `resolve_script(parse_script(...), context=...)` → `document is not None`, `issues=[]`. 792 kabul edilen Latin harfin para/tarih sözcüklerine yerleştirildiği 4.186 varyantta 294 kaçışın tamamı bu tek temel sözcüğe indirgeniyor (`lirayla`); ör. `165 lirÀyla` da geçiyor. | açık |
| 2 | Ad-tabanlı `WITH`/ligatür ve IPA/fonetik sınıflarında ikinci bir eşleştirme atlatması bulunamadı. | — | Örnekler: `165 ƮL`, `165 ŦL`, `165 ʈɬ`, `165 ﬅerlin`; ad-tabanlı tüm geçerli eşdeğer yerleştirmelerde, #1'in aynı `lirayla` kökü dışındaki 3.892 varyant yakalandı. | kabul edildi |
| 3 | Katlama + görünmez + confusable + süslü rakam bileşimleri kaçamadı. | — | Circled/dingbat/parenthesized/fullwidth/Arabic-Indic/matematiksel rakam; `Cf`/`Cn`/`Co`, combining mark, Kiril/Coptic/Greek harf ve Latin `WITH` birleşimleri: **6/6** ret. | kabul edildi |
| 4 | `T.L.` gramerinde yeni ayırıcılarla atlatma bulunamadı. | — | Ethiopic wordspace, ideographic/Arabic comma, fraction slash, interlinear separator, em space, uzun nokta dizisi, `_` ve satır sonu: **9/9** `SCRIPT_FABRICATED_PRICE`. Tarif ve ASCII madde/fıkra kontrolleri **3/3** geçti; `165 T. L. maddesi` ise para birimi grameri gereği reddedildi (ölçülen yanlış-pozitif sınırı). | kabul edildi |
| 5 | Meşru aksanlı ad ve sıradan kopya örneklerinde yeni yan etki bulunmadı. | — | `Café Nero`, `Łukasz Kebap`, `Straße Burger`, `Smørrebrød ve Æblekage`, `Æblekage`, `ǅakovo`, `Əge Lokantası` ve Türkçe promosyon metni: **8/8** geçti. | kabul edildi |
| 6 | Mevcut kapı yeşil, ancak #1 için regresyon testi yok. | orta | Ruff check + format: yeşil; mypy: 178 dosya temiz; `RUN_INTEGRATION_TESTS=1` gerçek PG/MinIO/FFmpeg pytest: **864 passed** (1 Starlette/httpx deprecation uyarısı). Runtime imajında `make` yoktu; eşdeğer alt komutlar doğrudan çalıştırıldı. | açık |

**Karar:** düzeltme gerekiyor. `lirayla` ve varyantlarını para birimi gramerine ekleyen, birim + HTTP kalıcılık engeli regresyonu içeren küçük bir W17 takip düzeltmesi gerekli.
