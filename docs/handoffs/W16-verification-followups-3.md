# W16 — Doğrulama bulguları 3. tur: log `extra` sızıntısı + dedektör Unicode atlatması

**Dal:** `fix/verification-followups-3` · **Base:** `main` · **Migration slotu: YOK** (`0014` W15'te — migration dosyalarına dokunma)
**Durum:** hazır, tetiklenmedi
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

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum doldurur — özellikle: yeni normalizasyonu yine Unicode ile atlatma [Mn kombinasyon zincirleri, homoglif rakamlar, Cyrillic karışımı], redaksiyonu QueueHandler/child-process logger üzerinden atlatma, fast-path'in yanlış negatif üretip üretmediği)_
