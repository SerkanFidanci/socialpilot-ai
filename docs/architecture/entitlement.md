# Hak muhasebesi: kredi defteri ve tüketim mimarisi

**Kapsam:** append-only kredi defteri, rezervasyon yaşam döngüsü, PRD §12.4'ün sürümlenmiş puan
tablosu, bakiye türetimi, eşzamanlılık kilidi ve tüketimin içerik projesine bağlandığı nokta.
Slice 2E'nin ikinci yarısı (W20) getirdi; **W23 bütünlüğü çağırandan şemaya taşıdı.**
**İlgili:** PRD §12.4, §12.7, §12.8, §32.1, §32.4 →
[50-subscription-entitlement.md](../product/requirements/50-subscription-entitlement.md) ·
[90a-database-design.md](../product/requirements/90a-database-design.md) §28.6/§28.9 ·
`ADR-017-entitlement-ledger.md` ·
[content-render.md](content-render.md) (yaşam döngüsü) ·
[error-handling.md](error-handling.md) (`ENTITLEMENT_*`)

**Kapsam dışı:** mağaza/IAP doğrulaması, abonelik durumu, yenileme, plan eşlemesi ve fiyat →
**Phase 3**. [K1](../STATUS.md) (IAP mı web-first mi) hâlâ kullanıcının açık kararı; bu katman o
karardan bağımsızdır ve onu beklemez.

## Şekil

```
POST /content/projects                    ┌── aynı transaction ──────────────────┐
        │                                 │                                      │
        ▼                                 │  yetki + aktif işletme               │
ContentProjectService.create_project ─────┤  idempotency                         │
                                          │  doğrulanmış girdiler (tenant)       │
                                          │  content_projects satırı             │
                                          │                                      │
                                          │  EntitlementService.reserve:         │
                                          │    pg_advisory_xact_lock(tenant)     │
                                          │    puan = PointTable(sürüm)          │
                                          │            .credits_for(senaryo,     │
                                          │                        profil)       │
                                          │    bakiye = SUM(delta_credits)       │
                                          │    bakiye < puan → 402               │◄── her şey geri alınır
                                          │    usage_reservations  (reserved)    │
                                          │    credit_ledger      (consume −N)   │
                                          └──────────────────────────────────────┘

     ... sequencer projeyi yürütür: senaryo → seslendirme → timeline → render → QC ...
         (her adım kendi idempotency'siyle; render tekrarı YENİ hak tüketmez — K4)

ContentProjectAdvanceService._settle       ┌── aynı transaction ─────────────────┐
   (durum terminal oldu)                   │  geçişler + proje satırı            │
        │                                  │                                     │
        ▼                                  │  EntitlementService.settle:         │
   source_outcome(state)                   │    settlement_outcome(...)          │
     PREVIEW_READY → DELIVERED             │    resolve_settlement(durum, sonuç) │
     FAILED        → ABANDONED             │    consumed → satır yazılmaz        │
     diğerleri     → RUNNING (tut)         │    released → credit_ledger +N      │
                                           └─────────────────────────────────────┘
```

Sonuçlandırma projeyi terminal yapan transaction'ın **içinde**dir. Bu yüzden "bitmiş projenin
hâlâ açık hakkı var" diye bir pencere yoktur ve bir çökme onu yaratamaz: ya iki gerçek de commit
oldu ya hiçbiri.

## Bakiye neden saklanmıyor

`bakiye = SUM(credit_ledger.delta_credits)`, tenant kapsamlı ve `ix_credit_ledger_business_created`
index'i altında. Sistemde `balance` adında bir sütun yoktur ve entegrasyon testi bunu
`information_schema` üzerinden zorlar.

`delta_credits` **işaretli**dir ve işaret `ck_credit_ledger_delta_sign` ile tipine bağlanmıştır
(`grant`/`refund` > 0, `consume`/`expire` < 0). Böylece bakiye tek bir ifadedir; ikinci bir
sorgunun farklı yazabileceği bir `CASE` yoktur.

PRD §32.4'ün taslağındaki `balance_after` **uygulanmadı** — gerekçe ADR-017'de. §32.4'ün asıl
talebi ("Negatif bakiye oluşmamalıdır") kaybolmadı: `trg_credit_ledger_insert_guard` her negatif
satırda toplamı yeniden hesaplar ve reddeder — **ve W23'ten beri bunu eşzamanlı yazarlara karşı
da yapıyor** (aşağıda).

## Açık rezervasyon bakiyeyi şimdiden düşürür

`consume` satırı rezervasyon **açılırken** yazılır. İki sonucu var:

