# W23 — Defter bütünlüğü: yazar disiplinini şemaya taşı

**Dal:** `fix/ledger-integrity` · **Base:** `main` · **Migration slotu: SENDE** (`0020`)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Neden bu iş:** Bağımsız doğrulama turu (2026-08-03) W20'de üç açık buldu ve üçü de aynı kök nedene çıkıyor: **defterin bütünlüğü, yazan kodun doğru davranmasına bırakılmış.** Bugün doğru davranıyor — `EntitlementService` advisory lock alıyor, idempotency anahtarı kanonik, dış rotalar temiz. Ama **defterin kendisi** korumuyor, ve bu bir zaman bombası: Phase 3'te mağaza webhook'u grant yazacak, Phase 5'te reklam harcaması yazacak, bir gün bir bakım script'i düzeltme satırı yazacak. O yazarların hiçbiri bugünkü disiplini bilmiyor olacak.

Bu, projenin başka her yerinde uyguladığımız ilkeyle çelişiyor: QC'de "bir kontrolü atlamak *ifade edilemez*", W22'de "planlama para harcayamaz" sınıf imzasıyla korunuyor. Defter para tutuyor ve en zayıf korunan yer o.

## Bulgular (doğrulama turundan, tam kayıt W20 dosyasında)

| # | Bulgu | Şiddet | Kanıt |
|---|---|---|---|
| **W20-F2** | **Eşzamanlı ham defter yazıları negatif bakiye trigger'ını aşıyor.** İki ayrı gerçek transaction bariyerde eşzamanlı `consume -5` yazıp commit etti; türetilen bakiye **`-5`**. Trigger diğer transaction'ın commit edilmemiş satırını göremiyor (READ COMMITTED'ın doğal sonucu). | **Yüksek** | Servis yolu güvenli kaldı (gerçek HTTP yarışı `[201, 402]`, bakiye `0`). **Kapsam ikinci turda daraldı:** tek transaction içinde çoklu `consume` ve `COPY` ile toplu yazım **engelleniyor** (`IntegrityError` / `CheckViolationError`); açık yalnızca **iki ayrı eşzamanlı transaction** yolunda. Yani çözüm kilit/serileştirme tarafında — kısıt tarafı zaten çalışıyor |
| **W20-F1** | **Aynı rezervasyona ikinci `refund` yazılabiliyor.** Farklı idempotency anahtarıyla, şema açısından geçerli ikinci iade commit edildi: refund sayısı `1→2`, bakiye `5→10`. Yani **para yaratılabiliyor.** | **Yüksek** | Kanonik `refund:<reservation_id>` replay'i doğru davranıyor; şema tekilleştirmiyor |
| **W20-F3** | **`reserve` aynı `(business_id, source_type, source_id)` için ikinci rezervasyon açıyor** (yeni idempotency anahtarıyla). Dış parametrik-render rotası bunu yapmıyor ama servis kendini korumuyor. | Orta | Kaynak başına 2 rezervasyon / 10 kredi |

W21 ve W22 turları **temiz** döndü (7/7 ve 7/7 engellendi) — bu WO onlara dokunmuyor.

## PM kararları

### 1. Bütünlük **şemada** olacak, çağıranın disiplininde değil

Üçünün de çözümü aynı ilkeden çıkmalı: **defterin değişmezleri veritabanı tarafından zorlanmalı**, çünkü yarın yazacak kodu bugün yazmıyoruz. Uygulama biçimi senin, ama şu şart: **düzeltmeden sonra ham SQL ile aynı saldırıyı tekrarlamak mümkün olmamalı.**

Yol gösterici (seçim senin, gerekçesini yaz):
- **F2 için:** negatif bakiyeyi trigger'ın *okuma* yaparak koruması yapısal olarak yetersiz — commit edilmemiş satırı göremez. Seçenekler: her defter yazısını `business_id` üzerinde serileştiren bir kilit (trigger içinde `pg_advisory_xact_lock`, ya da her tenant için bir "ledger anchor" satırının `FOR UPDATE` kilidi), veya defter yazan transaction'ların `SERIALIZABLE` koşması. **Bakiyeyi sütunda saklama** — W20'nin append-only kararı duruyor ve doğru; anchor satırı bakiye *tutmaz*, yalnızca kilit noktasıdır.
- **F1 için:** rezervasyon başına iade **tekil** olmalı — kısmi unique index (`WHERE entry_type='refund'`) veya eşdeğeri. Kısmi iade ihtimali varsa (bugün yok) tekillik "toplam iade ≤ rezerve edilen" biçiminde ifade edilmeli, ama **bugün gerekmiyorsa yazma** — basit tekillik yeter, gerekçesini yaz.
- **F3 için:** `reserve` kaynak başına tekilleştirsin (aktif rezervasyon varken ikincisi açılmasın). Kısmi unique index + servis tarafında dokümante hata.

### 2. Performans bedeli ölçülecek, tahmin edilmeyecek

Kilit veya izolasyon değişikliği yazma yolunu yavaşlatır. **Ölç:** düzeltme öncesi/sonrası, tek tenant'a paralel N rezervasyon (N=1, 10, 50) ve karışık tenant yükü. W18/W19'un ölçüm disiplinini izle — sayı ver, "muhtemelen hızlı" deme. Kabul edilemez bir yavaşlama çıkarsa dur ve bildir.

### 3. Mevcut davranış **değişmeyecek**

Servis yolunun bugünkü doğru davranışı (advisory lock, kanonik idempotency, karar tabloları) aynen kalır. Bu WO **koruma ekler**, mantık değiştirmez. 1459 testin hepsi dokunulmadan geçmeli.

### 4. Bu bir "ham SQL'e karşı savunma" değil, **gelecekteki kodumuza karşı**

Raporunda bunu doğru çerçevele: kimse veritabanımıza ham SQL atmıyor. Koruduğumuz şey, Phase 3'ün store webhook'u ve Phase 5'in reklam muhasebesi yazılırken **defterin kendisinin hayır diyebilmesi**.

## Kapsam dışı (dokunma)

- W21/W22 (turları temiz), QC, senaryo dedektörü, planlayıcı mantığı.
- Store/ödeme, plan eşleme → Phase 3. Puan tablosu kalibrasyonu → W08 benchmark'ı sonrası.
- Bakiyeyi sütunda saklamak → **yasak** (W20'nin append-only kararı, ADR-017).
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/modules/entitlement/{models,repository,service,ledger}.py
services/api/migrations/versions/0020_*.py            (SLOT SENDE — kısıtlar/index'ler/kilit yapısı)
services/api/app/modules/entitlement/CLAUDE.md        (yeni değişmezler)
services/api/tests/unit/ + tests/integration/
docs/architecture/entitlement.md · error-handling.md
```

## Kabul kriterleri

1. Migration `0020` up → down → up; tek head. **Mevcut veriyle uyumlu:** düzeltmeden önce yazılmış defter satırları migration'ı düşürmüyor (varsa çakışan satırlar için davranışı raporda yaz).
2. **F2 kapandı:** iki eşzamanlı ham `consume` transaction'ı bariyerde commit edilmeye çalışıldığında toplam **negatife düşmüyor** — biri başarısız oluyor. Test doğrulama turunun kurduğu düzeneğin aynısını kurar (gerçek paralel transaction, bariyer, ham SQL).
3. **F1 kapandı:** aynı rezervasyona ikinci `refund` ham SQL ile **yazılamıyor**; kanonik replay hâlâ doğru davranıyor (yeni satır yazmıyor, hata da vermiyor).
4. **F3 kapandı:** aynı `(business_id, source_type, source_id)` için ikinci `reserve` reddediliyor; dokümante hata; mevcut rezervasyon iade edildikten sonra yeni rezervasyon açılabiliyor (aksi hâlde iptal edilen proje yeniden başlatılamazdı — testle).
5. **Ölçüm tablosu:** öncesi/sonrası yazma gecikmesi, N=1/10/50 paralel rezervasyon, tek tenant ve karışık tenant.
6. **Regresyon:** 1459 testin tamamı geçiyor, hiçbiri düzenlenmeden; `make verify` yeşil.
7. **Kendi düzeltmene saldır:** kilit/kısıt eklendikten sonra aynı üç açığı başka yollardan tekrar dene (farklı entry_type sıraları, `COPY`, tek transaction'da çok satır, iç içe savepoint, farklı izolasyon seviyeleri) ve tabloyu rapora yaz.
8. **Grant girdi doğrulaması** (doğrulama turunda tamamlanamayan tek ucuz kalem — buraya alındı): `grant` ucu negatif miktarı, sıfırı, ondalıklı sayıyı ve taşacak kadar büyük değeri reddediyor; `editor`/`approver`/`viewer` rollerinin üçü de `403` alıyor (owner dışı rol matrisi tam). Testli.
9. Rapor + araç zinciri sürümleri. **Merge etme, dalda bırak.**

## ADR numara kuralı

Bu karar ADR-017'ye (append-only defter) **ek** olarak yazılmalı: "bütünlük çağıranda değil şemada". Numarayı PM verir; sen `ADR-018` yaz.

## Rapor — 2026-08-03 · yürüten oturum (Opus 5 / high)

**Dal:** `fix/ledger-integrity` (base `main` = `0a383e4`) · **Migration:** `0020_ledger_integrity`
· **Durum:** tamamlandı, dalda · **merge edilmedi**

### Yapılanlar

**Muhafız trigger'ı yeniden yazıldı** (`trg_credit_ledger_non_negative` →
`trg_credit_ledger_insert_guard`). Bir *tahsilat* satırında sırayla: (1)
`pg_advisory_xact_lock(20020, hashtext(business_id))` — `lock_tenant`'ın aldığı kilidin
**aynısı**, (2) tenant'ın anchor satırını damgalar, (3) toplamı hesaplar ve negatifse reddeder.
Grant ve iade üçünü de atlar: kredi ekleyen satır bakiyeyi negatife düşüremez.

**`entitlement_ledger_anchors` eklendi** — tenant başına bir satır, **hiçbir şey tutmuyor**
(`last_write_at` okunmuyor; bakiye değil, sayaç değil, ADR-017 duruyor). Gerekçesi PM kararı 1'in
"anchor satırı yalnızca kilit noktasıdır" seçeneği, ama nedeni tahmin ettiğimden başka çıktı —
aşağıda "kendi düzeltmeme saldırı"da.

**Kısıtlar:** `uq_credit_ledger_reservation_entry` (rezervasyon başına her tipten bir satır,
kısmi), `ck_credit_ledger_refund_reserved` (iade bir rezervasyon adlandırır — kısmi index'in NULL
deliğini kapatır), `uq_usage_reservations_standing_source` (iş birimi başına ayakta bir hak,
`WHERE status <> 'released'`). Trigger ayrıca iadenin **tutarının** rezervasyonun tuttuğuna eşit
olmasını ve defter satırının **kendi tenant'ının** rezervasyonunu adlandırmasını istiyor.

**Servis:** `reserve` artık ayakta hak varken `409 ENTITLEMENT_SOURCE_ALREADY_RESERVED` veriyor
(tekrar yolu değişmedi: aynı anahtar → var olan hak). `reservation_for_source` ayakta olanı
seçiyor (`reserved` > `consumed` > `released`) — iade sonrası yeniden açılan hak varken
sonuçlandırmanın iade edilmişi bulması yanlış olurdu. Süpürücü kilit sırasına uyduruldu: adayları
kilitsiz okuyor, sonra tenant tenant ve sabit sırayla önce tenant kilidini sonra satırları
alıyor.

**ADR-018** yazıldı (`docs/adr/ADR-018-ledger-integrity-in-the-schema.md`), ADR-017'ye ek.

### Kendi düzeltmeme saldırı — ilk uygulama yetersizdi

Kabul kriteri 7'nin bulduğu en önemli şey benim kendi ilk çözümümdü. **Yalnızca kilit yetmiyor.**
`REPEATABLE READ` bir yazar snapshot'ını `INSERT` başlarken alır — trigger kilidi istemeden
*önce*. Kuyrukta usulca bekler, kilidi alır, sonra kazananı **hâlâ içermeyen** bir küme toplar ve
bakiye yine `-5` olur. Testi yazdım, kırmızı döndü, tasarımı değiştirdim: kilidi beklemek
snapshot'ı ilerletmiyor, ama *kazananın güncellediği bir satırı güncellemek* her izolasyon
seviyesinin gördüğü çakışma. Anchor satırı bunun için var. Karışık seviyeler için de geçerli:
`SERIALIZABLE`'ın tespiti yalnızca bütün katılımcılar serializable olduğunda çalışıyor, anchor
ise karşı taraf ne olursa olsun çalışıyor.

| Saldırı | Sonuç | Kanıt |
|---|---|---|
| İki eşzamanlı ham `consume -5` (bariyer, gerçek paralel transaction) | Engellendi | 1 commit + 1 `negative`; bakiye `0`. Test: `test_two_concurrent_raw_charges_cannot_drive_the_balance_below_zero` |
| Aynısı 8 yazar / 3 kredilik bakiye | Engellendi | tam 3 commit, 5 ret, bakiye `0` |
| Aynısı `REPEATABLE READ` ve `SERIALIZABLE` ile | Engellendi (**ilk uygulamada geçiyordu**) | her ikisinde tam 1 commit; RR'de `40001`, RC'de `negative` |
| Tek transaction'da çoklu satır + savepoint geri alma | Engellendi | ikinci satır `negative`; savepoint geri alındıktan sonra kredi geri geliyor, sonraki aşırı tahsilat yine `negative` |
| `COPY` ile iki tahsilat | Engellendi | `CheckViolationError: ... would go negative`; COPY satır trigger'larını tetikliyor |
| `COPY` ile ikinci ayakta hak + iki iade | Engellendi | ayakta hak `UniqueViolationError`; iade 1 kabul, iade 2 `UniqueViolationError` |
| İadeyi tahsilattan **önce** yazıp sonra ikişer tane deneme | Engellendi | her tipten bir satır geçti, ikinci iade reddedildi; sıra fark etmiyor |
| Uydurulmuş anahtarla ikinci iade | Engellendi | `uq_credit_ledger_reservation_entry`; kanonik replay hâlâ satır yazmıyor ve hata vermiyor |
| Rezervasyonun tuttuğundan büyük iade | Engellendi | `must return the 5 credits reservation ... holds` |
| Rezervasyonsuz iade | Engellendi | `ck_credit_ledger_refund_reserved` |
| Başka tenant'ın rezervasyonunu gösteren defter satırı | Engellendi | `names a reservation of another business` |
| Aynı kaynağa ikinci `reserve` (yeni anahtar) | Engellendi | `409 ENTITLEMENT_SOURCE_ALREADY_RESERVED`; ham INSERT `uq_usage_reservations_standing_source` |
| İade edilmiş hakkın kaynağını yeniden rezerve etme | **Kasıtlı olarak serbest** | yeni hak açılıyor, sonuçlandırma ayakta olanı buluyor (iptal → yeniden başlatma) |
| Süpürücü ile sonuçlandırma aynı hakka, 10 tur | Engellendi, **kilitlenme yok** | 10/10 turda `refund` sayısı `1`; deadlock `0` |
| 2 s süren transaction defteri tutuyor | Yalnızca kendi tenant'ını bloke ediyor | aynı tenant `1908 ms` bekledi, başka tenant `55 ms` |
| **Superuser trigger'ı kapatıyor** | **Geçti — bilinen sınır** | `SET LOCAL session_replication_role = replica` sonrası `-500`'lük tahsilat commit oldu (bakiye `-495`). Aynı oturumda `ALTER TABLE ... DISABLE TRIGGER` denemesi **unique index'e** takıldı: kısıtlar bundan etkilenmiyor |

Son satır dürüstlük gereği burada: satırlar arası bir toplamın kısıt biçimi yok, dolayısıyla
negatif bakiye koruması zorunlu olarak bir trigger ve superuser onu kapatabilir. Kısıtlar ve
unique index'ler kapanmıyor. **Uygulamanın veritabanı rolü üretimde superuser olmamalı** — dev
compose'da bugün superuser (`socialpilot`), yani bu bir dağıtım kalemi. PM'e bırakıyorum.

### Ölçüm (kabul kriteri 5)

Aynı harness, aynı konteyner, aynı oturum. Öncesi için dal `git stash` ile geri alındı ve şema
`0019`'a düşürüldü; sonrası için geri yüklendi. Her konfigürasyon 12 tur, ilk 2 tur atıldı.
`EntitlementService.reserve`, çağrı başına kendi session'ı ve kendi transaction'ı, her çağrı
farklı `source_id`.

| Şekil | N | p50 önce → sonra | p95 önce → sonra | parti (medyan) önce → sonra |
|---|---:|---|---|---|
| tek tenant | 1 | 3,87 → **4,57 ms** (+18%) | 4,43 → 4,90 ms | 3,91 → 4,60 ms |
| tek tenant | 10 | 23,74 → **26,83 ms** (+13%) | 40,08 → 46,14 ms | 41,48 → 47,75 ms |
| tek tenant | 50 | 118,14 → **133,79 ms** (+13%) | 218,17 → 241,73 ms | 206,70 → 255,42 ms (+24%) |
| karışık tenant | 1 | 4,03 → **4,37 ms** (+8%) | 4,29 → 4,79 ms | 4,06 → 4,40 ms |
| karışık tenant | 10 | 19,12 → **22,85 ms** (+20%) | 20,42 → 25,81 ms | 20,64 → 24,49 ms |
| karışık tenant | 50 | 95,39 → **121,19 ms** (+27%) | 127,50 → 163,58 ms | 100,81 → 126,02 ms (+25%) |

Yani rezervasyon başına **~0,5–0,8 ms**, göreli olarak %8–27. Kabul edilebilir bulundu:
rezervasyon proje açılışında bir kez yazılıyor, insan hızında.

Ayrıca **birikimli bir yavaşlama olmadığı** ayrıca ölçüldü — anchor satırı sık güncellenen tek
bir satır, dolayısıyla ölü tuple birikimi sorulacak ilk şeydi. Tek tenant'a 20 tur × 10 paralel
rezervasyon: `[393, 54, 50, 48, 49, 54, 49, 47, 46, 47, 54, 50, 46, 45, 46, 45, 43, 43, 44, 47]`
ms — ilk tur soğuk, kalan 19 tur düz ve sonu başından hızlı. Tabloya `fillfactor = 70` verildi ki
bu güncellemeler HOT kalsın.

### Kapsam dışı bıraktıklarım ve nedeni

- **Kısmi iade / "toplam iade ≤ rezerve edilen"** — PM kararı 1 açıkça "bugün gerekmiyorsa yazma"
  dedi ve yazmadım. Onun yerine dejenere hâli var (eşitlik), çünkü tekillik iade *sayısını*
  sınırlıyor ve miktarı sınırlayan başka bir şey yoktu; tek satırlık bir kısıt, kısmi iade geldiği
  gün tek satır değişir. Bunu PM kararının ihlali değil, ayrı bir eksen olarak okudum.
- **`consume` tutarının rezervasyona eşit olması** yazılmadı. Yazılabilirdi ama mevcut bir testi
  düzenlemek gerekirdi (`test_a_charge_cannot_exist_without_a_version_or_a_reservation` geçerli
  bir rezervasyonla `consume -1` yazıp `ck_credit_ledger_consume_versioned` bekliyor) ve kabul
  kriteri 6 bunu yasaklıyor. Zaten para yaratmıyor: eksik tahsilat az faturalamadır, fazlası
  negatif bakiye muhafızına takılır, ve rezervasyon başına tek `consume` kuralı zaten var.
- **`docs/index.md`, `docs/adr/README.md`** — iş emri kapsam dışı bıraktı, eklenmedi.
- **ADR-017'nin son sonuç maddesi** ("trigger mekanizma değil yedektir") artık eskimiş: trigger
  mekanizmanın kendisi. ADR-017 dosya listemde olmadığı için **dokunmadım**; ADR-018 bunu açıkça
  yazıyor, düzeltme PM'de.
- **Uygulama rolünün superuser olmaması** bir compose/dağıtım değişikliği; dosya listemde yok.

### Doğrulama

Araç zinciri: Python 3.13.14 · mypy 2.3.0 · ruff 0.16.0 · pytest 9.1.1 · PostgreSQL 16.14
(konteyner) · gerçek MinIO + FFmpeg · `COMPOSE_PROJECT_NAME=sp-w23`, portlar ayrı (55523/56523/
59023/8023), başka projenin konteynerine dokunulmadı.

| Kontrol | Sonuç |
|---|---|
| `ruff check` + `ruff format --check` | ✅ 234 dosya |
| `mypy .` (strict) | ✅ `no issues found in 220 source files` |
| migration `0020` up → down → up, tek head | ✅ ayrıca `downgrade base` → `upgrade head` (0001→0020) temiz; `0020_ledger_integrity (head)` |
| OpenAPI kontratı | ✅ yeniden üretildi, commit'li dosyayla **byte özdeş** (yeni uç yok) |
| **K1** mevcut veriyle uyum | ✅ migration veri **düzeltmez**: her yeni kısıt için ihlal sayan bir guard var ve varsa sayılarla `RuntimeError` atıyor. Defter satırını düzenleyerek "onaran" bir migration, append-only'nin var olma sebebini çiğnerdi. Bugünkü verilerde üç guard da sıfır sayıyor |
| **K2** F2 kapandı | ✅ yukarıdaki saldırı tablosunun ilk üç satırı |
| **K3** F1 kapandı | ✅ uydurulmuş anahtarla ikinci iade reddediliyor; kanonik replay hâlâ satır yazmıyor, hata da vermiyor |
| **K4** F3 kapandı | ✅ 409 + index; iade sonrası yeniden rezervasyon açılabiliyor ve sonuçlandırma ayakta olanı buluyor |
| **K5** ölçüm tablosu | ✅ yukarıda |
| **K6** regresyon | ✅ `pytest` (gerçek PostgreSQL + MinIO + FFmpeg, `STORAGE_ADAPTER=s3`): **1474 passed**, 798,77 s. Taban 1459, +15 yeni test; **hiçbir mevcut test düzenlenmedi, silinmedi, atlanmadı** |
| **K7** kendi düzeltmene saldır | ✅ 16 satırlık tablo; biri **kendi ilk uygulamamı düşürdü** |
| **K8** grant girdi doğrulaması | ✅ `0`/`-5`/`5.0` → `400 REQUEST_VALIDATION_FAILED`, tavan üstü → `422 ENTITLEMENT_GRANT_INVALID` (mevcut test); **yeni**: `2**31`/`2**63`/`10**30` → `400`, defterde satır yok. Rol matrisi `admin`/`editor`/`viewer`/`approver` dördü de `403` (mevcut parametrik test) |
| **K9** rapor + sürümler, merge yok | ✅ |

`make verify` hedefi doğrudan açılamadı (API imajında `make` yok — W20/doğrulama turu da aynı
notu düşmüştü). Makefile'ın beş adımı tek tek aynen koşuldu: `ruff check`, `ruff format --check`,
`mypy .`, tam `pytest`, OpenAPI yeniden üretimi + karşılaştırma. Beşi de yeşil.

Yeni test: `tests/integration/test_entitlement.py` +15 (14 fonksiyon, biri iki izolasyon
seviyesiyle parametrik). Değişen mevcut dosya yok.

### Açıkça belirtmem gerekenler

1. **ADR numarası PM'de.** `ADR-018-ledger-integrity-in-the-schema.md` yazıldı, indekse
   eklenmedi. ADR-017'ye **ek**, hiçbir kararını geri almıyor.
2. **ADR-017'nin son sonuç maddesi eskidi** — "negatif bakiye trigger'ı mekanizma değil yedektir"
   artık doğru değil. ADR-017 dosya listemde olmadığı için dokunmadım; tek satırlık düzeltme PM'de.
3. **Uygulamanın veritabanı rolü superuser olmamalı.** Negatif bakiye koruması zorunlu olarak bir
   trigger (satırlar arası toplamın kısıt biçimi yok) ve superuser onu
   `session_replication_role = replica` ile kapatabiliyor — ölçüldü, tabloda. Kısıtlar ve unique
   index'ler kapanmıyor. Dev compose'da rol bugün superuser; bu bir dağıtım kalemi ve kapsam
   dışıydı.
4. **Süpürücünün claim'i iki adıma bölündü.** Davranış aynı (aynı hakları bırakıyor, aynı
   `examined`/`released`/`batch_full` sözleşmesi), mekanizma farklı: eski hâli satır kilidini
   tenant kilidinden **önce** alıyordu, yani modülün kendi `CLAUDE.md`'sinde yazan sıralamanın
   istisnasıydı ve muhafız kilidi aldığı andan itibaren sonuçlandırmayla kilitlenebilirdi.
   10 turluk süpürücü↔sonuçlandırma yarışı ölçüldü: kilitlenme yok, iade sayısı hep 1.
5. **`reservation_for_source` artık ayakta olanı seçiyor** (eskiden en eskiyi). Bugünkü akışta
   kaynak başına tek hak olduğu için hiçbir mevcut davranış değişmiyor; kabul kriteri 4'ün
   "iade sonrası yeniden rezerve edilebilir" senaryosunda ise eski sıralama iade edilmiş hakkı
   bulup yanlış olanı sonuçlandırırdı.
6. **`entitlement_ledger_anchors` şemaya bir tablo daha ekledi ve hiçbir şey tutmuyor.** Bunun
   *neden* böyle olması gerektiği yalnızca ADR-018'te, migration docstring'inde ve
   `models.LedgerAnchor` docstring'inde yazılı — okunmadan silinirse F2 geri gelir, ve testte
   `REPEATABLE READ` satırı bunu yakalar.
7. **Ölçüm ortamı tek makine, konteyner içi.** Mutlak sayılar üretim donanımını temsil etmez;
   karşılaştırma anlamlı çünkü öncesi/sonrası aynı oturumda, aynı konteynerde, aynı harness'la
   koşuldu.

## Doğrulama

_(test eden oturum: **kendi girdilerini üret.** F1/F2/F3'ü yeniden dene; ayrıca yeni kilidin kilitlenme (deadlock) üretip üretmediğini, iki farklı tenant'ın birbirini bloke edip etmediğini, ve uzun süren bir transaction'ın defteri kitleyip kitlemediğini sına)_
