# W18 — Phase 2D: Otomatik QC (§19.4)

**Dal:** `slice/2d-automatic-qc` · **Base:** `main` · **Migration slotu: SENDE** (`0015`)
**Durum:** tamamlandı (dalda) · **TAKİP 1 AÇIK — Celery bağlantısı** (aşağıdaki bölüm)
**Model/effort:** Opus 5 / high

## Takip 1 — Celery bağlantısı (PM, 2026-08-02)

**Bu benim WO hatam, oturumun değil:** kapsam "ölçüm worker'da" diyordu ama dosya listesine worker dosyalarını koymamıştım. Oturum çelişkiyi sessizce çözmek yerine bildirdi — doğru davranış. İzin veriliyor, sıcak oturum aynı dalda (`slice/2d-automatic-qc`) tamamlıyor:

1. **Dosya listesine eklendi:** `services/api/app/worker/composition.py`, `services/api/app/worker/tasks.py`, `services/api/app/infrastructure/celery_app.py` ve bunların testleri.
2. Raporundaki yamayı uygula: `WorkerContext`'e `qc_probe` + `visual_qc`, `content_qc_service` fabrikası, `content.qc.drain` task'ı, beat girdisi.
3. **`process_next()`'in kendi kendine tarama davranışı kalsın mı — senin kararın**, ama raporda gerekçelendir: beat tetiklemesi geldikten sonra tarama gereksiz bir tam-tablo taraması mı oluyor, yoksa kaçan render'lar için ikinci ağ mı? Ne seçersen davranış testli olsun.
4. **Testler:** beat zamanlaması ve task kaydı diğer drain task'larıyla aynı disiplinde doğrulanır (mevcut worker testlerinin desenini izle); QC job'ı gerçekten beat üzerinden akıyor.
5. `make verify` yeşil; taban **1071**'in altına düşmez; migration yok (`0015` zaten dalda). **Merge etme, dalda bırak.**

Diğer her şey kabul edildi — aşağıdaki rapora ve doğrulama tablosuna dokunma.
**Plan:** [Phase 2 planı](../plans/active/phase-2-content-generation.md) — slice 2D
**Neden bu iş:** 2A–2C üretimi kurdu; **güvenilirliği kurmadı.** Bugün render biten her çıktı, gerçekten açılıyor mu, sesi var mı, yazısı kadrajda mı, fiyatı kaynağa uyuyor mu bilinmeden `completed` sayılıyor. QC olmadan preview kullanıcıya gösterilemez — ve gösterilirse hatayı kullanıcı bulur.

## Okunacaklar

