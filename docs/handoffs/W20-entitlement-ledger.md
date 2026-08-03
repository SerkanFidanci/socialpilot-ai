# W20 — Phase 2E (ikinci yarı): Kredi defteri ve hak tüketimi

**Dal:** `slice/2e-entitlement` · **Base:** `main` · **Migration slotu: SENDE** (`0017`)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Neden bu iş:** W19 içerik projesini uçtan uca yürütüyor ve **hiçbir şey saymıyor.** Bugün bir kullanıcı sınırsız render tetikleyebilir; her render gerçek para (sağlayıcı + CPU) harcıyor. Bu, ücretli sağlayıcılar bağlanmadan **önce** kapatılması gereken bir açık: hak muhasebesi olmadan gerçek bir sağlayıcı bağlamak, faturayı ölçüsüz bir kullanıma açmaktır.

## Kapsam sınırı (PM kararı — dikkatle oku)

**Bu slice ödeme almaz, mağaza entegrasyonu yapmaz, fiyat belirlemez.** K1 (faturalandırma modeli: IAP vs web-first) hâlâ **kullanıcının açık kararı** ve Phase 3'ün konusu. Bu slice yalnızca **defteri ve tüketimi** kurar:

- Bir işletmenin ne kadar hakkı olduğu **veri** olarak durur (`credit_ledger`), nereden geldiği bu slice'ın işi değil — bu slice yalnızca **manuel/seed** bir kaynak tanır (`grant` kaydı, admin/seed tarafından).
- Store doğrulaması, webhook, yenileme, iade, plan eşleme → **Phase 3**.
- Bu ayrım bilinçli: tüketim tarafı doğru kurulursa, kaynak tarafı (IAP mı web mi) sonradan **tek bir grant yazıcısı** olarak takılır. Tersi mümkün değil — önce ödeme alıp sonra saymaya başlamak, sayılmamış tüketimi kalıcı borç yapar.

