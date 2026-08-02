# W19 — Phase 2E (birinci yarı): İçerik projesi yaşam döngüsü

**Dal:** `slice/2e-content-lifecycle` · **Base:** `main` · **Migration slotu: SENDE** (`0016`)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Plan:** [Phase 2 planı](../plans/active/phase-2-content-generation.md) — slice 2E
**Neden bu iş:** 2A–2D beş ayrı yetenek üretti (senaryo, seslendirme, timeline, render, QC) ve **hiçbiri diğerini tanımıyor.** Bugün bir "içerik projesi" diye bir kayıt yok: kullanıcı senaryo üretir, ayrı bir çağrıyla seslendirir, ayrı bir çağrıyla timeline yazar, ayrı bir çağrıyla render ister. PRD §20'nin durum makinesi hiçbir yerde yok, dolayısıyla "bu içerik nerede kaldı?" sorusunun cevabı da yok. Bu slice o iskeleti kurar ve 2D'nin verdiği kararları **eyleme** bağlar.

## Kapsam bölünmesi (PM kararı)

Faz planındaki 2E "yaşam döngüsü + entitlement" idi; **ikiye bölündü.** Bu WO **yalnızca yaşam döngüsü**. Entitlement/kota tüketimi ayrı bir WO (W20) olacak, çünkü (a) hak tüketimi para modeline (K1, kullanıcı kararı) bağlı ve (b) tek WO'ya sığdırmak migration slotunu ve dosya sahipliğini şişirir. **Bu slice hak tüketmez, kota kontrolü yapmaz** — sadece durum makinesini ve iş akışını kurar; W20 tüketim noktalarını buraya takar.

