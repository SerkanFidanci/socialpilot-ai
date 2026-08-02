# entitlement — kredi defteri ve hak tüketimi

**Sahibi:** append-only kredi defteri (`credit_ledger`), rezervasyon yaşam döngüsü
(`usage_reservations`, PRD §12.7/§12.8), PRD §12.4'ün **sürümlenmiş** puan tablosu, bakiye
türetimi ve eşzamanlılık kilidi, manuel grant (§32.1).
**Sahibi değil:** mağaza/IAP doğrulaması, abonelik, yenileme, plan eşlemesi, fiyatlandırma
(→ **Phase 3**, K1 kararı beklemede), tüketimi *tetikleyen* iş kuralları (→ `../content/`),
revizyon kotası (→ 2F), HTTP taşıma (→ `../../api/routes/entitlement.py`).

## Değişmezler

- **Defter append-only'dir ve bunu veritabanı söyler.** `0017` `credit_ledger` üzerine `UPDATE`
  ve `DELETE`'i reddeden bir trigger kurar. Düzeltme = yeni satır. Bir defter satırını
  düzenlemek, kaydı olmayan bir bakiye değişikliğidir — defterin var olma sebebi tam olarak
  bunun olmamasıdır. `TRUNCATE` satır trigger'ı tetiklemez, bu yüzden test temizliği etkilenmez.
- **Bakiye saklanmaz, `SUM(delta_credits)` ile türetilir.** Hiçbir yerde `balance` sütunu yoktur
  ve PRD §32.4'ün `balance_after` taslağı **bilinçli olarak uygulanmadı** (ADR-017). Satır başına
  yürüyen toplam, yazımların tam sıralı olmasını gerektirir ve girdilerin zaten verdiği cevabı
  saklar; girdilerle çeliştiği gün hangisinin doğru olduğu anlaşılamaz.
- **Açık rezervasyon bakiyeyi *şimdiden* düşürmüştür.** `consume` satırı rezervasyon
  **açılırken** yazılır, iş biterken değil. Çift harcamayı engelleyen şey budur: ikinci isteğin
  topladığı küme birincinin satırını zaten içerir. Sonuçlandırma satır yazmaz; iade
  telafi edici bir `refund` satırıdır.
- **Kontrol ve rezervasyon çağıranın transaction'ındadır.** `reserve`/`settle` **kendi
  transaction'ını açmaz**. Kontrolün ayrı commit edilmesi, iki isteğin de kontrolden geçmesi
  demektir; iş ile hakkın aynı transaction'da olması "işi var ama hakkı yok" anını
  ifade edilemez kılar.
- **Sıralama: önce tenant kilidi, sonra satır kilidi.** `lock_tenant` transaction ömürlü bir
  advisory lock'tur ve **bakiye okunmadan önce** alınır. Sonra alınırsa hiçbir şeyi kilitlemez.
  Aynı sıra hem `reserve` hem `settle` içindedir, bu yüzden ikisi birbirini bekleyemez.
  PostgreSQL varsayılan izolasyonu bunu yakalamaz: iki transaction da diğerinin okuduğu satırı
  değiştirmiyor, tespit edilecek çakışma yok.
- **Negatif bakiye veritabanında da imkânsız.** `trg_credit_ledger_non_negative` mekanizma değil
  **yedektir**: kilidi unutan bir kod yolunu pahalı değil ölümcül yapar. Trigger yalnızca commit
  edilmiş satırları görür; kesinliği kilitten gelir.
- **İşaret tipin özelliğidir.** `signed_credits` tek dönüşüm noktasıdır ve `ck_credit_ledger_delta_sign`
  aynı kuralı şemada tekrar eder. Ters işaretli satır **eklenemez**; bakiye tek bir ifadedir,
  ikinci bir `CASE` yazılamaz.
- **Her `consume` sürümünü ve rezervasyonunu adlandırır** (`ck_credit_ledger_consume_versioned`,
  `ck_credit_ledger_consume_reserved`). Sürümsüz bir tahsilat sonradan açıklanamaz; rezervasyonsuz
  bir tahsilat serbest bırakılamaz.
- **Puan tablosu sürümlüdür ve eski satırlar yeniden yorumlanmaz.** Çözümleme rezervasyon
  açılırken bir kez olur; sonuç satıra yazılır. `points_table_version` tarihe düşülmüş bir
  etikettir, sonraki bir hesabın **girdisi değildir** — saklanmış bir satırdan krediyi yeniden
  türeten hiçbir fonksiyon yoktur.
- **`PointTable` eksikse var olamaz.** Yapıcı her `ContentPointKind`'ı ve
  `ScenarioCode × RenderProfile` çarpımının tamamını ister; kontrol **import anında** koşar. Yeni
  bir render profili fiyatlanmadan uygulama açılmaz — fiyatlanmamış içerik *bedava* içeriktir.
- **Karar tabloları total, tanımsız kombinasyon yok.** `settlement_outcome` (`SourceOutcome` ×
  hata kodu) ve `resolve_settlement` (`ReservationStatus` × `SettlementOutcome`) kapalı çarpımın
  tamamını kapsar. **Tekrar ile çelişki ayrı cevaplardır:** aynı sonucu ikinci kez uygulamak
  `ALREADY_APPLIED` (hiçbir şey yazılmaz), tersini uygulamak `CONFLICT`. Append-only bir defterde
  sessiz ikinci iade kalıcıdır.