Router: [`docs/index.md`](../index.md). Asgari set:

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/plans/active/phase-2-content-generation.md`](../plans/active/phase-2-content-generation.md) — §2 kararlar (özellikle **üretimde fake AI genel kuralı**)
3. [`docs/product/requirements/40b-scenario-render-lifecycle.md`](../product/requirements/40b-scenario-render-lifecycle.md) — **§19.4 QC listesi**, §18.3 doğrulama, §19.2 profiller
4. `services/api/app/modules/content/CLAUDE.md` — değişmezler; `validation.py`, `render.py`, `render_service.py`, `tts.py` (sapma aritmetiği)
5. `services/api/app/infrastructure/render/CLAUDE.md` (varsa) — FFmpeg adapter'ının bugünkü şekli

## PM kararları (slice bunları yeniden tartışmaz)

### 1. QC **fail-closed**: ölçülemeyen kontrol "geçti" değildir

Bir kontrol çalıştırılamadıysa (adapter yok, ölçüm hata verdi, model kabiliyeti üretimde `disabled`), sonucu **`unknown`** olur ve genel karar en az **`needs_review`**'a düşer. Hiçbir kontrol sessizce atlanmaz, hiçbir `unknown` `passed` sayılmaz. Gerekçe: QC'nin tek işi güven üretmek; ölçmediğini onaylayan bir QC, QC'siz olmaktan **daha kötüdür** çünkü sahte güven verir.

### 2. Deterministik kontroller bu slice'ta; model kontrolleri port + fake

- **Bu slice inşa eder (FFmpeg/ffprobe/kod ile, gerçek ölçüm):** video açılıyor mu · süre profil hedefine uyuyor mu · ses akışı var mı · **loudness** (EBU R128, `ebur128`/`loudnorm` ölçümü) · siyah/boş frame oranı · sabit (donuk) görüntü · yazı kadraj/safe-area dışında mı (timeline geometrisi × render çözünürlüğü — deterministik) · seslendirme-süre senkronu (2C'nin `drift_ms`'i, eşik burada) · **fiyat/tarih kaynağa uyuyor mu** (render planındaki çözülmüş değerler ile `product_prices`/`campaign_offers` karşılaştırması).
- **Port olarak tanımlanır, fake adapter ile:** logo görünürlüğü · hassas/uygunsuz içerik · yüz bozulması · üretken sahnede ürün şekli. Bunlar VLM işi; gerçek sağlayıcı W08 benchmark'ı sonrası. **Üretimde `disabled` → sonuç `unknown` → `needs_review`** (kural 1 ile tutarlı; W13 kural onayı 1'in QC'deki karşılığı).

### 3. QC **karar verir, eylem yapmaz**

QC raporu genel kararı (`passed` / `needs_review` / `failed`) ve **önerilen yolu** (`retry_render` · `alternative_scene` · `alternative_provider` · `human_review` · `request_new_media`) deterministik bir tablodan üretir. **Yeniden render tetiklemez, sağlayıcı değiştirmez, döngü saymaz** — otomatik yeniden deneme, deneme sınırı ve yaşam döngüsü geçişleri **2E'nindir**. Gerekçe: eylemi karara aynı slice'ta bağlamak sınırsız render döngüsü riskini QC'nin içine gömer; sınır yaşam döngüsünün işidir.

### 4. Eşikler config'de ve gerekçeli

Her eşik (`QC_*`) `config.py`'de, PRD veya ölçüm gerekçesiyle. Marka/profil bazlı eşik **yok** (erken karmaşıklık). 2C'nin süre sapması eşiği burada ilk kez sayı olur: 2C ölçtü, 2D yargılar.

### 5. Devralınan borç: `forbidden_matcher` birleştirmesi

Timeline metin tarafındaki yasak terim eşleşmesi, W16/W17'nin `normalize_for_matching` + `contains_unsupported_letter` ikilisini **import eder**; ikinci bir katlama uygulaması yazılmaz. Senaryo tarafında kapatılan atlatmalar (görünmez karakter, confusable, aksan, çekim) timeline metninde açık kalmamalı — bu bir düzeltme değil, **aynı savunmanın ikinci kapısı**. Kapatılmış her sınıfın timeline tarafında da kapalı olduğunu gösteren test.

**Yasak terimlerde çekim eşleşmesi YAPILMAZ** (W17'nin sorusuna cevap): `şeker` yasakken `şekerli` serbest kalır. Gerekçe: liste **markanın**, kalıp bizim; kök eşleşmesi `az` yasakken `azalttık`ı da yakalar ve markanın kastetmediğini yasaklar. Ürün tarafı markaya "kökü değil, yasaklamak istediğin biçimleri yaz" der. Mevcut pin (`az` yasakken `lezzetli` serbest) korunur.

## Kapsam

1. **`render_qc_reports` (migration `0015`)** — render output referansı, kontrol başına sonuç ve ölçülen değer (JSONB), genel karar, önerilen yol, QC sürümü (eşik seti sürümlenir — dünkü raporun hangi eşiklerle üretildiği bilinmeden karşılaştırılamaz), varsa route/usage referansı.
2. **QC çalıştırma yolu** — render job'ı tamamlandıktan sonra, aynı dayanıklı job disiplininde (durum, timeout, deneme, correlation ID, dead-letter). Ölçüm worker'da; API katmanında FFmpeg yok.
3. **Rapor okuma ucu** — render output'un QC raporu okunabilir (roller: `business.read`); imzalı URL sızmaz.
4. Dokümantasyon: `content-render.md` QC bölümü, `error-handling.md` yeni kodlar, modül `CLAUDE.md`'leri, `.env.example`.

## Kapsam dışı (dokunma)

- **Otomatik yeniden render / alternatif sahne / sağlayıcı değişimi / deneme sınırı** → 2E.
- **Gerçek VLM sağlayıcısı** → W08 sonrası.
- **Render adapter'ına voiceover miksajı** → 2E (W15'in bıraktığı açık).
- **Senaryo tarafı dedektör** (`script.py`) — kapandı, dokunma; yalnızca import et.
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/modules/content/qc.py + qc_service.py        (yeni — kontrol tanımları, karar tablosu, eşikler)
services/api/app/modules/content/{models,repository}.py       (render_qc_reports)
services/api/app/modules/content/validation.py                (forbidden_matcher birleştirmesi)
services/api/app/infrastructure/render/*                      (deterministik ölçüm adapter'ı)
services/api/app/infrastructure/ai/fake_visual_qc.py + __init__.py
services/api/app/api/routes/content.py                        (rapor okuma ucu)
services/api/app/core/config.py                               (QC_* eşikleri)
services/api/migrations/versions/0015_*.py                    (SLOT SENDE)
services/api/tests/unit/ + tests/integration/
docs/architecture/content-render.md · error-handling.md · .env.example
```