## Okunacaklar

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/product/requirements/40b-scenario-render-lifecycle.md`](../product/requirements/40b-scenario-render-lifecycle.md) — **§20 durum makinesi**, §19.4 QC, §14.8 akış
3. [`docs/plans/active/phase-2-content-generation.md`](../plans/active/phase-2-content-generation.md) — §2 kararlar
4. `services/api/app/modules/content/CLAUDE.md` — değişmezlerin tamamı
5. [W18](W18-automatic-qc.md) — **takip raporundaki claim ölçümü** (aşağıda 4. madde)
6. [W15](W15-tts-voiceover.md) — "Açıkça belirtmem gerekenler" 1 ve 4 (voiceover miksajı, `pending` süpürücü)

## PM kararları

### 1. Durum makinesi PRD §20'nin kendisidir, kısaltılmaz

`content_projects` tablosu ve kapalı bir durum enum'u: `PLANNED`, `WAITING_MEDIA`, `ANALYZING`, `SCRIPTING`, `VOICE_GENERATION`, `TIMELINE_BUILDING`, `RENDERING`, `QUALITY_CHECK`, `PREVIEW_READY`, `FAILED`, `RETRYING`. **Bu slice'ta `WAITING_APPROVAL`/`REVISION_REQUESTED`/`SCHEDULED` ve sonrası YOK** — onlar 2F/Phase 4. Geçiş tablosu **kapalı ve saf**: tanımsız geçiş kod hatasıdır, veri hatası değil. Her geçiş transactional kaydedilir (§20 son cümlesi) — kim, ne zaman, hangi sebeple; audit'e değil, **projenin kendi geçiş tablosuna** (sorgu yüzeyi lazım: "bu proje nerede takıldı?").

### 2. Proje mevcut yeteneklerin **sahibi** değil, **sıralayıcısıdır**

Senaryo, seslendirme, timeline, render, QC servisleri **değişmez**; proje onları çağıran ve sonuçlarını durum makinesine bağlayan katmandır. Mevcut uçlar (tekil senaryo üretimi vb.) çalışmaya devam eder — proje bağlamı **opsiyonel** bir referanstır. Gerekçe: 2A–2D'nin testleri ve kontratı bozulmadan üstüne katman eklenmeli; "her şeyi projeye zorla" yeniden yazma olur ve bu slice'ın kapsamı değil.

### 3. QC kararı burada eyleme dönüşür — **sınırlı** olarak

2D karar verdi, eylemi 2E'ye bıraktı. Bu slice:
- `passed` → `PREVIEW_READY`.
- `needs_review` → `PREVIEW_READY` ama **insan incelemesi işaretiyle** (2F onay akışı bunu okuyacak). Kullanıcıya gösterilir, çünkü fail-closed kural gereği bugün *her* render `needs_review` (VLM fake) — bunu `FAILED` saymak ürünü durdurur.
- `failed` → `retry_render` önerisi varsa **sınırlı** otomatik yeniden render, yoksa `FAILED`.
- **Deneme sınırı zorunlu ve konfigüre edilebilir** (`LIFECYCLE_MAX_RENDER_ATTEMPTS`, varsayılan 2). Sınır aşılınca `FAILED` + `human_review`. **Sınırsız döngü ihtimali kodda ifade edilemez olmalı** — sayaç projede tutulur, her denemede artar, testte kanıtlanır.
- `alternative_scene` / `alternative_provider` / `request_new_media` önerileri **bu slice'ta uygulanmaz**; kaydedilir ve `FAILED`+öneri olarak durur (2F/2G).

### 4. Devralınan üç borç bu slice'ta kapanır

1. **Render adapter'ına voiceover miksajı** (W15'in açığı): `FFmpegRenderAdapter` ve fake adapter `voiceover` ses kaynağını bildirmeli; ducking (`duck_under_voice`) zaten şemada. Bu kapanmadan seslendirme fiilen kullanılamıyor.
2. **QC claim tetikleyicisi** (W18'in ölçümü): `render_service` başarıyla bitince QC işini **kuyruğa yazsın**; tarama ikinci ağ olarak kalsın ama artık birincil yol olmasın. W18 ölçümü elde: 200 bin render'da tarama 134 ms/tick ve index tek başına çözmüyor — olay eklenince taramayı **seyrek** bir süpürmeye düşür (aralığı config'de) ve o zaman anlamlı hale gelen index'leri ekle. Ölçümü tekrarla, raporda göster.
3. **`pending` süpürücü** (W13/W15 borcu): sağlayıcı çağrısı ortasında düşen senaryo/seslendirme satırları `pending`de kalıyor. Yaş eşiğine göre `failed`e düşüren, dokümante ve testli bir süpürme.

### 5. Enumerasyon kuralı

Durum ve geçiş kümesi **kapalı** — PRD tarafından sonlu, yazılabilir. Ama **"hangi hatada ne yapılır"** kombinatoryaldir: karar tablosu yazarken 2D'nin desenini izle (total fonksiyon, tanımsız kombinasyon yok, permütasyonla tüketilen test).

## Kapsam dışı (dokunma)

- **Entitlement/kota tüketimi** → W20. **Onay/revizyon** → 2F. **Planlayıcı/takvim** → 2G. **Yayınlama** → Phase 4.
- Gerçek AI/VLM sağlayıcıları → W08 sonrası. QC kontrol kümesi ve eşikleri → 2D'de kapandı, dokunma.
- `script.py`, `text_normalization.py` → kapandı, dokunma.
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/modules/content/lifecycle.py + project_service.py  (yeni — durum makinesi, geçiş tablosu, saf)
services/api/app/modules/content/{models,repository}.py             (content_projects + geçiş tablosu)
services/api/app/modules/content/render_service.py                  (QC olayı, voiceover kaynağı)
services/api/app/modules/content/{script_service,tts_service}.py    (yalnızca pending süpürücü + proje bağlama)
services/api/app/infrastructure/render/**                           (voiceover miksajı + ducking)
services/api/app/worker/{tasks,composition}.py                      (proje ilerletici + süpürücü job'ları)
services/api/app/infrastructure/celery_app.py                       (beat girdileri)
services/api/app/api/routes/content.py                              (proje uçları)
services/api/app/core/config.py                                     (LIFECYCLE_* eşikleri)
services/api/migrations/versions/0016_*.py                          (SLOT SENDE)
services/api/tests/unit/ + tests/integration/
docs/architecture/content-render.md · background-jobs.md · error-handling.md · .env.example
```

