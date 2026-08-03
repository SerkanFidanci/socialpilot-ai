# W22 — Phase 2G: İçerik planlayıcı (§13)

**Dal:** `slice/2g-content-planner` · **Base:** `main` · **Migration slotu: SENDE** (`0019`)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Plan:** [Phase 2 planı](../plans/active/phase-2-content-generation.md) — slice 2G (**Phase 2'nin son dilimi**)
**Neden bu iş:** 2A–2F bir içeriği baştan sona üretip onaylatabiliyor — ama **her zaman biri elle tetiklediği için.** Ürünün vaadi bu değil: "işletme uyurken içerik hazır olsun." §13 bunu `content_obligation` üzerinden tanımlıyor: abonelikten talep türer, talep projeye dönüşür. Bu dilim kapanınca Phase 2 biter.

## Okunacaklar

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/product/requirements/40a-content-planning-scenarios.md`](../product/requirements/40a-content-planning-scenarios.md) — **§13.1 obligation şekli, §13.2 on öncelik, §13.3 içerik karması**
3. [`docs/product/requirements/90a-database-design.md`](../product/requirements/90a-database-design.md) — `content_obligations` satırı ve index'i
4. `services/api/app/modules/content/CLAUDE.md` — W19'un durum makinesi, W21'in onay kenarları
5. `services/api/app/modules/entitlement/CLAUDE.md` — planlayıcı **kredi harcatacak**, rezervasyon disiplinini oradan al

## PM kararları

### 1. `SCHEDULED` kenarı `APPROVED`'dan çıkar (W21'in yükselttiği karar — kabul edildi)

W21 §20'ye `APPROVED` ve `CANCELLED` durumlarını ekledi ve haklı olarak sordu. Karar: **`APPROVED → SCHEDULED`**, `WAITING_APPROVAL → SCHEDULED` değil. Onaylanmamış içerik takvime giremez; `never_within_guardrails` politikasında zaten aktörsüz `auto_approved` kaydıyla `APPROVED`'a düşüyor, yani yol her politikada tanımlı.

**Bu slice `SCHEDULED`'ı ekler ve orada durur.** `PUBLISHING`/`PUBLISHED` → Phase 4.

### 2. Planlayıcı **talep üretir, içerik üretmez**

`content_obligations` bir **sıraya alma** kaydıdır: hangi işletme, hangi içerik tipi, hangi pencerede, ne zaman yayınlanmalı, üretimin son teslim anı ne. Obligation'dan projeye geçiş **ayrı ve idempotent** bir adımdır. Gerekçe: planlama ile üretim aynı işlem olursa, planlayıcının bir hatası doğrudan para harcar; ayrı olunca plan gözden geçirilebilir ve iptal edilebilir.

**Obligation → proje dönüşümünde kredi rezerve edilir** (W20'nin yolu). Yetersiz bakiye → obligation `blocked` olur, **sessizce kaybolmaz** ve kullanıcıya görünür.

### 3. Öncelikler **saf ve sıralı**, ağırlıklı skor değil

§13.2'nin on maddesi bir **sıra** — ağırlıklı bir puan değil. Deterministik ve açıklanabilir bir sıralayıcı yaz: girdi = aday obligation kümesi + bağlam (aktif kampanya, marka dengesi, medya yeterliliği, geçmiş tekrar), çıktı = sıralanmış liste **ve her adayın hangi kuralla o sıraya girdiği** (açıklama alanı — kullanıcı "neden bu içerik?" diye sorabilmeli).

**Bu slice'ta 1–3, 6–9 uygulanır** (kampanya, abonelik, marka dengesi, ürün tekrarı, platform uygunluğu, sessiz saatler, kullanıcı tercihleri). **4 (geçmiş performans) ve 10 (özel günler) YOK:** performans verisi Phase 5'te doğuyor, özel gün takvimi doğrulanmış dış kaynak istiyor (§13.2'nin kendi şartı) ve o kaynak seçilmedi. İkisi için alan açılır, doldurulmaz — raporda bildir.

### 4. Sessiz saatler ve zaman disiplini

`planned_publish_at` **işletmenin saat diliminde** anlamlı, depoda **UTC** (AGENTS.md kuralı). Sessiz saat penceresi işletme ayarı; pencereye düşen bir zaman **pencere dışına kaydırılır**, iptal edilmez. **DST geçişi ve yerel gece yarısı testte olmalı** — Türkiye'de DST yok ama kod tenant timezone'una göre çalışmalı ve bunu varsayım olarak gömmemeli.

### 5. İçerik karması **hedef**, kota değil (§13.3)

Yüzdeler bir **dağılım hedefi**; tek bir haftada tutturulamayabilir. Planlayıcı, karma hedefinden en çok sapan kategoriyi öne alır (§13.2'nin 3. maddesi "marka içerik dengesi"). **Sert kota koyma** — "bu hafta eğitici içerik kotası doldu, kampanya üretme" davranışı kampanyası olan bir işletmeyi cezalandırır. Sapma **ölçülür ve raporlanır**, yargılanmaz (2C→2D deseninin aynısı).

### 6. Planlayıcı beat'te çalışır ve **idempotenttir**

Aynı pencere için ikinci koşu ikinci obligation üretmez (doğal anahtar: business + subscription_item + period). Beat aralığı config'de. W19/W20/W21'in job disiplini geçerli: durum, timeout, deneme, correlation, dead-letter, süpürücü.

## Kapsam dışı (dokunma)

- **Yayınlama** (`PUBLISHING` ve sonrası, platform API'leri) → Phase 4.
- **Abonelik kalemlerinin kendisi** (`subscription_items`, plan tanımı) → Phase 3. Bu slice obligation'ı **var olan bir kaynaktan** türetir; kaynak yoksa **manuel/seed** bir abonelik kalemi tanımı yeterli (W20'nin grant deseni gibi).
- Geçmiş performans (§13.2/4) ve özel günler (§13.2/10) → alan aç, doldurma.
- `script.py`, `qc.py`, `text_normalization.py`, W21'in onay mantığı → dokunma.
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/modules/planner/**                       (yeni modül: obligation, öncelik sıralayıcı, karma ölçümü, sessiz saat, CLAUDE.md)
services/api/app/modules/content/{lifecycle,project_service}.py   (SCHEDULED kenarı + obligation→proje)
services/api/app/modules/content/models.py                (proje ↔ obligation bağı)
services/api/app/api/routes/planner.py + routes/__init__.py
services/api/app/core/config.py                           (PLANNER_* ayarları)
services/api/app/worker/{tasks,composition}.py + infrastructure/celery_app.py
services/api/migrations/versions/0019_*.py                (SLOT SENDE)
services/api/tests/unit/ + tests/integration/
docs/architecture/ (planlayıcı bölümü — hangi dosyaya yazdığını bildir) · error-handling.md · .env.example
```

## Kabul kriterleri

1. Migration `0019` up → down → up; tek head.
2. **Uçtan uca:** seed'lenmiş bir abonelik kaleminden obligation üretiliyor → obligation projeye dönüşüyor (kredi rezerve edilerek) → proje `APPROVED` olunca `SCHEDULED`'a geçiyor; her adım kayıtlı, gerçek PostgreSQL.
3. **İdempotency:** aynı pencere için ikinci planlayıcı koşusu ikinci obligation **üretmiyor**; eşzamanlı iki koşu da üretmiyor (gerçek paralel transaction).
4. **Sıralayıcı saf ve açıklanabilir:** aynı girdi → aynı sıra; her adayın sıra gerekçesi çıktıda var. Uygulanan yedi öncelik permütasyon/tablo testiyle kapsanıyor; uygulanmayan ikisi **açıkça** "alan var, kural yok" olarak testli.
5. **Sessiz saat:** pencereye düşen zaman dışarı kaydırılıyor, iptal edilmiyor; tenant timezone'u UTC'den türetiliyor; yerel gece yarısı ve DST'li bir timezone testte (ör. `Europe/Berlin`) — kod TR'ye gömülü değil.
6. **Yetersiz bakiye:** obligation `blocked`, proje oluşmuyor, kredi harcanmıyor, durum kullanıcıya görünür uçtan okunuyor.
7. **Karma ölçülüyor, yargılanmıyor:** sapma raporlanıyor; sert kota yok (testle: kampanyası olan işletme karma yüzünden engellenmiyor).
8. Tenant izolasyonu her sorguda; roller (planlayıcı ayarları `business.update`, okuma `business.read`); idempotency kanonik gövdeden; imzalı URL sızmıyor.
9. `make verify` yeşil; test sayısı **1375** tabanının altına düşmez; kontrat yeniden üretilip commit'li; modül `CLAUDE.md` yazıldı.
10. Rapor + araç zinciri sürümleri. **Merge etme, dalda bırak.**

## Enumerasyon kuralı

Öncelik listesi ve içerik kategorileri PRD tarafından **kapalı** — yazılabilir. Ama **"hangi bağlamda hangi aday öne geçer"** kombinatoryaldir: total fonksiyon + permütasyon testi (2D/2E/2F deseni).

## ADR numara kuralı

Gerçek karar çıkarsa `ADR-XXX-<konu>.md`; numarayı PM verir. ("Planlayıcı talep üretir, içerik üretmez" ayrımı ADR'lık.)

## Rapor — 2026-08-02 · Opus 5 / high

**Dal:** `slice/2g-content-planner` · **Commit:** `3e62cc3` · **Durum:** tamamlandı, dalda

### Yapılanlar

**Yeni modül `modules/planner/**` (6 dosya + `CLAUDE.md`).** Saf yarı `obligation.py`'de: §13.1'in
obligation durum makinesi (kapalı ve total), §13.2'nin on önceliği, §13.3'ün karma ölçümü, sessiz
saat + yerel takvim aritmetiği, `ContentType → (ScenarioCode, RenderProfile)` eşlemesi (import
anında total). Dünyaya dokunan yarı `service.py`'de, **üç ayrı servis** olarak — PM kararı 2'nin
sınıf adlarına yazılmış hâli:

1. `ObligationPlanningService` — ayakta duran talepten pencere obligation'ları. Yapıcısında ne
   `EntitlementService` ne `ContentProjectService` var: planlama **para harcayamaz**.
2. `ObligationDispatchService` — en yüksek sıradaki obligation → proje, `create_project` üzerinden
   (yani rezervasyon projeyi yaratan transaction'da, W20'nin yolu). Kredi harcayan tek adım.
3. `ProjectSchedulingService` — `APPROVED → SCHEDULED` + biten projelerin obligation'larını
   uzlaştırma + kapanan pencerelerin süresini doldurma.

**`content` tarafında dört dokunuş.** `ProjectState.SCHEDULED` + `ProjectEvent.SCHEDULED` ve
`APPROVED → SCHEDULED` kenarı; terminal küme `{scheduled, failed, cancelled}` (yani **`approved`
yeniden açıldı**, 2F'nin `preview_ready`'yi açtığı gibi); `waits_for_user` → `waits_for_handoff`
(APPROVED da o kümede — planlayıcıyı beklemek durmuş bir iş değil); `content_projects
.scheduled_publish_at` + `ck_content_project_scheduled_has_time`.

**Migration `0019`.** İki enum swap (`ADD VALUE` kullanılamaz — yeni değeri *kullanan* kısıt ve
index predicate'i aynı transaction'da), üç yeni tablo, beş yeni enum tipi, bir yeni proje sütunu,
ve eski terminal kümeyi yazan üç nesnenin (kısmi claim index'i + iki check constraint) bir durum
ileri taşınması. `approved`'daki mevcut projelere backfill ile due time veriliyor; zamanlama
drain'i onları alıp slot veriyor. Downgrade `scheduled` durumunda proje varsa **reddediyor**
(`0011`/`0018`'in cevabı).

**Dokuz uç** (`/v1/businesses/{id}/planner/…`): ayarlar (GET/PUT), ayakta duran talep
(POST/GET liste/GET tek/POST status), obligation (GET liste/GET tek/POST cancel), **sıralı plan**
(her adayın on gerekçesiyle) ve **karma raporu**. Obligation *yaratan* veya *dönüştüren* uç
bilerek yok: ikisi de bir saatin ve bir worker'ın işi, ve aynı etkiye giden ikinci bir yol
§13.2'nin sırasıyla er ya da geç anlaşmazlığa düşerdi.

**Üç beat girdisi + üç drain task'ı**, worker composition'a bağlı; hiçbiri sağlayıcı portu
taşımıyor.

### Kapsam dışı bıraktıklarım ve nedeni

- **§13.2/4 (geçmiş performans) ve §13.2/10 (özel günler):** alan açıldı, kural yazılmadı.
  `RankContext.performance_score` ve `special_day_code` taşınıyor, **hiçbir şey okumuyor**, ikisi
  de sabit katkı veriyor. `UNIMPLEMENTED_PRIORITIES` ikisini adlandırıyor ve iki test bunu
  zorluyor: değer vermek hiçbir sırayı değiştirmiyor, ve dominance testinin o iki adımı "kuralı
  yok" dalına düşüyor. Gerekçe WO'nun kendi gerekçesi (Phase 5 verisi / seçilmemiş takvim kaynağı).
- **Yayınlama:** `SCHEDULED` son durak. `PUBLISHING` Phase 4.
- **Abonelik kaleminin kendisi:** `planner_subscription_items` §12.2'nin **yer tutucusu**, elle
  kuruluyor (W20'nin grant deseni). Obligation §13.1'in sütun adını (`subscription_item_id`)
  koruyor, böylece Phase 3 bir FK'yi yeniden hedefliyor, PRD'nin adlandırdığı alanı yeniden
  adlandırmıyor.
- **Ürün/sahne seçimi:** ayakta duran talep ikisini de adlandırıyor. §13.2/6 bu yüzden bir
  *sıralama cezası*, bir seçim kuralı değil — §13.2 adayları sıralıyor, icat etmiyor.
- **`docs/index.md` / `docs/adr/README.md`:** WO'nun dediği gibi eklenmedi (aşağıda).

### Doğrulama

Araç zinciri: Python 3.13.14 · mypy 2.3.0 · ruff 0.16.0 · alembic 1.18.5 · PostgreSQL 16.14 ·
FFmpeg 7.1.5 · MinIO RELEASE.2025-04-22. Compose projesi `sp-w22`, **ayrı host portlarıyla** —
`main`'in konteynerlerine dokunulmadı. Doğrulayan oturum aynısını şöyle kurar (dosya `.gitignore`
kapsamında, bu yüzden commit'lenmedi):

```
COMPOSE_PROJECT_NAME=sp-w22
POSTGRES_HOST_PORT=55532
REDIS_HOST_PORT=56479
MINIO_HOST_PORT=59200
MINIO_CONSOLE_HOST_PORT=59201
API_HOST_PORT=8100
S3_PRESIGN_ENDPOINT_URL=http://127.0.0.1:59200
```

`docker compose --env-file .env.w22 up -d --build`, sonra
`docker compose --env-file .env.w22 exec -T -e RUN_INTEGRATION_TESTS=1 -e STORAGE_ADAPTER=s3 api
sh -c "cd /app/services/api && python -m pytest"`.

| Kontrol | Sonuç |
|---|---|
| `ruff check` + `ruff format --check` (app, tests, migrations, scripts) | ✅ temiz |
| `mypy .` (strict) | ✅ 219 dosya, hata yok |
| migration `upgrade head` → `downgrade base` → `upgrade head` | ✅ tek head (`0019_content_planner`) |
| `pytest` (unit) | ✅ 1162 geçti |
| `pytest tests/integration/test_content_planner.py` | ✅ 20 geçti (gerçek PostgreSQL + MinIO + FFmpeg) |
| `pytest` (tam suite, `RUN_INTEGRATION_TESTS=1`, `STORAGE_ADAPTER=s3`) | ✅ **1459 geçti**, 0 başarısız, 0 atlandı (13 dk 54 sn) |
| `make check-openapi` | ✅ kontrat yeniden üretildi ve commit'li (50 → **61 endpoint**) |

Kabul kriterleri, sırayla:

1. **`0019` up→down→up, tek head.** ✅ Zincir `0001→0019`, `alembic heads` tek satır.
2. **Uçtan uca.** ✅ `test_a_standing_demand_becomes_a_scheduled_content_project`: seed'lenmiş
   abonelik kalemi → obligation (§13.1'in dört anı, `generation_deadline_at < planned_publish_at`)
   → proje (rezervasyon `reserved`, bakiye düştü) → **gerçek FFmpeg render + gerçek ffprobe QC** →
   `waiting_approval` → onay → `SCHEDULED`. Geçiş tablosunun son satırı `('scheduled','scheduled')`,
   obligation `fulfilled`.
3. **İdempotency.** ✅ Üç ayrı test: ardışık ikinci koşu `planned: 0`; **gerçek paralel iki
   transaction** biri `planned: 1` biri hiç (advisory lock + `SKIP LOCKED`); ve doğal anahtar
   servisi devre dışı bırakarak doğrudan zorlandı (`uq_content_obligation_period`). Ayrıca
   dönüşümün tekrarı: obligation `planned`'a geri alınıp yeniden dispatch edildiğinde **tek proje,
   tek rezervasyon, değişmeyen bakiye**.
4. **Sıralayıcı saf ve açıklanabilir.** ✅ Birim tarafında: on önceliğin **her ardışık çifti** için
   dominance (üstteki en iyi + alttakilerin hepsi en kötü, yine kazanıyor), sıra bağımsızlığı,
   eşitlik bozucular, kova sınırları. Uygulanmayan ikisi ayrı testle "alan var, kural yok".
   Entegrasyon tarafında: `GET …/planner/plan` on gerekçeyi döndürüyor, kampanyalı aday
   kullanıcının elle daha yüksek sıraladığı adayı yeniyor, ve **dispatcher planın birincisini
   dönüştürüyor**.
5. **Sessiz saat + timezone.** ✅ Pencereye düşen 23:00 → ertesi yerel 08:00 (iptal değil, kaydırma;
   obligation hâlâ `planned`). Yerel gece yarısı, sarmalı pencerenin iki yarısı, boş pencere,
   `Europe/Berlin`'de **ilerlemeyen yerel saate** kaydırma ve **ikilenen saatte** kaydırma, ve
   DST günlerinin 23/25 saatlik uzunluğu birim testte; entegrasyonda gerçek `Europe/Berlin`
   işletmesi yerel gece yarısı sınırları ve yerel öğlen slotu üretiyor. Kod TR'ye gömülü değil —
   `resolve_timezone` bilinmeyen dilimi **UTC'ye düşürmüyor**, reddediyor.
6. **Yetersiz bakiye.** ✅ Obligation `blocked` + `reason_code=ENTITLEMENT_INSUFFICIENT_CREDITS`,
   `content_projects`/`usage_reservations`/`credit_ledger` **hepsi boş**, durum
   `GET …/planner/obligations?status=blocked` ile okunuyor. Bakiye yüklenince aynı pencere
   dönüşüyor (`blocked` bir ölüm değil bir durum).
7. **Karma ölçülüyor, yargılanmıyor.** ✅ Kampanya payı hedefin çok üstündeyken bile kampanya
   obligation'ı ilk dönüşen oluyor; sapma `GET …/planner/mix`'te raporlanıyor. Birim tarafında
   `measure_mix`'in **başarısızlık modu yok**.
8. **Tenant + roller + idempotency + imzalı URL.** ✅ Başka tenant'ın obligation'ı 404 (gerçek id
   ile uydurma id aynı cevabı veriyor), cross-tenant cancel de 404. `editor` ve `viewer` planlayıcı
   ayarlarını ve kalemlerini **yazamıyor** (403), ikisi de okuyabiliyor. Başka tenant'ın ürününü
   adlandıran kalem yaratılışta reddediliyor. Kalem yaratma `Idempotency-Key` alıyor ve parmak izi
   kanonik gövdeden; dönüşüm anahtarı obligation'dan türetiliyor. Planlayıcı hiçbir imzalı URL
   görmüyor — depoya hiç dokunmuyor.
9. **`make verify` yeşil; test sayısı 1459** (taban 1375'in üstünde); kontrat yeniden üretilip
   commit'li; `modules/planner/CLAUDE.md` yazıldı.
10. Rapor + araç zinciri sürümleri yukarıda. **Merge edilmedi, dalda bırakıldı.**

### Açıkça belirtmem gerekenler

**1. İlan dışı iki dosyaya dokundum, ikisi de zorunluydu.**

- `services/api/app/infrastructure/database/metadata.py` — `MODEL_MODULES`'e `planner` eklendi
  (bir satır). O dosyanın var olma sebebi "hangi giriş noktası olursa olsun her tablo kayıtlı
  olsun"; planlayıcıyı dışarıda bırakmak, worker'ın `verify_mapping_is_complete`'inin eksik bir
  metadata üzerinde koşması demekti.
- `services/api/tests/unit/{test_celery_publisher,test_content_lifecycle_unit,test_entitlement_unit}.py`
  ve `services/api/tests/integration/test_content_approval.py` — dördü de ilan edilmiş
  `tests/unit/` + `tests/integration/` altında; kaydediyorum çünkü **başka slice'ların** yazdığı
  testler ve `approved`'ın terminal olmaktan çıkması dördünün de sabitlediği bir şeyi değiştirdi
  (terminal küme, `waits_for_handoff` kümesi, `source_outcome`'un DELIVERED kümesi, ve W21'in
  "otomatik onay sonrası due time yok" iddiası). Değişiklikler mekanik; her birinin gerekçesi
  testin içinde yazılı. W21'in testinde iddia **zayıflatılmadı, taşındı**: artık "slot yok ve
  sıralayıcı ne kadar bakarsa baksın `approved`'da kalıyor" diyor, çünkü oradan çıkaran kenar
  planlayıcının.

**2. ADR yazılmadı, numara ve dosya PM'e bırakıldı.** WO "planlayıcı talep üretir, içerik üretmez"
ayrımını ADR'lık sayıyor ve haklı — ama `docs/adr/` ilan edilen dosya listesinde yok. Karar
[`docs/architecture/content-planner.md`](../architecture/content-planner.md) §"Dört karar"da tam
gerekçesiyle yazılı; ADR'a taşınması bir kopyalama işi. Aynı sebeple `docs/index.md` ve
`docs/adr/README.md` indekslerine hiçbir şey eklenmedi (WO bunu zaten istiyordu).

**3. Üç drain de yalnızca tick'le uyanıyor, ve bunun yalnızca biri ilkesel.**
`planner.obligations.plan` için üretici *olamaz* — "yeni bir dönem başladı" diye olay yazan bir şey
yok, ve bir tarihin gelişini gözleyebilen tek şey bir tick (W19/W21'in iki süpürmesiyle aynı
gerekçe). `planner.obligations.dispatch` ve `planner.projects.schedule` için **olabilirdi**:
planlanan bir obligation dispatcher'ı, bir onay zamanlayıcıyı uyandırabilirdi. Yapmadım çünkü
outbox zarfı `infrastructure/celery_publisher.py`'de ve o dosya ilan listesinde yok. Bedeli
gecikme, doğruluk değil; iki tick de kısa (60 s / 120 s) ve
`test_the_planner_drains_run_on_ticks_alone_and_the_first_one_has_to` iddiayı sabitliyor.
**PM'e:** bu, `celery_publisher.py`'ye iki satır ekleyen küçük bir takip işi.

**4. `approved` terminal olmaktan çıktı — bu 2F'nin `preview_ready`'ye yaptığının aynısı ve
sonuçları var.** (a) `can_cancel(APPROVED)` artık **true**: onaylanmış ama zamanlanmamış bir proje
iptal edilebiliyor (§20'nin "terminal olmayan her yerden iptal" kuralının doğru sonucu).
(b) `source_outcome(SCHEDULED)` = `DELIVERED`; ücretlendirme anı değişmedi (`preview_delivered_at`
hâlâ `PREVIEW_READY`'de damgalanıyor), sonraki her `settle` `ALREADY_APPLIED`. (c) Migration
mevcut `approved` projelere due time veriyor ve zamanlama drain'i onlara slot veriyor — planlayıcı
yokken onaylanmış içeriğin doğru muamelesi.

**5. Elle yaratılmış projeler de planlayıcıdan slot alıyor.** `approved` herkes için terminal
olmaktan çıktığı için, hiçbir obligation'ın planlamadığı bir proje de zamanlanmak zorunda —
yoksa hiçbir şeyin çıkmadığı bir durumda otururdu. Slot `PLANNER_MANUAL_PUBLISH_DELAY_SECONDS`
(varsayılan 15 dk) sonrası, sessiz pencerenin dışına itilerek. Testte pinli.

**6. `in_progress` terminal değil ama due time taşımıyor**, ve kısıt bunu söylüyor
(`ck_content_obligation_due_matches_status`, claim index'iyle **aynı küme** üzerine yazıldı).
İlk yazımda kısıt "terminal olanlar" üzerineydi ve entegrasyon testi bunu **gerçek bir hata**
olarak yakaladı: obligation projeye dönüşünce yoklanmayı bırakıyor, çünkü artık dayanıklı iş
projenin kendisi.

**7. Bilerek bırakılan üç şey.**
(a) `PLANNER_MANUAL_PUBLISH_DELAY_SECONDS`, `PLANNER_URGENT_WINDOW_SECONDS`,
`MIX_TOLERANCE_POINTS` (5 puan) ve `PLANNER_REPETITION_WINDOW_DAYS` (14 gün) **tahmin**; hiçbiri
ölçülmedi. Karma toleransı ve tekrar penceresi ürün kararı, ilk gerçek tenant'la gözden geçirilmeli.
(b) `ContentType` bugün altı üye ve **hepsi** `ScenarioCode.PRODUCT_REELS`'e eşleniyor, çünkü §14'ün
tek açık senaryosu o. §14.2/§14.5 geldiğinde değişecek tek tablo `_SURFACES`.
(c) Bir standing item pencere başına **bir** obligation üretiyor (doğal anahtar bu). "Günde iki
Reels" iki kalem demek — bilinçli, çünkü anahtarın üçüncü bir bileşeni (sıra numarası) idempotency
argümanını zayıflatırdı.

**8. `preference_rank` sınırsız değil ama sıralamada ham sayı.** 0–999 arası ve leksikografik
anahtarın 9. bileşeni; yani bir tenant `preference_rank`'i 999 yaparak kendi adayını sona atabilir
ama **1–8 arası önceliklerin hiçbirini** etkileyemez. Bu doğru taraf: §13.2/9 kullanıcı tercihini
sekiz kuralın *altına* koyuyor.

## Doğrulama

Bağımsız test oturumu: **2026-08-02 · Codex**. Worktree kökünden
`COMPOSE_PROJECT_NAME=sp-verify` ile, migration head `0019_content_planner` üzerinde çalışıldı.
Mevcut test fixture'ları saldırı kanıtı olarak kullanılmadı: çalıştırma kimliği `b2d8650722` olan
geçici harness repo dışında tutuldu ve her saldırı için yeni UUID'ler, tenantlar, standing item'lar,
obligation'lar, projeler ve saat dilimi girdileri üretti.

| # | Saldırı | Sonuç | Kendi girdimizle kanıt | Durum |
|---|---|---|---|---|
| W22-A1 | Aynı pencerede çift obligation üret | Engellendi | İki ayrı eşzamanlı plan transaction'ından biri `planned=1`, diğeri kayıt üretmeden tamamlandı; zorunlu replay `planned=0`; satır sayısı `1`. Aynı doğal anahtarla ham ikinci INSERT `UniqueViolation`. | Geçti |
| W22-A2 | Sessiz saate yayın kaydırt veya sessiz saat dışını gereksiz kaydır | Doğru sınırlandı | `20:15–06:45` penceresinde `23:17` slotu ertesi gün `06:45 +03`'e kaydı (`shifted=true`); `12:34` aynen kaldı (`shifted=false`). | Geçti |
| W22-A3 | Yetersiz bakiyeyle proje aç | Engellendi | Dispatch sonucu `converted=0, blocked=1`; obligation `blocked / ENTITLEMENT_INSUFFICIENT_CREDITS`, `project_id=NULL`. Proje, rezervasyon ve defter satırı sayıları ayrı ayrı `0`; kayıt API'de `status=blocked` ile görünür. | Geçti |
| W22-A4 | Onaylanmamış projeyi `SCHEDULED` yap | Engellendi | `waiting_approval` projesi için dört ayrı scheduling drain'i iş üretmedi; durum ve `scheduled_publish_at=NULL` kaldı, scheduled transition sayısı `0`. | Geçti |
| W22-A5 | Başka tenant'ın obligation'ını oku, iptal et veya planına kat | Engellendi | Kurban obligation için saldırgan tenant'ın GET ve cancel çağrıları `404`; kurban UUID'si saldırganın plan listesinde yoktu. | Geçti |
| W22-A6 | Planlayıcıyı sonsuz obligation üretmeye zorla | Engellendi | Aynı aktif item/pencere 40 defa zorlandı; ilk çağrı `planned=1`, sonrakiler `planned=0`; toplam obligation `1`. | Geçti |
| W22-A7 | Yerel gece yarısı veya DST'de yanlış pencere üret | Doğru hesaplandı | Bağımsız 2027 girdilerinde Berlin bahar günü `23 saat`, sonbahar günü `25 saat`; İstanbul periyodu yerel `00:00`'da başladı. Var olmayan Berlin `02:45` quiet-end anı `03:45+02`'ye taşındı ve pencere dışına çıktı. | Geçti |

| Bulgu | Şiddet | Yeniden üretim | Durum |
|---|---|---|---|
| Açık W22 ürün/kod bulgusu yok | — | Yukarıdaki yedi saldırı, gerçek PostgreSQL transaction'ları, API rotaları ve `zoneinfo` girdileriyle yeni veri üzerinde uygulandı. | Kapalı |

### Araç zinciri

| Araç | Sürüm |
|---|---|
| Docker Engine (client/server) | 25.0.3 |
| Docker Compose | v2.24.6-desktop.1 |
| Python | 3.13.14 |
| pytest / pluggy / pytest-asyncio / anyio | 9.1.1 / 1.6.0 / 1.4.0 / 4.14.2 |
| Ruff / mypy | 0.16.0 / 2.3.0 |
| Alembic | 1.18.5 |
| PostgreSQL | 16.14 (Alpine) |
| Redis | 7.4.10 (jemalloc 5.3.0) |
| MinIO | RELEASE.2025-04-22T22-12-26Z (Go 1.24.2) |
| FFmpeg / ffprobe | 7.1.5-0+deb13u1 |

### Ortak kapılar

| Kontrol | Sonuç |
|---|---|
| Migration zinciri | İzole test verisi temizlendikten sonra `downgrade base` → `upgrade head`; current/head `0019_content_planner`. Veri varken 0018 downgrade guard'ı beklendiği gibi işlemi reddetti. |
| Ruff | `check` temiz; `format --check`: 233 dosya biçimli. |
| mypy | `no issues found in 219 source files`. |
| Tam test paketi | MinIO bucket ilk kurulumundan sonra **1459 passed**, 1 Starlette deprecation uyarısı, 986.96 sn. İlk koşudaki 43 hata eksik test bucket'ının 404 vermesiydi; `minio-init` sonrası aynı kodla kayboldu. |
| OpenAPI | Kontrat ve endpoint indeksi yeniden üretildi; commit'li dosyalarla içerik farkı yok. |
| `make verify` eşdeğeri | API imajında `make` bulunmadığı için hedef doğrudan açılamadı; Makefile'daki Ruff, format, mypy, tam pytest ve OpenAPI adımları tek tek aynen çalıştırıldı ve geçti. |

**Karar: teslim edilebilir.** W22 saldırı listesinde açık bulgu kalmadı. Bu oturum uygulama veya
test kaynak kodunu değiştirmedi.
