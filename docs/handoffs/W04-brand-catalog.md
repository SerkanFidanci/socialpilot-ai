# W04 — Marka profili + ürün/hizmet kataloğu

**Dal:** `slice/1f-brand-catalog` · **Base:** `main` · **Migration slotu: SENDE** (`0010`) · **W05 ile paralel** (dosya-ayrık)
**Durum:** tamamlandı (2026-07-30) — rapor aşağıda, merge ve üç tek satırlık PM kararı bekliyor
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

## Rapor — 2026-07-30 · Claude Opus 4.8 (yürüten oturum)

**Dal:** `slice/1f-brand-catalog` (base `main` = `374c02b`) · **Commit'ler:** aşağıdaki tek slice commit'i · **Durum:** tamamlandı

### Yapılanlar

- **`modules/brands`** — `businesses` desenini izleyen yeni domain modülü: `models.py` (10 tablo),
  `domain.py` (saf kurallar), `policy.py`, `repository.py`, `service.py`, `CLAUDE.md`.
  Tablolar: `brand_profiles`, `brand_assets`, `target_audiences`, `products`, `product_prices`,
  `campaign_offers`, `campaign_offer_products`, `approved_claims`, `forbidden_claims`,
  `approved_ctas`.
- **Migration `0010_brand_catalog`** — tek head, up/down/up temiz. Tenant-öncelikli indeksler,
  `brand_assets → media_assets` FK'si `RESTRICT`, kampanya penceresi ve indirim tutarlılığı için
  CHECK constraint'leri, **ürün başına tek açık fiyat** için kısmi unique index.
- **Para birimi değişmezi** — `price_minor`/`discount_amount_minor` `BigInteger` minor unit +
  `String(3)` ISO-4217. Modülde `float`/`Decimal`/`Numeric` yok; test bunu üç yerden doğrular
  (mapped sütunlar, modül kaynak kodu, üretilmiş OpenAPI şeması). `CURRENCY_MISMATCH` üç yolda:
  markanın kayıt para birimi ↔ ürün fiyatı, ürünün mevcut fiyatı ↔ yeni fiyat, kampanya sabit
  indirim ↔ kampanya ürünlerinin fiyatı.
- **Fiyat geçmişi append-only** — reprice açık satırı kapatır, yeni satır ekler; aynı tutara
  reprice no-op. Geçen ay yayınlanmış bir gönderinin fiyatı hâlâ açıklanabilir.
- **Kampanya aktifliği deterministik** — `evaluate_campaign_activity` (saf) + SQL yüklemi
  `active_campaign_conditions`, pencere yarı açık `[starts_at, ends_at)`. İkisinin sınır
  satırlarında aynı cevabı verdiği integration testiyle bağlandı (drift koruması).
- **Marka sağlık skoru** — PRD §10.4'ün 11 bileşeni; 8'i ölçülüyor, modülü olmayan 3'ü
  `unavailable` ve paydadan düşülüyor. Salt okuma ucu, `advisory: true`; hiçbir mutation'ı
  bloke etmiyor (testte gösterildi).
- **`core/pagination.py`** — yeniden kullanılabilir keyset primitifi: opak unpadded base64url
  cursor, `created_at DESC, id DESC` kararlı sıralama (tie-break dahil), `limit` tavanı 100,
  bozuk cursor `400 PAGINATION_CURSOR_INVALID` (sessiz sıfırlama yok). Domain tipi bilmiyor.
- **API** — PRD §29.4'teki 8 ucun tamamı. Router **yalnızca** `routes/__init__.py`'a bir import +
  bir satır ile bağlandı; `main.py` **değişmedi** (`git diff` boş).
- Yeni hata kodları `error-handling.md`'ye, mimari `docs/architecture/brand-catalog.md`'ye yazıldı;
  OpenAPI + `endpoints.md` yeniden üretildi (16 → 24 endpoint, drift yok).

### Kararlar (gerekçeleriyle)

- **Yeni `Permission` satırı eklemedim.** `businesses/policy.py` dokunulacak dosya listemde
  değil ve WO "yetki `businesses`'ten gelir, yeniden yazılmaz" diyor. `brands/policy.py` yalnızca
  **eşleme** yapıyor: yazma → `business.update` (owner/admin), okuma → `business.read` (tüm
  roller). Bu, istenen rol matrisini birebir üretiyor ve ikinci bir politika tablosu doğurmuyor.
  İleride ayrı `brand.*` yetkileri istenirse değişecek tek yer bu eşleme.
