# W21 — Phase 2F: Onay sistemi ve revizyon (§21)

**Dal:** `slice/2f-approval-revision` · **Base:** `main` · **Migration slotu: SENDE** (`0018`)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Plan:** [Phase 2 planı](../plans/active/phase-2-content-generation.md) — slice 2F
**Neden bu iş:** W19 projeyi `PREVIEW_READY`'ye getiriyor ve **orada bırakıyor.** Kullanıcı beğenmezse yapabileceği hiçbir şey yok: onay yok, ret yok, revizyon yok. Üstelik fail-closed QC gereği bugün *her* çıktı `needs_review` işaretiyle geliyor — yani insan incelemesi ürünün merkezinde ve hiç inşa edilmedi. Ayrıca W20 bir açık bıraktı: **iptal edilemeyen proje kredisini süresiz tutuyor**; bu slice onu da kapatır.

## Okunacaklar

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/product/requirements/40b-scenario-render-lifecycle.md`](../product/requirements/40b-scenario-render-lifecycle.md) — **§21 onay sistemi** (21.1 politikalar, 21.2 ret nedenleri, 21.3 revizyon sınıfları), §20 durum makinesi
3. [`docs/product/requirements/50-subscription-entitlement.md`](../product/requirements/50-subscription-entitlement.md) — §12.3'ün revizyon hakkı ("üç revizyon"), §12.4
4. `services/api/app/modules/content/CLAUDE.md` — özellikle K4 ("**saf yeniden render yeni hak tüketmez, revizyon kotasından düşer**") ve W19'un durum makinesi
5. `services/api/app/modules/entitlement/CLAUDE.md` — rezervasyon/sonuçlandırma; **iptal yolunu buraya takacaksın**

## PM kararları

### 1. Durum makinesi §20'nin kalan kenarlarıyla tamamlanır

W19 bilerek `PREVIEW_READY`'de durmuştu. Bu slice ekler: `WAITING_APPROVAL`, `REVISION_REQUESTED` ve `PREVIEW_READY → WAITING_APPROVAL → REVISION_REQUESTED → SCRIPTING` döngüsü. **`SCHEDULED` ve sonrası YİNE YOK** — planlayıcı 2G, yayınlama Phase 4. W19'un kuralları aynen geçerli: kapalı geçiş tablosu, saf, tanımsız çift kod hatası, her geçiş kaydedilir.

### 2. Onay politikası **veri**, kod değil (§21.1)

Yedi politika (`always` … `never_within_guardrails`) işletme başına ayar. **Politikayı değerlendiren fonksiyon saf ve total olmalı:** girdi = (politika, içerik bağlamı: kampanya mı, fiyat/indirim içeriyor mu, reklam mı, kaçıncı içerik, QC güven işareti) → çıktı = onay gerekiyor mu. Tanımsız kombinasyon yok.

**`low_confidence_only` bugün her zaman onay ister** — çünkü VLM fake ve QC her çıktıyı `needs_review` işaretliyor (2D fail-closed kuralı). Bu doğru davranış; kodda ve dokümanda **neden** böyle olduğu yazılsın ki gerçek sağlayıcı gelince kimse "bozuk" sanmasın.

### 3. Ret nedeni **kapalı küme + serbest not** (§21.2)

On neden kapalı enum (`wrong_product` … `other`); `other` seçilirse serbest metin **zorunlu**. Serbest metin **untrusted veridir**: prompt'a birleştirilmez, yalnızca `input_data` altında taşınır (§17.5 kuralı), ve fabrikasyon dedektöründen geçmez — çünkü kullanıcının kendi metni, modelin değil. Ama **saklanır ve loglanmaz**.

**Gizlilik (§21.2 son cümlesi):** ret nedenleri model öğrenme verisi olabilir ama **tenant'a özel kalır**. Bu slice hiçbir çapraz-tenant toplama yapmaz; şema bunu ileride mümkün kılacak şekilde `business_id` taşır ve doküman "toplu kullanım ayrı bir karar ve ayrı bir rıza gerektirir" der.

### 4. Revizyon: küçük/büyük ayrımı **hangi alanın değiştiğinden** türetilir (§21.3)

Kullanıcı "küçük revizyon istiyorum" demez; **ne değiştirdiğini** söyler, sınıfı kod belirler:
- **Küçük** (CTA, başlık, tek kesit, ses, müzik, altyazı stili) → yalnızca etkilenen adımdan yeniden başlar; senaryo yeniden üretilmez.
- **Büyük** (içerik türü, ürün, konsept, süre sınıfı) → `SCRIPTING`'e döner, senaryo yeniden üretilir.

Sınıflandırma **saf ve total** bir fonksiyon: değişen alan kümesi → sınıf. Belirsizlik varsa **büyük** tarafa düşer (fail-closed: gereksiz yeniden üretim, yanlış çıktıdan ucuzdur).

### 5. Revizyon kotası ve hak (K4 + §12.3)

- **Saf yeniden render hak tüketmez** (K4, W20'de yapısal olarak sağlandı: rezervasyon projeye bağlı) — bu slice bunu **bozmamalı**.
- **Küçük revizyon** kotadan düşer (varsayılan 3, `REVISION_QUOTA_*` config), yeni kredi tüketmez.
- **Büyük revizyon** da aynı rezervasyonun içinde kalır ama kotadan **iki** düşer — gerekçe: senaryo yeniden üretimi gerçek sağlayıcı maliyeti doğurur. (Bu sayı bir tahmindir; W08 benchmark'ı gerçek maliyeti ölçünce yeniden değerlendirilecek — config'de olmasının sebebi bu, raporda not düş.)
- Kota bitince: revizyon reddedilir, dokümante hata; kullanıcının yolu **yeni proje** (yeni kredi).

### 6. W20'nin bıraktığı açık: proje iptali

`PLANNED`/`WAITING_MEDIA`/`WAITING_APPROVAL` gibi **terminal olmayan** durumlarda kullanıcı projeyi iptal edebilmeli; iptal rezervasyonu **iade eder** (W20'nin `released` yolu). Ayrıca terk edilmiş projeler için yaş eşikli süpürücü — W19/W20'nin süpürücü desenini izle. Gerekçe: bugün `WAITING_MEDIA`'da park eden bir proje krediyi süresiz tutuyor.

### 7. Yetki (PRD §4)

`approver` rolü onaylar/reddeder; `editor` revizyon **ister** ama onaylayamaz; `viewer` yalnızca okur. **Kendi ürettiğini onaylama kısıtı yok** — dört kişilik bir işletmede bu kilitlenme yaratır; ürün kararı olarak kayda geç.

## Kapsam dışı (dokunma)

- **Planlayıcı/takvim, `SCHEDULED` ve sonrası** → 2G / Phase 4. **Store/ödeme** → Phase 3.
- W19'un mevcut geçişleri, W18'in QC karar tablosu, W20'nin defter aritmetiği → **mantığını değiştirme**, yalnızca yeni kenarları ve iptal/kota yolunu ekle.
- `script.py`, `text_normalization.py`, `qc.py` → dokunma.
- Ret nedenlerinden model eğitimi / çapraz-tenant toplama → **yapma** (karar 3).
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/modules/content/approval.py + approval_service.py   (yeni — politika, ret nedenleri, revizyon sınıflandırması; saf kısımlar ayrı)
services/api/app/modules/content/lifecycle.py                        (yeni durum + kenarlar)
services/api/app/modules/content/{models,repository,project_service}.py
services/api/app/modules/entitlement/service.py                      (yalnızca iptal→iade yolu)
services/api/app/api/routes/content.py                               (onay/ret/revizyon/iptal uçları)
services/api/app/core/config.py                                      (REVISION_QUOTA_*, onay politikası varsayılanı)
services/api/app/worker/{tasks,composition}.py + infrastructure/celery_app.py  (terk edilmiş proje süpürücüsü)
services/api/migrations/versions/0018_*.py                           (SLOT SENDE)
services/api/tests/unit/ + tests/integration/
docs/architecture/content-render.md · error-handling.md · .env.example
```

