# W12 — Doğrulama bulgularının kapatılması

**Dal:** `slice/0n-verification-followups` · **Base:** `main` · **Migration slotu: YOK** (ikisi de migration gerektirmiyor)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high — küçük ama ikisi de değişmez (invariant) tarafında
**Neden bu iş:** Bağımsız doğrulamanın (Codex) bıraktığı **iki açık bulgu.** İkisi de "çalışmıyor" değil, "yanlış nedenle çalışıyor" sınıfında — yani sessiz kalırsa ileride pahalı yerde patlar. Kaynak: [W04 raporu, Doğrulama bulgu 2](W04-brand-catalog.md) ve [W05 raporu, Doğrulama bulgu 3](W05-opentelemetry.md).

## Okunacaklar

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/handoffs/W04-brand-catalog.md`](W04-brand-catalog.md) — **Doğrulama bölümü, bulgu 2**
3. [`docs/handoffs/W05-opentelemetry.md`](W05-opentelemetry.md) — **Doğrulama bölümü, bulgu 3**
4. `services/api/app/modules/brands/CLAUDE.md`, `services/api/app/core/CLAUDE.md`
5. [`docs/architecture/observability.md`](../architecture/observability.md) — mevcut correlation/trace bağı
6. [`docs/product/requirements/85-orchestration-events.md`](../product/requirements/85-orchestration-events.md) — **§26.4 olay zarfı standardı**

## Kalem 1 — Parasal alanlarda katı tamsayı

**Bulgu:** `price_minor: 165.0` `201` ile kabul edilip `165`'e çevriliyor; `165.5` ise `400`. Aynısı `discount_amount_minor: 500.0` için. Yani kesirli float reddediliyor, integral float sessizce coerce ediliyor.

**Neden önemli:** para kaybı yok, ama hata modu aralıklı ve teşhisi zor. Bir istemci `fiyat * 100`'ü float'ta hesaplarsa çoğu değer `16500.0` (geçer), bazıları `16499.999999999998` (400 alır). İstemci "çalışıyor" görünür, sonra rastgele kırılır. Ayrıca "parasal alanda float yok" bu projenin sert kuralı ([W04 kabul kriteri 4](W04-brand-catalog.md)) ve reklam bütçesi katmanı (Phase 5) aynı tuzağa hazır.

**Yapılacak:**

- **Tek bir yeniden kullanılabilir katı parasal tip** tanımla (ör. `core/`'da bir `MinorUnits` annotated tipi): JSON `int` kabul eder, JSON `float`'u — integral olsa bile — **reddeder**; negatif ve üst sınır kuralları tek yerde.
- Mevcut **tüm** parasal alanları bu tipe geçir. Bul, tahmin etme: `*_minor` ile biten her alan + para taşıyan diğer alanlar. Bulduğun listeyi rapora yaz.
- **Testin şeklini düzelt.** Mevcut test yalnızca kesirli float'u deniyordu ve açığı gizledi. Yeni test **integral float** (`165.0`), kesirli float (`165.5`), string sayı (`"165"`), `true`, `null` ve çok büyük değeri ayrı ayrı denemeli. Bu, bulgunun tekrar etmemesinin tek garantisi.
- Hata kodu mevcut doğrulama kontratına uymalı; yeni bir kod gerekiyorsa [`error-handling.md`](../architecture/error-handling.md)'ye ekle.
- Kontrat değişikliği: bu bir **sıkılaştırma**. Daha önce kabul edilen `165.0` artık `400` alacak. Mobil istemcinin bu alanları float göndermediğini **doğrula** (`apps/mobile` içinde ilgili yerleri kontrol et) ve sonucu rapora yaz. Gönderiyorsa dur ve bildir — istemci düzeltmesi ayrı slice olur.

## Kalem 2 — Trace zincirinin worker'a taşınması

**Bulgu:** `X-Correlation-ID` server span'ine bağlanıyor ve response header'ı korunuyor, ama API → outbox → Beat → worker arasında `traceparent` saklanmadığı için trace zinciri kopuyor. Sonuç: API trace'leri ve worker trace'leri **iki ayrı ada**.

**Gerekçe düzeltmesi:** W05 bunu "migration gerektiriyor" diyerek takip işine bıraktı ve Codex de öyle kabul etti. **Migration gerekmiyor:** olay zarfı JSON (`payload_json`) ve §26.4 standardı zaten `correlation_id` taşıyor — `traceparent` de zarfın içinde taşınabilir. Yeni kolon yok.

**Yapılacak:**

- W3C `traceparent` (ve varsa `tracestate`) değerini olay **zarfına** yaz; §26.4'ün alan listesine ekle ve dokümana işle.
- Worker tarafında zarftan okunan bağlamı **span üst bağlamı** olarak kur: API'de başlayan isteğin tetiklediği iş aynı trace'te görünsün.
- **Telemetri kapalıyken hiçbir şey değişmemeli:** zarfa `traceparent` yazmak telemetri kapalıyken de zararsız olmalı (ya boş, ya hiç). W05'in "kapalıyken sıfır maliyet" garantisi bozulamaz — bunu test et.
- **Redaksiyon garantisi korunur:** `traceparent` bir kimlik, sır değil; ama zarfa başka hiçbir telemetri verisi (attribute, prompt, URL) yazılmaz.
- Zarftaki `traceparent` **doğrulanmadan** span bağlamına konmaz: bozuk/kötü niyetli değer trace'i kirletmemeli, geçersizse yeni trace başlar. Test var.
- `observability.md`'yi güncelle: artık zincir nerede devam ediyor, nerede kopuyor.

## Kapsam dışı (dokunma)

- **Migration.** İkisi de gerektirmiyor. Gerekiyorsa **dur** ve rapora yaz — slot W10/W11'de.
- Yeni metrik, yeni span, collector/dashboard — W05 kapsamı kapandı.
- Mobil istemci düzeltmesi (kalem 1'in son maddesi çıkarsa ayrı slice).
- Reklam bütçesi alanları — henüz yok; katı tip onların da kullanacağı şekilde yazılır, ama alan eklenmez.
- `compose.yaml` → W06. `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.
- `services/api/app/modules/content/**` ve `migrations/0012*` → **W11'in.** Çakışırsa dur ve bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/core/money.py                    (yeni — katı MinorUnits tipi; ya da uygun mevcut core dosyası, gerekçesini yaz)
services/api/app/api/routes/brands.py             (parasal alanların tipi)
services/api/app/modules/brands/domain.py         (gerekiyorsa)
services/api/app/core/telemetry.py               (zarf bağlamı enjeksiyon/çıkarma)
services/api/app/modules/operations/service.py    (outbox zarfına traceparent)
services/api/app/worker/tasks.py                  (zarftan bağlam kurma)
services/api/tests/unit/ + tests/integration/
docs/architecture/observability.md
docs/product/requirements/85-orchestration-events.md   (§26.4 zarf alanına traceparent notu)
docs/architecture/error-handling.md               (yeni kod gerekiyorsa)
```

## Kabul kriterleri

1. **Integral float reddediliyor:** `price_minor: 165.0` ve `discount_amount_minor: 500.0` artık `400` alıyor; `165` (int) kabul ediliyor. Test integral float, kesirli float, string, bool ve aşırı büyük değeri **ayrı ayrı** deniyor.
2. Katı parasal tip **tek yerde** tanımlı ve tüm mevcut parasal alanlar onu kullanıyor; bulunan alan listesi raporda.
3. Mobil istemcinin bu alanlara float gönderip göndermediği **kontrol edilip** rapora yazıldı.
4. **Trace zinciri devam ediyor:** telemetri açıkken, API isteğinin tetiklediği worker işi **aynı trace ID** altında görünüyor; bunu gösteren bir test var.
5. **Telemetri kapalıyken hiçbir davranış değişmiyor:** span/metric/thread yok, zarf yazımı zararsız; W05'in sıfır-maliyet testi hâlâ geçiyor.
6. Bozuk/kötü niyetli `traceparent` değeri span bağlamına konmuyor; geçersizse yeni trace başlıyor (test var).
7. Zarfa `traceparent` dışında telemetri verisi yazılmıyor; presigned URL/token sızıntısı testleri hâlâ geçiyor.
8. §26.4 zarf standardı ve `observability.md` gerçeği anlatıyor.
9. `make verify` yeşil; test sayısı azalmıyor (şu an 392); Alembic head değişmemiş; kontrat drift'i varsa yeniden üretilip commit'lenmiş.

## ADR numara kuralı

Gerçek bir karar çıkarsa `ADR-XXX-<konu>.md` yaz, raporda bildir; **numarayı PM verir.**

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum doldurur — özellikle: integral float'ın başka bir yoldan sızması, traceparent enjeksiyonuyla trace kirletme, telemetri kapalıyken zarf yazımının yan etkisi)_