- **Kampanya `approval_status` varsayılanı `not_required`.** Onay akışı (PRD §11.1/11) ve
  `approver` rolü yok; create yalnızca `not_required`/`pending` kabul ediyor, `approved`
  veremiyor. Aksi hâlde aktiflik sorgusu hiçbir kampanyayı kullanılabilir saymazdı.
- **Reklam bütçesi alanı (PRD §11.3) bilinçli olarak YOK.** Guardrail doğrulaması olmadan bütçe
  saklamak kod inceleme kuralını ihlal eder; reklam modülüyle gelmeli.
- **`campaign_offer_products` §28.2 listesinde yok** ama ilişki bunu gerektiriyor; JSON id dizisi
  yerine FK'li link tablosu seçtim ki kampanya silinmiş ürünü gösteremesin.
- **ADR yazmadım.** Yeni bir mimari karar çıkmadı: her şey mevcut ADR-001/002 ve PRD'nin içinde.

### Kapsam dışı bıraktıklarım ve nedeni

- **`businesses` ve `media` listelerinin cursor pagination'a çevrilmesi** — WO'da açıkça yok;
  kontrat + mobil istemci değişikliği, ayrı slice. **Öneri:** `slice/…-list-pagination-retrofit`.
- **Şema borcu üç kalemi** (`provider_usage`, `storage_upload_id`, fotoğraf enum'u) — W10'da.
- **ETag/optimistic locking (§29.1)** — `version` sütunu ekleyip zorlamamak dekorasyon olurdu;
  kabul kriterlerinde de yok. Ayrı, bütün API'yi kapsayan bir slice olmalı.
- `docs/index.md` / `docs/adr/README.md` indekslerine dokunmadım (W03 tekeli): **PM,
  `brand-catalog.md`'yi mimari tabloya eklemeli.**

### Sahibi olmadığım dosyalar — DURDUM, PM'e bırakıyorum

| # | Dosya | Neden gerekiyor | Etki |
|---|---|---|---|
| 1 | `services/api/app/modules/businesses/models.py` + `policy.py` | **`approver` rolü `BusinessRole` enum'unda yok.** Kabul kriteri 3'ün "approver yalnızca kendi kaynaklarını görür" yarısı bu yüzden test edilemedi | Rol eklemek enum migration'ı da ister. Yazdığım test `BusinessRole`'daki **her** rol için marka cevabının tanımlı olduğunu doğruluyor; `approver` eklendiği an bu test onu eşlemeye eklemeye zorlar |
| 2 | `services/api/app/infrastructure/database/metadata.py` | `MODEL_MODULES`'a `brands` eklenmeli (tek satır). Şu an API süreci route üzerinden import ettiği için çalışıyor, worker brands tablolarına dokunmuyor — **fonksiyonel açık yok**, ama dosyanın sözleşmesi "her tabloyu kaydet" | Autogenerate/metadata tabanlı bir kontrol ileride eksik görebilir |
| 3 | `services/api/scripts/generate_endpoints_doc.py` | `TAG_TITLES`'a `"brands": "brands — marka, katalog, kampanya"` (tek satır); şu an bölüm başlığı düz `brands` | Yalnızca kozmetik |

### Doğrulama

Docker (`COMPOSE_PROJECT_NAME=sp-w04`, ayrık host portları 55442/56389/59010/8010) içinde,
gerçek PostgreSQL + Redis + MinIO ile. Araç zinciri konteynerde: Python 3.13.14 · mypy 2.3.0 ·
ruff 0.16.0 · pytest 9.1.1.

| Kontrol | Sonuç |
|---|---|
| `ruff check` (app, tests, migrations, scripts) | ✅ temiz |
| `ruff format --check` | ✅ temiz |
| `mypy .` (strict) | ✅ 133 dosya, hata yok |
| `pytest` (RUN_INTEGRATION_TESTS=1) | ✅ **378 passed** (öncesi 313; +65 yeni test) |
| migration up → down → up | ✅ tek head `0010_brand_catalog` |
| OpenAPI + `endpoints.md` drift | ✅ yeniden üretildi ve commit'lendi (24 endpoint) |
| `main.py` değişmedi | ✅ `git diff services/api/app/main.py` boş |
| Kabul kriteri 1–10, 12, 13 | ✅ |
| Kabul kriteri 3 (`approver` yarısı) | ⚠️ rol enum'da yok — yukarıdaki tablo #1 |
| Kabul kriteri 11 | ✅ |

Kabul kriteri karşılıkları: (2) cross-tenant marka/ürün/kampanya okuma **ve** yazma `404`,
`PRODUCT_NOT_FOUND`/`BUSINESS_NOT_FOUND` ayrımı testli · (4) para birimi testleri üç katmanda ·
(5) sınır anları (`starts_at`, `ends_at - 1µs`, `ends_at`) + SQL/saf kural mutabakatı · (6) liste
girdilerinde boş/uzun/duplicate sınırları · (7) skor testte 0 iken tüm yazmalar başarılı ·
(8) 7 ürün üzerinde limit=2 ile sayfa yürüyüşü: atlama/tekrar yok, bozuk cursor `400` ·
(9) replay aynı ürünü döndürüyor, farklı gövde `IDEMPOTENCY_CONFLICT`, tek satır yazılmış.

### Açıkça belirtmem gerekenler

- **`STATUS.md` ile `git` çelişiyordu:** tablo `main`'i `7b9fd35` gösteriyor, gerçek `374c02b`
  (W04/W05 iş emirlerinin yazıldığı commit). `main` satırı PM'in; ben yalnızca W04 satırını
  güncelledim. **PM düzeltmeli.**
- **Compose kazası ve düzeltmesi:** ilk denemede `cd` bir dizin fazla gittiğinden `docker compose`
  üst dizine yürüyüp **`main` deposunun** `compose.yaml`'ını kullandı. Ayrı proje adı ve ayrı host
  portları sayesinde `main`'in konteynerleri **ele geçirilmedi**; yanlış stack `down -v` ile
  silindi ve doğrulama worktree kökünden yeniden koştu. Protokolün uyardığı hata tam olarak bu:
  proje adını ayırmak yeterli değil, **compose'u worktree kökünden çalıştırmak** da gerekiyor.
- **Yakalanıp düzeltilen gerçek regresyon:** ilk yazımda response sınıfımın adı `AssetResponse`
  idi ve `media` modülünde aynı adda bir model var. FastAPI çakışmayı **iki şemayı da** tam nitelikli
  ada çevirerek çözüyor — yani benim dosyam, `media`'nın mevcut public şema adını
  `app__api__routes__media__AssetResponse`'a **yeniden adlandırıyordu** (üretilmiş istemcileri
  bozan bir kontrat değişikliği). Sınıflar `BrandAssetPayload`/`BrandAssetResponse` olarak
  adlandırıldı ve şema adlarında `__` bulunmadığını doğrulayan bir **regresyon testi** eklendi;
  bir sonraki modül aynı tuzağa sessizce düşemez.