1. Eşzamanlı ikinci isteğin topladığı küme birincinin satırını zaten içerir — çift harcama
   dağıtık bir argüman gerektirmeden kapanır.
2. Sonuçlandırma satır yazmaz (tahsilat zaten oldu); iade telafi edici bir `refund` satırıdır.

`GET /entitlement/balance` iki sayı döner: `balance_credits` (harcanabilir) ve `reserved_credits`
(açık rezervasyonların tuttuğu). İkincisi **çıkarılacak ikinci bir terim değildir** — birinciden
zaten düşmüştür; reddi açıklayabilmek için ayrı raporlanır.

## Yarış nasıl kapatıldı

`pg_advisory_xact_lock(ADVISORY_LOCK_NAMESPACE, hashtext(business_id))`, **bakiye okunmadan
önce**, çağıranın transaction'ında. Commit/rollback ile kendiliğinden bırakılır; temizlenecek
bir yol yoktur.

PostgreSQL'in varsayılan izolasyonu bu yarışı yakalamaz: iki transaction da diğerinin okuduğu
satırı değiştirmez, dolayısıyla tespit edilecek bir çakışma yoktur. `businesses` satırını
kilitlemek yerine advisory lock seçildi çünkü satır kilidi, rezervasyon sürdüğü sürece o
işletmeye yapılan alakasız her yazmayı da bloke ederdi.

Ölçüm yerine kanıt: son krediyi hedefleyen iki eşzamanlı `create_project`'ten tam olarak biri
başarılı oluyor, üç kredilik bakiyeye giden on eşzamanlı istekten tam üçü — gerçek PostgreSQL,
gerçek paralel transaction (`tests/integration/test_entitlement.py`).

## Bütünlük çağıranda değil şemada (W23)

Yukarıdaki her şey **`EntitlementService` üzerinden gidildiğinde** doğruydu. Bağımsız doğrulama
turu (2026-08-03) servisten geçmeyen üç yol buldu ve üçü de para yazabiliyordu. W23 korumayı
şemaya taşıdı; mantık değişmedi.

**Defterin insert muhafızı** (`trg_credit_ledger_insert_guard`) her *tahsilat* satırında sırayla:

1. `pg_advisory_xact_lock(20020, hashtext(business_id))` — **uygulamanın aldığı kilidin aynısı**.
   Servis yolunda zaten tutulduğu için bedelsiz (advisory lock transaction içinde yeniden
   girilebilir); kilidi hiç duymamış bir yazar için gerçek bir bariyer.
2. `entitlement_ledger_anchors` satırını damgalar. **Kilidi beklemek snapshot'ı ilerletmez:**
   `REPEATABLE READ` bir yazar snapshot'ını `INSERT` başlarken alır, kuyrukta bekler, sonra
   kazananı hâlâ içermeyen bir küme toplardı — kilit tek başına yetmiyor. Kazananın güncellediği
   bir satırı güncellemek her izolasyon seviyesinin gördüğü tek çakışmadır: `READ COMMITTED`
   bekler ve yeniden okur, katı seviyeler `40001` ile düşer.
3. `SUM(delta_credits)` ve negatiflik kontrolü.

Grant ve iade bu üçünü **atlar**: kredi ekleyen bir satır bakiyeyi negatife düşüremez, dolayısıyla
sıraya sokulması gerekmez.

Anchor satırı **veri tutmaz** — `last_write_at` hiçbir yerde okunmaz, bakiye değildir, sayaç
değildir. ADR-017'nin "bakiye yalnızca girdilerde" kararı olduğu gibi duruyor; satırın tek işi
*yazılmak*.

**Tekillikler:**

| Kısıt | Ne diyor | Neden |
|---|---|---|
| `uq_credit_ledger_reservation_entry` | rezervasyon başına her tipten **bir** satır | `uq_credit_ledger_idempotency` bir *tekrarı* eler; uydurulmuş anahtarlı ikinci iade para yaratırdı |
| `ck_credit_ledger_refund_reserved` | iade bir rezervasyon adlandırır | rezervasyonsuz iade hem açıklanamaz hem de kısmi index'in NULL deliği |
| iade tutarı = rezervasyonun tuttuğu (trigger) | tekillik *sayıyı*, bu *miktarı* sınırlar | kısmi iade bugün yok; toplam-formu üreticisi olmayan bir makine olurdu |
| `uq_usage_reservations_standing_source` | iş birimi başına **ayakta bir** hak | `released` ayakta değil (iptal → yeniden başlatılabilir), `consumed` ayakta (K4 şemada) |
| rezervasyon aynı tenant'ta (trigger) | defter satırı komşunun hakkını gösteremez | tenant izolasyonu sorguların değil tablonun özelliği |

