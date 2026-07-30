# W04 — Marka profili + ürün/hizmet kataloğu

**Dal:** `slice/1f-brand-catalog` · **Base:** `main` · **Migration slotu: SENDE** (`0010`) · **W05 ile paralel** (dosya-ayrık)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Neden bu iş:** Phase 1'in son eksik parçası ve **Phase 2'nin ön koşulu.** İçerik üretimi doğrulanmış ürün, fiyat ve kampanya verisi olmadan çalışamaz — PRD'nin en sert kuralı bu: *"AI kampanya tarihini veya fiyatı yazmaz. Doğrulanmış kayıttan alır."* (§11.3). O kayıt henüz yok. Marka tonu, yasak kelimeler ve onaylı CTA listesi de burada doğuyor; senaryo üretimi bunlara dayanacak.

## Okunacaklar

Router: [`docs/index.md`](../index.md) → "Yeni modül" satırı. Asgari set:

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/product/requirements/20-brand-catalog.md`](../product/requirements/20-brand-catalog.md) — **PRD §11'in tamamı**, bu WO'nun kaynak gereksinimi
3. [`docs/architecture/tenant-isolation.md`](../architecture/tenant-isolation.md) — rol matrisi ve zorunlu veri kontrolleri
4. [`docs/architecture/backend-modules.md`](../architecture/backend-modules.md) — katman kuralları
5. `services/api/app/modules/businesses/CLAUDE.md` — **taklit edeceğin desen** (tenant-kapsamlı repository, policy, service)
6. [`docs/architecture/error-handling.md`](../architecture/error-handling.md) — hata kataloğu; yeni kodlar buraya eklenir
7. [`docs/product/requirements/90b-api-error-contracts.md`](../product/requirements/90b-api-error-contracts.md) — §29.4 marka endpoint'leri, ortak API kuralları

## Kapsam

### 1. `modules/brands` — yeni domain modülü

`businesses` modülünün desenini izle: domain + models + repository + policy + service. PRD §28.2'deki tablo kümesinden bu slice'ın kapsadıkları:

- `brand_profiles` — marka kimliği, ton, iletişim dili, renk paleti, font tercihi
- `products` + `product_prices` — ürün/hizmet kataloğu
- `campaign_offers` — kampanya kaydı (tarih aralığı, indirim, kupon, stok limiti, yasal metin)
- `approved_claims` / `forbidden_claims` / `approved_ctas` — içerik güvenlik listeleri
- `target_audiences`

`brand_assets` (logo vb.) **medya modülüne bağlanır**, yeni bir depolama yolu açmaz: mevcut `media_assets` kaydına FK ile referans verilir.

### 2. Değişmezler (ihlal = iş reddedilir)

- **Para her yerde integer minor unit.** `price_minor` + `currency`. Kod tabanının hiçbir yerinde parasal `float`/`Decimal→float` dönüşümü olmayacak. Bunu doğrulayan bir test yaz.
- **Zaman UTC.** Kampanya `start_at`/`end_at` UTC saklanır; iş saat dilimine çevirme yalnızca sınırda.
- **Her sorgu `business_id` ister.** Tenant tablolarında genel amaçlı `list_all()` yok.
- **Yetki `businesses` modülünden gelir**, yeniden yazılmaz. Rol matrisi: `owner`/`admin` marka+katalog yazar, `editor` yazamaz (PRD §4: editor medya yükler ve içerik üretir, marka ayarını değiştirmez), `viewer` okur, `approver` yalnızca onay kaynaklarını görür.
- **Controller'da iş kuralı yok.** Route yalnızca transport.
- **Kampanya tarihi geçmişse içerik üretilemez** (§2.2). Bu kuralın *veri tarafı* burada kurulur: bir kampanyanın aktif olup olmadığını **deterministik** olarak söyleyen bir sorgu/servis olmalı. Kuralı tüketen içerik hattı Phase 2'de yazılacak; bu WO doğru cevabı verebilen kaydı ve sorguyu bırakır.

### 3. Marka sağlık skoru (§10.4)

Skor **tavsiye amaçlıdır ve kullanıcıyı engellemez.** Bileşenleri PRD §10.4'te sayılı. Deterministik hesaplanır (AI yok). Kritik eksik varsa ilgili senaryoyu engelleme yetkisi **skorun değil**, senaryo kurallarının işi — skor yalnızca sinyal üretir.

### 4. Cursor pagination — teknik borç kapanışı

Tenant listeleri sınırsız büyüyebilir; `products` bunun ilk gerçek örneği. Bu WO **yeniden kullanılabilir cursor pagination primitifini** kurar ve kendi listelerinde uygular: opak cursor, kararlı sıralama (tie-break dahil), `limit` üst sınırı, sonraki-sayfa göstergesi. Ortak kurallar §29.1'de.

**Mevcut `businesses` ve `media` listelerini geri dönük pagination'a çevirmek bu WO'da YOK** — o bir kontrat değişikliği, mobil istemciyi de etkiliyor ve ayrı bir slice olmalı. Sen primitifi kur, kendi uçlarında kullan, retrofit'i rapora öneri olarak yaz.

### 5. API

PRD §29.4'teki uçlar. Her mutation: yetki + validation + **idempotency** + audit (gerekiyorsa) + hata kodu. Router'ı **yalnızca** `app/api/routes/__init__.py` içindeki `register_routes()`'a bir satır ekleyerek bağla — `main.py`'a **dokunma** (sahibi W05).

## Kapsam dışı (dokunma)

- **AI ile marka içeriği üretmek.** Bu WO veriyi ve kuralları kurar, içerik üretmez.
- **Abonelik/entitlement, yayınlama, reklam.**
- **`app/main.py`** → W05'in. Router'ı `routes/__init__.py`'den bağla.
- **`app/core/config.py`, `app/core/logging.py`, `pyproject.toml`, `uv.lock`** → W05'in. Bağımlılık eklemen gerekiyorsa **dur** ve rapora yaz.
- **Şema borcu üç kalemi** (`provider_usage` tablosu, `storage_upload_id` genişletmesi, fotoğraf analiz enum'u) → **bu WO'da YOK.** Migration slotu sende ama bu üçü ayrı bir slice (W10) olarak sıraya alındı; diff'in odaklı kalması için bilinçli ayrıldı. Kendi migration'ına ekleme.
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/modules/brands/                     (yeni: __init__, domain, models, repository, policy, service, CLAUDE.md)
services/api/app/api/routes/brands.py                (yeni)
services/api/app/api/routes/__init__.py              (yalnızca bir import + bir satır)
services/api/app/core/pagination.py                  (yeni — cursor primitifi; core teknik kalır, domain sızmaz)
services/api/migrations/versions/0010_*.py           (yeni — MIGRATION SLOTU SENDE)
services/api/tests/unit/                             (policy, para birimi, skor, pagination)
services/api/tests/integration/                      (tenant izolasyonu, rol matrisi, idempotency, pagination)
docs/architecture/brand-catalog.md                   (yeni)
docs/architecture/error-handling.md                  (yeni hata kodları)
docs/adr/ADR-XXX-<konu>.md                           (yalnızca gerçek bir karar çıktıysa)
```