- **Aktiflik kuralı iki yerde** (saf fonksiyon + SQL yüklemi). Kaçınılmaz; mutabakat testiyle
  bağlandı, ama gelecekte biri kuralı değiştirirse **iki yeri** değiştirmeli.
- **`MediaAssetReader`** brands içinden `media_assets`'i salt-okunur sorguluyor (sağlık skoru
  foto/video sayımı + marka asset'inin tenant doğrulaması). `media` modülü kendi envanter
  okumasını yayınlamadığı ve o modül dosya listemde olmadığı için port arkasına tek isimli bir
  nesne olarak koydum; medya bunu kendi sunduğunda silinecek tek yer o sınıf.
- Kampanya sabit indirimi, fiyatı olmayan ürüne izin veriyor (mutlak tutar); fiyatı olan her
  ürünün para birimiyle uyuşmak zorunda.
- Ürün adı tenant içinde tekil (`PRODUCT_NAME_CONFLICT`) — katalog hijyeni ve üretimin "hangi
  Soğuk Latte" belirsizliğini yaşamaması için bilinçli.

## Doğrulama

_(test eden oturum doldurur — özellikle: cross-tenant okuma/yazma, para biriminde kayan nokta, süresi geçmiş kampanyanın aktif görünmesi, cursor ile sayfa atlama)_
