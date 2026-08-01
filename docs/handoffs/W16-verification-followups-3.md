# W16 — Doğrulama bulguları 3. tur: log `extra` sızıntısı + dedektör Unicode atlatması

**Dal:** `fix/verification-followups-3` · **Base:** `main` · **Migration slotu: YOK** (`0014` W15'te — migration dosyalarına dokunma)
**Durum:** merge edildi (`5505537`) · **düzeltme turu 2 tamamlandı** (`fix/verification-followups-3`, merge edilmedi) — bkz. "Rapor — düzeltme turu 2"

## Düzeltme turu 2 — teyit bulguları (PM, 2026-08-01)

Teyit turunun tablosu en alttaki "Doğrulama" bölümünde; reprolar orada. Sıcak oturum kuralı: **aynı dal** (`fix/verification-followups-3`), önce `git merge main` ile dalını güncelle (W16 main'e merge edildi). Bulgular ve istenen düzeltmeler:

1. **Confusable kapsamı — SINIF kapatılsın, örnek değil.** Coptic `Ⲧ` (U+2CA6) için tabloya bir satır eklemek kabul edilmez; bir sonraki tur Cherokee/Lisu/Deseret bulur. Şunlardan birini (veya ikisini) seç ve gerekçele:
   - UTS #39 confusables verisinden **üretilmiş** tam eşleme (hedefi ASCII harf/rakam olanlar) — üretici script depoya girer, elle satır yazılmaz;
   - karma-yazı token kuralı: normalizasyon sonrası tek token içinde Latin + Latin-olmayan harf karışımı varsa ve katlanamıyorsa metin reddedilir (UTS #39 mixed-script mantığı).
2. **Görünmezler kategoriyle temizlensin, enumerasyonla değil.** U+2065 atanmamış (`Cn`). `Cn` ve `Co` (private use) kod noktaları normalizasyonda çıkarılır/reddedilir — tek tek kod noktası listesi değil, kategori kuralı.
3. **Redaksiyon yüzde-kodlu parametre adlarını da görsün.** Ham metinde aday yoksa **yüzde-çözülmüş kopyada** da ara (çift kodlama `%2553…` dahil en az iki çözüm turu); hit varsa maskeleme **ham biçimde** uygulanır. Fast-path'in yeni yanlış negatif üretmediğini sabitleyen test güncellenir.
4. Üç bulgu için regresyon testleri: HTTP'de kalıcılık oluşmuyor (`document IS NULL`), QueueHandler/`extra` çıktısında sentinel yok.
5. **Kendi düzeltmene yine düşman gözle saldır** ve tabloyu rapora yaz: farklı yazı sistemleri (Cherokee, Lisu, Deseret, N'Ko…), `Cn`/`Co` örnekleri, çift/üçlü yüzde kodlama, `+` ile boşluk kodlaması, UTF-8 çift kodlama.
6. `make verify` yeşil; taban **743**'ün altına düşmez; migration yok; kontrat değişmiyorsa yeniden üretim farksız.
7. Raporu bu dosyaya "Rapor — düzeltme turu 2" başlığıyla ekle; araç zinciri sürümleri.
**Model/effort:** Opus 5 / high
**Neden bu iş:** Codex'in 2026-08-01 düşman turu iki **kritik** bulgu döndürdü (kayıtları [W14-verification-followups-2.md](W14-verification-followups-2.md) ve [W13-script-generation.md](W13-script-generation.md) "Doğrulama" bölümlerinde — ikisini de oku, yeniden üretim adımları orada):

1. **Log redaksiyonu `extra` yüzeyini görmüyor.** `LogRecordFactory`, `Logger.makeRecord` içinde `extra` kopyalanmadan **önce** çalışıyor; `logger.info("…", extra={"url": httpx.URL(imzalı_url)})` + `%(url)s` biçimli bir handler ham imzayı basıyor. API ve worker'da tekrarlandı. İç içe dict (`extra.payload`) içinde de ham kalıyor.
2. **Fabrikasyon dedektörü Unicode normalizasyon/görünmez karakter varyantlarına açık.** `1​6​5​TL` (ZWSP'li), NFD `Tu¨rk lirası` (combining diaeresis), combining-dot `YÜZDE YİRMİ İNDİRİM` → üçü de `PASS` verip HTTP üzerinden kalıcı `generated` senaryo oldu.

Ayrıca düşük şiddette: GCS `GoogleAccessId` parametresi maskelenmiyor (W14 raporu maskeleniyor demişti — rapor/kod tutarsızlığı).

## Uçuş uyarısı — W15 paralel çalışıyor

W15 (`slice/2c-tts-voiceover`) şu dosyalara dokunuyor: `modules/content/{models,policy,repository,service,timeline,validation}.py`, `api/routes/content.py`, `core/config.py`, `infrastructure/ai/__init__.py`, `migrations/0014_*`. **Bunların hiçbirine dokunma.** Senin dosyaların aşağıda ilan edildi; listenin dışına ihtiyaç çıkarsa dur ve raporuna yaz.

## Okunacaklar

1. [`docs/STATUS.md`](../STATUS.md)
2. [W14](W14-verification-followups-2.md) Rapor + Doğrulama — mevcut redaksiyon mekanizması ve Codex reproları
3. [W13](W13-script-generation.md) Doğrulama (2026-08-01 bölümü) — üç atlatma girdisi
4. `services/api/app/core/logging.py` — `install_signature_redaction()`
5. `services/api/app/modules/content/script.py` — `find_fabrication()`

## Kapsam

### 1. `extra` yüzeyini kapatan merkezi redaksiyon

- Mekanizma senin seçimin ama şu gerçeği hesaba kat: record factory `extra`'yı göremez çünkü `Logger.makeRecord` extra'yı factory'den **sonra** record'a yazar. Çözüm, değerler herhangi bir handler'a ulaşmadan önce merkezî tek noktada redakte etmeli — sonradan oluşturulmuş logger'lar ve sonradan eklenmiş sentetik handler'lar dahil (handler-filter yaklaşımı bu yüzden yetmez: Codex kendi handler'ını ekledi).
- Redaksiyon rezerve olmayan tüm record attribute'larını kapsar; `str` olmayan değerler (`httpx.URL` gibi objeler, iç içe dict/list) derinlik sınırıyla ele alınır. Objeyi mutasyona uğratma — record üzerindeki referansı redakte edilmiş string ile değiştir.
- **Performans:** her log kaydında ağır regex koşma — aday substring yoksa (ör. `Signature`, `sig=`, `X-Amz`, `GoogleAccessId`, `token`) erken çık. Mevcut mesaj-yüzeyi fast-path'i neyse onunla tutarlı ol.
- `GoogleAccessId` redaksiyon parametrelerine eklenir (mesaj + extra, tüm yüzeyler).
- [W14 dosyasına](W14-verification-followups-2.md) Rapor bölümünün sonuna tek satır erratum ekle: "GoogleAccessId maskelenmesi W16'ya kadar eksikti; rapor iddiası hatalıydı."

### 2. Dedektöre Unicode normalizasyon ön işlemesi

- `find_fabrication` literal eşleştirmeden önce metni normalize eder: **NFKC** (NFD/combining varyantları ve fullwidth'i katlar) + **Unicode `Cf` kategorisi format karakterlerinin çıkarılması** (ZWSP U+200B, ZWNJ U+200C, ZWJ U+200D, WJ U+2060, BOM U+FEFF, bidi işaretleri U+200E/200F, soft hyphen U+00AD dahil) + mevcut Türkçe büyük/küçük katlaması normalizasyondan **sonra** uygulanır (combining-dot `İ` NFC'de U+0130'a katlanır, oradan mevcut Türkçe katlama devralır).
- Normalizasyon **tek bir yeniden kullanılabilir fonksiyonda** yaşar: `services/api/app/modules/content/text_normalization.py` (yeni dosya). `script.py` onu çağırır. 2D (QC) timeline `forbidden_matcher` birleştirmesinde aynı fonksiyonu import edecek — genel amaçlı ve bağımsız test edilebilir yaz, docstring'e bunu not düş.
- Kayıt/kalıcılık davranışı değişmez: reddedilen metin yine aynı hata kodlarıyla (`SCRIPT_FABRICATED_*`) reddedilir; **yeni hata kodu yok**, migration yok.

### 3. Yanlış pozitif politikası (PM kararı — uygulama değil, pinleme)

Codex #3 (`1 Ağustos böceğiyle tanışın` → tarih reddi) ve #4 (`Yüzde yüz pamuk dokusuyla` → fiyat reddi) **bilinçli politika sınırı olarak KALIR**. Gerekçe: deterministik dedektör bağlam anlayamaz; bağlam beyaz listesi ("böceği" vb.) kendisi bir atlatma kanalı olur (`1 Ağustos böceği indirimi…`). Kullanıcı yolu revizyon + doğrulanmış alanlar. Yapılacak: bu iki girdi için **davranışı pinleyen test** yaz (reddedildiklerini doğrula) ve test içinde tek satır yorumla bilinçli olduğunu işaretle.

## Kapsam dışı (dokunma)

- W15'in dosyaları (yukarıdaki uçuş uyarısı) ve `migrations/` tamamı.
- Timeline `forbidden_matcher` birleştirmesi ve `İ/I` katlaması timeline tarafı → **2D**.
- Dedektörün kural setini genişletme (yeni kalıp sınıfı ekleme) — bu tur yalnızca normalizasyon.
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/core/logging.py                            (extra yüzeyi + GoogleAccessId)
services/api/app/modules/content/script.py                  (normalizasyon çağrısı)
services/api/app/modules/content/text_normalization.py      (yeni — tek normalizasyon fonksiyonu)
services/api/tests/unit/test_logging_redaction.py           (extra/handler/worker yüzeyleri)
services/api/tests/unit/test_content_script_unit.py         (Unicode reproları + pinler)
services/api/tests/integration/test_content_script.py       (HTTP'den kalıcılık engeli)
docs/handoffs/W14-verification-followups-2.md               (yalnız erratum satırı)
docs/handoffs/W16-verification-followups-3.md               (rapor)
```

Mimari dokümanlardan hangisi redaksiyon mekanizmasını anlatıyorsa (muhtemelen observability/logging bölümü) davranış değişikliğini oraya işle; hangi dosyaya yazdığını raporda bildir.

## Kabul kriterleri

Sayılı girdiler + atlatma senaryoları düşman gözüyle:

1. **Codex reproları birebir:** `extra`'da düz string imzalı URL · `extra`'da `httpx.URL` objesi · `extra` içinde iç içe dict (`payload`) · `%(message)s extra=%(url)s` biçimli sonradan eklenmiş sentetik handler · sonradan `logging.getLogger("yeni")` ile oluşturulmuş logger · **worker imajında** aynı senaryolar. Sentinel hiçbirinin handler çıktısında ham görünmez. (API + worker ayrı testler.)
2. Parametre envanteri sayılı testte: S3 `X-Amz-Credential`/`X-Amz-Signature`/`X-Amz-Security-Token`, GCS `Signature`/`GoogleAccessId`, Azure `sig` — mesaj ve extra yüzeylerinde.
3. **Dedektör:** Codex'in üç girdisi reddediliyor ve HTTP entegrasyon testinde `document` kalıcılığı oluşmuyor. Ek olarak şu sınıflar denenir ve sonuç rapora tablo olarak yazılır: ZWNJ/ZWJ/WJ/BOM/soft-hyphen/bidi eklemeli fiyat-tarih-yüzde literalleri, NFD'ye açılmış tüm anahtar kelimeler, fullwidth rakamlar, bunların kombinasyonları. **Kendi düzeltmene karşı atlatma aramaya çalış; bulduklarını düzelt, bulamadıklarını da "denedim, geçemedi" diye listele.**
4. Yanlış pozitif pinleri: iki girdi reddedilmeye devam ediyor, testte bilinçli-politika yorumu var.
5. Normalizasyon fonksiyonu bağımsız unit testli; `script.py` dışında import eden yok (2D için hazır, ama bu turda başka çağıran ekleme).
6. `make verify` yeşil; test sayısı **628 tabanının** altına düşmez (W15 önce merge olursa taban yükselir — raporda gördüğün sayıyı yaz); lint+format+mypy strict temiz.
7. Migration dosyası eklenmedi/değişmedi; Alembic head değişmedi.
8. Rapor + araç zinciri sürümleri.

## ADR numara kuralı

Gerçek karar çıkarsa `ADR-XXX-<konu>.md`; numarayı PM verir. (Redaksiyon mekanizması değişikliği ADR gerektirmeyebilir — mevcut ADR/doküman hangisiyse oraya ek yeterli; emin değilsen raporda sor.)

## Rapor — 2026-08-01 · yürüten oturum (Opus 5)

**Dal:** `fix/verification-followups-3` (base `main` @ `362777a`) · **Commit:** `3c7e095` · **Durum:** tamamlandı

### Yapılanlar

**1 — `extra` yüzeyi.** Record factory korundu ama artık tek kanca değil. `Logger.makeRecord`
`extra`'yı factory döndükten **sonra** kayda yazdığı için factory o yüzeyi hiç göremiyor; bu
yüzden redaksiyon **üç kancaya** dağıtıldı ve her biri diğerinin göremediği bir yolu kapatıyor:

| Kanca | Kapsadığı |
|---|---|
| record factory | `msg` + traceback, kayıt oluşurken. Bir handler'a hiç uğramadan doğrudan `Formatter`'a verilen kaydı da kapsar |
| `Logger.callHandlers` | Kaydın **tamamı**, `extra` dahil, herhangi bir handler çalışmadan hemen önce. `Logger.handle` değil: 3.12+ bir filtre **başka bir record döndürebilir**, `handle` onu görmeden önce temizlerdi. `logging.makeLogRecord` ile yeniden kurulmuş kaydı (kuyruk/soket dinleyicisi) da kapsar |
| `Handler.handle` | Elde kurulup logger'a hiç uğramadan handler'a verilen kayıt. **Bu yolu kendi düzeltmeme saldırırken buldum** (aşağıdaki tablo), sonra kapattım |

- Redaksiyon rezerve olmayan **tüm** record attribute'larını kapsıyor. `str` olmayan değerler
  ele alınıyor: `httpx.URL` gibi objeler `str()` üzerinden, iç içe dict/list **derinlik sınırıyla
  yürünerek** (sınırın altında değer bir kez render edilip metin olarak temizleniyor — atlanmıyor).
  Yürüyüşün tanımadığı bir şekil (`set`) de metin olarak temizleniyor.
- **Çağıranın nesnesi mutasyona uğratılmıyor**: record üzerindeki referans redakte edilmiş
  değerle değişiyor, kaynak dict/obje aynı kalıyor. Sayılı testi var.
- **Maliyet:** kayıt bir kez taranıp işaretleniyor, beş handler beş tarama etmiyor. Hızlı yol
  ortak: `"=" yok → çık`, sonra `sig|cred|token|keyid|accessid` ön filtresi. Ön filtrenin
  **yanlış negatif üretemeyeceği** testle sabitlendi — her imza parametresi bu işaretçilerden
  birini içermek zorunda, içermeyen bir parametre eklenirse test düşer. Mesaj yüzeyindeki eski
  `"=" not in message` kısayolu kaldırıldı; artık tek fast-path var.
- `GoogleAccessId` eklendi. W14 dosyasına erratum satırı düşüldü.

**2 — Dedektör normalizasyonu.** `services/api/app/modules/content/text_normalization.py`
(yeni) tek fonksiyon: `normalize_for_matching`. Adım sırası taşıyıcı:

1. `Cf` **önce** çıkarılır — görünmez bir karakter taban harf ile birleşen işaretinin arasında
   otururken NFKC'nin bunları birleştirmesini engellerdi;
2. NFKC — `u`+`¨` → `ü`, `I`+`◌̇` → `İ`, fullwidth/superscript/matematiksel/NBSP katlanır;
3. kalanlar atılır: `Cf` tekrar (NFKC soft hyphen ve BOM'u korur), birleşememiş `Mn/Me/Mc`
   işaretleri, ve `Cf` sınıfında **olmayan** görünmez kod noktaları. Sonuncusunun sebebi Hangul
   filler'ları: kategori `Lo`, yani `\w` için **harf** — bir fiyatın yanına konduğunda
   `(?<!\w)` sınırını çökertiyorlar, sadece dolgu yapmıyorlar;
4. confusable katlaması — Kiril/Yunan'ın ASCII ile aynı çizilen harfleri;
5. Türkçe küçük harf katlaması **en son**, artık birleşmiş metin üzerinde.

`script.py`'nin eski `fold`'u bu fonksiyonla değiştirildi, yani `find_fabrication`,
`contains_url` ve **yasak terim eşleştirmesi** aynı katlamayı kullanıyor (gizli karakterle
yasak iddia serbest bırakılamıyor). Yeni hata kodu yok, migration yok, kalıcılık davranışı aynı.

**3 — Yanlış pozitif pinleri.** Codex #3 (`1 Ağustos böceğiyle tanışın`) ve #4
(`Yüzde yüz pamuk dokusuyla`) reddedilmeye devam ediyor;
`test_a_known_false_positive_is_pinned_rather_than_narrowed` bunu sabitliyor ve testin içindeki
yorum bunun bilinçli politika olduğunu, bağlam beyaz listesinin kendisinin bir atlatma kanalı
olacağını söylüyor.

### Kabul kriteri 3 — kendi düzeltmeme saldırı

**Dedektör.** 66 girdi, gerçek kodda (`sp-w16` konteyneri) koşuldu.

| Sınıf | Denenen | Sonuç |
|---|---|---|
| `Cf` görünmezler | ZWSP·ZWNJ·ZWJ·WJ·BOM·soft hyphen·LRM/RLM·Mongolian vowel sep·invisible times·interlinear annotation | **10/10 engellendi** |
| NFD / birleşen | `u+¨`, `I+◌̇`, `g+˘` (Ağustos), `s+¸` (Şubat), `o+¨`, `c+¸` | **6/6 engellendi** |
| `Mn` zincirleri | `T`+3 işaret, rakam aralarına işaret, CGJ (U+034F) | **3/3 engellendi** |
| Uyumluluk formları | fullwidth rakam/harf/yüzde, matematiksel kalın rakam **ve harf**, daire içi, üst simge, NBSP, ideographic space | **8/8 engellendi** |
| Rakam sistemleri | Arap-Hint, Devanagari | **2/2 engellendi** |
| Homoglif | Kiril `Т`/`а`/`о`/`е`, Yunan `ο`/`Τ` | **6/6 engellendi** |
| `Cf` olmayan görünmezler | Hangul filler L/V/uyumluluk/halfwidth, braille blank | **5/5 engellendi** |
| Kombinasyonlar | zwsp+NFD+fullwidth, Kiril+zwsp, BOM+işaret+fullwidth | **3/3 engellendi** |
| Tarih | zwsp, fullwidth, soft hyphen, ay adında Kiril, ISO içinde zwsp | **5/5 engellendi** |
| URL | zwsp, fullwidth `ｗｗｗ`, soft hyphen, Kiril | **4/4 engellendi** |
| Yanlış pozitif kontrolü | 6 zararsız Türkçe cümle | **6/6 geçti (doğru)** |

**Geçemediklerim — üçü de kalıp grameri açığı, normalizasyon açığı değil ve üçü de W16 öncesi
de vardı** (aşağıda "PM'e" başlığı altında):

| Girdi | Sonuç | Neden |
|---|---|---|
| `165 turk lirasi`, `yuzde yirmi`, `1 agustos`, `1 subat`, `yuz altmis bes lira` | `PASS` | Diyakritiksiz Türkçe yazım. Kapatmak `türk`/`ağustos`/`yüzde` gibi **her anahtar kelimeyi** yeniden yazmak demek — kural değişikliği |
| `165 T L`, `165 T.L.` | `PASS` | Kalıp bitişiklik istiyor. `T.L.` yaygın bir Türkçe kısaltma |
| `⑴⑸ TL` | `PASS` | NFKC parantezli rakamı `(1)(5)` yapıyor, araya noktalama giriyor |

**Redaksiyon.** 11 senaryo denendi: `extra` düz string · `extra` obje · iç içe dict ·
`QueueHandler`+`QueueListener` · `logging.makeLogRecord` (factory'den geçmemiş kayıt) · **record'u
değiştiren filtre** · `LoggerAdapter` · traceback+`extra` birlikte · **fork edilmiş çocuk süreç** ·
bytes/tuple/set `extra` · `handle()`'ı ezen handler. Biri sızdırdı — **elde kurulmuş kayıt +
doğrudan `Handler.handle`** — ve `Handler.handle` kancası eklenerek kapatıldı; testi var.

**Kalan tek açık (dokümante edildi):** kayıt *hem* elde kurulmuş *hem de* `handle()`'ı `super()`
çağırmadan ezen bir `Handler` alt sınıfına verilmişse üç kanca da devre dışı kalır. Bu, bir
kütüphanenin sızdırması değil, uygulama kodunun logging çerçevesini bilerek atlaması; bizim
kurduğumuz handler'ları `RedactingFormatter` zaten kapsıyor. `docs/architecture/observability.md`
ve `core/CLAUDE.md`'de yazılı.

### Kapsam dışı bıraktıklarım ve nedeni

- **Diyakritiksiz Türkçe katlaması (ASCII fold) uygulanmadı.** WO "yalnızca normalizasyon,
  kural setini genişletme" diyor; `türk`'ü `turk`'e eşitlemek her kalıp literalinin **anlamını**
  değiştirmek. Ayrıca `fold` bugün `_scene_tags`'in **sakladığı** değeri de üretiyor: ASCII
  katlaması sahne etiketlerini `ürün` → `urun` yapar ve 2C/2E'de video-understanding
  etiketleriyle eşleşmeyi sessizce bozar. Yani bu bir hata düzeltmesi değil, ürün sonucu olan bir
  tasarım kararı. Aşağıda PM'e bırakıldı.
- **`T L` / `T.L.` ve `⑴⑸`** — kalıp grameri; aynı gerekçe.
- **Timeline `forbidden_matcher` birleştirmesi** yapılmadı (WO 2D'ye bıraktı).
- **`docs/index.md` ve `docs/adr/README.md`'ye dokunulmadı** (W03 tekeli): yeni ADR dosyası yok,
  indekse ekleme yok.
- **ADR yazılmadı.** Redaksiyon mekanizması değişikliği mevcut mimari dokümana ek olarak
  yeterli göründü; karar `docs/architecture/observability.md`'nin "Redaction" bölümüne işlendi.
  PM aksini düşünüyorsa ADR numarası verilmeli.

### Doğrulama

Araç zinciri: **Python 3.13.14 · pytest 9.1.1 · mypy 2.3.0 · ruff 0.16.0 · PostgreSQL 16.14 ·
MinIO · FFmpeg · Docker Engine 25.0.3 / Compose v2.24.6-desktop.1**. İzole stack
`COMPOSE_PROJECT_NAME=sp-w16` (worktree kökünden, `--env-file .env.w16`; API 8020, PG 55452,
Redis 56399, MinIO 59020/59021). Tüm koşular **konteyner içinde**.

| Kontrol | Sonuç |
|---|---|
| `ruff check` (app tests migrations scripts) | **yeşil** |
| `ruff format --check` | **yeşil** — 183 dosya |
| `mypy .` (strict) | **yeşil** — 171 dosya |
| `pytest` (`RUN_INTEGRATION_TESTS=1`, gerçek PG + MinIO + FFmpeg) | **yeşil** — **697 passed** (taban 628, +69; azalma yok) |
| `check-openapi` (kontrat drift) | **yeşil** — yeniden üretildi, **fark yok** |
| Alembic head | `0013_script_generation` — **değişmedi**; `migrations/` altında değişiklik yok |

| # | Kabul kriteri | Sonuç |
|---|---|---|
| 1 | Codex reproları birebir; sentetik handler; sonradan oluşturulmuş logger; worker | ✅ `test_a_signed_url_passed_as_a_plain_string_in_extra_is_masked`, `..._a_url_object_in_extra_...`, `..._nested_inside_an_extra_dict_...` — her biri **kurulumdan sonra** oluşturulmuş logger + `%(message)s extra=%(url)s` biçimli kendi handler'ıyla, pozitif kontrol dahil (`X-Amz-Signature=[REDACTED]` çıktıda **var**). Worker yarısı `test_the_worker_process_masks_the_extra_surface_too`: **taze bir yorumlayıcıda** `start_worker_process()` koşuyor, üç yüzeyde de maskeleniyor |
| 2 | Parametre envanteri sayılı testte, mesaj + extra | ✅ `test_every_signing_parameter_is_masked_on_both_the_message_and_the_extra_surface` — 6 parametre (S3 3, GCS `Signature`+`GoogleAccessId`, Azure `sig`) × 2 yüzey; her biri kendi sentinel'iyle, `count(...) == 2` |
| 3 | Üç girdi reddediliyor, HTTP'de kalıcılık yok, atlatma tablosu | ✅ Birim: `test_a_re_encoded_figure_is_the_same_figure` 31 girdi. HTTP: `test_a_re_encoded_figure_never_reaches_a_stored_script` — `422` + `content_scripts` satırı `("failed", <kod>, NULL)`, yani onaylanabilir bir doküman **oluşmuyor**. Tablo yukarıda |
| 4 | Yanlış pozitif pinleri + bilinçli-politika yorumu | ✅ `test_a_known_false_positive_is_pinned_rather_than_narrowed` |
| 5 | Normalizasyon bağımsız unit testli, tek çağıran | ✅ `test_the_normalizer_folds_each_channel_on_its_own`, `test_the_confusable_table_is_aligned_and_only_rewrites_non_ascii`, `test_normalization_leaves_ordinary_turkish_copy_alone`; `test_only_the_script_module_uses_the_shared_normalizer_so_far` `app/` altını tarayıp tek import edeni `modules/content/script.py` olarak sabitliyor |
| 6 | `make verify` yeşil, test sayısı düşmüyor | ✅ 628 → **697** |
| 7 | Migration eklenmedi/değişmedi, head sabit | ✅ `git status` `migrations/` altında değişiklik göstermiyor; head `0013_script_generation` |
| 8 | Rapor + araç zinciri sürümleri | ✅ yukarıda |

### Açıkça belirtmem gerekenler

1. **İlan listesi dışında 4 dosyaya dokundum, gerekçeleriyle:**
   - `docs/architecture/observability.md` — WO'nun kendisi istiyor ("mimari dokümanlardan hangisi
     redaksiyon mekanizmasını anlatıyorsa"). "Redaction" bölümüne log-record yüzeyi alt başlığı
     eklendi; o bölüm bugüne kadar yalnızca span/metric yüzeyini anlatıyordu.
   - `services/api/app/core/CLAUDE.md` ve `services/api/app/modules/content/CLAUDE.md` — DoD
     "modül dosyası değişince `CLAUDE.md` güncellenir". **W15 ile çakışma riski var:** W15
     `content` modülüne TTS dosyaları ekliyor ve aynı `CLAUDE.md`'nin dosya tablosuna satır
     yazacak. Benim eklediğim satır (`text_normalization.py`) ve iki değişmez satırı bitişik
     değil, yani merge çakışması olursa küçük ve elle çözülebilir.
   - `docs/STATUS.md` — yalnızca W16 satırı + backend doğrulama fact'i (628 → 697).

2. **PM'e — dedektörün kalan üç açığı, hepsi W16 öncesi de vardı ve hiçbiri normalizasyon
   değil:**
   - **Diyakritiksiz Türkçe (en önemlisi).** `165 turk lirasi`, `yuzde yirmi indirim`,
     `1 agustos` geçiyor. Gerçek hayatta *insanların yazdığı* biçim, yani düşman olmayan bir
     model bile buraya düşebilir. Önerilen şekil: `ascii_fold` adımı **yalnızca**
     `find_fabrication`/`contains_url` için, kalıp literalleri katlanmış biçimde yeniden
     yazılarak; `_scene_tags`'e **uygulanmadan** (saklanan etiket bozulmasın). Kararı gerektiren
     yan etki: yasak terim listesi de katlanırsa marka `şeker`'i yasakladığında `seker` de
     yasaklanır — muhtemelen istenen ama markanın listesi, bizim değil.
   - **`165 T.L.` / `165 T L`.** `T.L.` standart bir Türkçe kısaltma.
   - **`⑴⑸ TL`.** NFKC parantezli rakamları noktalamayla açıyor, kalıp bitişiklik istiyor.
   Üçü tek bir "kalıp grameri turu" WO'sunda birlikte kapatılabilir.

3. **`fold` fonksiyonu kaldırıldı, `normalize_for_matching` geldi.** Yan etki: `_scene_tags` ve
   `_slot_kind` de artık normalize ediliyor. Sahne etiketi için bu **iyileşme** — içinde gizli
   karakter taşıyan bir etiket eskiden `SCRIPT_SCENE_TAG_INVALID` ile reddediliyordu, şimdi
   temizlenip saklanıyor ve hiçbir zaman eşleşmeyecek bir etiket üretilmiyor. Türkçe harfli
   etiketler değişmiyor (NFKC sonrası zaten birleşik).

4. **Üç stdlib monkeypatch'i artık global bir kaynak.** W14 raporunun 7. maddesi record factory
   için bunu zaten söylüyordu; W16 ile `Logger.callHandlers` ve `Handler.handle` de aynı
   sınıfa girdi. Hepsi mevcut implementasyonu **zincirliyor** ve tekrar kurulum no-op, ama
   ileride bir logging instrumentor'ı (ör. OTel logging) eklenirse kurulum sırası kontrol
   edilmeli.

5. **`main`'e merge etmedim.** W15 (`slice/2c-tts-voiceover`) uçuşta ve WO'nun dosya-ayrıklığı
   ancak ikisi de merge edilirken sınanır; ayrıca protokol "doğrulaması geçmemiş iş merge
   edilmez" diyor. Dal tek commit, base `main` ile aynı, yani merge fast-forward:

   ```
   git -C A:/socialpilot-ai merge --ff-only fix/verification-followups-3
   ```

   `origin`'e push edilmedi. Dal ve worktree, protokol gereği (merge **ve** bağımsız doğrulama
   bitene kadar silinmez) duruyor.

## Doğrulama

Araç zinciri: worktree kökü `A:\socialpilot-ai` (`main` `fc5555f`) · `COMPOSE_PROJECT_NAME=sp-codex` · Docker Engine 25.0.3 · Docker Compose v2.24.6-desktop.1 · API/worker Python 3.13.14 · pytest 9.1.1 · Ruff 0.16.0 · mypy 2.3.0 · PostgreSQL 16.14 · MinIO · FFmpeg. Host portları yalnız izole `sp-codex` stack'i için `55433`/`56380`/`59002`/`8001` oldu; test DB'si head `0014_voiceover_assets` üzerinde çalıştı ve saldırı verisi temizlendi.

| # | Bulgu | Şiddet | Yeniden üretim | Durum |
|---|---|---|---|---|
| 1 | Confusable tablosu Coptic `Ⲧ` (U+2CA6) karakterini Latin `T`'ye katlamıyor; `TL` para birimi bu yolla atlatılabiliyor ve kalıcı script'e giriyor. | kritik | `find_fabrication("165 ⲦL")` → `PASS`. Kötü niyetli sağlayıcı çıktısı olarak gerçek HTTP'den gönderildi: `201`, `status=generated`, DB'de `document IS NOT NULL`. | açık |
| 2 | U+2065 (atanmamış, görünmez ayırıcı) temizlenmediği için fiyat kalıbını bölüyor. | kritik | `find_fabrication("1\\u20656\\u20655\\u2065TL")` → `PASS`; aynı literal HTTP'de `201/generated` oldu ve kalıcı document satırı oluştu. | açık |
| 3 | Hızlı redaksiyon yolu, yüzde-kodlu imza parametre adlarını tanımıyor. | orta | `X-Amz-%53ignature=…&X-Amz-%43redential=…` hem QueueHandler/QueueListener mesajında hem `extra` yüzeyinde ham sentinel ile kaldı. `urllib.parse.parse_qsl` aynı query'yi standart olarak `X-Amz-Signature`/`X-Amz-Credential` anahtarlarına çözüyor; redaktör ham karakter dizisinde `sig`/`cred` ön filtresini geçemiyor. Bu URL biçiminin MinIO/S3 imza doğrulamasında kabulü ayrıca ölçülmedi. | açık |
| 4 | İstenen QueueHandler/QueueListener ve fork çocuk-süreç yolları kanonik imza parametreleri için atlatılamadı. | — | S3 `X-Amz-Signature`, GCS `GoogleAccessId` ve Azure `sig`, mesaj + `extra` + iç içe payload olarak QueueHandler/QueueListener'dan geçirildi; sentinel yoktu. Fork edilmiş çocuk süreçte mesaj ve `extra` yine maskelendi. | kabul edildi |
| 5 | Yeni Mn/rakam/Kiril denemelerinin test edilenleri engellendi. | — | Uzun Mn/CGJ/variation-selector zinciri, matematiksel kalın rakamlar, N'Ko ve Tibet rakamları ile Kiril `ТL` fiyatı `SCRIPT_FABRICATED_PRICE` verdi. Bilinen W17 gramer açıkları (diyakritiksiz Türkçe, `T.L.`, `⑴⑸`) ve `handle()`'ı bilinçli ezmiş handler tekrar raporlanmadı. | kabul edildi |
| 6 | Mevcut odaklı süitler yeni #1–#3 girdilerini kapsamıyor. | orta | `RUN_INTEGRATION_TESTS=1 python -m pytest -q tests/unit/test_logging_redaction.py tests/unit/test_content_script_unit.py tests/integration/test_content_script.py` → `191 passed` (1 Starlette/httpx deprecation uyarısı). | açık |

**Karar:** düzeltme gerekiyor. Normalizasyonun görünmez/uyumlu karakter seti ve confusable kapsamı genişletilmeli; redaksiyon, query parametre adını eşleştirmeden önce güvenli şekilde yüzde-çözülmüş biçimi de incelemelidir. Her üç açık HTTP/Queue regresyon testleriyle kapatılmalıdır.

## Rapor — düzeltme turu 2 · 2026-08-02 · yürüten oturum (Opus 5)

**Dal:** `fix/verification-followups-3` (`git merge main` ile `96ba2f1` üzerine güncellendi) ·
**Commit:** `ac48c87` · **Durum:** tamamlandı, dalda bırakıldı

### 1 — Confusable kapsamı: sınıf kapatıldı

**Seçim: WO'nun (b) seçeneği, ama daha katı hâliyle — "karma yazı" değil, "tek yazı".** Gerekçe,
ikisini de deneyerek:

- **(a) UTS #39 tablosundan üretilmiş eşleme** dış bir Unicode veri dosyası ister ve her Unicode
  sürümüyle bayatlar. Daha önemlisi kendisi bir *bilinen-kötü listesi*: az önce Coptic karşısında
  düşen savunmanın aynı şekli. Bir sonraki tur tabloya girmemiş bir çifti bulur.
- **(b) karma-yazı token kuralı** yazıldığı gibi eksik: `165 ⲦⲚ` tokeninde **hiç Latin harf yok**,
  yani "Latin + Latin-olmayan karışımı" tetiklenmez ve metin geçer. Bunu ölçtüm; tabloda
  "coptic throughout" satırı bu girdiyi pinliyor.
- **Uygulanan:** `contains_non_latin_letter` — normalize edilmiş literalde **Latin dışı bir harf**
  varsa metin `SCRIPT_UNSUPPORTED_CHARACTER` ile reddedilir. Kontrol `parse_text`'te, kontrol
  karakteri kuralının hemen yanında: hiçbir içerik kuralı çalışmadan önce. Latin olma testi
  `unicodedata.name(...)` ile yapılır (`LATIN SMALL LETTER DOTLESS I`, `LATIN CAPITAL LETTER I
  WITH DOT ABOVE` — Türkçe alfabesinin tamamı tek bir string karşılaştırmasının beri tarafında).

Bu, bir tablo değil bir **sınır**: Coptic, Cherokee, Lisu, Deseret, N'Ko, Osage, Vai, Tifinagh,
Hangul, Katakana, Han — hepsi "Latin değil" olduğu için tek kuralla kapanıyor ve yeni bir Unicode
sürümü kuralı bayatlatmıyor. Harf **olmayanlar** serbest bırakıldı: başka bir sayı sisteminin
rakamı zaten fiyat kuralının işi (Arap-Hint `١٦٥ TL` yakalanıyor), emoji ve noktalama kimsenin.

Confusable tablosu **kaldırılmadı**: artık taşıyıcı değil, ama gerçekten karşılaşılan
alfabelerde (Kiril/Yunan) reddin *gerekçesini* doğru tutuyor — "uydurulmuş fiyat", "yanlış
alfabe" değil.

### 2 — Görünmezler kategoriyle

`_INVISIBLE_CODE_POINTS` enumerasyonu tek başına U+2065'i kaçırdı çünkü karakter **atanmamış**;
hiçbir görünmez-karakter listesinde yoktu ve arkasında ~800 bin atanmamış kod noktası daha var.
Normalizasyon artık kategoriyle çalışıyor: `Cf` (format), **`Cn` (atanmamış)**, **`Co` (private
use)** ve `Cs` (surrogate). `Cc` bilinçli olarak **dışarıda** — kontrol karakteri sessizce
temizlenecek bir şey değil, `SCRIPT_CONTROL_CHARACTER` ile dokümante bir rettir.

Kalan tek enumerasyon, *atanmış, adı olan ve yine de hiçbir şey çizmeyen* dört kod noktası
(Hangul dolgu harfleri ve braille boşluğu). Bunları adlandıran bir kategori yok; Hangul
dolguları `Lo`, yani **kelime karakteri**, ve bir rakamın yanına konduğunda `(?<!\w)` sınırını
çökertiyorlar. Bunlar ayrıca alfabe kuralının da kapsamında (Latin değiller), yani iki katman.

### 3 — Yüzde kodlu parametre adları

`X-Amz-%53ignature` sunucu ve `parse_qsl` için hâlâ `X-Amz-Signature`, ama ham metinde `sig`
yok — hızlı yol satırı atlıyordu. Kaçış **derinliği sınırsız**: `%2553` → `%53` → `S`. Sabit
sayıda çözme turu bunu kapatmaz, o yüzden çözmek yerine kalıp genişletildi: adın her karakteri
`(?:c|%(?:25)*XX)` olarak kabul ediliyor, `=` da `%3d` biçiminde gelebiliyor. Her iki hex
büyük/küçük hâli listeleniyor çünkü `%53` ile `%73` farklı **rakam** çiftleri ve `(?i)` onları
`S`/`s` gibi ilişkilendirmiyor.

Maskeleme **ham biçimde** uygulanıyor: parametre adı ve ayırıcı geldiği gibi kalıyor, yalnızca
değer düşüyor — log satırı hâlâ gerçekten yapılan isteği okutuyor.

Geniş kalıp yalnızca metinde `%` varsa çalışıyor, yani sıradan bir log satırı bedelini ödemiyor.
Hızlı yolun güvenliği artık **iki dallı bir argüman** ve iki dal da testli: (i) harfi harfine
yazılmış bir ad işaretçilerden (`sig`/`cred`/`token`/`keyid`/`accessid`) birini içerir; (ii)
başka türlü yazılmış bir ad bunu yapmak için `%` gerektirir.

### 4 — Regresyon testleri

| Bulgu | Test | Ne kanıtlıyor |
|---|---|---|
| #1 Coptic | `test_a_letter_from_another_alphabet_is_refused_before_any_rule_runs` (12 alfabe) + `test_an_unknown_alphabet_or_code_point_never_reaches_a_stored_script` | HTTP'de `422`, satır `failed`, **`document IS NULL`** |
| #2 U+2065 | `test_an_unassigned_or_private_code_point_cannot_break_a_figure_apart` (5 girdi) + aynı HTTP testi | fiyat kalıbı bölünmüyor; kalıcı doküman yok |
| #3 yüzde kodlama | `test_a_percent_encoded_parameter_name_still_loses_its_value` (10 biçim) · `..._on_the_extra_surface_too` (10 biçim) · `test_a_percent_encoded_parameter_name_cannot_ride_a_queue_out` | mesaj, `extra` ve **QueueHandler/QueueListener** çıktısında sentinel yok |
| yanlış pozitif | `test_latin_copy_is_not_collateral_damage` (7 girdi) · `test_an_ordinary_percent_sign_is_not_mistaken_for_an_encoded_name` | Türkçe alfabesinin tamamı, emoji, tire, `₺`, `progress=50%` etkilenmiyor |

Ayrıca: bu turda test dosyalarındaki **görünmez saldırı karakterleri `\uXXXX` kaçış metnine
çevrildi** (17 karakter entegrasyon dosyasında, 101 birim dosyasında). Payload'ı görünmez olan
bir güvenlik testi diff'te de görünmez — saldırının dayandığı özelliğin ta kendisi. Okunur Türkçe
harfler, tireler ve emoji olduğu gibi bırakıldı; yalnızca bir atlatma taşıyan karakterler kaçışa
çevrildi.

### 5 — Kendi düzeltmeme saldırı

**Dedektör** (35 girdi, `sp-w16` konteynerinde gerçek kodda):

| Sınıf | Denenen | Sonuç |
|---|---|---|
| Farklı yazı sistemleri | Coptic (karışık ve tamamen), Cherokee (ikisi), Lisu, Deseret, N'Ko, Osage, Vai, Tifinagh, Hangul jamo, Katakana, Han | **12/12 reddedildi** (`SCRIPT_UNSUPPORTED_CHARACTER`) |
| `Cn`/`Co`/`Cs` | U+2065, U+0378, U+05EB, U+E000, U+F8FF, U+FDD0 (noncharacter), U+10FFFF, tek başına surrogate | **8/8 engellendi** (fiyat olarak yakalandı) |
| Uyumluluk formları | modifier capital T, daire içi `ⓉⓁ`, script small l, matematiksel kalın `𝐓𝐋`, fullwidth | **hepsi katlandı ve yakalandı** |
| Kombinasyon | Coptic+ZWSP, Cherokee+U+2065 | **2/2 reddedildi** |
| Yanlış pozitif kontrolü | Türkçe alfabesinin tamamı, emoji'li kopya, `₺`, sıradan sayılar | **4/4 doğru geçti** |

**Redaksiyon** (21 biçim). Kritik ölçüt: bir biçim ancak `parse_qsl` onu **kanonik bir imza
parametresi adına çözüyorsa** gerçek bir kaçış kanalıdır; aksi hâlde hiçbir S3/GCS/Azure sunucusu
onu imza parametresi saymaz.

| Biçim | `parse_qsl` çözümü | Redaktör |
|---|---|---|
| `%53` · küçük harf `%73` · ilk karakter `%58` · tamamı kodlu · kodlu tire · `%3D` ayırıcı · credential · security-token · GoogleAccessId · Azure `sig` | **kanonik** | **maskelendi** |
| `%2553` · `%252553` · `%25252553` · karışık seviye · `%253D` | kanonik **değil** (tek tur çözülüyor) | yine de maskelendi (iki kez çözen bir proxy/sunucu için güvenli taraf) |
| `+` boşluk · `%C2%53` · overlong `%C1%93` · HTML entity `&#83;` · fullwidth `Ｓ` · `%00` | kanonik **değil** (`X-Amz-Sign ature`, `X-Amz-\ufffdSignature`, …) | maskelenmedi — **kaçış kanalı değil**: sunucu bu adları imza parametresi olarak kabul etmez |

Fork edilmiş çocuk süreç, `logging.makeLogRecord`, record'u değiştiren filtre, `LoggerAdapter`,
doğrudan `Handler.handle` ve iç içe `extra` yolları 1. turdan beri testli ve bu turda tekrar
koşuldu.

**Bulduğum ve DÜZELTMEDİĞİM tek sınıf — PM kararı gerektiriyor:**

`165 ṬL` (nokta altlı T, U+1E6C) ve `165 ŦL` (çizgili T, U+0166) **geçiyor**. Bunlar Latin
harfleri, yani alfabe kuralı onları reddetmiyor; katlama da `t`'ye indirmiyor.

Bunu bilerek bırakmamın nedeni, WO'nun kendi ilkesi — *örneği değil sınıfı kapat*:

- `Ṭ` ayrıştırılabilir (NFD → `T` + birleşen nokta), yani bir "Latin aksanlarını at" adımı onu
  kapatır. **Ama `Ŧ`, `Ⱦ`, `Ƭ`, `Đ`, `Ł` ayrıştırılamaz** — tek kod noktası, kanonik ayrıştırması
  yok. Sadece `Ṭ`'yi kapatmak tam olarak "örneği kapatmak" olurdu.
- Sınıfın tamamını kapatan iki yol var ve **ikisi de PM kararı**: (i) aksanları atan katlama +
  kalıp literallerinin aksansız biçimde yeniden yazılması — bu **PM'in zaten W17'ye yazdığı
  "diyakritiksiz Türkçe" işiyle aynı değişiklik, sadece diğer yönü** (`165 turk lirasi` ile
  `165 ṬL` tek bir katlamayla birlikte kapanır, ayrı ayrı değil); (ii) alfabeyi Türkçe alfabesine
  daraltmak — bu sınıfı bütünüyle kapatır ama **işletme adı aksanlıysa üretimi kalıcı olarak
  bloke eder** ("Café Nero" yazan model her seferinde reddedilir ve yeniden üretim bunu çözmez),
  yani ürün kararı.
- Bu WO'nun kapsam-dışı maddesi hâlâ "dedektörün kural setini genişletme" diyor ve (i) her kalıp
  literalini yeniden yazmayı gerektiriyor.

**PM'e somut istek:** W17 "diyakritiksiz Türkçe" olarak değil, **"Latin harf katlaması, iki yön"**
olarak kapsanmalı — eksik aksan (`turk lirasi`) ve fazla/farklı aksan (`ṬL`, `ŦL`) tek bir
normalizasyon + kalıp yeniden yazımıyla kapanır; ayrı turlar hâlinde yapılırsa ikinci yön
kaçınılmaz olarak bir sonraki doğrulama turunda kritik olarak geri gelir. Ayrıca ayrıştırılamayan
Latin harfleri için (ii)'nin dar bir hâli gerekebilir; onun ürün maliyeti yukarıda.

### Kapsam dışı bıraktıklarım ve nedeni

- **UTS #39 tablosu üretici script'i yazılmadı** — yukarıdaki (a)/(b) gerekçesi; kısıt kuralı
  tablonun kapsadığı her şeyi ve kapsamadıklarını da kapatıyor.
- **Latin harf katlaması** — yukarıda, W17.
- **`165 T.L.` / `165 T L` / `⑴⑸`** — 1. turda raporlandı, kalıp grameri, W17.
- `docs/index.md` ve `docs/adr/README.md`'ye dokunulmadı (W03 tekeli); yeni ADR dosyası yok.

### Doğrulama

Araç zinciri: **Python 3.13.14 · pytest 9.1.1 · mypy 2.3.0 · ruff 0.16.0 · PostgreSQL 16.14 ·
MinIO · FFmpeg · Docker Engine 25.0.3 / Compose v2.24.6-desktop.1**. İzole stack
`COMPOSE_PROJECT_NAME=sp-w16` (worktree kökünden, `--env-file .env.w16`; API 8020, PG 55452,
Redis 56399, MinIO 59020/59021). Tüm koşular **konteyner içinde**.

| Kontrol | Sonuç |
|---|---|
| `ruff check` (app tests migrations scripts) | **yeşil** |
| `ruff format --check` | **yeşil** — 190 dosya |
| `mypy .` (strict) | **yeşil** — 178 dosya |
| `pytest` (`RUN_INTEGRATION_TESTS=1`, gerçek PG + MinIO + FFmpeg) | **yeşil** — **792 passed** (taban 743, +49) |
| `check-openapi` (kontrat drift) | **yeşil** — yeniden üretildi, **fark yok** (yeni kod `meta.issue` değeri, şema değil) |
| Alembic head | `0014_voiceover_assets` — **değişmedi**; `migrations/` altında değişiklik yok |

| # | WO maddesi | Sonuç |
|---|---|---|
| 1 | Confusable sınıfı kapatıldı, gerekçeli | ✅ tek-yazı kısıtı; (a) ve (b)'nin neden seçilmediği ölçümle yukarıda |
| 2 | Görünmezler kategoriyle | ✅ `Cf`/`Cn`/`Co`/`Cs`; `Cc` bilinçli dışarıda, testle pinli |
| 3 | Yüzde kodlu parametre adları, ham biçimde maskeleme, fast-path testi güncel | ✅ `%(?:25)*XX`, 10 biçim × 2 yüzey + queue; fast-path testi iki dallı argümanı anlatıyor |
| 4 | Üç bulgu için regresyon; `document IS NULL`; queue'da sentinel yok | ✅ yukarıdaki tablo |
| 5 | Düşman turu + tablo | ✅ 35 dedektör + 21 redaksiyon girdisi; bulunan tek sınıf ve neden düzeltilmediği yukarıda |
| 6 | `make verify` yeşil, taban 743'ün altına düşmüyor, migration yok | ✅ 743 → **792** |
| 7 | Rapor + araç zinciri | ✅ |

### Açıkça belirtmem gerekenler

1. **Yeni bir hata kodu eklendi: `SCRIPT_UNSUPPORTED_CHARACTER`** (şema kodu, `meta.issue`).
   1. turda "yeni hata kodu yok" denmişti; alfabe kısıtı bunu gerektirdi çünkü reddin *nedeni*
   uydurulmuş fiyat değil. `docs/architecture/error-handling.md`'ye işlendi. OpenAPI şeması
   değişmiyor (issue kodları string), kontrat yeniden üretimi farksız.

2. **İlan listesi dışında 5 dosyaya dokundum:** `docs/architecture/error-handling.md` (yeni kod —
   DoD), `docs/architecture/observability.md` (redaksiyon davranışı), `app/core/CLAUDE.md` ve
   `app/modules/content/CLAUDE.md` (DoD), `docs/STATUS.md` (yalnız W16 satırı + test sayısı).
   W15 kapandığı için dosya çakışması yok.

3. **Alfabe kısıtı `parse_text`'te, yani şema katmanında.** Sonucu: `resolve_script`'in "tüm
   sorunları birlikte topla" davranışı bu kural için geçerli değil — ilk ihlalde durur. Bilinçli:
   karakter kümesi metnin *iddiasının* değil *kendisinin* özelliği ve kontrol karakteri kuralı da
   aynı yerde.

4. **Türkçe olmayan Latin harfleri şu an serbest** (`é`, `Š`, `ñ`). Bu bilinçli: aksanlı bir
   işletme adı ("Café Nero") aksi hâlde her üretimi kalıcı olarak bloke ederdi. Maliyeti yukarıdaki
   `ṬL` açığı; ikisi aynı madalyonun yüzleri ve karar PM'in.

5. **`main`'e merge etmedim** (talimat gereği). Dal `main`'i içeriyor (`96ba2f1` merge edildi),
   yani ileri sarma:

   ```
   git -C A:/socialpilot-ai merge --ff-only fix/verification-followups-3
   ```

   `origin`'e push edilmedi.