**Kilit sırası artık istisnasız:** önce tenant advisory lock, sonra rezervasyon satır kilidi.
Süpürücü de buna uyuyor — adaylarını kilitsiz okur, sonra tenant tenant (sabit sırayla) önce
kilidi sonra satırları alır. Eski sırası (önce satır) muhafız kilidi aldığı andan itibaren
sonuçlandırmayla kilitlenebilirdi.

**Bilinen sınır:** muhafız bir *trigger*'dır. `session_replication_role = replica` diyebilen bir
**superuser** negatif bakiye korumasını kapatabilir (ölçüldü). Kısıtlar ve unique index'ler
bundan etkilenmez — aynı denemede tetiklendiler. Üretimde uygulama rolü superuser olmamalıdır.

## Puan tablosu (§12.4)

Sürümlenmiş kayıt: `POINT_TABLES[sürüm] → PointTable`. Aktif sürüm konfigürasyondadır
(`ENTITLEMENT_POINTS_VERSION`, varsayılan `1`) ve **hiçbir sürüm kaydından silinmez.**

`PointTable` yapıcısı **import anında** iki totallik ister:

- her `ContentPointKind` (§12.4'ün satırları) fiyatlı,
- `ScenarioCode × RenderProfile` çarpımının **tamamı** bir satıra eşlenmiş.

Fiyatlanmamış içerik bedava içeriktir; bu yüzden yeni bir render profili fiyatlanmadan uygulama
açılmaz.

Sürüm 1, §12.4'ün örnek puanlarını olduğu gibi taşır. Yüzey → satır eşlemesi:

| Render profili | §12.4 satırı | Puan |
|---|---|---:|
| `instagram_reels_1080x1920` | Standard Reels | 5 |
| `instagram_story_1080x1920` | Hikâye | 1 |
| `instagram_feed_1080x1350`, `instagram_square_1080x1080` | Statik post | 2 |
| `x_video_1280x720`, `x_vertical_1080x1920` | X gönderisi | 1 |
| `preview_540x960` | Standard Reels (önizlediği teslimatın fiyatı) | 5 |

`professional_reels` (8), `premium_video` (20), `ad_creative_variation` (5) ve
`generative_video_scene` (10) fiyatlı ama **hiçbir yüzey onlara eşlenmiyor**: §12.3'ün kalite
seviyeleri abonelik kalemiyle (§12.2 `quality_tier`) Phase 3'te geliyor, bugün her proje standart
seviyede. §12.4 generative sahne için "10+" yazıyor; fiyat listesi açık ucu tutamaz, taban fiyat
alındı.

> **Kalibrasyon açığı (STATUS'ta kayıtlı):** bu puanlar ölçülmüş sağlayıcı maliyetine
> kalibre edilmedi. W08 benchmark'ı bu yüzden aynı zamanda fiyatlandırma girdisi. Kalibrasyon
> geldiğinde **yeni bir sürüm** eklenir; eski defter satırları kendi sürümlerini taşıdığı için
> yeniden yorumlanmazlar.

## Sonuçlandırma: iki total tablo

**Hangi başarısızlıkta iade edilir** (`ledger.settlement_outcome`), iki kapalı boyut üzerinde:

| `SourceOutcome` | Hata sınıfı | Sonuç |
|---|---|---|
| `RUNNING` | — | `None` (tut) |
| `DELIVERED` | herhangi biri (eski bir hata kodu dahil) | `CONSUME` |
| `ABANDONED` | `TECHNICAL` / `INPUT` / `UNCLASSIFIED` | `RELEASE` |

Eşlenmemiş bir hata kodu tanımsız kombinasyon değil `UNCLASSIFIED`'dır. Her sınıfın bugün iade
etmesi yer tutucu değil §12.7/§12.8'in kuralıdır: kredi ön izleme var olduğunda tüketilir.

**Bir rezervasyona ne uygulanabilir** (`ledger.resolve_settlement`), `ReservationStatus ×
SettlementOutcome` üzerinde total:

| Durum | `CONSUME` | `RELEASE` |
|---|---|---|
| `reserved` | `APPLY` | `APPLY` |
| `consumed` | `ALREADY_APPLIED` | `CONFLICT` (409) |
| `released` | `CONFLICT` (409) | `ALREADY_APPLIED` |

Tekrar ile çelişki ayrı cevaplardır. Sonuçlandırma tekrar oynatılabilen bir transaction'ın
içindedir, bu yüzden aynı sonucun ikinci uygulaması **hiçbir şey yazmayan bir başarı** olmak
zorunda; tersinin uygulanması ise iki çağıranın "iş teslim oldu mu" konusunda anlaşmazlığa
düştüğünü söyler ve append-only bir defterde sessiz ikinci iade kalıcıdır.

## Tüketim noktası ve K4

Tüketim noktası **proje başlatmadır**, adım değil. Tek rezervasyon senaryoyu, seslendirmeyi,
timeline'ı ve projenin **bütün render denemelerini** kapsar.

K4 ("saf yeniden render yeni hak tüketmez") böylece yapısal olarak sağlanır: rezervasyon
render'a değil projeye bağlı olduğu için QC başarısızlığından doğan yeniden render yeni bir
rezervasyon **açamaz**. Uçtan uca kanıt `test_content_lifecycle.py`'de: iki render, tek `consume`
satırı, ve ikinci render satırı `consumes_entitlement = false`.

**Tekil uçlar bugün ücretsizdir** — proje bağlamı olmadan senaryo üretimi, seslendirme, timeline
yazma ve tekil render isteği. Bilinçli ve geçici bir geliştirici/entegrasyon yüzeyi; Phase 3
kapatacak.

## Kredi nereden gelir

Bu slice'ta **tek kaynak** manuel grant'tir (`POST /entitlement/grants`, PRD §32.1'in
"Promosyon/admin grant"i) ve yalnızca `owner` yazabilir (`Permission.ENTITLEMENT_GRANT`) — bir
admin işletmeyi kapatmak dışında her şeyi yapabilir, ve krediyi yaratmak dışında.

Harcama için ayrı bir yetki **yoktur ve olmayacak**: harcama, ihtiyacı olan işlemin kendi
yetkisiyle onun transaction'ında olur. Etkisi yalnızca "krediyi düşür" olan bir uç, aynı tabloya
giden ikinci ve daha zayıf bir yol olurdu.

## Süpürücü: boş kümenin bakımı

`entitlement.reservation.sweep` (beat: `sweep-entitlement-reservations`, varsayılan saatlik)
açık kalmış rezervasyonları **kaynağı bittiyse** bırakır. Kaynağın bitip bitmediğini
`ReservationSourceProbe` söyler ve protokolü `content` uygular (`ContentProjectReservationProbe`)
— `entitlement` hiçbir zaman `content_projects`'e sorgu atmaz, aksi hâlde bağımlılık çift yönlü
olurdu.

Sağlıklı sistemde bu süpürme **hiçbir şey bulmaz**, çünkü sonuçlandırma zaten atomiktir.
Kapsadığı şey atomikliğin kapsayamadığı durum: kaynak satırın hiç var olmaması. Yaş eşiği
(`ENTITLEMENT_RESERVATION_SWEEP_AGE_SECONDS`) boot'ta bir yaşam döngüsü adım zaman aşımını
geçtiği doğrulanır.

**Bilinen sınır:** süpürme partisi `ENTITLEMENT_SWEEP_BATCH_SIZE` ile sınırlı ve en eskiden
başlar; parti dolduğunda sonuç `batch_full` ile bunu **bildirir** (sessiz kısaltma temiz bir
koşu gibi okunurdu). `WAITING_MEDIA`'da park etmiş çok sayıda eski proje, teorik olarak
süpürmenin arkasındaki gerçek bir yetimi geciktirebilir.

## Bu slice'ın taşımadıkları

- **İptal ucu yok.** `WAITING_MEDIA`'da park eden bir proje kredisini süresiz tutar; iptal +
  hakkın iadesi **2F**'nin işi. Projenin adım zaman aşımı bu durumu kapsamaz (o durum muaf) ve
  süpürme de kapsamaz (kaynak canlı).
- **§12.7'nin `CONSUMED → REFUNDED` yolu yok** — tüketilmiş bir üretimin sonradan iadesi
  destek/admin yüzeyi ister (Phase 3). `refund` girdi tipi var; bugün tek üreticisi bırakılan bir
  rezervasyon.
- **`AVAILABLE`/`EXPIRED`/`ROLLED_OVER` yok.** Bunlar bir *pencere*nin özellikleri (§12.6, §12.9)
  ve fatura dönemiyle birlikte Phase 3'te geliyor. `expire` girdi tipi şemada var, üreticisi yok.
- **`entitlement_windows` tablosu yok.** PRD §28.9'un `usage_reservations(entitlement_window_id,
  status)` index'i bugün `(business_id, status)` olarak duruyor; şekli aynı, kapsam tenant.
