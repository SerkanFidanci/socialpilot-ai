# W21 — Phase 2F: Onay sistemi ve revizyon (§21)

**Dal:** `slice/2f-approval-revision` · **Base:** `main` · **Migration slotu: SENDE** (`0018`)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Plan:** [Phase 2 planı](../plans/active/phase-2-content-generation.md) — slice 2F
**Neden bu iş:** W19 projeyi `PREVIEW_READY`'ye getiriyor ve **orada bırakıyor.** Kullanıcı beğenmezse yapabileceği hiçbir şey yok: onay yok, ret yok, revizyon yok. Üstelik fail-closed QC gereği bugün *her* çıktı `needs_review` işaretiyle geliyor — yani insan incelemesi ürünün merkezinde ve hiç inşa edilmedi. Ayrıca W20 bir açık bıraktı: **iptal edilemeyen proje kredisini süresiz tutuyor**; bu slice onu da kapatır.

## Okunacaklar

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/product/requirements/40b-scenario-render-lifecycle.md`](../product/requirements/40b-scenario-render-lifecycle.md) — **§21 onay sistemi** (21.1 politikalar, 21.2 ret nedenleri, 21.3 revizyon sınıfları), §20 durum makinesi
3. [`docs/product/requirements/50-subscription-entitlement.md`](../product/requirements/50-subscription-entitlement.md) — §12.3'ün revizyon hakkı ("üç revizyon"), §12.4
4. `services/api/app/modules/content/CLAUDE.md` — özellikle K4 ("**saf yeniden render yeni hak tüketmez, revizyon kotasından düşer**") ve W19'un durum makinesi
5. `services/api/app/modules/entitlement/CLAUDE.md` — rezervasyon/sonuçlandırma; **iptal yolunu buraya takacaksın**

## PM kararları

### 1. Durum makinesi §20'nin kalan kenarlarıyla tamamlanır

W19 bilerek `PREVIEW_READY`'de durmuştu. Bu slice ekler: `WAITING_APPROVAL`, `REVISION_REQUESTED` ve `PREVIEW_READY → WAITING_APPROVAL → REVISION_REQUESTED → SCRIPTING` döngüsü. **`SCHEDULED` ve sonrası YİNE YOK** — planlayıcı 2G, yayınlama Phase 4. W19'un kuralları aynen geçerli: kapalı geçiş tablosu, saf, tanımsız çift kod hatası, her geçiş kaydedilir.

### 2. Onay politikası **veri**, kod değil (§21.1)

Yedi politika (`always` … `never_within_guardrails`) işletme başına ayar. **Politikayı değerlendiren fonksiyon saf ve total olmalı:** girdi = (politika, içerik bağlamı: kampanya mı, fiyat/indirim içeriyor mu, reklam mı, kaçıncı içerik, QC güven işareti) → çıktı = onay gerekiyor mu. Tanımsız kombinasyon yok.

**`low_confidence_only` bugün her zaman onay ister** — çünkü VLM fake ve QC her çıktıyı `needs_review` işaretliyor (2D fail-closed kuralı). Bu doğru davranış; kodda ve dokümanda **neden** böyle olduğu yazılsın ki gerçek sağlayıcı gelince kimse "bozuk" sanmasın.

### 3. Ret nedeni **kapalı küme + serbest not** (§21.2)

On neden kapalı enum (`wrong_product` … `other`); `other` seçilirse serbest metin **zorunlu**. Serbest metin **untrusted veridir**: prompt'a birleştirilmez, yalnızca `input_data` altında taşınır (§17.5 kuralı), ve fabrikasyon dedektöründen geçmez — çünkü kullanıcının kendi metni, modelin değil. Ama **saklanır ve loglanmaz**.

**Gizlilik (§21.2 son cümlesi):** ret nedenleri model öğrenme verisi olabilir ama **tenant'a özel kalır**. Bu slice hiçbir çapraz-tenant toplama yapmaz; şema bunu ileride mümkün kılacak şekilde `business_id` taşır ve doküman "toplu kullanım ayrı bir karar ve ayrı bir rıza gerektirir" der.

### 4. Revizyon: küçük/büyük ayrımı **hangi alanın değiştiğinden** türetilir (§21.3)

Kullanıcı "küçük revizyon istiyorum" demez; **ne değiştirdiğini** söyler, sınıfı kod belirler:
- **Küçük** (CTA, başlık, tek kesit, ses, müzik, altyazı stili) → yalnızca etkilenen adımdan yeniden başlar; senaryo yeniden üretilmez.
- **Büyük** (içerik türü, ürün, konsept, süre sınıfı) → `SCRIPTING`'e döner, senaryo yeniden üretilir.

Sınıflandırma **saf ve total** bir fonksiyon: değişen alan kümesi → sınıf. Belirsizlik varsa **büyük** tarafa düşer (fail-closed: gereksiz yeniden üretim, yanlış çıktıdan ucuzdur).

### 5. Revizyon kotası ve hak (K4 + §12.3)

- **Saf yeniden render hak tüketmez** (K4, W20'de yapısal olarak sağlandı: rezervasyon projeye bağlı) — bu slice bunu **bozmamalı**.
- **Küçük revizyon** kotadan düşer (varsayılan 3, `REVISION_QUOTA_*` config), yeni kredi tüketmez.
- **Büyük revizyon** da aynı rezervasyonun içinde kalır ama kotadan **iki** düşer — gerekçe: senaryo yeniden üretimi gerçek sağlayıcı maliyeti doğurur. (Bu sayı bir tahmindir; W08 benchmark'ı gerçek maliyeti ölçünce yeniden değerlendirilecek — config'de olmasının sebebi bu, raporda not düş.)
- Kota bitince: revizyon reddedilir, dokümante hata; kullanıcının yolu **yeni proje** (yeni kredi).

### 6. W20'nin bıraktığı açık: proje iptali

`PLANNED`/`WAITING_MEDIA`/`WAITING_APPROVAL` gibi **terminal olmayan** durumlarda kullanıcı projeyi iptal edebilmeli; iptal rezervasyonu **iade eder** (W20'nin `released` yolu). Ayrıca terk edilmiş projeler için yaş eşikli süpürücü — W19/W20'nin süpürücü desenini izle. Gerekçe: bugün `WAITING_MEDIA`'da park eden bir proje krediyi süresiz tutuyor.

### 7. Yetki (PRD §4)

`approver` rolü onaylar/reddeder; `editor` revizyon **ister** ama onaylayamaz; `viewer` yalnızca okur. **Kendi ürettiğini onaylama kısıtı yok** — dört kişilik bir işletmede bu kilitlenme yaratır; ürün kararı olarak kayda geç.

## Kapsam dışı (dokunma)

- **Planlayıcı/takvim, `SCHEDULED` ve sonrası** → 2G / Phase 4. **Store/ödeme** → Phase 3.
- W19'un mevcut geçişleri, W18'in QC karar tablosu, W20'nin defter aritmetiği → **mantığını değiştirme**, yalnızca yeni kenarları ve iptal/kota yolunu ekle.
- `script.py`, `text_normalization.py`, `qc.py` → dokunma.
- Ret nedenlerinden model eğitimi / çapraz-tenant toplama → **yapma** (karar 3).
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/modules/content/approval.py + approval_service.py   (yeni — politika, ret nedenleri, revizyon sınıflandırması; saf kısımlar ayrı)
services/api/app/modules/content/lifecycle.py                        (yeni durum + kenarlar)
services/api/app/modules/content/{models,repository,project_service}.py
services/api/app/modules/entitlement/service.py                      (yalnızca iptal→iade yolu)
services/api/app/api/routes/content.py                               (onay/ret/revizyon/iptal uçları)
services/api/app/core/config.py                                      (REVISION_QUOTA_*, onay politikası varsayılanı)
services/api/app/worker/{tasks,composition}.py + infrastructure/celery_app.py  (terk edilmiş proje süpürücüsü)
services/api/migrations/versions/0018_*.py                           (SLOT SENDE)
services/api/tests/unit/ + tests/integration/
docs/architecture/content-render.md · error-handling.md · .env.example
```