## Kabul kriterleri

1. Migration `0010` up → down → up çalışıyor; tek head.
2. Tenant izolasyonu: B işletmesinin markası/ürünü A'dan **`404`** ile görünmez (varlık ifşası yok); repository `business_id` olmadan çağrılamıyor; test var.
3. Rol matrisi testleri: `editor` marka/katalog yazamaz, `viewer` hiçbir mutation yapamaz, `owner`/`admin` yazar, `approver` yalnızca kendi kaynaklarını görür.
4. **Para birimi:** fiyat integer minor unit olarak saklanıyor; parasal alanlarda `float` bulunmadığını doğrulayan bir test var; para birimi uyuşmazlığı reddediliyor.
5. Kampanya aktifliği deterministik bir sorgu ile cevaplanıyor; süresi geçmiş kampanya "aktif değil" diyor; sınır anları (tam bitiş saniyesi) test edilmiş.
6. Yasak kelime / onaylı CTA listeleri kaydedilebiliyor ve okunabiliyor; boş/çok uzun girdi sınırları var.
7. Marka sağlık skoru deterministik, tavsiye niteliğinde, **hiçbir mutation'ı bloke etmiyor**; testte gösterilmiş.
8. Cursor pagination: opak cursor, kararlı sıralama, `limit` tavanı, bozuk cursor `400` ile reddediliyor, aynı veri üzerinde sayfa atlaması/tekrarı olmuyor (test var).
9. Her mutation idempotency değerlendirmesinden geçmiş; create uçlarında `Idempotency-Key` desteği var ve tekrar aynı sonucu dönüyor.
10. Yeni hata kodları `error-handling.md`'ye eklendi; hiçbir hata gövdesi iç bilgi sızdırmıyor.
11. `main.py` **değişmemiş** (`git diff` ile kanıtla).
12. `make verify` yeşil; OpenAPI ve `endpoints.md` yeniden üretilip commit'lendi (drift yok).
13. Modülün `CLAUDE.md`'si yazıldı: sahip olduğu/olmadığı şey, değişmezler, her dosya için bir satır, geçerli requirements + ADR, test yolları. ≤40 satır.

## ADR numara kuralı

Numarayı **sen seçmiyorsun.** Gerçek bir mimari karar çıktıysa dosyayı `ADR-XXX-<konu>.md` adıyla yaz, başlıkta da `ADR-XXX` bırak, raporda bildir. PM merge sırasında numaralandırır.

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum doldurur — özellikle: cross-tenant okuma/yazma, para biriminde kayan nokta, süresi geçmiş kampanyanın aktif görünmesi, cursor ile sayfa atlama)_
