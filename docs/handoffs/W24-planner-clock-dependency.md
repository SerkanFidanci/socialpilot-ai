# W24 — Planlayıcı duvar saatine bağlı: test kusuru mu, ürün kusuru mu?

**Dal:** `fix/planner-clock` · **Base:** `main` · **Migration slotu: YOK**
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Neden bu iş:** W06 merge'ünden sonra PM'in tam doğrulama koşusunda **`tests/integration/test_content_planner.py`'nin 8 testi düştü** — izole koşuda da. Aynı testler W22 merge'ünde (2026-08-02) ve W06'nın kendi ortamında (2026-08-04) geçiyordu; W06 `services/api/app/**` altına dokunmadı. Yani kod değişmedi, **koşulduğu saat değişti.**

**PM'in kanıtı (kesin teşhis değil, başlangıç noktası):**
- Konteyner saati koşu anında **2026-08-05 21:18 UTC** = `Europe/Istanbul` yerel **00:18**.
- Testlerin ayarı: sessiz saat **22:00–08:00**, `planning_horizon_days: 0`.
- Yani koşu, sessiz saat penceresinin içinde ve **yerel günün ilk saatlerinde** yapıldı.
- İlk düşen assert: obligation oluşuyor (`planned`, `quiet_hours_shifted: False`) ama `GET /planner/plan` **boş liste** dönüyor: `assert [] == ['5217c96a-…']`.
- `read_plan` `datetime.now(UTC)` ile çalışıyor, yani gerçek duvar saatine bağlı.

## İlk iş: hangisi olduğunu belirle

Bu bulgu **iki farklı şey** olabilir ve ikisinin çözümü zıt yönde:

**(a) Kırılgan test.** Ürün doğru davranıyor, testler duvar saatine bağlı yazılmış ve günün belirli saatlerinde kırılıyor. → **Testler zamanı sabitlemeli.**

**(b) Ürün kusuru.** `read_plan` gerçekten de yerel gün başında / sessiz saat içinde planı boş döndürüyor. Bu durumda **kullanıcı gece 00:30'da planına bakınca boş görüyor** — ve bu sessizce yanlış bir cevap, hata bile değil. → **Ürün düzeltilmeli.**

**Önce bunu ayır ve kanıtla.** Servis katmanı zaten `now` parametresi alıyor (`_RankContextReader.contexts(..., now=now)`), yani kontrollü saatle çağırmak mümkün: aynı senaryoyu yerel `10:00`, `18:00`, `23:30`, `00:18`, `07:59` anlarında koştur ve **hangi saatlerde planın boş döndüğünü tablo hâlinde göster.** Cevap "her saatte dolu" ise (a), "bazı saatlerde boş" ise (b).

Raporun ilk bölümü bu tablo olmalı. Kararı ona göre ver.

## PM kararları

### 1. (b) çıkarsa: boş plan bir cevap değil, bir hata

Kullanıcının plan ekranı boş görünüyorsa nedeni **görünür** olmalı: "bu pencerede planlanmış iş yok" ile "hepsi sessiz saate denk geldiği için gösterilmiyor" aynı şey değil. Ürün tarafı düzeltmesi, sessiz saat ve gün sınırının planı **gizlememesini** sağlamalı — kaydırma zaten var (W22: pencereye düşen zaman dışarı kaydırılır, iptal edilmez), yani plan da kaydırılmış zamanı göstermeli.

### 2. (a) çıkarsa: testler saatten bağımsız olmalı

Duvar saatine bağlı test, günde birkaç saat kırmızı yanıp geri kalanında yeşil olan bir testtir — yani **hiçbir şey doğrulamaz**, sadece gürültü üretir. Zamanı test içinde sabitle (kontrollü `now` enjeksiyonu veya eşdeğeri; W22 servisi buna zaten hazır). **Testi "artık geçiyor" diye bırakma** — hangi saatlerde koşulursa koşulsun aynı sonucu vermeli.

### 3. Her iki durumda da: **günün her saatinde yeşil** kanıtlanmalı

Düzeltmeden sonra planner süiti en az şu yerel anlarda koşulup geçmeli: `00:05`, `03:00`, `07:59`, `08:01`, `12:00`, `21:59`, `22:01`, `23:55`. Sabit zamanla koşulacaksa hepsi tek koşuda; gerçek saatle koşulacaksa yöntemini yaz.

### 4. Aynı sınıfı **başka testlerde de** ara

Planner tek şüpheli olmayabilir. `tests/` altında duvar saatine bağlı başka test var mı — `datetime.now`, `now()`, `today` kullanan ve sonucu saate göre değişebilecek olanlar. Bulduklarını **listele**; hepsini bu turda düzeltmek zorunda değilsin, ama listelenmemiş olan bir sonraki gece patlar.

### 5. Bu W06'nın hatası değil

W06 `app/**` altına dokunmadı ve raporunda bunu belirtti; kusur W22'den beri oradaydı, W06'nın koşu saati onu ortaya çıkardı. Raporunda bunu doğru çerçevele — suç ataması için değil, **bir sonraki turun yanlış yerde aramaması** için.

## Kapsam dışı (dokunma)

- W06'nın imaj/yedek işi (kapandı), W23'ün defter yapısı, QC, senaryo dedektörü.
- Planner'ın öncelik sırası, karma ölçümü, obligation durum makinesi → **mantığını değiştirme**; yalnızca saat bağımlılığını çöz.
- Migration yok.

## Dokunulacak dosyalar (ilan)

```
services/api/tests/integration/test_content_planner.py
services/api/app/modules/planner/{service,obligation}.py     (yalnızca (b) çıkarsa)
services/api/app/api/routes/planner.py                       (yalnızca (b) çıkarsa)
services/api/tests/unit/test_content_planner_unit.py         (gerekirse)
docs/architecture/ (planlayıcı bölümü — davranış değişirse)
```

## Kabul kriterleri

1. **Teşhis tablosu:** planın hangi yerel saatlerde boş/dolu döndüğü, (a) mı (b) mi kararı ve gerekçesi.
2. Düşen 8 test geçiyor — ve **düzeltme testi zayıflatarak yapılmadı** (assert silinmedi, gevşetilmedi; ne değiştiğini raporda tek tek yaz).
3. **Saat bağımsızlığı kanıtlı:** planner süiti 8 farklı yerel anda geçiyor (karar 3'teki liste).
4. (b) çıktıysa: kullanıcının boş plan görmesi artık mümkün değil ya da **görünür bir açıklamayla** birlikte; davranış dokümana işlendi.
5. **Tarama listesi:** duvar saatine bağlı olabilecek diğer testler listelendi (düzeltilmeseler bile).
6. `make verify` yeşil; test sayısı **1474** tabanının altına düşmez; migration yok; kontrat farksız.
7. Rapor + araç zinciri sürümleri + **koşu saatleri**. **Merge etme, dalda bırak.**

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum: **kendi girdilerini üret.** Süiti farklı saatlerde koştur; sessiz saat sınırlarında (`21:59`/`22:01`/`07:59`/`08:01`) ve yerel gün sınırında (`23:59`/`00:01`) planın ne döndüğünü kendi harness'inle ölç; DST'li bir timezone'da aynısını dene)_