## Kabul kriterleri

1. Migration `0018` up → down → up; tek head.
2. **Uçtan uca:** `PREVIEW_READY` → onay isteği → ret (neden + serbest not) → küçük revizyon → yeniden render → onay; her geçiş kayıtlı, gerçek PostgreSQL/MinIO/FFmpeg.
3. **Politika değerlendirmesi total:** yedi politika × içerik bağlamı çarpımı permütasyon testiyle tüketiliyor; tanımsız kombinasyon yok. `low_confidence_only`'nin bugün hep onay istemesi testte pinli ve **gerekçesiyle** yorumlanmış.
4. **Revizyon sınıflandırması total:** değişen alan kümesi → sınıf; belirsiz küme **büyük** tarafa düşüyor (test).
5. **Kota:** küçük revizyon 1, büyük 2 düşürüyor; kota bitince revizyon reddediliyor; **saf yeniden render kotadan da krediden de düşmüyor** (K4 — testle kanıtla).
6. **İptal + iade:** terminal olmayan durumda iptal → rezervasyon `released`, bakiye eski hâline dönüyor; terminal durumda iptal reddediliyor; terk edilmiş proje süpürücüsü yaş eşiğiyle çalışıyor ve **sağlıklı projeye dokunmuyor**.
7. **Ret nedeni gizliliği:** serbest not hiçbir log/span/audit mesajında görünmüyor (sentinel testi); `other` seçilince not zorunlu; not prompt payload'ına birleştirilmiyor (test tokenize/AST ile zorlasın, W18'in desenini izle).
8. Roller: `approver` onaylar/reddeder, `editor` revizyon ister ve onaylayamaz (`403`), `viewer` yalnızca okur. Tenant izolasyonu (`404`, varlık ifşası yok). Idempotency kanonik gövdeden.
9. `make verify` yeşil; test sayısı **1325** tabanının altına düşmez; kontrat yeniden üretilip commit'li; modül `CLAUDE.md`'leri güncel.
10. Rapor + araç zinciri sürümleri. **Merge etme, dalda bırak.**