## Kabul kriterleri

1. Migration `0016` up → down → up; tek head.
2. **Uçtan uca gerçek akış:** bir proje `PLANNED`'dan `PREVIEW_READY`'ye, gerçek PostgreSQL + MinIO + FFmpeg üzerinde, fake AI adapter'larıyla — senaryo, seslendirme, timeline, render, QC sırayla ve her geçiş kaydedilmiş.
3. **Voiceover fiilen çalışıyor:** seslendirme taşıyan bir timeline render ediliyor, çıktıda ses var (ffprobe ile doğrulanır), ducking uygulanıyor. W15'in açığı kapandı.
4. **Döngü sınırı:** QC sürekli `failed` dönen bir render için deneme sayacı sınırı aşmıyor, sonunda `FAILED` + `human_review`; testte sayaç değeri kanıtlanıyor.
5. Geçiş tablosu total: tanımsız (durum, olay) çifti yok; permütasyon testi. Geriye/ileriye kaçak geçiş (ör. `RENDERING`→`PREVIEW_READY` doğrudan) reddediliyor.
6. **QC kuyruk olayı çalışıyor** ve tarama seyrek süpürmeye düştü; W18'in 200 bin satırlık ölçümü tekrarlanıp raporda karşılaştırılıyor (index eklendiyse planın gerçekten değiştiği gösterilir — W18 "index tek başına çözmüyor" demişti).
7. **`pending` süpürücü:** yapay olarak `pending`de bırakılmış senaryo/seslendirme satırı yaş eşiği sonrası `failed`e düşüyor; sağlıklı `pending` satıra dokunulmuyor.
8. Tenant izolasyonu her sorguda; roller (`content.generate` yazma, `business.read` okuma); idempotency kanonik gövdeden; imzalı URL sızmıyor (sentinel testi).
9. `make verify` yeşil; test sayısı **1151** tabanının altına düşmez; kontrat yeniden üretilip commit'li; modül `CLAUDE.md`'leri güncel.
10. Rapor + araç zinciri sürümleri. **Merge etme, dalda bırak.**

## ADR numara kuralı

Gerçek karar çıkarsa `ADR-XXX-<konu>.md`; numarayı PM verir. (Durum makinesinin kapalılığı ve "proje sıralayıcıdır, sahip değildir" duruşu ADR'lık olabilir.)

## Rapor — 2026-08-02 · Opus 5 / high

**Dal:** `slice/2e-content-lifecycle` (base `main` = `1e28746`) · **Commit:** `3378b98`
**Durum:** tamamlandı, **merge edilmedi** (iş emri gereği dalda bırakıldı)

### Yapılanlar

**Durum makinesi (`lifecycle.py`, saf).** `ProjectState` 11 durum, `ProjectEvent` 15 olay.
`next_state` `(durum, olay)` **çarpımının tamamı** için cevap veriyor; §20'nin çizmediği çift
`None`, `require_next_state` onu `PROJECT_TRANSITION_NOT_ALLOWED` hatasına çeviriyor. Permütasyon
testi tanımlı kenar kümesini PRD §20'nin diyagramıyla **birebir** karşılaştırıyor (diyagram testin
içine sabit olarak yazıldı, iki dosya açmadan karşılaştırılabilsin diye).

**Tek ekleme `STEP_FAILED`** ve PM'e bildiriyorum: §20 `FAILED`'a yalnızca `QUALITY_CHECK` ve
`PUBLISHING`'den geliyor. Senaryo üretimi düşen bir projenin gidecek yeri yok. Ekleme her çalışan
durumdan `FAILED`'a, başka hiçbir yere; test bunu küme eşitliğiyle sabitliyor. Alternatif
`QC_FAILED`'ı iki ayrı anlama genişletmekti — daha kötü.

**Geçiş kaydı.** `content_project_transitions`: proje başına `sequence` (unique), `from_state`
yalnızca §20'nin giriş okunda NULL (check constraint), `reason` kod, `actor_user_id` yalnızca
insanın sebep olduğu geçişlerde dolu. Audit log'a değil kendi tablosuna — cevaplaması gereken
soru tek bir projenin geçmişi üzerinde yürümek.