## Okunacaklar

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/product/requirements/50-subscription-entitlement.md`](../product/requirements/50-subscription-entitlement.md) — **§12.4 kredi sistemi** (puan tablosu), §12.5 ayrımı, `credit_ledger` şekli
3. [`docs/product/requirements/90a-database-design.md`](../product/requirements/90a-database-design.md) — `credit_ledger` satırı
4. [`docs/product/requirements/00-vision-principles.md`](../product/requirements/00-vision-principles.md) — "abonelik hakkının kaynağı backend entitlement ledger'dır" (madde 4)
5. `services/api/app/modules/content/CLAUDE.md` — özellikle K4'ün "**saf yeniden render yeni hak tüketmez**" kuralı ve W19'un yaşam döngüsü
6. `services/api/app/modules/operations/**` — mevcut `provider_usage` ve outbox desenleri (para birimi/minor units disiplini W12'de kuruldu)

## PM kararları

### 1. Defter **append-only**, bakiye türetilir

`credit_ledger` yalnızca satır ekler: `grant` (+), `consume` (−), `refund` (+), `expire` (−). Bakiye **hiçbir yerde tek bir sayı olarak saklanmaz** — toplamdan türetilir; performans gerekirse materialize edilmiş bir görünüm/özet satırı eklenebilir ama **doğruluk kaynağı defterdir**. Gerekçe: mutasyona uğrayan bir bakiye alanı, eşzamanlı iki tüketimde sessizce yanlışa düşer ve hatayı geriye doğru izlemek imkânsız olur.

### 2. Tüketim **rezervasyon + sonuçlandırma**, tek adımda değil

İş başlarken hak **rezerve edilir** (`consume` satırı, `pending` durumunda), iş bitince **sonuçlandırılır** (`settled`) veya **iade edilir** (`released`). Gerekçe: render 8 dakika sürüyor; başta düşüp sonda iade etmek çift-harcamayı önler, sonda düşmek ise sınırsız paralel başlatmaya açık kapı bırakır.

**İade kuralı (PRD §12.4 + K4'ün kuralı):**
- İş **teknik sebeple** başarısızsa (sağlayıcı hatası, worker çökmesi, QC `failed` sonrası tükenen deneme) → **iade edilir.** Kullanıcı bizim hatamız için ödemez.
- İş **başarılıysa** → sonuçlandırılır, iade yok.
- **Saf yeniden render** (timeline değişmemiş, yalnızca yeniden üretim) → **hak tüketmez** (K4 kararı, ADR'da kayıtlı). Revizyon kotasından düşer — kotanın kendisi 2F'nin.

### 3. Puan tablosu **veri**, kod değil

§12.4'ün puanları (`X gönderisi=1` … `Premium video=20`) bir **sürümlenmiş tablo** olarak durur; tüketim satırı **hangi sürümle** hesaplandığını taşır. Gerekçe: puanlar W08 benchmark'ının ölçtüğü gerçek maliyetle kalibre edilecek (STATUS'ta kayıtlı açık) ve dünkü tüketimin hangi tabloyla hesaplandığı bilinmeden fatura tartışması çözülemez.

### 4. Kontrol **iş başlamadan önce**, ve tenant-güvenli

Yetersiz bakiye → iş **hiç başlamaz**, dokümante hata (`ENTITLEMENT_INSUFFICIENT_CREDITS`), 402 sınıfı. Kontrol ve rezervasyon **aynı transaction'da** olmalı — okuyup sonra yazmak yarış açar. Eşzamanlı iki isteğin ikisinin de son krediyi harcayamadığı **testle kanıtlanmalı** (gerçek PostgreSQL, gerçek eşzamanlılık).

### 5. Bu slice'ın tüketim noktaları

W19'un yaşam döngüsünde: **proje başlatma** (paket olarak, içerik tipine göre puan) — tekil senaryo/TTS/render çağrıları **ayrı ayrı** ücretlendirilmez. Gerekçe: kullanıcı "bir içerik" satın alıyor, adımları değil; adım başına ücretlendirme hem şaşırtıcı hem de W19'un otomatik yeniden denemesiyle çelişir.

Tekil uçlar (proje bağlamı olmadan senaryo üretimi vb.) bugün **ücretsiz kalır** ve bu bilinçli bir geçici durum: onlar geliştirici/entegrasyon yüzeyi. Raporda bunu açıkça yaz — Phase 3 kapatacak.

## Kapsam dışı (dokunma)

- Store/IAP doğrulama, webhook, yenileme, iade akışı, plan eşleme, fiyatlandırma → **Phase 3**.
- Revizyon kotası ve onay akışı → **2F**. Planlayıcı → **2G**.
- W19'un durum makinesi ve QC → kapandı; yalnızca tüketim noktalarını **tak**, mantığını değiştirme.
- `script.py`, `qc.py` karar tablosu, `text_normalization.py` → dokunma.
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/modules/entitlement/**                  (yeni modül: ledger, puan tablosu, rezervasyon servisi, CLAUDE.md)
services/api/app/modules/content/project_service.py      (yalnızca tüketim noktası çağrısı)
services/api/app/modules/content/render_service.py       (yalnızca iade/sonuçlandırma çağrısı)
services/api/app/api/routes/entitlement.py + routes/__init__.py  (bakiye/defter okuma uçları)
services/api/app/core/config.py                          (ENTITLEMENT_* ayarları)
services/api/app/worker/{tasks,composition}.py           (sonuçlandırma/iade job'ı gerekirse)
services/api/migrations/versions/0017_*.py               (SLOT SENDE)
services/api/tests/unit/ + tests/integration/
docs/architecture/ (entitlement bölümü — hangi dosyaya yazdığını raporda bildir) · error-handling.md · .env.example
```

## Kabul kriterleri

1. Migration `0017` up → down → up; tek head.
2. **Bakiye türetiliyor, saklanmıyor:** defterden hesaplanan bakiye ile beklenen değer birebir; bakiyeyi tek bir mutasyona uğrayan alandan okuyan hiçbir yol yok (test bunu zorlar).
3. **Eşzamanlılık:** son krediyi hedefleyen iki eşzamanlı proje başlatma isteğinden **tam olarak biri** başarılı; diğeri `ENTITLEMENT_INSUFFICIENT_CREDITS`. Gerçek PostgreSQL, gerçek paralel transaction — mock değil.
4. **Rezervasyon yaşam döngüsü:** başarılı iş → `settled`; teknik başarısızlık (sağlayıcı hatası + deneme tükenmesi) → `released`, bakiye eski haline dönüyor; yarım kalmış rezervasyon (worker çöktü) yaş eşiğiyle süpürülüyor (W19'un süpürücü desenini izle).
5. **Saf yeniden render hak tüketmiyor** (K4) — testle kanıtlanıyor; timeline değişmişse tüketiyor.
6. Puan tablosu sürümlü; tüketim satırı sürümü taşıyor; sürüm değişince eski satırlar yeniden yorumlanmıyor.
7. Tenant izolasyonu: başka işletmenin defteri okunamıyor/harcanamıyor (`404`, varlık ifşası yok). Roller: bakiye okuma `business.read`; **manuel grant yalnızca** `owner` veya sistem/seed yolu.
8. Parasal/sayısal disiplin: krediler **tam sayı**, negatife düşemez (kısıt veritabanında da), `provider_usage` ile ilişki kurulabiliyor (hangi tüketim hangi sağlayıcı maliyetini doğurdu).
9. `make verify` yeşil; test sayısı **1237** tabanının altına düşmez; kontrat yeniden üretilip commit'li; modül `CLAUDE.md` yazıldı.
10. Rapor + araç zinciri sürümleri. **Merge etme, dalda bırak.**

## Enumerasyon kuralı

Kredi işlem tipleri kapalı bir küme (`grant`/`consume`/`refund`/`expire`) — yazılabilir. Ama **"hangi başarısızlıkta iade edilir"** kombinatoryaldir: 2D/2E desenini izle (total fonksiyon, tanımsız kombinasyon yok, permütasyon testi).

## ADR numara kuralı

Gerçek karar çıkarsa `ADR-XXX-<konu>.md`; numarayı PM verir. (Append-only defter + rezervasyon/sonuçlandırma duruşu ADR'lık.)

## Rapor — 2026-08-02 · Claude Code (Opus 5 / high)

**Dal:** `slice/2e-entitlement` (base `main` = `f22c980`) · **Durum:** tamamlandı, **merge edilmedi**
**Doğrulama ortamı:** `COMPOSE_PROJECT_NAME=sp-w20` (host portları worktree'ye özel bir
gitignore'lu `.env` ile ayrıldı: 8020/55440/56390/59100 — başka worktree'lerin konteynerleri
ele geçirilmedi)

### Yapılanlar

**Yeni modül `services/api/app/modules/entitlement/`** — `points.py` (§12.4 sürümlü puan
tablosu), `ledger.py` (saf aritmetik + iki total karar tablosu), `models.py`, `repository.py`,
`policy.py`, `service.py`, `CLAUDE.md`.

1. **Defter append-only, bakiye türetilir (PM kararı 1).** `credit_ledger` yalnızca satır ekler;
   `bakiye = SUM(delta_credits)`. Hiçbir yerde `balance` sütunu yok ve entegrasyon testi bunu
   `information_schema` taramasıyla **zorluyor** (kriter 2'nin "okuyan hiçbir yol yok" kısmı).
   `delta_credits` işaretli ve işaret `ck_credit_ledger_delta_sign` ile tipine bağlı, yani bakiye
   tek bir ifade — ikinci bir `CASE` yazılamaz.
2. **Append-only ve negatif bakiye veritabanı seviyesinde.** `trg_credit_ledger_append_only`
   `UPDATE`/`DELETE`'i reddeder; `trg_credit_ledger_non_negative` §32.4'ün "Negatif bakiye
   oluşmamalıdır" kuralını uygular. Trigger **mekanizma değil yedektir**: asıl kesinlik tenant
   advisory lock'undan gelir ve trigger yalnızca commit edilmiş satırları görür. `TRUNCATE` satır
   trigger'ı tetiklemediği için test temizliği etkilenmiyor.
3. **Rezervasyon + sonuçlandırma (PM kararı 2), `consume` satırı başta.** Rezervasyon açılırken
   `usage_reservations` (`reserved`) + `credit_ledger` `consume` (−N) yazılır. Sonuçlandırma satır
   **yazmaz** (tahsilat oldu); iade telafi edici `refund` (+N) yazar. Böylece açık rezervasyon
   bakiyeyi zaten düşürmüştür ve bakiye tek bir sütunun toplamı olarak kalır — alternatif
   ("`consume`'u sonda yaz") bakiyeye ikinci bir terim eklerdi.
4. **Kontrol + rezervasyon aynı transaction'da (PM kararı 4).** `reserve`/`settle` kendi
   transaction'ını açmaz; `create_project`'in `begin()`'i içinde koşar. Yarışı kapatan mekanizma
   `pg_advisory_xact_lock(namespace, hashtext(business_id))` ve **bakiye okunmadan önce**
   alınıyor. `businesses` satır kilidi yerine advisory lock: satır kilidi rezervasyon boyunca o
   işletmeye yapılan alakasız her yazmayı da bloke ederdi.
5. **Puan tablosu sürümlü (PM kararı 3).** `POINT_TABLES` sürüm kaydı; aktif sürüm
   `ENTITLEMENT_POINTS_VERSION`. Çözümleme rezervasyon açılırken **bir kez**; sonuç hem
   rezervasyona hem defter satırına yazılır ve **saklanmış bir satırdan krediyi yeniden türeten
   hiçbir fonksiyon yok**. `PointTable` yapıcısı **import anında** totallik ister: her
   `ContentPointKind` fiyatlı, `ScenarioCode × RenderProfile` çarpımının tamamı eşlenmiş —
   fiyatlanmamış bir render profili uygulamayı açtırmaz, çünkü fiyatlanmamış içerik bedava
   içeriktir.
6. **Tüketim noktası proje başlatma (PM kararı 5).** Tek rezervasyon senaryoyu, seslendirmeyi,
   timeline'ı ve **tüm render denemelerini** kapsar. **K4 böylece yapısal olarak sağlanıyor:**
   rezervasyon render'a değil projeye bağlı olduğu için QC başarısızlığından doğan yeniden render
   yeni rezervasyon *açamaz*.
7. **Sonuçlandırma projeyi terminal yapan transaction'ın içinde.** `_settle` içinde
   `source_outcome(state)` → `settle(...)`. Ayrı bir job olsaydı "bitmiş proje hâlâ hak tutuyor"
   penceresi olurdu; bu şekilde ya iki gerçek de commit oldu ya hiçbiri.
8. **Enumerasyon kuralı: iki total tablo.** `settlement_outcome` (`SourceOutcome` × hata kodu) ve
   `resolve_settlement` (`ReservationStatus` × `SettlementOutcome`); eşlenmemiş hata kodu
   `UNCLASSIFIED`, yani cevabı olan bir durum. **Tekrar ile çelişki ayrı cevaplar:** aynı sonucun
   ikinci uygulaması `ALREADY_APPLIED` (hiçbir şey yazılmaz), tersi `CONFLICT` (409). Permütasyon
   testleri `ProjectState × hata kodu` (11 × 15) ve `durum × sonuç` (3 × 2) çarpımlarını **tam**
   dolaşıyor.
9. **Uçlar.** `GET .../entitlement/{balance,ledger,reservations}` (`business.read`) ve
   `POST .../entitlement/grants` (**yalnızca `owner`** — yeni `Permission.ENTITLEMENT_GRANT`).
   Harcama için yetki **yok ve olmayacak**: harcama, ihtiyacı olan işlemin kendi yetkisiyle onun
   transaction'ında olur.
10. **Süpürücü** `entitlement.reservation.sweep` (beat: saatlik). Yaş eşiği + `SKIP LOCKED` +
    boş dönünce `None`, W19 desenini izliyor. **Asla tahmin etmez:** bir hakkı ancak işi sahiplenen
    modül "iş bitti" derse bırakır (`ReservationSourceProbe` protokolü, uygulaması `content`'te) —
    yaş tek başına kanıt değil.
11. **Modül sınırı.** `entitlement` `content`'in *sözlüğünü* okur (`ScenarioCode`,
    `RenderProfile` — fiyat listesinin totalliği buna dayanır), **tablolarını değil**. Bir proje
    hakkında sorulan tek sorgu probe protokolünden geçer, bu yüzden bağımlılık tek yönlü:
    `content → entitlement`.

**Migration `0017_entitlement_ledger`** — iki tablo, iki enum, iki trigger, beş index (biri
kısmi tekil).

### Kapsam dışı bıraktıklarım ve nedeni

- **Mağaza/IAP, webhook, yenileme, plan eşleme, fiyat** → Phase 3 (K1 açık). Bu slice'ta tek
  kredi kaynağı `owner`'ın manuel grant'i.
- **Tekil uçlar ücretsiz kaldı** (proje bağlamı olmayan senaryo üretimi, seslendirme, timeline
  yazma, tekil render isteği) — iş emrinin kararı 5 böyle diyor. Bilinçli ve **geçici**; Phase 3
  kapatmalı. Bugün bir geliştirici bu uçlarla ücretsiz senaryo üretebilir.
- **Proje iptali yok** → 2F. Sonucu: `WAITING_MEDIA`'da park eden bir proje kredisini **süresiz**
  tutar. Adım zaman aşımı bu durumu kapsamıyor (o durum muaf) ve süpürme de kapsamıyor (kaynak
  canlı). **PM'e bırakılan iş.**
- **§12.7'nin `CONSUMED → REFUNDED` yolu yok** (tüketilmiş üretimin sonradan iadesi) — destek/admin
  yüzeyi ister. `refund` girdi tipi var; bugün tek üreticisi bırakılan bir rezervasyon.
- **§12.6/§12.9 hak penceresi, süre sonu ve devir yok.** `expire` girdi tipi şemada var, üreticisi
  yok; `entitlement_windows` tablosu yok (§28.9'un index'i `(business_id, status)` olarak duruyor).
- **`docs/index.md` ve `docs/adr/README.md` güncellenmedi** (iş emri kapsam dışı bırakıyor).
  Eklenmesi gerekenler: router'a `entitlement` satırı,
  [`docs/architecture/entitlement.md`](../architecture/entitlement.md) ve ADR-017.
- **`docs/plans/active/phase-2-content-generation.md` güncellenmedi** — ilan listesinde yok.
- `script.py`, `qc.py`, `text_normalization.py`, W19 durum makinesi: dokunulmadı.

### İlan dışı dokunuşlar (dördü zorunlu, gerekçeleriyle)

| Dosya | Neden |
|---|---|
| `app/modules/businesses/policy.py` | `Permission.ENTITLEMENT_GRANT` — **tek satır**. Kriter 7 "manuel grant yalnızca `owner`" diyor; `businesses/CLAUDE.md` "yetki kararı yalnızca `policy.permits` üzerinden, elle rol karşılaştırması yazılmaz" diyor. İkisini birden karşılamanın tek yolu merkezî tabloya bir üye eklemek. `ROLE_PERMISSIONS[OWNER] = frozenset(Permission)` olduğu için diğer roller otomatik olarak dışarıda. |
| `app/infrastructure/database/metadata.py` | Yeni model modülü burada kayıtlı değilse `verify_mapping_is_complete` cross-module FK'yi çözemez ve worker/API açılışta patlar. Bir import + bir tuple girdisi. |
| `app/infrastructure/celery_app.py` | Beat girdisi (`sweep-entitlement-reservations`). İlan `worker/{tasks,composition}.py` veriyor ama beat schedule bu dosyada; task'ı zamanlamadan bırakmak yarım iş olurdu. |
| `app/modules/content/CLAUDE.md` | `project_service.py` değiştiği için AGENTS.md'nin "modülün `CLAUDE.md`'si aynı değişiklikte güncellenir" kuralı. |

Ayrıca **`docs/STATUS.md`'de kendi satırımın dışına da yazdım**: Alembic head satırı (`0016` →
`0017`, dal notuyla), dosya sahipliği tablosunda W19 satırının yerine W20 satırı (W19 kapandı),
ve "Sırada" bloğuna 2E ikinci yarı özeti + açık kalanlar. PM isterse geri alsın.
`docs/architecture/background-jobs.md`'ye beat tablosu satırı + süpürme bölümü eklendi (iş emri
"hangi dosyaya yazdığını raporda bildir" diyordu: **yeni dosya
[`docs/architecture/entitlement.md`](../architecture/entitlement.md)**, artı background-jobs'a
süpürücü girdisi).
`docs/generated/openapi.json` + `docs/api/endpoints.md` `make generate-docs` ile yeniden üretildi.
Worktree kökünde gitignore'lu bir `.env` var (yalnızca host port ayrımı) — commit edilmiyor.

### Yanlış teşhis koyup düzelttiğim bir nokta (kayda geçsin)

Ara bir tam koşu **5 düşen + 2 hata** verdi ve hiçbiri benim testlerimde değildi
(`test_brand_catalog`, `test_celery_orchestration`); hata
`asyncpg.exceptions.DeadlockDetectedError` — bir teardown `TRUNCATE ... CASCADE`'i, ve düşen
dosyalar tek başlarına geçiyordu. İlk teşhisim şuydu: `businesses` iki yeni bağımlı tablo
kazandığı için TRUNCATE'in kilit kümesi genişledi ve latent bir yarış görünür oldu. Buna göre
**16 entegrasyon dosyasının** teardown listesine iki tabloyu başa yazdım ve koşu temiz geldi.

**Teşhis yanlıştı.** `main`'in `2f7cbc4` commit'i aynı arızayı zaten kayda geçmiş: *"earlier 8
failures were two concurrent pytest processes"*. Bendeki de aynı şeydi — daha önce arka planda
başlatıp durdurduğum bir koşunun konteyner tarafındaki `pytest`'i hayatta kalmış ve ikinci koşuyla
aynı PostgreSQL üzerinde eşzamanlı TRUNCATE'ler atmıştı. Bir teardown deadlock'ta düşünce veri
sızıyor ve sonraki dosya yanlış sayı görüyor (`processed: 2 != 1`) — düşen testlerin benim
dosyalarımda olmamasının sebebi buydu.

**Kontrol koşusu:** 16 dosyadaki değişikliği geri aldım, konteynerde başıboş `pytest` olmadığını
`/proc` taramasıyla doğruladım ve tam süiti yeniden koştum → **1325 passed**. Yani değişiklik
gereksizdi ve geri alındı. `credit_ledger`/`usage_reservations` yalnızca `test_entitlement.py` ve
`test_content_lifecycle.py`'nin listelerinde adlandırılmış durumda — cascade zaten ulaşıyor,
adlandırma o iki dosyanın "test neyden başlıyor" beyanının parçası.

**Ders (operasyonel):** `docker compose exec` ile başlatılan bir koşuyu yerelden durdurmak
konteyner tarafındaki süreci öldürmüyor. Yeni bir tam koşu başlatmadan önce
`/proc` taraması ile başıboş `pytest` olmadığı doğrulanmalı; aksi hâlde iki koşu aynı veritabanını
paylaşıyor ve bu arıza başka bir şey gibi görünüyor.

### PRD ile ayrışma (metin değiştirilmedi, PM'e bırakıldı)

1. **§32.4'ün `balance_after` sütunu uygulanmadı.** Satır başına yürüyen toplam, yazımların tam
   sıralı olmasını gerektirir ve girdilerin zaten verdiği cevabı saklar; çeliştiği gün hangisinin
   doğru olduğunu söyleyecek bir şey yoktur. PM kararı 1 zaten "bakiye hiçbir yerde tek bir sayı
   olarak saklanmaz" diyor. §32.4'ün asıl talebi (negatif bakiye olmaması) trigger'la karşılandı.
   Gerekçe ADR-017'de; **gereksinim metni değiştirilmedi** (AGENTS.md gereksinim metnini
   yeniden yazmayı yasaklıyor ve dosya ilan listemde değil).
2. **§32.4'ün `reserve` ve `adjust` girdi tipleri yok.** PM kararı 1 kümeyi
   `grant`/`consume`/`refund`/`expire` olarak kapattı. `reserve`, `consume` + rezervasyon durumu
   olarak ifade ediliyor (bakiyenin tek toplam kalması için); `adjust`'ın üreticisi yok.
3. **"Puan tablosu veri, kod değil" (PM kararı 3) kısmen.** Tablo sürümlenmiş bir Python kaydı,
   aktif sürüm konfigürasyon. Gerekçe: bu slice'ta tabloyu yazacak admin yüzeyi yok (Phase 3),
   dolayısıyla DB tablosu yalnızca migration'la yazılabilirdi — fazladan adımı olan kod. Kararın
   **amacı** (sürümlü + denetlenebilir + dünkü tahsilatın hangi tabloyla hesaplandığının
   bilinmesi) tam karşılanıyor ve taşıma geriye dönük hiçbir şeyi değiştirmez, çünkü defter
   satırları sürümü zaten taşıyor.

### Kabul kriteri 5'i nasıl okudum

"Saf yeniden render hak tüketmiyor; timeline değişmişse tüketiyor." Kararı 5 tüketim noktasını
**proje başlatma** olarak sabitlediği ve tekil uçları ücretsiz bıraktığı için bu ikisi ancak
proje bağlamında ölçülebilir. Uyguladığım okuma:

- **Saf yeniden render → ücretsiz:** aynı projenin QC sonrası yeniden render'ı ikinci bir
  rezervasyon açmaz. Kanıt uçtan uca: iki `render_outputs` satırı, **tek** `consume` satırı, ve
  ikinci render `consumes_entitlement = false`.
- **"Timeline değişmişse tüketiyor" → yeni bir üretim yeni bir projedir ve yeniden ücretlendirilir.**
  Kanıt: ikinci `create_project` ikinci rezervasyonu açıyor.

Mevcut `service.request_render` semantiği (revizyon 1 = `INITIAL`, sonrası = `REVISION`)
**değiştirilmedi** — W11/W19'un kararı ve K4'ün "parametrik düzenleme revizyon kotasından düşer"
kuralıyla tutarlı. Farklı okunması gerekiyorsa PM söylemeli.

### Doğrulama

Araç zinciri: Python 3.13.14 · mypy 2.3.0 · ruff 0.16.0 · pytest 9.1.1 · SQLAlchemy 2.0.51 ·
Alembic 1.18.5 · FastAPI 0.141.1 · Pydantic 2.13.4 · PostgreSQL 16.14 (konteyner) · gerçek MinIO
+ FFmpeg.

| Kontrol | Sonuç |
|---|---|
| `ruff check` + `ruff format --check` | ✅ temiz (217 dosya) |
| `mypy .` (strict) | ✅ `no issues found in 204 source files` |
| `pytest` (`RUN_INTEGRATION_TESTS=1`, gerçek PostgreSQL + MinIO + FFmpeg) | ✅ **1325 passed** (taban 1237, +88; azalma yok) |
| `make check-openapi` | ✅ kontrat + `endpoints.md` yeniden üretildi ve commit'li |
| migration `0017` up → down → up, tek head | ✅ `0017_entitlement_ledger (head)` |
| **K1** migration up/down/up, tek head | ✅ |
| **K2** bakiye türetiliyor, saklanmıyor | ✅ defterden hesaplanan = beklenen; ayrıca `information_schema` taraması `balance`/`credits_remaining` adlı **hiçbir sütun** bulmuyor |
| **K3** eşzamanlılık | ✅ son krediyi hedefleyen 2 eşzamanlı `create_project` → tam 1 başarı + 1 `ENTITLEMENT_INSUFFICIENT_CREDITS`; 3 kredilik bakiyeye 10 eşzamanlı istek → tam 3 başarı. Gerçek PostgreSQL, `asyncio.gather` ile gerçek paralel transaction, mock yok |
| **K4** rezervasyon yaşam döngüsü | ✅ başarılı proje → `consumed` (ek satır yok); teknik başarısızlık (deneme tükenmesi) → `released` + `refund`, bakiye eski haline döndü; yetim rezervasyon süpürüldü (`ENTITLEMENT_RESERVATION_ABANDONED`), canlı proje ve eşik altındaki hak **dokunulmadı** |
| **K5** saf yeniden render ücretsiz | ✅ 2 render / 1 `consume`; yeni proje → yeni rezervasyon |
| **K6** puan tablosu sürümlü | ✅ sürüm 2 kaydedilip aktif edildiğinde eski rezervasyon ve defter satırları **birebir aynı**, yeni iş 3× fiyatla açıldı |
| **K7** tenant izolasyonu + roller | ✅ başka tenant'ın `balance`/`ledger`/`reservations`/`grants` uçları `404 BUSINESS_NOT_FOUND` (uydurma id ile **ayırt edilemez**); `admin`/`editor`/`viewer`/`approver` grant'te `403`; `admin`/`editor`/`viewer` bakiyeyi okuyabiliyor |
| **K8** sayısal disiplin + `provider_usage` ilişkisi | ✅ krediler tam sayı (JSON float `400 REQUEST_VALIDATION_FAILED`), negatif bakiye trigger'la reddediliyor, ters işaretli/sürümsüz/rezervasyonsuz satır kısıtlarla reddediliyor, ikinci iade kısmi tekil index'le reddediliyor; rezervasyon ↔ `provider_usage` `correlation_id` ile joinleniyor (uçtan uca testte gerçek sağlayıcı kaydıyla) |
| **K9** test sayısı + kontrat + `CLAUDE.md` | ✅ |
| **K10** rapor + sürümler, **merge yok** | ✅ |

Yeni test: `tests/unit/test_entitlement_unit.py` (54) · `tests/integration/test_entitlement.py`
(34); ayrıca `test_content_lifecycle.py`'nin iki uçtan uca testine sonuçlandırma ve K4
doğrulaması eklendi. `tests/unit/test_celery_publisher.py`'nin beat schedule beklentisine yeni
girdi eklendi. Başka hiçbir mevcut test dosyası değişmedi (yukarıdaki "yanlış teşhis" notu).

### Açıkça belirtmem gerekenler

1. **ADR numarası PM'in.** `ADR-017-entitlement-ledger.md` yazıldı (sıradaki boş numara) ve
   `docs/adr/README.md` indeksine **eklenmedi**. Numara teyidi + indeks PM'de.
2. **Park etmiş proje kredisi tutar.** İptal ucu 2F'de olduğu için `WAITING_MEDIA`'da bekleyen
   proje hakkını süresiz tutuyor. Bugün çıkış yolu yok; ürün tarafında kabul edilebilir mi, PM
   kararı.
3. **Tekil uçlar ücretsiz.** İş emrinin kararı, ama üretimde gerçek sağlayıcı bağlanmadan **önce**
   kapatılması gereken bir açık — bu slice'ın var olma gerekçesinin aynısı.
4. **Süpürme partisi sessiz kısaltma yapmıyor ama teorik bir starvation var:** en eskiden başlar
   ve `ENTITLEMENT_SWEEP_BATCH_SIZE` ile sınırlıdır, dolayısıyla çok sayıda eski *canlı* hak
   (park etmiş projeler) arkasındaki gerçek bir yetimi geciktirebilir. Parti dolduğunda sonuç
   `batch_full` ile bunu bildiriyor. Süpürme zaten atomik sonuçlandırmanın üstünde bir yedek
   olduğu için kabul edildi.
5. **Puan tablosu kalibre değil** (STATUS'ta zaten kayıtlı açık). §12.4'ün örnek puanları
   birebir alındı. Kalibrasyon **yeni bir sürüm** olarak gelmeli; eski satırlar sürümlerini
   taşıdığı için yeniden yorumlanmaz.
6. **`ENTITLEMENT_POINTS_VERSION` boot'ta doğrulanmıyor.** `core` `modules`'e bağımlılık
   veremediği için (core/CLAUDE.md değişmezi) kayıtlı olmayan bir sürüme pinlemek ilk
   rezervasyonda `PointTableError` ile patlar. Birim testi varsayılanın kayıtlı olduğunu pinliyor;
   yine de bilinçli bir boşluk.
7. **PostgreSQL sürümü.** Doğrulama konteynerde PostgreSQL **16.14** ile koştu (compose'un
   mevcut imajı). W06 (PostgreSQL 18 geçişi) bekletilmiş durumda; advisory lock, kısmi tekil
   index ve `plpgsql` trigger'ları sürümden bağımsız, ama not düşüyorum.

## Doğrulama

Test eden oturumu — `COMPOSE_PROJECT_NAME=sp-verify`, şema `0019_content_planner`.
Saldırı kanıtı mevcut testlerden alınmadı; yeni kullanıcılar, tenantlar, reservations ve defter
satırları bu oturumda oluşturuldu. Mevcut paket yalnız ortak regresyon kapısı olarak ayrıca
çalıştırıldı. Araç zinciri: Docker Engine 25.0.3 · Docker Compose
v2.24.6-desktop.1 · Python 3.13.14 · PostgreSQL 16.14 · SQLAlchemy 2.0.51 ·
Alembic 1.18.5 · pytest 9.1.1 · FFmpeg 7.1.5.

| Saldırı | Sonuç | Kanıt |
|---|---|---|
| Aynı son 5 krediyi iki gerçek transaction ile harcama | Servis yolunda engellendi | Ayrı PostgreSQL PID'leri `876`/`877`: bir `reserve` başarılı, diğeri `ENTITLEMENT_INSUFFICIENT_CREDITS`. Xact advisory lock'u `pg_advisory_unlock` ile açma denemesi `false`. |
| İade edilmiş reservation'ı tekrar iade etme | **Ham SQL ile başarılı — bulgu** | Servis replay'i ilk iade satırını korudu; aynı `reservation_id` için farklı anahtarlı ham `refund` kabul edildi: refund sayısı `1 → 2`. Şema reservation başına refund'ı tekilleştirmiyor. |
| Negatif bakiye / trigger atlatma | **Başarılı — bulgu** | Aynı 5 krediye karşı iki eşzamanlı, geçerli FK/kısıtlı ham `consume -5` transaction'ı commit edildi; toplam **`-5`**. Trigger diğer transaction'ın commit edilmemiş satırını görmedi. |
| Başka tenant'ın defterini okuma veya yazma | Engellendi | Doğrudan servis çağrıları: okuma ve grant `404 BUSINESS_NOT_FOUND`. |
| Saf yeniden render'ı yeniden ücretlendirme | **İç servis sınırı bulgusu** | Aynı `source_id`, yeni idempotency anahtarıyla ikinci `reserve` kabul edildi: iki reservation, ikinci `5` kredi. Dış render rotasının bunu çağırdığı kanıtlanmadı; `reserve` tek başına kaynak-tekrarını engellemiyor. |
| Puan tablosu değişince geçmişi yeniden yorumlama | Engellendi | V1 reservation `(5, 1)` kaldı; yeni V97 tablosu yalnız yeni reservation'ı `(15, 97)` fiyatladı. |
| Owner olmayan rolle grant | Engellendi | `admin` rolü `403 INSUFFICIENT_PERMISSION`. |
| Sonuçlandırılmış reservation'ı iade ettirme | Servis yolunda engellendi | Aynı release replay'i idempotent; ters `DELIVERED` sonuçlandırması servis karar tablosunda çatışmadır. Ham SQL ikinci-iade bulgusu yukarıda ayrı kaydedildi. |

### Bağımsız yeniden üretim bulguları

İlk tablodaki açıklar ikinci bir bağımsız veri setiyle de yeniden üretildi. `b2d8650722` dış/API
yüzeyini; `b642ad5d48` iç servis ve ham DB-yazarı sınırını hedefledi. Her iki harness repo dışında
tutuldu ve repo test fixture'larını kullanmadı.

| # | Bulgu | Şiddet | Kendi girdimizle yeniden üretim | Durum |
|---|---|---|---|---|
| W20-F1 | Aynı reservation'a farklı idempotency anahtarıyla ikinci refund yazılabiliyor | Yüksek (iç DB yazarı) | Normal cancel sonrası tek `refund` ve bakiye `5` idi. Aynı `reservation_id` için farklı anahtarlı, şema açısından geçerli ham refund commit edildi; refund sayısı `2`, bakiye `10` oldu. Kanonik `refund:<reservation_id>` replay'i ise yeni satır yazmadı. | Açık |
| W20-F2 | Eşzamanlı ham defter yazıları negatif toplam trigger'ını aşabiliyor | Yüksek (iç DB yazarı) | `grant +5` ve iki geçerli reserved reservation hazırlandı. İki ayrı gerçek PostgreSQL transaction'ı bariyerde eşzamanlı `consume -5` yazıp commit etti; ikisi de başarılı oldu ve türetilen bakiye `-5` oldu. Normal `ContentProjectService` yarışı ise bir başarı + bir `402` ile güvenli kaldı. | Açık |
| W20-F3 | `reserve`, aynı `(business_id, source_type, source_id)` için yeni idempotency anahtarını kabul ediyor | Orta (iç servis çağrısı) | Bir gerçek proje rezervasyonundan sonra aynı kaynak kimliğiyle doğrudan `EntitlementService.reserve` çağrısı ikinci reservation'ı açtı: kaynak için `2` reservation / `10` kredi. Dış parametric-rerender rotası ayrıca sınandı ve ikinci rezervasyon/defter satırı üretmedi. | Açık |

Geçen ek saldırılar: dış API'de son 5 kredi yarışı tek rezervasyonla ve bakiye `0` ile bitti;
cross-tenant grant `404` verdi; tek ham `expire -1` check/trigger ile reddedildi; eski puan tablosu
satırı `(5, v1)` kalırken yeni v77 yalnız yeni rezervasyonu `(15, v77)` fiyatladı.

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

**Karar: düzeltme gerekiyor.** HTTP rotaları ve kurallı servis akışları saldırıları engelliyor;
ancak W20-F1–F3 entitlement bütünlüğünü iç servis/DB-yazarı sınırında yalnız çağıran kodun doğru
davranmasına bırakıyor. Bu test oturumu uygulama veya test kaynak kodunu değiştirmedi.

### Saldırı 1 — 2026-08-03, `sp-verify`

| Saldırı | Sonuç | Kanıt / sınır |
|---|---|---|
| İki gerçek HTTP `create_project` transaction'ı ile son kredi yarışı | Engellendi | Yeni tenant, marka, ürün, CTA ve tam 5 krediyle iki ayrı `Idempotency-Key` altında gerçek Uvicorn HTTP isteği `asyncio.gather` ile gönderildi: HTTP `[201, 402]`; ikinci gövde `ENTITLEMENT_INSUFFICIENT_CREDITS`. Doğrudan SQL: `consume_count=1`, türetilen bakiye `0` (negatif değil). Önceki `reserve`-seviyesi yarışı bu sonucun yerine sayılmadı. |

### Saldırı 2 — 2026-08-03, `sp-verify`

| Saldırı | Sonuç | Kanıt / sınır |
|---|---|---|
| Teknik hata sonrası paralel çifte iade | Servis yolunda engellendi | Yeni tenantta 5 kredi → reserve → `ABANDONED/PROJECT_RENDER_FAILED` ile `released`; ardından aynı `settle(RELEASE)` iki ayrı transaction'dan `asyncio.gather` ile tekrarlandı. SQL sonucu: `refunds=1`, defter toplamı `5` — verilen krediyi aşmadı. |
| İptal ucu + süpürücüyle aynı reservation'a ikinci iade | **Tamamlanamadı** | Bu çapraz yol gerçek `content_project` ve onun iptal/süpürücü bağlamını gerektirir. Saldırı 1'deki proje oluşturma HTTP veri hazırlığı tamamlanamadığından bu alt yol çalıştırılmadı; yukarıdaki servis-replay sonucu onun yerine kanıt sayılmaz. |

### Saldırı 3 — 2026-08-03, `sp-verify`

| Saldırı | Sonuç | Kanıt / sınır |
|---|---|---|
| Doğrudan büyük negatif `consume` | Engellendi | Yeni tenantın 5 kredisine karşı, geçerli reservation FK'siyle `consume -99` ham SQL yazısı `IntegrityError` verdi; SQL toplamı `5` kaldı. Trigger devre dışı bırakılmadı. |
| Append-only satırı `UPDATE` veya `DELETE` | Engellendi | Mevcut grant satırını güncelleme ve silme denemelerinin ikisi de `IntegrityError`; bakiye değişmedi. |
| Aynı transaction'da çoklu negatif satır / `COPY` | Engellendi | Yeni iki geçerli `reserved` reservation ve ham `grant +5` ile aynı transaction içinde iki `consume -5` yazıldı: ikinci satırda `IntegrityError`, transaction geri alındı. Aynı iki kayıt `asyncpg.copy_records_to_table` ile `COPY` olarak da denendi: `CheckViolationError`. Son SQL: bakiye `5`, mevcut `consume` sayısı yalnız önceki gerçek proje tüketimi olan `1`; toplu yazım yeni harcama bırakmadı. |

### Saldırı 4 — 2026-08-03, `sp-verify`

| Saldırı | Sonuç | Kanıt |
|---|---|---|
| İkinci tokenla gerçek başka-tenant balance / ledger / reservations / grant | Engellendi — PM kararıyla kapandı | Dört uç da `404 BUSINESS_NOT_FOUND` verdi; uydurma business_id ile status ve hata kodu birebir aynı, gerçek `reservation_id` dönmedi. RFC 9457 `instance` içindeki istek URI'si ve saldırganın zaten gönderdiği `business_id`, PM kararına göre varlık ifşası/sızıntı değildir. |
| Gerçek reservation kimliği sızıntısı | Engellendi | Aynı hata gövdelerinde gerçek `reservation_id` bulunmadı. |

### Saldırı 5 — 2026-08-03, `sp-verify`

| Saldırı | Sonuç | Kanıt / sınır |
|---|---|---|
| Değişmeyen timeline ile saf yeniden render ve sürekli QC-failed otomatik retry | **Tamamlanamadı** | Gerçek `PREVIEW_READY` projesi, geçerli timeline ve render/QC artefakt zinciri bu turda kurulamadı. SQL'de ikinci consume/reservation veya retry başına kredi sonucu ölçülmedi; önceki iç-servis bulgusu bu saldırının kanıtı değildir. |

### Saldırı 6 — 2026-08-03, `sp-verify`

| Saldırı | Sonuç | Kanıt / sınır |
|---|---|---|
| Proje rezervasyonu sonrası puan sürümü değişimi, iade ve fiyatlanmamış kombinasyon | **Tamamlanamadı** | Bu saldırının gerçek `create_project` + `PREVIEW_READY`/iade akışı için gerekli CTA/timeline verisi kurulamadı. Eski/yeni sürüm bakiyesi veya iade fiyatı bu turda ölçülmedi. |

### Saldırı 7 — 2026-08-03, `sp-verify`

| Saldırı | Sonuç | Kanıt / sınır |
|---|---|---|
| Owner olmayan editor/approver/viewer grant'i ve dolaylı grant varyantları | **Kısmen tamamlandı** | `admin` rolüyle yapılan bağımsız grant denemesi önceki bu oturum kaydında `403 INSUFFICIENT_PERMISSION` verdi. Editor/approver/viewer token matrisi ile replay, negatif/sıfır/aşırı/ondalıklı grant varyantları bu turda henüz çalıştırılmadı; tamamlanmış sayılmaz. |

### Saldırı 8 — 2026-08-03, `sp-verify`

| Saldırı | Sonuç | Kanıt / sınır |
|---|---|---|
| `PREVIEW_READY` sonrası iptal, süpürücü ve doğrudan settled-reservation iadesi | **Tamamlanamadı** | Gerçek başarılı proje/`PREVIEW_READY` zinciri kurulamadığından iptal ve süpürücü yolları ölçülmedi. Saldırı 2'deki `released` reservation replay'i, settled reservation için kanıt değildir. |