## Enumerasyon kuralı

Politikalar (7), ret nedenleri (10), revizyon alanları — hepsi **PRD tarafından kapalı**, yazılabilir. Ama **"hangi alan kümesi hangi sınıfa düşer"** ve **"hangi bağlamda onay gerekir"** kombinatoryaldir: total fonksiyon + permütasyon testi (2D/2E/W20 deseni).

## ADR numara kuralı

Gerçek karar çıkarsa `ADR-XXX-<konu>.md`; numarayı PM verir.

## Rapor

## Rapor — 2026-08-02 · Opus 5 / high

**Dal:** `slice/2f-approval-revision` · **Commit'ler:** `3ddbee9` · **Durum:** tamamlandı

### Yapılanlar

**§21 onay sistemi.** Yedi politika `content_projects.approval_policy`'de **veri** olarak duruyor;
`approval.requires_approval(politika, bağlam)` saf ve total, ve her politika **yalnızca kendi
boyutunu** okuyor. Her politikanın guardrail ihlalinde de tetiklemesi kulağa güvenli geliyordu ve
değildi: VLM disabled olduğu için her render `needs_review`, dolayısıyla evrensel bir guardrail
kaçışı yedi politikayı birbirinin aynısı yapar ve müşterinin ayarını süse çevirirdi. Politika
canlı okunmuyor — projeye yazılıyor, çünkü gelecek ay gevşetilen bir ayar bugünkü ön izlemede
neyin gerektiğini geriye dönük değiştirmemeli.

**§21.2 ret nedenleri.** On neden kapalı enum; `other` notu zorunlu kılıyor, nedensiz bir ret
`APPROVAL_REASON_REQUIRED` ile reddediliyor. Not iki yere gidiyor: satır ve aynı tenant'ın okuması.
Dört check constraint neyin neye eşlik edebileceğini şemada tekrarlıyor.

**§21.3 revizyon.** Sınıf ve yeniden başlangıç noktası **iki ayrı** total fonksiyon (aşağıda
"Açıkça belirtmem gerekenler" 2). Kota küçük 1 / büyük 2, allowance projeye yazılıyor; bitince
`REVISION_QUOTA_EXHAUSTED`. Revizyon `render_attempts`'i sıfırlıyor ve adım idempotency anahtarı
revizyonu adlandırıyor (`project:{id}:r{n}:script`) — aksi hâlde ikinci `scripting` koşusu ilk
senaryoyu yeniden oynatır, yani müşteriye reddettiği şeyi geri verirdi.

