# ADR-017: Append-only kredi defteri, türetilen bakiye, rezervasyon + sonuçlandırma

**Status:** Accepted
**Date:** 2026-08-02
**Karar veren:** PM/mimar oturumu ([W20 iş emri](../handoffs/W20-entitlement-ledger.md) PM
kararları 1–5) · yürüten: W20 (slice 2E ikinci yarı)
**Not:** ADR numarası PM tarafından teyit edilmeli — W20 sıradaki boş numarayı (`017`) kullandı
ve indekse eklemedi (iş emri kapsam dışı bırakıyor).

## Context

W19 içerik projesini uçtan uca yürütüyor ve **hiçbir şey saymıyor**. Bir kullanıcının
tetikleyebileceği render sayısı bugün sınırsız ve her render gerçek para harcıyor (sağlayıcı
çağrıları + CPU). [STATUS K5](../STATUS.md) maliyet sıralamasında AI çağrılarını birinci sıraya
koyuyor; hak muhasebesi olmadan gerçek bir sağlayıcı bağlamak, faturayı ölçüsüz bir kullanıma
açmak demek.

Faturalandırma modeli (K1: mağaza IAP mı, web-first mi) **hâlâ açık** ve Phase 3'ün konusu.
Ama tüketimin sayılması o karara bağlı değil ve sırası tersine çevrilemez: tüketim tarafı doğru
kurulursa kaynak tarafı sonradan tek bir grant yazıcısı olarak takılır, oysa önce ödeme alıp
sonra saymaya başlamak sayılmamış tüketimi kalıcı ve rekonstrüksiyonu imkânsız bir borca çevirir.

PRD tarafında üç metin var ve ikisi birbirini tamamlıyor, biri bu ADR'ın ayrıştığı yer:

- **§12.7** hak yaşam döngüsünü çiziyor: `AVAILABLE → RESERVED → CONSUMED | RELEASED`.
- **§12.8** tüketme kurallarını sıralıyor: hak kontrol edilir, `usage_reservation` açılır, iş
  tamamlanır, **ön izleme ve kalite kontrol başarıysa tüketilir**, teknik hata varsa bırakılır.
- **§32.4** defterin sütunlarını taslak olarak veriyor ve içinde `balance_after` var.

## Decision

### 1. Defter append-only, bakiye türetilir

`credit_ledger` yalnızca satır ekler. `UPDATE` ve `DELETE` bir trigger tarafından **veritabanı
seviyesinde reddedilir** (`trg_credit_ledger_append_only`) — "append-only" bir konvansiyon
olarak yorum, bir kural olarak özelliktir. Düzeltme her zaman yeni bir satırdır.

Bakiye `SUM(delta_credits)`'tir ve **hiçbir yerde saklanmaz.**

**§32.4'ün `balance_after` sütunu bilinçli olarak uygulanmadı.** Gerekçe: satır başına yürüyen
toplam, yazımların tam sıralı olmasını gerektirir (yani her tüketimin serileşmesini — zaten
aldığımız kilidin ötesinde bir gereklilik), ve girdilerin zaten verdiği bir cevabı saklar.
Sakladığı cevap girdilerle çeliştiği gün hangisinin doğru olduğunu söyleyecek bir şey yoktur;
mutasyona uğrayan bir bakiye alanı ise eşzamanlı iki tüketimde sessizce yanlışa düşer ve hatayı
geriye izlemek imkânsız olur. §32.4'ün asıl talebi olan **"Negatif bakiye oluşmamalıdır"**
kaybedilmedi: `trg_credit_ledger_non_negative` her negatif satırda toplamı yeniden hesaplayıp
reddeder.

Bu ADR bu noktada PRD §32.4'ün taslak sütun listesinden ayrılıyor. Gereksinim metni
**değiştirilmedi** (iş emri kapsam dışı bırakıyor); ayrışma burada kayıtlı ve PM'in metni
güncellemesi bekleniyor.