**Proje satırı dayanıklı job'dır, ayrı `jobs` satırı yok.** Gerekçe: sıralayıcının durumu zaten
sonucudur; ikisini iki tabloya yazmak çökme sonrası iki cevap üretir. AGENTS.md'nin job
gereklilikleri satırın üstünde: durum (`state`), timeout (`state_entered_at` ×
`LIFECYCLE_STEP_TIMEOUT_SECONDS`), sayaçlar, correlation, dead-letter (`FAILED`).
`next_check_at` hem sıralama anahtarı hem lease.

**QC kararı eyleme döndü, sınırlı.** `decide_after_qc` saf ve total (3 karar × 6 öneri × sayaç);
`passed`→preview, `needs_review`→preview + `requires_human_review`, `failed`+`retry_render`+deneme
kalmışsa §20'nin kendi yolundan (`FAILED`→`RETRYING`) sınırlı yeniden render, aksi halde `FAILED`
+ insan incelemesi. `alternative_scene`/`alternative_provider`/`request_new_media` **kaydediliyor,
uygulanmıyor**. Döngü sınırı: `LIFECYCLE_MAX_RENDER_ATTEMPTS` (varsayılan 2, alanın kendisi 10'da
tavanlı), render **istenmeden önce** okunuyor ve `decide_after_qc` tavanda hiçbir girdi için
"retry" dönmüyor — sınırsız döngü ifade edilemiyor.

**Timeline otomatik kuruluyor** (`compose_timeline`, saf). Senaryonun segmentleri sırayla,
her biri `required_scene_tags`'iyle kesişen ilk kullanılmamış sahneyi alıyor. Seslendirme
kesitten uzunsa son klip uzatılıyor → sahne ekleniyor → yetmezse **reddediliyor**
(`PROJECT_TIMELINE_TOO_SHORT_FOR_VOICEOVER`). Bindirme yok, altyazı kapalı — gerekçe kodda ve
`content-render.md`'de: bindirme metni K4'ün düzenleme yüzeyi (2F), transcript altyazısı ise
seslendirmenin *altındaki* sesi altyazılardı.

**Üç devralınan borç kapandı.**

