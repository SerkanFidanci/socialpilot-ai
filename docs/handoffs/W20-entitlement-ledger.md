# W20 — Phase 2E (ikinci yarı): Kredi defteri ve hak tüketimi

**Dal:** `slice/2e-entitlement` · **Base:** `main` · **Migration slotu: SENDE** (`0017`)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Neden bu iş:** W19 içerik projesini uçtan uca yürütüyor ve **hiçbir şey saymıyor.** Bugün bir kullanıcı sınırsız render tetikleyebilir; her render gerçek para (sağlayıcı + CPU) harcıyor. Bu, ücretli sağlayıcılar bağlanmadan **önce** kapatılması gereken bir açık: hak muhasebesi olmadan gerçek bir sağlayıcı bağlamak, faturayı ölçüsüz bir kullanıma açmaktır.

## Kapsam sınırı (PM kararı — dikkatle oku)

**Bu slice ödeme almaz, mağaza entegrasyonu yapmaz, fiyat belirlemez.** K1 (faturalandırma modeli: IAP vs web-first) hâlâ **kullanıcının açık kararı** ve Phase 3'ün konusu. Bu slice yalnızca **defteri ve tüketimi** kurar:

- Bir işletmenin ne kadar hakkı olduğu **veri** olarak durur (`credit_ledger`), nereden geldiği bu slice'ın işi değil — bu slice yalnızca **manuel/seed** bir kaynak tanır (`grant` kaydı, admin/seed tarafından).
- Store doğrulaması, webhook, yenileme, iade, plan eşleme → **Phase 3**.
- Bu ayrım bilinçli: tüketim tarafı doğru kurulursa, kaynak tarafı (IAP mı web mi) sonradan **tek bir grant yazıcısı** olarak takılır. Tersi mümkün değil — önce ödeme alıp sonra saymaya başlamak, sayılmamış tüketimi kalıcı borç yapar.