## Kabul kriterleri

1. Migration `0018` up → down → up; tek head.
2. **Uçtan uca:** `PREVIEW_READY` → onay isteği → ret (neden + serbest not) → küçük revizyon → yeniden render → onay; her geçiş kayıtlı, gerçek PostgreSQL/MinIO/FFmpeg.
3. **Politika değerlendirmesi total:** yedi politika × içerik bağlamı çarpımı permütasyon testiyle tüketiliyor; tanımsız kombinasyon yok. `low_confidence_only`'nin bugün hep onay istemesi testte pinli ve **gerekçesiyle** yorumlanmış.
4. **Revizyon sınıflandırması total:** değişen alan kümesi → sınıf; belirsiz küme **büyük** tarafa düşüyor (test).
5. **Kota:** küçük revizyon 1, büyük 2 düşürüyor; kota bitince revizyon reddediliyor; **saf yeniden render kotadan da krediden de düşmüyor** (K4 — testle kanıtla).
6. **İptal + iade:** terminal olmayan durumda iptal → rezervasyon `released`, bakiye eski hâline dönüyor; terminal durumda iptal reddediliyor; terk edilmiş proje süpürücüsü yaş eşiğiyle çalışıyor ve **sağlıklı projeye dokunmuyor**.
7. **Ret nedeni gizliliği:** serbest not hiçbir log/span/audit mesajında görünmüyor (sentinel testi); `other` seçilince not zorunlu; not prompt payload'ına birleştirilmiyor (test tokenize/AST ile zorlasın, W18'in desenini izle).
8. Roller: `approver` onaylar/reddeder, `editor` revizyon ister ve onaylayamaz (`403`), `viewer` yalnızca okur. Tenant izolasyonu (`404`, varlık ifşası yok). Idempotency kanonik gövdeden.
9. `make verify` yeşil; test sayısı **1325** tabanının altına düşmez; kontrat yeniden üretilip commit'li; modül `CLAUDE.md`'leri güncel.
10. Rapor + araç zinciri sürümleri. **Merge etme, dalda bırak.**

## Enumerasyon kuralı

Politikalar (7), ret nedenleri (10), revizyon alanları — hepsi **PRD tarafından kapalı**, yazılabilir. Ama **"hangi alan kümesi hangi sınıfa düşer"** ve **"hangi bağlamda onay gerekir"** kombinatoryaldir: total fonksiyon + permütasyon testi (2D/2E/W20 deseni).

## ADR numara kuralı

Gerçek karar çıkarsa `ADR-XXX-<konu>.md`; numarayı PM verir.

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum: **kendi girdilerini üret; mevcut testleri koşmak doğrulama değildir.** Özellikle: kotayı aşarak revizyon iste, saf yeniden render'ı kotadan düşürt, iptal edilmiş projeyi tekrar iptal et / iptalden sonra ilerlet, iade edilmiş krediyi ikinci kez iade ettir, `editor` ile onayla, başka tenant'ın projesini onayla, ret notunu log'a/prompt'a sızdır, büyük revizyonu küçük saydırıp senaryo yeniden üretimini atlat)_