1. **Voiceover miksajı (W15'in açığı).** Her iki adapter `audio_sources={original, voiceover}`
   bildiriyor. Satır başına WAV'lar `aformat`+`concat` ile tek track'e birleşiyor (demuxer değil:
   sağlayıcının aynı akış parametrelerini döndürme yükümlülüğü yok), sonra `filter_complex`
   içinde miksleniyor; `duck_under_voice` varsa `sidechaincompress`, ardından
   `amix(normalize=0)` + `alimiter`. `duration=first` → mix uzunluğu görüntünün.
   **Seslendirmesiz timeline eski yolu birebir koruyor** (`0:a`, boş graf, `-af` gain) ve bu
   testli. `music` bilinçli olarak hâlâ bildirilmiyor (lisans kaydı ister, §18.3).
2. **QC kuyruk olayı (W18'in ölçümü).** `render_service._succeed` `content.qc.requested` yazıyor;
   tick `sweep-content-qc`'ye (900 s) düştü. **Ve sorgu yeniden şekillendirildi** — W18'in asıl
   sonucu buydu: `render_outputs.qc_claimed_at` (raporla aynı transaction'da damgalanır, hiç
   temizlenmez) + kısmi index `WHERE status='succeeded' AND qc_claimed_at IS NULL`. Migration
   `0016` mevcut raporlu render'lar için backfill yapıyor. Ölçüm tekrarlandı, tabloda.
3. **`pending` süpürücü (W13/W15 borcu).** `content.pending.sweep` + `AbandonedRunSweeper`.
   Yaş eşiği `Settings` doğrulamasında iki kabiliyetin en uzun dürüst koşusundan büyük olmaya
   **zorlanıyor** — sadece yavaş olan bir koşuyu terk edilmiş ilan etmemek bu süpürmenin
   yapmaması gereken tek şey, ve bu bir yorum değil bir kural.

**Uçlar:** `POST/GET /content/projects`, `GET /content/projects/{id}`,
`POST /content/projects/{id}/media`, `GET /content/projects/{id}/transitions`. Yazma
`content.generate`, okuma `business.read` — proje sıraladığı yazmalardan başka bir şey
üretmediği için kendi izniyle değil onların çizgisiyle.

### Ölçüm — QC claim'i (kabul kriteri 6)

Aynı 200 bin render'lık fixture, `EXPLAIN (ANALYZE, BUFFERS)`, PostgreSQL 16.14, tek sunucu:

| Claim şekli | Plan | Süre |
|---|---|---|
| W18: anti-join, render satırında yordam yok | merge anti-join; 200 bin satırlık index scan + raporların **external merge sort**'u (disk 6,2 MB) | **199 ms** (soğuk 354 ms) |
| W19: `qc_claimed_at IS NULL`, bir render bekliyor | **`ix_render_outputs_awaiting_qc` index scan** + nested-loop anti-join | **3,6 ms** |
| W19: durağan durum, bekleyen yok | aynı index scan; anti-join **hiç çalıştırılmadı** | **0,05 ms** |

**Plan gerçekten değişti** — W18'in açık sorusu buydu ("index tek başına çözmüyor, sorgunun
korelasyonu ifade etmesi gerek"). Kalan 3,6 ms, anti-join'in `ix_render_qc_reports_business_render`'ı
yalnız `render_id` ile taraması; `render_qc_reports(render_id)` index'i onu da kaldırırdı ve
**bilinçli olarak eklenmedi**: o maliyet yalnızca zaten koca bir QC job'ı başlatacak tick'te
ödeniyor, yazma maliyeti ise her raporda. Anti-join yerinde kaldı, "render başına bir koşu"nun
bağımsız ikinci ifadesi olarak.

### Kapsam dışı bıraktıklarım ve nedeni

- **Entitlement/kota** — W20, iş emri gereği. Ama bir kancayı düzelttim: `request_render` artık
  isteğe bağlı `trigger` alıyor ve sıralayıcı yeniden render'ları `REVISION` olarak damgalıyor.
  Gerekçe yeni bir karar değil, faz planı §2'nin zaten yazdığı kural ("saf yeniden render yeni
  hak tüketmez"); aksi halde W20 yanlış bir sütun okuyacaktı. Varsayılan davranış (revizyondan
  çıkarım) değişmedi.
- **Onay/revizyon (2F), planlayıcı (2G), gerçek sağlayıcılar (W08 sonrası)** — dokunulmadı.
- **`alternative_scene` / `alternative_provider` / `request_new_media`** — kaydediliyor,
  uygulanmıyor; her biri olmayan bir kabiliyet gerektiriyor.
- **`docs/index.md` ve `docs/adr/README.md`'ye ekleme yapılmadı** (iş emri gereği). **ADR
  yazılmadı:** iki duruş ADR'lık olabilir ("durum makinesi kapalıdır", "proje sıralayıcıdır,
  sahip değildir") ama ikisi de iş emrinin verdiği kararın uygulanması; numarayı PM verirse
  gerekçe `lifecycle.py`/`project_service.py` docstring'lerinde ve `content-render.md`'de hazır.
- **`docs/plans/active/` altına ayrı plan dosyası açılmadı** — W18'in gerekçesiyle aynı: bu
  slice'ın planı iş emrinin kendisi ve dosya listesinde `docs/plans/` yok.

### Doğrulama

Araç zinciri: Python 3.13.14 · mypy 2.3.0 · ruff 0.16.0 · PostgreSQL 16.14 · FFmpeg 7.1.5 ·
`COMPOSE_PROJECT_NAME=sp-w19` (izole portlarla; başka worktree'nin konteynerine dokunulmadı).

| Kontrol | Sonuç |
|---|---|
| `ruff check` · `ruff format --check` · `mypy` | ✅ temiz (205 dosya biçimli, 193 kaynak) |
| `pytest` (RUN_INTEGRATION_TESTS=1, gerçek PostgreSQL + MinIO + FFmpeg) | ✅ **1204 geçti**, taban 1151'in üstünde |
| 1 · migration `0016` up → down → up, tek head | ✅ `0016_content_projects (head)`; suite bu döngüden sonra yeniden koşuldu |
| 2 · uçtan uca `PLANNED` → `PREVIEW_READY` | ✅ `test_a_project_walks_from_planned_to_preview_ready...` — senaryo, seslendirme, timeline, render, QC sırayla; geçiş listesi ve `from_state` zinciri doğrulandı; çıktı `aac` sesli gerçek dosya |
| 3 · voiceover fiilen çalışıyor + ducking | ✅ `test_speech_reaches_the_output_and_ducking_changes_what_it_sounds_like` — aynı kesitin üç render'ı (altlık / +ses / +ducking) **çözülmüş PCM hash'iyle** karşılaştırıldı; ses var, ses mikse girdi, ducking mikse etki etti |
| 4 · döngü sınırı | ✅ `test_a_render_that_never_passes_quality_control_stops_at_the_configured_ceiling` — QC her turda `failed`+`retry_render`, proje tam 2 render'da duruyor, `render_attempts == 2`, `requires_human_review`, `RETRY_REQUESTED` tam bir kez |
| 5 · geçiş tablosu total, kaçak geçiş reddediliyor | ✅ permütasyon (11×15) + §20 kenar kümesi birebir + 9 kaçak geçiş parametrik olarak reddediliyor (`RENDERING`→`PREVIEW_READY` dahil) |
| 6 · QC olayı + tarama seyrekleşti + ölçüm tekrarı | ✅ yukarıdaki tablo; olay uçtan uca testte `outbox_events`'te doğrulanıyor, `qc_claimed_at IS NULL` kalan `succeeded` render yok |
| 7 · `pending` süpürücü | ✅ `test_a_stale_pending_run_is_settled_and_a_healthy_one_is_left_alone` — bayat satır `failed`, **sağlıklı satıra dokunulmuyor**, ikinci geçiş `None` (drain dönüyor, dönmüyor) |
| 8 · tenant izolasyonu / roller / idempotency / imzalı URL | ✅ dört ayrı test: başka tenant'ın projesi okunamıyor, ilerletilemiyor, listede görünmüyor; editor yazabiliyor, viewer okuyor yazamıyor, approver hiçbiri; aynı anahtar + aynı gövde replay, farklı gövde `409`; proje gövdesinde `X-Amz-Signature` ve `object_key` yok |
| 9 · kontrat yeniden üretildi ve commit'li | ✅ `generate_openapi.py`; 41 endpoint · modül `CLAUDE.md`'leri güncel |

### Açıkça belirtmem gerekenler

1. **İlan edilen dosya listesinin dışına iki dosyada çıktım.** Hiçbiri `STATUS.md`'nin sahiplik
   tablosunda başka bir WO'ya ait değil ve şu an uçuşta başka WO yok; yine de kayda geçiyorum:
   - **`app/infrastructure/celery_publisher.py`** — `DRAIN_TASK_BY_EVENT`'e iki satır
     (`content.qc.requested`, `content.project.advance.requested`). **Kaçınılmazdı:** kabul
     kriteri 6 QC olayını şart koşuyor, ve haritada karşılığı olmayan bir outbox olayı
     `OUTBOX_EVENT_TYPE_UNSUPPORTED` ile dead-letter'a gider — yani olay yazmak tek başına
     kriteri karşılamıyor, zararlı oluyordu.
   - **`app/modules/content/service.py`** — `request_render`'a isteğe bağlı `trigger` (yukarıda
     gerekçelendirildi) ve W15'in zaten dokunduğu dosya.
   Ayrıca `tests/` altında altı mevcut test dosyası güncellendi; hepsi ilan edilmiş kapsamda ve
   hepsi bu slice'ın bilinçli olarak değiştirdiği bir davranışı sabitliyordu (voiceover
   kabiliyeti, beat girdileri, izin matrisi, normalizer çağıran listesi).