### 2. Tüketim rezervasyon + sonuçlandırma, ve `consume` satırı **başta** yazılır

Rezervasyon açıldığında iki şey olur: `usage_reservations` satırı (`reserved`) ve `credit_ledger`
`consume` satırı (`−N`). Sonuçlandırma (`consumed`) **satır yazmaz** — tahsilat zaten yapıldı.
İade (`released`) telafi edici bir `refund` satırı (`+N`) yazar.

Alternatif — `consume`'u sonda yazmak — bakiyeyi açık rezervasyonlardan haberdar etmek için
ikinci bir terim gerektirirdi (`bakiye = defter toplamı − açık rezervasyonlar`), yani iki
kaynak. Bu şekilde bakiye **tek bir sütunun toplamıdır** ve açık bir rezervasyon onu zaten
düşürmüştür.

### 3. Kontrol ve rezervasyon **çağıranın transaction'ında**, tenant advisory lock ile

`reserve`/`settle` kendi transaction'ını açmaz. Kontrol ile tutmanın ayrı commit edilmesi, iki
isteğin de kontrolden geçmesi demektir; işi yaratan transaction'ın parçası olması ise "iş var
ama hakkı yok" (ve tersi) anını ifade edilemez kılar.

Yarışı kapatan mekanizma `pg_advisory_xact_lock(namespace, hashtext(business_id))`'tir ve
**bakiye okunmadan önce** alınır. PostgreSQL'in varsayılan izolasyonu bunu yakalamaz: iki
transaction da diğerinin okuduğu satırı değiştirmez, dolayısıyla tespit edilecek bir çakışma
yoktur. `businesses` satırının kilitlenmesi yerine advisory lock seçildi çünkü satır kilidi,
rezervasyon sürdüğü sürece o işletmeye yapılan **alakasız** her yazmayı da bloke ederdi.

### 4. Puan tablosu sürümlü, eski satırlar yeniden yorumlanmaz

PRD §12.4'ün puanları sürümlenmiş bir kayıt (`POINT_TABLES`) ve her sürüm saklanır. Çözümleme
rezervasyon açılırken **bir kez** olur; çözülen kredi ve sürüm hem rezervasyona hem defter
satırına yazılır. Saklanmış bir satırdan krediyi yeniden türeten hiçbir fonksiyon yoktur, bu
yüzden aktif sürümü değiştirmek **yalnızca yeni işi** fiyatlar.

**Sapma notu:** iş emri "puan tablosu **veri**, kod değil" diyor. Tablo bir veritabanı satırı
değil, sürümlenmiş bir Python kaydı olarak duruyor; aktif sürüm ise konfigürasyon
(`ENTITLEMENT_POINTS_VERSION`). Gerekçe: bu slice'ta tabloyu yazacak bir admin yüzeyi yok
(Phase 3), dolayısıyla bir DB tablosu yalnızca migration'la yazılabilirdi — yani fazladan adımı
olan kod. Kararın **amacı** (sürümlü + denetlenebilir + dünkü tahsilatın hangi tabloyla
hesaplandığının bilinmesi) tam olarak karşılanıyor. Admin yüzeyi geldiğinde tablo veritabanına
taşınabilir; defter satırları sürümü zaten taşıdığı için taşıma geriye dönük hiçbir şeyi
değiştirmez.

`PointTable` **import anında** totallik ister: her `ContentPointKind` fiyatlı, her
`ScenarioCode × RenderProfile` çifti eşlenmiş olmak zorunda. Fiyatlanmamış içerik bedava
içeriktir; bu yüzden yeni bir render profili fiyatlanmadan uygulama açılmaz.

### 5. Sonuçlandırma kuralı total bir tablodur ve yalnızca teslimat ücretlendirir

İki boyut, ikisi de kapalı: işin nasıl bittiği (`SourceOutcome`) ve — başarısızsa — hangi
sınıftan bir başarısızlık olduğu (`FailureClass`). Eşlenmemiş bir hata kodu tanımsız bir
kombinasyon değil, `UNCLASSIFIED`'dır; yani cevabı olan bir durumdur.

