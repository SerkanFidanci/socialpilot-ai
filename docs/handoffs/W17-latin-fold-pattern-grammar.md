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

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(W17 sonrası birleşik Codex turu: W16 2. tur yüzeyleri + W17 birlikte saldırılır; bulgular buraya ve W16 dosyasına)_