## Kabul kriterleri

Sayılı girdiler + düşman gözü:

1. Migration `0015` up → down → up; tek head.
2. **Her deterministik kontrol gerçek bozuk medyayla test edilir** — FFmpeg ile üretilmiş fixture'lar: tamamen siyah video, sessiz video, aşırı sessiz/aşırı yüksek ses, donuk tek kare, hedeften sapan süre, bozuk konteyner. "Kontrol var" değil, **kontrol gerçekten yakalıyor**.
3. **Fail-closed üç yoldan da kanıtlanır:** ölçüm hata verirse `unknown` → `needs_review` · model kabiliyeti üretimde `disabled` ise `unknown` → `needs_review` · hiçbir kontrol sonucu eksik bırakılamaz (rapor kontrol kümesinin tamamını taşır, testle sabitlenir).
4. Fiyat/tarih uyum kontrolü: render planındaki çözülmüş değer kayıttaki değerden **farklıysa** yakalanır (kampanya bitmiş/fiyat değişmiş senaryosu gerçek DB ile).
5. Karar tablosu saf ve testli: aynı kontrol sonucu kümesi → aynı karar + aynı öneri; hiçbir kombinasyon tanımsız değil.
6. **`forbidden_matcher` birleştirmesi:** senaryo tarafında kapatılan atlatma sınıflarının (görünmez karakter, confusable, Latin katlama, çekim, süslü rakam) timeline metninde de kapalı olduğunu gösteren test; ikinci bir katlama uygulaması yok (import zorlanır).
7. QC yolu dayanıklı job disiplinine uyuyor; imzalı URL hiçbir log/rapor/span'e sızmıyor (sentinel testi).
8. Roller + idempotency: rapor okuma `business.read`; başka tenant'ın raporu `404`.
9. `make verify` yeşil; test sayısı **947** tabanının altına düşmez; kontrat yeniden üretilip commit'li; modül `CLAUDE.md`'leri güncel.
10. Rapor + araç zinciri sürümleri. **Merge etme, dalda bırak.**

## Enumerasyon kuralı (bu hattın dört turluk dersi)

Kalıp, liste veya küme yazan her yerde sor: **bu bir enumerasyon mu?** Elle sayılmış her küme bu projede bir sonraki doğrulama turunda delindi (confusable tablosu → Coptic; görünmez listesi → atanmamış kod noktası; çekim listesi → `lirayla`). Tutan çözümlerin hepsi kategori kuralı, üretilmiş veri veya fail-closed sınır oldu. QC eşik ve kontrol kümesi de bu soruya tabidir.

## ADR numara kuralı

Gerçek karar çıkarsa `ADR-XXX-<konu>.md`; numarayı PM verir. (QC'nin fail-closed duruşu ADR'lık olabilir — gerekçeni yaz, numarayı isteme.)

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum: bozuk medyayı QC'ye "geçti" dedirtmeye çalış — ölçüm hatasını sessiz geçirme, eksik kontrol kümesi, karar tablosunda tanımsız kombinasyon, timeline metninde senaryo tarafında kapalı bir atlatmanın açık kalması)_
