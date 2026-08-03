# W23 — Defter bütünlüğü: yazar disiplinini şemaya taşı

**Dal:** `fix/ledger-integrity` · **Base:** `main` · **Migration slotu: SENDE** (`0020`)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Neden bu iş:** Bağımsız doğrulama turu (2026-08-03) W20'de üç açık buldu ve üçü de aynı kök nedene çıkıyor: **defterin bütünlüğü, yazan kodun doğru davranmasına bırakılmış.** Bugün doğru davranıyor — `EntitlementService` advisory lock alıyor, idempotency anahtarı kanonik, dış rotalar temiz. Ama **defterin kendisi** korumuyor, ve bu bir zaman bombası: Phase 3'te mağaza webhook'u grant yazacak, Phase 5'te reklam harcaması yazacak, bir gün bir bakım script'i düzeltme satırı yazacak. O yazarların hiçbiri bugünkü disiplini bilmiyor olacak.

Bu, projenin başka her yerinde uyguladığımız ilkeyle çelişiyor: QC'de "bir kontrolü atlamak *ifade edilemez*", W22'de "planlama para harcayamaz" sınıf imzasıyla korunuyor. Defter para tutuyor ve en zayıf korunan yer o.

## Bulgular (doğrulama turundan, tam kayıt W20 dosyasında)

| # | Bulgu | Şiddet | Kanıt |
|---|---|---|---|
| **W20-F2** | **Eşzamanlı ham defter yazıları negatif bakiye trigger'ını aşıyor.** İki ayrı gerçek transaction bariyerde eşzamanlı `consume -5` yazıp commit etti; türetilen bakiye **`-5`**. Trigger diğer transaction'ın commit edilmemiş satırını göremiyor (READ COMMITTED'ın doğal sonucu). | **Yüksek** | Servis yolu güvenli kaldı (bir başarı + bir `402`); açık ham yazar sınırında |
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
8. Rapor + araç zinciri sürümleri. **Merge etme, dalda bırak.**

## ADR numara kuralı

Bu karar ADR-017'ye (append-only defter) **ek** olarak yazılmalı: "bütünlük çağıranda değil şemada". Numarayı PM verir; sen `ADR-XXX` yaz.

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum: **kendi girdilerini üret.** F1/F2/F3'ü yeniden dene; ayrıca yeni kilidin kilitlenme (deadlock) üretip üretmediğini, iki farklı tenant'ın birbirini bloke edip etmediğini, ve uzun süren bir transaction'ın defteri kitleyip kitlemediğini sına)_
