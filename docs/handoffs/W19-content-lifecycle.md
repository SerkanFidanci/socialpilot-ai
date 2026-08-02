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

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum: kaçak durum geçişi zorla, deneme sınırını aşmaya çalış, süpürücüyü sağlıklı satıra vurdurmaya çalış, voiceover'sız/bozuk sesle render'ı `PREVIEW_READY` yaptırmaya çalış, başka tenant'ın projesini ilerletmeye çalış)_