2. **`text_normalization` üçüncü bir çağıran kazandı: `lifecycle.py`.** Modül `CLAUDE.md`'sinin
   "üçüncü bir çağıran testle yasaktır" değişmezi bilinçli olarak genişletildi ve testin
   docstring'ine gerekçe yazıldı. Çağrı `normalize_encoding` (saklama katlaması), **eşleştirme
   katlaması değil** — sahne etiketi 2B'nin sakladığı değerle karşılaştırılıyor, `ürün`ü `urun`
   yapmak eşitliğin bir tarafını bozardı. Repository bu modülü hiç import etmiyor;
   `lifecycle.normalize_scene_tag`'i çağırıyor, yani "sahne etiketi nasıl yazılır" tek yerde.
3. **Türkçe `I` bulgusu — küçük ama gerçek.** `normalize_encoding` Türkçe küçük harf uyguluyor,
   yani `PREPARATION` → `preparatıon`. Sağlayıcı etiketini büyük harfle, senaryo etiketini küçük
   harfle yazdığında **hiç eşleşmiyorlardı** ve belirti sessizdi: sahne seçimi sessizce
   "sıradaki kullanılmamış çekim"e düşüyordu. Yalnızca karşılaştırmada bir `_match_key`
   (noktasız/noktalı `ı`/`i` katlaması) eklendi; saklanan hiçbir değer değişmedi ve
   `normalize_scene_tag` `script._scene_tags` ile birebir aynı kaldı. Bunu bir *ürün* hatası
   olarak bildiriyorum, güvenlik değil.