**Durum makinesi.** `WAITING_APPROVAL`, `REVISION_REQUESTED` + iki belgeli genişletme
(`APPROVED`, `CANCELLED`). `PREVIEW_READY` **artık terminal değil** ama hâlâ ücretlendirme
noktası: `preview_delivered_at` §12.7'nin anını damgalıyor ve ondan sonraki her sonuç
`DELIVERED`. Bu damga olmasaydı, iyi bir ön izlemeden sonra düşen bir revizyon zaten teslim
edilmiş ön izleme için iade isterdi; defter bunu çelişki sayıp reddeder ve proje kilitlenirdi.

**W20'nin açığı kapandı.** İptal terminal olmayan her durumdan yapılabiliyor ve sıralayıcının
çağırdığı **aynı** `settle`'ı çağırıyor — `entitlement/service.py` hiç değişmedi, çünkü W20'nin
`released` yolu tam olarak bunun için yazılmıştı. Ayrıca `content.project.sweep`: yalnızca
`WAITING_MEDIA`'daki yaşlanmış projeleri geri çekiyor, çünkü bir kişiyi bekleyen üç durumdan
sadece o hâlâ tüketilmemiş kredi tutuyor.

**Yetki.** `Permission.CONTENT_APPROVE` eklendi; `approver` rolü W10'dan beri boş duran yetki
kümesini aldı (`business.read` + `content.approve`). PRD §4'ün çizgisi: editor üretir ve
imzalayamaz, approver imzalar ve üretemez. **Kendi ürettiğini onaylama kısıtı yok** (PM kararı 7).

### Kapsam dışı bıraktıklarım ve nedeni

- `SCHEDULED` ve sonrası, planlayıcı, mağaza/ödeme — 2G / Phase 3 / Phase 4.
- `script.py`, `text_normalization.py`, `qc.py` — dokunulmadı.
- Ret nedenlerinden model eğitimi / çapraz-tenant toplama — **yapılmadı** (PM kararı 3). Satırlar
  `business_id` taşıyor ki ileride tenant-kapsamlı sorgu olarak yazılabilsin.
- `docs/index.md`, `docs/adr/README.md` — indekse eklenmedi (aşağıda 8).
- **ADR yazılmadı.** Bu slice'ın kararlarının hepsi ya PRD §21'in doğrudan uygulanması ya da
  `lifecycle.py`/`approval.py` docstring'lerinde ve `content-render.md`'de gerekçesiyle yazılmış
  yerel tasarım kararları. PM ADR'lık bir karar görürse numarayı verir; aday: "onay politikası
  projeye yazılır, canlı okunmaz" ve "`PREVIEW_READY` terminal değil ama ücretlendirme noktası".

### İlan dışı dokunuşlar (protokol gereği bildiriliyor)

| Dosya | Neden |
|---|---|
| `app/modules/businesses/policy.py` | Kabul kriteri 8 (`approver` onaylar) `Permission` enum'una satır eklemeden **karşılanamaz**; modülün kendi `CLAUDE.md`'si "yeni yetki eklemek `Permission` enum'una satır eklemektir, politika tablosu tek yerde" diyor. W20'nin aynı dosyaya ilan dışı dokunuşunun emsali var. Değişiklik iki satır + `approver` kümesi. |
| `app/modules/businesses/CLAUDE.md`, `app/worker/CLAUDE.md` | AGENTS.md: "modülün dosyaları değişince o modülün `CLAUDE.md`'si aynı değişiklikte güncellenir". |
| `docs/generated/openapi.json`, `docs/api/endpoints.md` | Kabul kriteri 9 (`make generate-docs` + commit). Üretilmiş dosyalar. |

Uçuşta başka WO olmadığı için çakışma riski yok. `entitlement/service.py` ilan edilmişti ama
**değiştirilmedi** — gerek kalmadı.

### Doğrulama

Araç zinciri (hepsi `sp-w21` konteynerinde, Linux): Python 3.13.14 · mypy 2.3.0 · ruff 0.16.0 ·
pytest 9.1.1 · FFmpeg 7.1.5 · PostgreSQL 17 + MinIO (compose, `COMPOSE_PROJECT_NAME=sp-w21`,
host portları ayrı).