## Okunacaklar

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/product/requirements/50-subscription-entitlement.md`](../product/requirements/50-subscription-entitlement.md) — **§12.4 kredi sistemi** (puan tablosu), §12.5 ayrımı, `credit_ledger` şekli
3. [`docs/product/requirements/90a-database-design.md`](../product/requirements/90a-database-design.md) — `credit_ledger` satırı
4. [`docs/product/requirements/00-vision-principles.md`](../product/requirements/00-vision-principles.md) — "abonelik hakkının kaynağı backend entitlement ledger'dır" (madde 4)
5. `services/api/app/modules/content/CLAUDE.md` — özellikle K4'ün "**saf yeniden render yeni hak tüketmez**" kuralı ve W19'un yaşam döngüsü
6. `services/api/app/modules/operations/**` — mevcut `provider_usage` ve outbox desenleri (para birimi/minor units disiplini W12'de kuruldu)

## PM kararları

### 1. Defter **append-only**, bakiye türetilir

`credit_ledger` yalnızca satır ekler: `grant` (+), `consume` (−), `refund` (+), `expire` (−). Bakiye **hiçbir yerde tek bir sayı olarak saklanmaz** — toplamdan türetilir; performans gerekirse materialize edilmiş bir görünüm/özet satırı eklenebilir ama **doğruluk kaynağı defterdir**. Gerekçe: mutasyona uğrayan bir bakiye alanı, eşzamanlı iki tüketimde sessizce yanlışa düşer ve hatayı geriye doğru izlemek imkânsız olur.

### 2. Tüketim **rezervasyon + sonuçlandırma**, tek adımda değil

İş başlarken hak **rezerve edilir** (`consume` satırı, `pending` durumunda), iş bitince **sonuçlandırılır** (`settled`) veya **iade edilir** (`released`). Gerekçe: render 8 dakika sürüyor; başta düşüp sonda iade etmek çift-harcamayı önler, sonda düşmek ise sınırsız paralel başlatmaya açık kapı bırakır.

**İade kuralı (PRD §12.4 + K4'ün kuralı):**
- İş **teknik sebeple** başarısızsa (sağlayıcı hatası, worker çökmesi, QC `failed` sonrası tükenen deneme) → **iade edilir.** Kullanıcı bizim hatamız için ödemez.
- İş **başarılıysa** → sonuçlandırılır, iade yok.
- **Saf yeniden render** (timeline değişmemiş, yalnızca yeniden üretim) → **hak tüketmez** (K4 kararı, ADR'da kayıtlı). Revizyon kotasından düşer — kotanın kendisi 2F'nin.

### 3. Puan tablosu **veri**, kod değil

§12.4'ün puanları (`X gönderisi=1` … `Premium video=20`) bir **sürümlenmiş tablo** olarak durur; tüketim satırı **hangi sürümle** hesaplandığını taşır. Gerekçe: puanlar W08 benchmark'ının ölçtüğü gerçek maliyetle kalibre edilecek (STATUS'ta kayıtlı açık) ve dünkü tüketimin hangi tabloyla hesaplandığı bilinmeden fatura tartışması çözülemez.

### 4. Kontrol **iş başlamadan önce**, ve tenant-güvenli

Yetersiz bakiye → iş **hiç başlamaz**, dokümante hata (`ENTITLEMENT_INSUFFICIENT_CREDITS`), 402 sınıfı. Kontrol ve rezervasyon **aynı transaction'da** olmalı — okuyup sonra yazmak yarış açar. Eşzamanlı iki isteğin ikisinin de son krediyi harcayamadığı **testle kanıtlanmalı** (gerçek PostgreSQL, gerçek eşzamanlılık).

### 5. Bu slice'ın tüketim noktaları

W19'un yaşam döngüsünde: **proje başlatma** (paket olarak, içerik tipine göre puan) — tekil senaryo/TTS/render çağrıları **ayrı ayrı** ücretlendirilmez. Gerekçe: kullanıcı "bir içerik" satın alıyor, adımları değil; adım başına ücretlendirme hem şaşırtıcı hem de W19'un otomatik yeniden denemesiyle çelişir.

Tekil uçlar (proje bağlamı olmadan senaryo üretimi vb.) bugün **ücretsiz kalır** ve bu bilinçli bir geçici durum: onlar geliştirici/entegrasyon yüzeyi. Raporda bunu açıkça yaz — Phase 3 kapatacak.

## Kapsam dışı (dokunma)

- Store/IAP doğrulama, webhook, yenileme, iade akışı, plan eşleme, fiyatlandırma → **Phase 3**.
- Revizyon kotası ve onay akışı → **2F**. Planlayıcı → **2G**.
- W19'un durum makinesi ve QC → kapandı; yalnızca tüketim noktalarını **tak**, mantığını değiştirme.
- `script.py`, `qc.py` karar tablosu, `text_normalization.py` → dokunma.
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/modules/entitlement/**                  (yeni modül: ledger, puan tablosu, rezervasyon servisi, CLAUDE.md)
services/api/app/modules/content/project_service.py      (yalnızca tüketim noktası çağrısı)
services/api/app/modules/content/render_service.py       (yalnızca iade/sonuçlandırma çağrısı)
services/api/app/api/routes/entitlement.py + routes/__init__.py  (bakiye/defter okuma uçları)
services/api/app/core/config.py                          (ENTITLEMENT_* ayarları)
services/api/app/worker/{tasks,composition}.py           (sonuçlandırma/iade job'ı gerekirse)
services/api/migrations/versions/0017_*.py               (SLOT SENDE)
services/api/tests/unit/ + tests/integration/
docs/architecture/ (entitlement bölümü — hangi dosyaya yazdığını raporda bildir) · error-handling.md · .env.example
```

## Kabul kriterleri

1. Migration `0017` up → down → up; tek head.
2. **Bakiye türetiliyor, saklanmıyor:** defterden hesaplanan bakiye ile beklenen değer birebir; bakiyeyi tek bir mutasyona uğrayan alandan okuyan hiçbir yol yok (test bunu zorlar).
3. **Eşzamanlılık:** son krediyi hedefleyen iki eşzamanlı proje başlatma isteğinden **tam olarak biri** başarılı; diğeri `ENTITLEMENT_INSUFFICIENT_CREDITS`. Gerçek PostgreSQL, gerçek paralel transaction — mock değil.
4. **Rezervasyon yaşam döngüsü:** başarılı iş → `settled`; teknik başarısızlık (sağlayıcı hatası + deneme tükenmesi) → `released`, bakiye eski haline dönüyor; yarım kalmış rezervasyon (worker çöktü) yaş eşiğiyle süpürülüyor (W19'un süpürücü desenini izle).
5. **Saf yeniden render hak tüketmiyor** (K4) — testle kanıtlanıyor; timeline değişmişse tüketiyor.
6. Puan tablosu sürümlü; tüketim satırı sürümü taşıyor; sürüm değişince eski satırlar yeniden yorumlanmıyor.
7. Tenant izolasyonu: başka işletmenin defteri okunamıyor/harcanamıyor (`404`, varlık ifşası yok). Roller: bakiye okuma `business.read`; **manuel grant yalnızca** `owner` veya sistem/seed yolu.
8. Parasal/sayısal disiplin: krediler **tam sayı**, negatife düşemez (kısıt veritabanında da), `provider_usage` ile ilişki kurulabiliyor (hangi tüketim hangi sağlayıcı maliyetini doğurdu).
9. `make verify` yeşil; test sayısı **1237** tabanının altına düşmez; kontrat yeniden üretilip commit'li; modül `CLAUDE.md` yazıldı.
10. Rapor + araç zinciri sürümleri. **Merge etme, dalda bırak.**

## Enumerasyon kuralı

Kredi işlem tipleri kapalı bir küme (`grant`/`consume`/`refund`/`expire`) — yazılabilir. Ama **"hangi başarısızlıkta iade edilir"** kombinatoryaldir: 2D/2E desenini izle (total fonksiyon, tanımsız kombinasyon yok, permütasyon testi).

## ADR numara kuralı

Gerçek karar çıkarsa `ADR-XXX-<konu>.md`; numarayı PM verir. (Append-only defter + rezervasyon/sonuçlandırma duruşu ADR'lık.)

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum: **kendi girdilerini üret, mevcut testleri koşmak doğrulama değildir.** Özellikle: aynı krediyi iki kez harcatmaya çalış (yarış), iade edilmiş rezervasyonu tekrar iade ettir, negatif bakiye üret, başka tenant'ın defterine yaz, saf yeniden render'ı ücretlendirt, puan tablosu sürümünü değiştirip eski satırları yeniden yorumlat)_