- **Yalnızca teslim edilmiş iş ücretlendirilir** (§12.7, §12.8: "ön izleme başarıyla hazır").
  `FailureClass`'ın her üyesi bugün iade eder — müşterinin kendi medyası sebep olsa bile, çünkü
  kimsenin almadığı çıktı faturalandırılamaz. Tablo, bir sınıf iade edilmez olduğu gün
  değişikliğin bir satır olması için var.
- **Modül `content`'in *sözlüğünü* okur, tablolarını değil.** `points.py` `ScenarioCode` ve
  `RenderProfile` enum'larını import eder (fiyat listesinin totalliği buna dayanır); ikinci bir
  kopya tutmak, fiyat gibi görünen bir sapma üretirdi. Proje hakkında sorulan **tek sorgu**
  `ReservationSourceProbe` üzerinden gider — bağımlılık tek yönlü kalır, çünkü `content` zaten
  `entitlement`'ı çağırıyor.
- **Krediyi yaratmak yalnızca `owner`'ındır** (`Permission.ENTITLEMENT_GRANT`). Harcamak için
  ayrı bir yetki **yoktur ve olmayacak**: harcama, ihtiyacı olan işlemin kendi yetkisiyle onun
  transaction'ında olur. Etkisi yalnızca "krediyi düşür" olan bir uç, aynı tabloya giden ikinci
  ve daha zayıf bir yol olurdu.
- **Her rezervasyonun bir sahibi vardır** (`requested_by_user_id` NOT NULL). `audit_logs` aktör
  ister; arka plan süpürücüsünün yazdığı iade bile kredisi hareket eden kişiyi adlandırır.

## Tüketim noktası (bu slice)

**Proje başlatma** — paket olarak, `(scenario_code, profile)` çiftinden çözülen puanla. Tekil
uçlar (proje bağlamı olmadan senaryo üretimi, seslendirme, timeline, tekil render isteği) bugün
**ücretsizdir**; bu bilinçli ve geçici bir geliştirici/entegrasyon yüzeyidir, Phase 3 kapatır.
Projenin otomatik yeniden render'ı (QC başarısızlığı → `RETRYING`) **yeni hak tüketmez** (K4):
tek rezervasyon tüm adımları ve tüm render denemelerini kapsar.

## Dosyalar

| Dosya | İş |
|---|---|
| `points.py` | PRD §12.4 puan tablosu **sürümlü veri** olarak: `ContentPointKind` (§12.4 satırları), `PointTable` (import anında totallik zorlaması), `POINT_TABLES` sürüm kaydı, `point_table()` |
| `ledger.py` | Saf yarı: `CreditEntryType`/`ReservationStatus`/`SourceOutcome`/`SettlementOutcome`/`SettlementAction`/`FailureClass`, `ENTRY_SIGNS` + `signed_credits`, `FAILURE_CLASSES` + `REFUND_POLICY` + `classify_failure`, total `settlement_outcome` ve `resolve_settlement`, `SETTLED_STATUS`/`SETTLEMENT_ENTRY`, dokümante hata kodları |
| `models.py` | `UsageReservation` (durum, krediler, sürüm, kaynak, idempotency, sahip, kısıtlar) + `CreditLedgerEntry` (işaretli `delta_credits`, sürüm/rezervasyon kısıtları, kısmi tekil idempotency index'i) + `SOURCE_*` sabitleri |
| `repository.py` | `EntitlementRepository` — tenant-kapsamlı okuma/yazma, `lock_tenant` (advisory lock), `balance` (tek `SUM`), `reserved_credits`, rezervasyon aramaları, cursor sayfalama, süpürücü claim'i (`SKIP LOCKED`) |
| `policy.py` | `EntitlementAction` → merkezî `Permission` eşlemesi (okuma `business.read`, grant `entitlement.grant`) |
| `service.py` | `EntitlementService` — `reserve`/`settle` (çağıranın transaction'ında), `grant` (kendi transaction'ı, idempotent, audit), bakiye/defter/rezervasyon okumaları · `ReservationSourceProbe` protokolü · `AbandonedReservationSweeper` |

## Gereksinim, karar, mimari

- [50-subscription-entitlement.md](../../../../../docs/product/requirements/50-subscription-entitlement.md)
  (§12.4 puan tablosu, §12.7 hak yaşam döngüsü, §12.8 tüketme kuralları, §32.1/§32.4 defter) ·
  [90a-database-design.md](../../../../../docs/product/requirements/90a-database-design.md)
  (§28.6 tablolar, §28.9 index'ler) ·
  [00-vision-principles.md](../../../../../docs/product/requirements/00-vision-principles.md)
  (madde 4: hakkın kaynağı backend defteridir)
- `ADR-017-entitlement-ledger.md` — append-only defter, türetilen bakiye, rezervasyon +
  sonuçlandırma, §32.4'ün `balance_after`'ının neden uygulanmadığı
- Mimari: [entitlement.md](../../../../../docs/architecture/entitlement.md) ·
  [error-handling.md](../../../../../docs/architecture/error-handling.md) (`ENTITLEMENT_*` kataloğu)

## Testler

`tests/unit/test_entitlement_unit.py` · `tests/integration/test_entitlement.py` ·
`tests/integration/test_content_lifecycle.py` (uçtan uca sonuçlandırma ve K4 kanıtı)
