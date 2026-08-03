# ADR-018: Defterin bütünlüğü çağıranın disiplininde değil şemada

**Status:** Accepted
**Date:** 2026-08-03
**Karar veren:** PM/mimar oturumu ([W23 iş emri](../handoffs/W23-ledger-integrity.md) PM kararları
1–4) · yürüten: W23
**İlişki:** [ADR-017](ADR-017-entitlement-ledger.md)'ye **ek**. ADR-017'nin hiçbir kararı geri
alınmıyor: defter append-only, bakiye türetiliyor, `balance_after` hâlâ yok. Değişen tek şey, o
kararların kim tarafından korunduğu.
**Not:** ADR numarası PM tarafından teyit edilmeli — W23 `XXX` bıraktı (iş emrinin kuralı) ve
`docs/adr/README.md` indeksine eklemedi (kapsam dışı).

## Context

ADR-017 doğru bir defter kurdu ve bir cümleyi çağırana emanet etti: *"Trigger yalnızca commit
edilmiş satırları görür; kesinliği kilitten gelir."* Kilit `EntitlementService`'te. Yani defterin
bütünlüğü, ona yazan kodun doğru davranmasına bağlıydı.

Bugün o kod doğru davranıyor. Bağımsız doğrulama turu (2026-08-03) bunu ayrıca kanıtladı: gerçek
HTTP yarışı `[201, 402]` ile bitti, kanonik iade replay'i ikinci satır yazmadı, dış rotalar temiz.
Aynı tur, servisten geçmeyen üç yol da buldu:

| # | Bulgu | Ne oluyordu |
|---|---|---|
| W20-F2 | İki eşzamanlı ham `consume -5`, 5 kredilik bakiyeye karşı ikisi de commit edildi | trigger toplamı okuyor; `READ COMMITTED` altında diğerinin commit edilmemiş satırını göremiyor. Türetilen bakiye **`-5`** |
| W20-F1 | Aynı rezervasyona farklı anahtarla ikinci `refund` | `uq_credit_ledger_idempotency` *tekrarı* eliyor, uydurulmuş anahtarı değil. Bakiye `5 → 10`: **para yaratıldı** |
| W20-F3 | Aynı `(business, source_type, source_id)` için ikinci `reserve` | servis kaynak başına tekilleştirmiyordu; kaynak için 2 rezervasyon / 10 kredi |

Üçü de aynı kök nedenin görüntüsü. Ve bu bir "ham SQL saldırısı" senaryosu **değil** — kimse
veritabanımıza ham SQL atmıyor. Korunması gereken şey, Phase 3'ün mağaza webhook'u, Phase 5'in
reklam muhasebesi ve bir gün yazılacak bir bakım script'i: hiçbiri bugünkü disiplini bilmeyecek.

Bu, projenin başka her yerinde uyguladığı ilkeyle de çelişiyordu. QC'de bir kontrolü atlamak
*ifade edilemez*; W22'de planlama para harcayamaz, çünkü sınıf imzası buna izin vermiyor. Defter
para tutuyor ve en zayıf korunan yerdi.

## Decision

### 1. Değişmezler veritabanı tarafından zorlanır

Uygulama katmanındaki hiçbir davranış değişmedi. Advisory lock, kanonik idempotency anahtarları,
total karar tabloları aynen duruyor ve aynı sonuçları veriyor. Eklenen tek şey, aynı kuralların
şemada ikinci kez — ve bu sefer bağlayıcı olarak — yazılması.

### 2. F2: kilit **ve** anchor. Kilit tek başına yetmiyor

İlk uygulama yalnızca kilitti: `credit_ledger`'ın insert trigger'ı, toplamı hesaplamadan önce
`pg_advisory_xact_lock(20020, hashtext(business_id))` alıyor — **uygulamanın aldığı kilidin
aynısı**. Servis yolunda bedelsiz (advisory lock transaction içinde yeniden girilebilir), kilidi
hiç duymamış bir yazar için gerçek bir bariyer: ikinci yazar, birincisi commit edene kadar
toplamını çalıştıramıyor.

Bu, kendi düzeltmemize saldırırken yetersiz çıktı. **Kilidi beklemek snapshot'ı ilerletmez.**
`REPEATABLE READ` bir yazar snapshot'ını `INSERT` başlarken — yani trigger kilidi istemeden *önce*
— alıyor; kuyrukta usulca bekliyor, sonra kazananı hâlâ içermeyen bir küme topluyor ve bakiye
yine `-5` oluyor. Karışık izolasyon seviyeleri için de aynısı geçerli: `SERIALIZABLE`'ın çakışma
tespiti yalnızca *bütün* katılımcılar serializable olduğunda çalışıyor.

Bu yüzden `entitlement_ledger_anchors` var: tenant başına bir satır ve **hiçbir şey tutmuyor**.
Her tahsilat o satırı damgalıyor. Damgalamak sıradan bir satır güncellemesi, ve kazananın
güncellediği bir satırı güncellemek her izolasyon seviyesinin gördüğü **tek** çakışma —
`READ COMMITTED` bekleyip yeniden okuyor, katı seviyeler `40001` ile düşüyor. Bütünlük artık
gelecekteki bir yazarın hangi izolasyon seviyesini seçtiğine bağlı değil.