`REFUND_POLICY`'nin her üyesi bugün **iade eder**. Bu bir yer tutucu değil, §12.7/§12.8'in
kuralı: kredi ön izleme var olduğunda tüketilir. Ön izleme yoksa tahsilat yoktur — müşterinin
kendi medyası sebep olduğunda bile, çünkü kimsenin almadığı bir çıktı faturalandırılamaz. Tablo,
bir sınıfın iade edilmez olduğu gün değişikliğin tek satır olması için var.

`ReservationStatus × SettlementOutcome` de ayrı ve total bir tablodur ve **tekrarı çelişkiden
ayırır**: aynı sonucu ikinci kez uygulamak `ALREADY_APPLIED`'dır (hiçbir şey yazılmaz, çünkü
sonuçlandırma tekrar oynatılabilen bir transaction'ın içindedir), tersini uygulamak
`CONFLICT`'tir. Append-only bir defterde sessiz ikinci iade kalıcıdır.

### 6. Tüketim noktası proje başlatmadır, adım değil

Kullanıcı "bir içerik" satın alıyor, adımlarını değil. Tek rezervasyon senaryoyu, seslendirmeyi,
timeline'ı ve **tüm render denemelerini** kapsar. K4 böylece yapısal olarak sağlanır: projenin
otomatik yeniden render'ı (QC başarısızlığı → `RETRYING`) yeni bir rezervasyon açmaz, çünkü
rezervasyon render'a değil projeye bağlıdır.

### 7. Modül sınırı: `entitlement` sözlük okur, tablo okumaz

`points.py` `content`'in `ScenarioCode`/`RenderProfile` enum'larını import eder — fiyat
listesinin totalliği buna dayanır ve ikinci bir kopya, fiyat gibi görünen bir sapma üretirdi.
Buna karşılık bir proje hakkında sorulan **tek sorgu** `ReservationSourceProbe` protokolünden
geçer ve protokolü `content` uygular. Böylece bağımlılık tek yönlü kalır: `content → entitlement`.

## Consequences

**Kazanılan:**

- Hak muhasebesi gerçek sağlayıcılar bağlanmadan **önce** yerinde; W08 sonrası sağlayıcı
  bağlamak artık ölçüsüz bir fatura riski taşımıyor.
- Yarış testle kanıtlanabilir ve kanıtlandı (gerçek PostgreSQL, gerçek paralel transaction).
- Kaynak tarafı (K1 kararı ne olursa olsun) **tek bir grant yazıcısı** olarak takılıyor; mağaza
  webhook'u da, kurumsal sözleşme de aynı `grant` satırını yazar.
- Defter, publishing (Phase 4) ve reklam (Phase 5) tüketimini `source_type` ile şemasız
  karşılayabiliyor.

**Bedeli:**

- `WAITING_MEDIA`'da park eden bir proje kredisini süresiz tutar ve bu slice'ta **iptal ucu
  yok** — 2F'nin işi. Bugünkü çıkış yolu projenin adım zaman aşımı değil (o durum muaf), süpürme
  de değil (kaynak canlı). Rapor bunu açık olarak bildiriyor.
- Tekil uçlar (proje bağlamı olmayan senaryo/seslendirme/timeline/render) **ücretsiz** kaldı.
  Bilinçli ve geçici; Phase 3 kapatacak.
- §12.7'nin `CONSUMED → REFUNDED` yolu (tüketilmiş bir üretimin sonradan iadesi) uygulanmadı:
  destek/admin yüzeyi yok. `refund` girdi tipi var ve tek üreticisi bugün bırakılan bir
  rezervasyon.
- Negatif bakiye trigger'ı her negatif satırda bir `SUM` koşar. Tenant başına index'li ve küçük
  bir küme; yine de bir maliyet ve mekanizma değil yedek olduğu için kabul edildi.