4. **`POST /projects/{id}/media` idempotency anahtarı taşımıyor** ve endpoint envanteri bunu
   "değerlendirilmeli" diye işaretliyor. Değerlendirdim: bu bir create değil, durum makinesinin
   koruduğu bir geçiş — tekrarlanan teslimat projeyi `WAITING_MEDIA`'nın ötesinde bulup
   `PROJECT_TRANSITION_NOT_ALLOWED` ile reddediliyor, yani iki kez uygulanamıyor. Gerekçe route
   docstring'inde. PM aksini isterse anahtar eklemek tek satır.
5. **`decide_after_qc`'nin `needs_review` → `PREVIEW_READY` kararı bugün *tek* gerçek yol.**
   Gerçek VLM sağlayıcısı bağlanana kadar (W08 sonrası) fail-closed kural her render'ı
   `needs_review` yapıyor. Ürün tarafına söylediği: her içerik insan incelemesi işaretiyle
   geliyor ve bu doğru sonuç, eksiklik değil.
6. **Sıralayıcı senaryo/seslendirmeyi dayanıklı bir job'a taşımadı.** W15'in 4. maddesi bunu
   2E'ye önermişti; iş emri "proje sıralayıcıdır, mevcut servisler değişmez" dediği için
   servislerin şekli korundu. Bunun yerine (a) terk edilmiş `pending` satırlar süpürülüyor,
   (b) her adım deterministik idempotency anahtarıyla çağrılıyor, yani lease dolup adım baştan
   koştuğunda ikinci kez ödeme yapılmıyor. Gerçek sağlayıcı bağlanınca senkron uçların dayanıklı
   job'a taşınması hâlâ açık bir iş — PM'e bırakıyorum.
7. **Sıralayıcı yoklama (poll) yapıyor.** Render ve QC kendi job'ları; proje onların bitmesini
   `LIFECYCLE_POLL_SECONDS` (15 s) aralığıyla kontrol ediyor. Alternatif, QC/render servislerine
   proje bilgisi koymaktı — "proje sıralayıcıdır, sahip değildir" duruşunu tersine çevirirdi.
   Bedeli tek sunucuda ihmal edilebilir (canlı proje başına 4 sorgu/dakika, kısmi index üzerinden).

## Doğrulama

_(test eden oturum: kaçak durum geçişi zorla, deneme sınırını aşmaya çalış, süpürücüyü sağlıklı satıra vurdurmaya çalış, voiceover'sız/bozuk sesle render'ı `PREVIEW_READY` yaptırmaya çalış, başka tenant'ın projesini ilerletmeye çalış)_