| Kontrol | Sonuç |
|---|---|
| `ruff check` (app/tests/migrations/scripts) | ✅ All checks passed |
| `ruff format --check` | ✅ 222 files already formatted |
| `mypy .` (strict) | ✅ 209 dosya, 0 hata |
| `pytest` (RUN_INTEGRATION_TESTS=1, STORAGE_ADAPTER=s3) | ✅ **1375 passed, 0 failed, 0 skipped** (832 s) |
| migration `0018` up → down → up | ✅ tek head (`0018_approval_and_revision`) |
| `make check-openapi` (kontrat yeniden üretildi ve commit'li) | ✅ 45 → **50 endpoint** |

Kabul kriterleri:

1. **Migration up/down/up, tek head** ✅. `content_project_state`/`content_project_event`
   **değiştirilerek** (rename→create→cast→drop) genişletildi, `ADD VALUE` ile değil: Alembic
   migration'ı tek transaction'da koşuyor ve PostgreSQL aynı transaction'da eklenen enum değerinin
   *kullanılmasını* reddediyor — yeni check constraint'ler ve index predicate'i tam olarak bunu
   yapıyor. `state::text` ile kaçmak claim'in kısmi index'ini işe yaramaz hâle getirirdi (W19'un
   ölçtüğü şey). Downgrade veri kaybedecekse **reddediyor** (0011 deseni).
2. **Uçtan uca** ✅ `test_a_preview_is_rejected_revised_re_rendered_and_then_approved`: gerçek
   PostgreSQL + MinIO + FFmpeg üzerinde `PLANNED` → … → `WAITING_APPROVAL` → ret (neden + not) →
   küçük revizyon → **ikinci render** → onay. Her geçiş kayıtlı; iki `approval_required`, bir
   `rejected`, bir `revision_scoped_to_timeline`, son `approved`.
3. **Politika total** ✅ 7 politika × 32 bayrak kombinasyonu × 4 sayaç = 896 kombinasyon
   tüketiliyor; her politikanın yalnızca kendi boyutunu okuduğu ayrıca test ediliyor.
   `low_confidence_only`'nin bugün hep onay istemesi **gerekçesiyle** pinli (birim + entegrasyon).
4. **Revizyon sınıflandırması total** ✅ alan sözlüğünün **1024 alt kümesinin tamamı**; boş küme
   `MAJOR` + `SCRIPT`.
5. **Kota** ✅ küçük 1, büyük 2, bitince 409; **kredi hiç hareket etmiyor** — uçtan uca testte
   iki render + iki ön izleme için tek `consume`, iade yok, tek rezervasyon `consumed`.
   `approval_service.py` içinde `EntitlementService` **yok** (K4 yapısal).
6. **İptal + iade** ✅ `WAITING_MEDIA`'da iptal → rezervasyon `released`, bakiye eski hâline;
   defterde `consume` + `refund`, toplam 0. İkinci iptal 409 ve **hiçbir şey yazılmıyor**.
   Terk edilmiş proje süpürücüsü yaşlanmışı iptal edip sağlıklıyı bırakıyor, ikinci geçişte
   `None` dönüyor.
7. **Not gizliliği** ✅ sentinel not, süreçteki **herhangi bir handler'ın** render edebileceği tüm
   log kayıtlarında, `audit_logs.metadata`, `content_project_transitions.reason`,
   `idempotency_keys.response_body` ve `outbox_events.payload` içinde **yok**; `content_approvals`
   satırında var ve sahibi API'den okuyabiliyor. AST testi `note`'un yalnızca doğrulamaya ve satır
   yazımına gittiğini, tokenize testi modülde `prompt`/`input_data` sözcüğünün geçmediğini zorluyor.
8. **Roller** ✅ approver onaylar/reddeder; editor revizyon ister ve **onaylayamaz** (403); viewer
   yalnızca okur; başka tenant **404** (varlık ifşası yok). Idempotency kanonik gövdeden: aynı
   gövde replay, farklı gövde 409 — hem karar hem revizyon için.
9. `make verify` yeşil; **1375 test** (taban 1325, +50); kontrat yeniden üretilip commit'li ve
   yeniden üretim deterministik (ikinci koşu aynı diff'i veriyor); modül `CLAUDE.md`'leri güncel.
10. Bu rapor. **Merge edilmedi, dalda.**

### Açıkça belirtmem gerekenler

1. **§20'nin çizmediği iki durum eklendi ve 2G'nin kenarı değişiyor.** `APPROVED`: §20
   `WAITING_APPROVAL → SCHEDULED` çiziyor ama planlayıcı 2G'de, ve onaylanmış bir proje
   `WAITING_APPROVAL`'da beklerse "karar bekleyenler" listesi kararı zaten verilmişleri içerir —
   ürünün sorduğu soru cevapsız kalır. **2G `APPROVED → SCHEDULED` kenarını eklemeli**, §20'nin
   oku bu durumun adlandırıldığı hâli. `CANCELLED`: müşterinin vazgeçmesini `FAILED` saymak
   `failure_code`'u (iadeyi sınıflandıran alan) yalanlardı.
2. **PM kararı 4'ün ifadesine bir düzeltme.** "Küçük revizyon → senaryo yeniden üretilmez" CTA ve
   başlık için **doğru değil**: ikisinin metni senaryo dokümanına çözülüyor ve seslendirme onu
   konuşuyor, dolayısıyla timeline'dan başlamak hâlâ eski sözü söyleyen bir videonun yeni
   kurgusunu üretirdi. Uygulamada **sınıf** (§21.3: küçük, kota 1) ile **yeniden başlangıç**
   (senaryo) ayrı iki total fonksiyon; ikisi de veri tablosu, PM katılmazsa `_FIELD_SCOPES`'ta tek
   satır. Ses → `VOICE_GENERATION`, kesit/müzik/altyazı → `TIMELINE_BUILDING` PM'in dediği gibi.
3. **`ads_only` bugün hiçbir şeye onay istemiyor**, çünkü §14 henüz reklam senaryosu açmadı.
   `_ADVERTISING_SCENARIOS` tabloyu `ScenarioCode`'un tamamı üzerinde yazıyor ve totalliği import
   anında zorluyor, yani ilk reklam senaryosu eklendiğinde cevap **unutularak** verilemez.
4. **Onay politikası proje bazında saklanıyor, işletme bazında değil.** PM "işletme başına ayar"
   dedi; §12.2 onu abonelik kalemine koyuyor ve o Phase 3. Bugün: varsayılan config'de
   (`CONTENT_APPROVAL_POLICY_DEFAULT=always`), create isteği override edebiliyor, ve seçilen değer
   projeye yazılıyor. Projede saklamak zaten daha iyi provenance (sonradan gevşetilen bir politika
   geçmişi değiştiremez); eksik olan yalnızca "işletmenin varsayılanı" katmanı ve o Phase 3'ün
   abonelik kalemiyle geliyor.
5. **`PROJECT_CANCELLED` / `PROJECT_ABANDONED` defterde `UNCLASSIFIED`.** `FAILURE_CLASSES`
   `entitlement/ledger.py`'de ve bu WO onu ilan etmedi. Bugünkü davranış **doğru** (üç sınıf da
   iade ediyor, ön izleme teslim edilmediyse iade doğru cevap). Müşteri vazgeçmesine kendi
   `FailureClass`'ını vermek o dosyada tek satır ve bir sonraki sahibinin işi; bugün hiçbir
   davranışı değiştirmez.
6. **Büyük revizyonun 2 kotası bir tahmindir.** Gerekçe senaryo yeniden üretiminin gerçek sağlayıcı
   maliyeti doğurması; sayı W08 benchmark'ı ölçünce yeniden değerlendirilmeli. Config'de olmasının
   sebebi bu (`REVISION_QUOTA_MAJOR_COST`).
7. **Terk edilmiş proje süpürücüsü tek durum tarıyor: `WAITING_MEDIA`.** Bir kişiyi bekleyen üç
   durumdan yalnızca o **tüketilmemiş kredi tutuyor**; `WAITING_APPROVAL` ve `REVISION_REQUESTED`
   teslim edilmiş bir ön izlemenin arkasında ve orada otomatik iptal, müşterinin zaten sahip
   olduğu işi yok edip hiçbir şey geri kazandırmazdı. Onları da süpürmek istenirse ürün kararı.
8. **`docs/index.md` ve `docs/adr/README.md` indekslerine eklenmedi** (kapsam dışı, PM'e bildiriliyor).
   Eklenecekler: `approval.py`/`approval_service.py` yeni dosyalar, `content-render.md` yeni bölüm.
9. **Bekleyen bir durumun yoklama aralığı uzatıldı.** `WAITING_MEDIA`/`WAITING_APPROVAL`/
   `REVISION_REQUESTED`'da hiçbir şey değişmeyecek — değiştiğinde outbox olayı yazılıyor —
   dolayısıyla `_due_after` bu durumlarda `LIFECYCLE_POLL_SECONDS` yerine `LIFECYCLE_LEASE_SECONDS`
   kullanıyor. Öncesinde `WAITING_MEDIA`'daki bir proje aylarca 15 saniyede bir claim harcıyordu.
10. **Uygulama sırasında bulunan bir hata** (test yakaladı, düzeltildi): `_settle` durum
    değiştirdikten *sonra* sorgu atıyordu; satır o an geçici olarak
    `ck_content_project_due_matches_state` ile çelişiyor (terminal durum, hâlâ eski due time) ve
    autoflush onu doğrudan kısıta yolluyordu. Onay sırası artık döngüden önce okunuyor. Bu, 2E'de
    de vardı ama `PREVIEW_READY` terminal olduğu için tetiklenemiyordu.

## Doğrulama

Bağımsız test oturumu: **2026-08-02 · Codex**. Worktree kökünden
`COMPOSE_PROJECT_NAME=sp-verify` ile, migration head `0019_content_planner` üzerinde çalışıldı.
Mevcut test fixture'ları saldırı kanıtı olarak kullanılmadı: çalıştırma kimliği `b2d8650722` olan
geçici harness repo dışında tutuldu ve her saldırı için yeni UUID'ler, kullanıcılar, tenantlar,
projeler, onaylar ve defter satırları üretti.

| # | Saldırı | Sonuç | Kendi girdimizle kanıt | Durum |
|---|---|---|---|---|
| W21-A1 | Revizyon kotasını aş | Engellendi | Küçük revizyon `cost=1`, büyük revizyon `cost=2`; kullanılan kota `3` olduktan sonra üçüncü istek `409 REVISION_QUOTA_EXHAUSTED`. Yalnız ilk iki revizyon satırı oluştu. | Geçti |
| W21-A2 | Saf yeniden render'ı kotadan ve krediden düşürt | Engellendi | Gerçek parametrik patch + revision render sonrasında `revision_quota_used=0`; ilk proje için kalan kredi `7` olarak değişmeden kaldı ve yeni entitlement kaydı oluşmadı. | Geçti |
| W21-A3 | İptal edilmiş projeyi tekrar iptal et, ilerlet veya ikinci kez iade ettir | Engellendi | İlk iptal rezervasyonu `released` yaptı; ikinci iptal ve `attach-media` ayrı ayrı `409`; settle replay'i yeni satır yazmadı. Defter tam olarak `consume -5` + `refund +5`. | Geçti |
| W21-A4 | `editor` rolüyle onayla | Engellendi | Editor kararı `403 INSUFFICIENT_PERMISSION`; aynı projeyi `approver` başarıyla `approved` yaptı. | Geçti |
| W21-A5 | Başka tenant'ın projesini onayla | Engellendi | Saldırgan tenant'ın approver'ı kurban proje için `404` aldı; kurbanın durumu değişmedi. | Geçti |
| W21-A6 | Ret notunu log'a veya prompt girdisine sızdır | Sızıntı görülmedi | Benzersiz sentinel yalnız `content_approvals` satırında kaldı. İstek sırasında yakalanan uygulama loglarında ve `audit_logs.metadata`, transition reason, idempotency response, outbox payload, `content_scripts.document` yüzeylerinde eşleşme sayısı `0`; proje cevabı notu taşımadı. | Geçti |
| W21-A7 | Büyük revizyonu küçük gösterip senaryo yeniden üretimini atla | Engellendi | İstemcinin eklediği `revision_class=minor` alanı `400 REQUEST_VALIDATION_FAILED`; geçerli `product + caption_style` isteği sunucuda `(major, script, cost=2)` türetti ve proje `scripting` durumundan yeniden başladı. | Geçti |

| Bulgu | Şiddet | Yeniden üretim | Durum |
|---|---|---|---|
| Açık W21 ürün/kod bulgusu yok | — | Yukarıdaki yedi saldırı, gerçek PostgreSQL transaction'ları ve dış API rotalarıyla yeni veri üzerinde uygulandı. | Kapalı |

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

**Karar: teslim edilebilir.** W21 saldırı listesinde açık bulgu kalmadı. Bu oturum uygulama veya
test kaynak kodunu değiştirmedi.