Anchor **bakiye tutmuyor ve tutmayacak.** `last_write_at` hiçbir yerde okunmuyor, bir sayaç
değil, bir toplam değil. ADR-017'nin "bakiye yalnızca girdilerde" kararı bu ADR'ın da temel
varsayımı; girdilerin yanında ikinci bir doğruluk kaynağı, o kararın var olma sebebi.

**Yalnızca tahsilatlar bu iki adımı ödüyor.** Grant ve iade atlıyor: kredi *ekleyen* bir satır
bakiyeyi negatife düşüremez, ve eşzamanlı bir tahsilatın onu henüz görmemesi zaten güvenli
tarafta kalmak demek.

### 3. F1: rezervasyon başına her tipten bir satır, ve iade tam olarak tutulanı geri verir

`uq_credit_ledger_reservation_entry` — kısmi unique index, `(reservation_id, entry_type)`.
`uq_credit_ledger_idempotency` bir *tekrarı* eliyordu; bu, uydurulmuş bir anahtarla yazılan aynı
iadeyi eliyor. `ck_credit_ledger_refund_reserved` de aynı kuralın parçası: rezervasyonsuz bir
iade hem açıklanamaz hem de kısmi index'in NULL deliği olurdu.

İş emri "kısmi iade ihtimali yoksa toplam-formunu **yazma**" dedi ve yazılmadı. Onun yerine
dejenere hâli var: trigger, iadenin tutarının rezervasyonun tuttuğu tutara **eşit** olmasını
istiyor. Tekillik iade *sayısını* sınırlar; miktarı sınırlayan budur, ve ikisi olmadan "para
yaratılamaz" cümlesi yarım kalıyordu. Kısmi iade geldiği gün bu tek satır değişir.

### 4. F3: iş birimi başına **ayakta** bir hak

`uq_usage_reservations_standing_source` — kısmi unique index,
`(business_id, source_type, source_id) WHERE status <> 'released'`.

`released` ayakta değildir: iade edilmiş bir hak yerini boşaltır, yoksa iptal edilen bir proje bir
daha başlatılamazdı (2F'nin iptal ucu bunu gerektiriyor). `consumed` ayaktadır: bu, K4'ün "saf
yeniden render yeni hak tüketmez" kuralının çağırana bırakılmak yerine şemaya yazılmış hâli.

Servis tarafında dokümante karşılığı `ENTITLEMENT_SOURCE_ALREADY_RESERVED` (409). Tekrar değil:
tekrar aynı idempotency anahtarını taşır ve zaten açılmış hakla cevaplanır.

### 5. Kilit sırası istisnasız: önce tenant, sonra satır

Trigger de tenant kilidini aldığı için, kural artık bağlayıcı. Süpürücü tek istisnaydı — adayları
`FOR UPDATE SKIP LOCKED` ile kilitleyip *sonra* iade yazıyordu, yani sırayı sonuçlandırmanın tam
tersine alıyordu. Şimdi adaylarını kilitsiz okuyor, sonra tenant tenant ve **sabit sırayla** önce
kilidi sonra satırları alıyor. İki süpürücü de aynı sırayla ilerlediği için birbirleriyle de
çevrim kuramıyorlar.

## Consequences

**Kazanılan:**

- Üç bulgu da kapandı ve kapanışları testte: ham SQL ile, gerçek paralel transaction'la,
  bariyerle. Aynı saldırılar `COPY` ile, tek transaction içinde çoklu satırla, savepoint'le ve
  `REPEATABLE READ`/`SERIALIZABLE` ile tekrarlandı.
- Tenant izolasyonu bir sorgu özelliği olmaktan çıkıp tablo özelliği oldu: bir defter satırı başka
  bir tenant'ın rezervasyonunu gösteremiyor.
- Gelecekteki yazar için maliyet sıfır: doğru davranan kod hiçbir şey fark etmiyor, yanlış
  davranan kod **yazamıyor**.

**Bedeli:**

- Yazma yolu yavaşladı. Tenant başına ölçüldü (W23 raporu, tablo): tek rezervasyonda p50
  `3,87 ms → 4,57 ms`; tek tenant'a 50 paralel rezervasyonda parti süresi `207 ms → 255 ms`;
  karışık tenant 50'de `101 ms → 126 ms`. Yani rezervasyon başına ~0,5–0,8 ms, göreli olarak
  %13–27. Rezervasyon proje açılışında bir kez yazıldığı için kabul edildi.
- Şemaya bir tablo daha girdi ve hiçbir şey tutmuyor. "Neden bu tablo var" sorusunun cevabı
  yalnızca burada ve `models.LedgerAnchor`'ın docstring'inde yazılı.
- **Muhafız bir trigger.** `session_replication_role = replica` diyebilen bir **superuser**
  negatif bakiye korumasını kapatabilir; ölçüldü ve rapora yazıldı. Kısıtlar ve unique index'ler
  bundan etkilenmiyor (aynı denemede tetiklendiler). Satırlar arası bir toplamın kısıt biçimi
  yok, dolayısıyla bu doğal bir sınır — cevabı uygulamanın veritabanı rolünün superuser
  olmamasıdır ve bu bugün **sağlanmıyor** (dev compose'da `socialpilot` superuser).
- ADR-017'nin son sonuç maddesi ("negatif bakiye trigger'ı ... mekanizma değil yedek olduğu için
  kabul edildi") artık **eskimiştir**: trigger yedek değil mekanizmanın kendisi. ADR-017 bu iş
  emrinin dosya listesinde olmadığı için düzeltilmedi; PM'e bırakıldı.
