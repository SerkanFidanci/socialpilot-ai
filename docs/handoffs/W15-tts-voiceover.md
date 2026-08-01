# W15 — Phase 2C: Seslendirme (`tts` portu, fake sağlayıcı)

**Dal:** `slice/2c-tts-voiceover` · **Base:** `main` · **Migration slotu: SENDE** (`0014`)
**Durum:** tamamlandı — bağımsız doğrulama bekliyor
**Model/effort:** Opus 5 / high
**Plan:** [Phase 2 planı](../plans/active/phase-2-content-generation.md) — slice 2C
**Neden bu iş:** Senaryo var (2B), timeline ve render var (2A) — ama ses yok. PRD §14.8'in sırası net: *önce senaryo ve seslendirme oluşturulur, ses segment süreleri çıkarılır, her cümleye uygun kesit atanır.* Bu slice onaylanmış bir senaryonun **çözülmüş** metnini sese çevirir, segment sürelerini çıkarır ve sesi timeline'a hizalar. Sağlayıcı **fake** (Phase 1 deseni); gerçek TTS W08 benchmark'ı sonrası.

## Okunacaklar

Router: [`docs/index.md`](../index.md) → "Yeni modülde özellik" satırı. Asgari set:

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/plans/active/phase-2-content-generation.md`](../plans/active/phase-2-content-generation.md) — §2 kararlar (özellikle **üretimde fake AI genel kuralı**: W13 kural onayı 1)
3. [`docs/product/requirements/40b-scenario-render-lifecycle.md`](../product/requirements/40b-scenario-render-lifecycle.md) — §18 timeline/ses, §14.8 seslendirmeli reklam akışı
4. [`docs/product/requirements/35-ai-routing-cost.md`](../product/requirements/35-ai-routing-cost.md) — §17.3 `TTSProvider` imzası, §17.5, §17.6
5. `services/api/app/modules/content/CLAUDE.md` — script/timeline/render mevcut şekli; **W13'ün route snapshot + `provider_usage` + maliyet tavanı desenini aynen izle**
6. `services/api/app/infrastructure/ai/CLAUDE.md` (varsa) veya `fake_script.py` — fake/disabled adapter deseni

## Kapsam

### 1. `TTSPort` + fake/disabled adapter

- Port domain'de, PRD §17.3 şekline sadık: metin + ses profili + çıktı formatı → `AudioResult`. Fake adapter deterministik, **gerçek ses dosyası üretir** (sessizlik/ton üreteci yeterli — süresi metin uzunluğundan türetilmiş, ffprobe ile ölçülebilir gerçek bir ses konteyneri). Byte üretmeyen bir fake, hizalama testlerini anlamsız kılar.
- **Üretim davranışı W13 kural onayı 1'e uyar:** TTS çıktısı insan-onaylanabilir sınıfta → üretimde `disabled` + `503 TTS_NOT_CONFIGURED`; boot çökmez; fake üretimde construct edilemez. `reject_non_production_adapters` listesine **girmez**; test eder.
- Ücretli çağrı disiplini: route snapshot çağrıdan önce, maliyet tavanı çağrıdan önce (`TTS_MAX_COST_MINOR` varsayılan `0`), `provider_usage` çağrıdan sonra (başarısızlıkta da). Ses profili sürümlenir (§17.6 deseni — hangi profil/prompt'la üretildiği bilinmeyen ses yok).

### 2. Seslendirme üretimi ve kalıcılık (migration `0014`)

- Girdi: **onaylanmış/`generated` bir `content_scripts` kaydı.** TTS'e giden metin senaryonun **çözülmüş** hali (verified slotlar değere basılmış) — fiyat sese doğrulanmış kayıttan girer. Serbest metin API'den kabul edilmez; istek gövdesi script id + ses profili taşır.
- `voiceover_assets` (PRD §28.5): business_id, script referansı, segment başına ses objesi referansı + **ölçülmüş süre** (`ffprobe`, sağlayıcı beyanı değil), toplam süre, profil sürümü, route/usage referansı, durum.
- Ses dosyaları object storage'a mevcut storage adapter'ıyla yazılır; ikinci bir yükleme yolu yazılmaz. İmzalı URL'ler hiçbir log/audit/span'e sızmaz (W14'ün süreç-geneli redaksiyonu zaten var — **test yine de yazılır**).

### 3. Timeline hizalaması

- Segment süreleri çıkarıldıktan sonra ses, 2A'nın timeline şemasındaki `audio_tracks`'e `voiceover` olarak bağlanabilir: yeni bir doğrulama kuralı — seslendirme süresi timeline süresini aşamaz (§18.3 "seslendirme süresi" kontrolü gerçeğe bağlanır).
- Segment süresi ile senaryonun `target_duration_ms`'i arasındaki sapma **kaydedilir** (2D QC bunu tüketecek); bu slice sapmayı ölçer, reddetmez — eşik kararı QC'nin.
- Müzik ducking 2A şemasında zaten var (`duck_under_voice`); bu slice yalnızca voiceover track'inin doğrulamasını bağlar, yeni ses işleme yazmaz.

## Kapsam dışı (dokunma)

- **Gerçek TTS sağlayıcısı** — W08 sonrası.
- **Senaryodan timeline'ın otomatik kurulması, sahne-segment ataması** — 2E. Bu slice sesi üretir ve *var olan* timeline'a bağlanabilir kılar.
- **QC eşikleri (2D), yaşam döngüsü/entitlement (2E), onay (2F).**
- Render tarafında yeni ses filtresi/miksaj — 2A'nın FFmpeg adapter'ı mevcut şemayı işliyor; eksik çıkarsa dur ve bildir.
- `compose.yaml` → W06. `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/modules/content/tts.py + tts_service.py    (yeni — port, üretim, hizalama)
services/api/app/modules/content/models.py + repository.py  (voiceover_assets)
services/api/app/infrastructure/ai/fake_tts.py + __init__.py (fake/disabled + fabrika genişletmesi)
services/api/app/api/routes/content.py                      (voiceover uçları)
services/api/app/core/config.py                             (TTS_* ayarları)
services/api/migrations/versions/0014_*.py                  (SLOT SENDE)
services/api/tests/unit/ + tests/integration/
docs/architecture/content-render.md                         (seslendirme bölümü)
docs/architecture/error-handling.md                         (yeni kodlar)
.env.example                                                (TTS_* anahtarları, güvenli varsayılan)
```

## Kabul kriterleri

Sayılı girdiler + **atlatma senaryoları düşman gözüyle** (W13 dersi):

1. Migration `0014` up → down → up; tek head.
2. **Uçtan uca:** `generated` senaryodan fake TTS ile seslendirme üretiliyor; segment başına gerçek ses objesi + **ffprobe ile ölçülmüş** süre; `voiceover_assets` kaydında profil sürümü + route/usage dolu; `provider_usage`'a satır yazılmış.
3. Serbest metin seslendirilemiyor: istek yalnızca script id taşıyor; `failed`/`rejected` durumundaki senaryo → hata; başka tenant'ın senaryosu → `404` (varlık ifşası yok).
4. Maliyet tavanı: varsayılan `0` ile fake dahi olsa tahmini maliyet >0 verildiğinde çağrı öncesi durur (test tavanı aşmayı gerçekten dener).
5. Üretim + fake → `503 TTS_NOT_CONFIGURED`; boot çökmüyor; fake üretimde construct edilemiyor (üç ayrı test — W13 deseninin aynısı).
6. Hizalama: seslendirme süresi timeline süresini aşarsa `TIMELINE_VALIDATION_FAILED` sınıfında dokümante hata; sapma kaydı yazılıyor (eşik yok, ölçüm var).
7. İmzalı URL sentinel'i hiçbir log handler'ında/span'de yok (W14 filtresi + bu yolun kendi testi).
8. Roller: `editor` üretebiliyor (`content.generate`), `viewer`/`approver` hayır; idempotency: aynı key aynı sonuç, farklı gövde `409`; **fingerprint kanonik gövdeden** (W14 dersi — operasyon sayısı gibi özet değil).
9. `make verify` yeşil; test sayısı azalmıyor (şu an **628**); kontrat yeniden üretilip commit'li; `content` CLAUDE.md güncel.

## ADR numara kuralı

Gerçek karar çıkarsa `ADR-XXX-<konu>.md`; numarayı PM verir.

## Rapor — 2026-08-01 · Claude (Opus 5 / high)

**Dal:** `slice/2c-tts-voiceover` · **Base:** `main` (`fa279ea`) · **Durum:** tamamlandı
**Araç zinciri:** py3.13 / mypy 2.3.0 / ruff 0.16.0, Linux konteyner
(`COMPOSE_PROJECT_NAME=sp-w15`, ayrılmış host portları — 55442/56389/59010/8010)

### Yapılanlar

- **`TTSPort` + `AudioProbePort` (`content/tts.py`).** §17.3'ün `synthesize(text, voice_profile,
  output_format)` şekli, iki eklemeyle: hedef dosyayı çağıran verir (adapter worker scratch'inde
  yol seçmez) ve `max_output_bytes` yazılabilecek byte'ı sınırlar. `ProviderDescriptor` ve
  `RouteSnapshot` `script.py`'den **yeniden kullanıldı**, kopyalanmadı — böylece her kabiliyetin
  route kaydı ve `provider_usage` satırı aynı şekilde, maliyet kabiliyetler arası toplanabiliyor.
- **Ses profili sürümlü, kapalı registry** (`VOICE_PROFILES`, §17.6 deseni). Çağıran serbest
  konuşma hızı veya ham sağlayıcı ses kimliği veremiyor. Sağlayıcıya verilen profil dokümanının
  tamamı sesin yanında saklanıyor, registry yarın düzenlense de.
- **Fake adapter gerçek ses üretiyor** (`ai/fake_tts.py`): metin uzunluğu × konuşma hızından
  türetilmiş süreyle 16-bit mono WAV, 220 Hz alçak ton — konuşma gibi *duyulmayan*, ama ffprobe
  ile ölçülebilen bir dosya. Test kancaları bilerek "uyuşmazlık" üretiyor: `declared_duration_ms`
  sağlayıcıya kendi dosyası hakkında yalan söyletiyor, `duration_ms` metnin ima etmediği
  uzunlukta dosya yazdırıyor, `fail_after_calls` koşuyu ortasında durduruyor.
- **Ölçüm ayrı bir port** (`ai/audio_probe.py`, `FFprobeAudioProbe`). `media/technical.py`'nin
  probe'u video akışı şart koşuyor (WAV orada `TECHNICAL_VIDEO_STREAM_REQUIRED`), o yüzden ayrı
  adapter; ortak olan kod değil disiplin. **Fake'i yok:** ölçüm bu slice'ın garantisinin
  kendisi, fixture bir probe fixture'ı doğrulayan fixture olurdu.
- **`voiceover_assets` (migration `0014`).** `pending` satır + route snapshot çağrılardan önce
  commit ediliyor (ADR-007). Segmentler JSONB (PRD §28.5 tek tablo adlandırıyor, satırlar tek
  transaction'da küme olarak yazılıp okunuyor); sonraki slice'ın filtreleyeceği her şey —
  durum, ölçülen toplam, sapma, profil sürümü, route/usage referansı — gerçek sütun.
- **`VoiceoverService`.** İstek yalnızca script id + ses profili kodu taşıyor; **metin alanı
  yok**. Seslendirilen metin senaryonun *çözülmüş* dokümanı. Maliyet tavanı iki kez çağrıdan
  önce (çağrı başına ve tüm koşu için), bir kez koşu sırasında (harcanan tavanı geçerse kalan
  satırlar iptal). Her çağrı kendi `provider_usage` satırını alıyor (§39.1) — başarısızlıkta da;
  satırlar isteğin `correlation_id`'siyle gruplanıyor, `provider_usage_id` koşuyu sonuçlandıran
  satırı gösteriyor. Kısmi koşuda depolanmış objeler `failed` satıra yazılıyor.
- **Üretim davranışı W13 kural onayı 1'e uyuyor:** `create_tts` üretimde `DisabledTTSAdapter`
  döndürüyor (`503 TTS_NOT_CONFIGURED`), `TTS_ADAPTER` `reject_non_production_adapters`'a
  **eklenmedi**, fake üretimde construct edilemiyor.
- **Timeline hizalaması.** `voiceover` ses track'i `voiceover_assets`'i gösteriyor; §18.3'ün
  "seslendirme süresi" kontrolü ffprobe toplamına bağlandı
  (`TIMELINE_VOICEOVER_DURATION_OVERFLOW`, ayrıca `_NOT_ACCESSIBLE` / `_NOT_READY`). Sapma
  kaydediliyor, yargılanmıyor — eşik 2D'nin.
- Doküman: `content-render.md` seslendirme bölümü + §18.3 kod tablosu, `error-handling.md`
  yeni katalog, `content` ve `infrastructure` CLAUDE.md'leri, `.env.example`, OpenAPI + endpoint
  envanteri yeniden üretildi (32 → 35 endpoint).

### Kapsam dışı bıraktıklarım ve nedeni

- **Gerçek TTS sağlayıcısı** — WO'da kapsam dışı, W08 sonrası.
- **Ses miksajı / render filtresi** — WO'da açıkça kapsam dışı. Aşağıdaki bloke edici bulguya
  bakın: bu yüzden seslendirmeli timeline bugün *kaydedilemiyor*.
- **Sahne-segment ataması, QC eşikleri, yaşam döngüsü, onay** — 2D/2E/2F.
- `docs/index.md` ve `docs/adr/README.md`'ye ekleme yapılmadı (W03 tekeli). **Yeni ADR
  yazılmadı:** bu slice'ta gerçek bir mimari karar çıkmadı — üretim/disabled kuralı zaten PM
  onayı 1, port deseni ADR-004, route snapshot ADR-007. Yerel kararlar (segmentlerin JSONB
  olması, çağrı başına usage satırı, ayrı ses probe adapter'ı) kodda ve mimari dokümanda
  gerekçeli.
- `compose.yaml`'a dokunulmadı.

### Doğrulama

| Kontrol | Sonuç |
|---|---|
| `ruff check` (app/tests/migrations/scripts) | ✅ temiz |
| `ruff format --check` | ✅ 189 dosya formatlı |
| `mypy .` (strict) | ✅ 177 dosyada sorun yok |
| `pytest` (RUN_INTEGRATION_TESTS=1, gerçek PostgreSQL + MinIO + FFmpeg) | ✅ **674 passed** (öncesi 628, +46) |
| migration `0014` up → down → up | ✅ tek head: `0014_voiceover_assets` |
| `make check-openapi` eşdeğeri | ✅ kontrat yeniden üretilip commit'lendi |
| K1 migration | ✅ |
| K2 uçtan uca (fake TTS → gerçek WAV → ffprobe → depolama → satır) | ✅ `test_a_generated_script_becomes_measured_voiceover_segments` |
| K3 serbest metin / `failed` senaryo / başka tenant | ✅ üç ayrı test; başka tenant'ın gerçek id'si uydurma id ile **birebir aynı** yanıtı alıyor |
| K4 maliyet tavanı çağrı öncesi durduruyor | ✅ `tts.calls == 0`, satır yok; ayrıca "toplam koşu tavanı aşıyor" testi |
| K5 üretim + fake → 503, boot çökmüyor, fake construct edilemiyor | ✅ dört test (üçü unit, biri uçtan uca 503) |
| K6 hizalama + sapma kaydı | ✅ unit (kural, tek başına) + integration (`build_context` gerçekten DB'den okuyor) |
| K7 imzalı URL sentinel'i | ✅ **gerçek MinIO'ya karşı**: `persist_file` satır başına gerçek imzalı PUT atıyor, hiçbir handler imzayı basamıyor, audit ve satır temiz |
| K8 roller + idempotency | ✅ editor 201 / viewer 403 / approver 403; aynı anahtar aynı sonuç, farklı ses profili `409`; fingerprint kanonik gövdeden |
| K9 `make verify` yeşil, test sayısı artıyor, kontrat commit'li, CLAUDE.md güncel | ✅ |

### Açıkça belirtmem gerekenler

1. **BLOKE EDİCİ DEĞİL AMA AÇIK — hiçbir render adapter'ı `voiceover` ses kaynağını
   bildirmiyor.** `FFmpegRenderAdapter` ve `FakeRenderAdapter` `audio_sources={original}`
   diyor, çünkü ses miksajı bu WO'da açıkça kapsam dışı ("eksik çıkarsa dur ve bildir" —
   bildiriyorum). Sonuç: seslendirme *üretiliyor ve ölçülüyor*, ama `voiceover` track'i taşıyan
   bir timeline bugün `TIMELINE_UNSUPPORTED_AUDIO_SOURCE` ile reddediliyor, yani **fiilen
   bağlanamıyor.** Süre kuralını yine de kabiliyet kontrolünden bağımsız çalıştırdım ve iki
   bulgu birlikte dönüyor; aksi halde kural "bir adapter özellik kazanınca var olmaya başlayan"
   bir şey olurdu. FFmpeg adapter'ına voiceover + ducking miksajı eklemek ayrı bir dilim (2E
   önerisi).
2. **İlan edilen dosya listesinin dışına dört dosyada çıktım.** Hiçbiri `STATUS.md`'nin dosya
   sahipliği tablosunda başka bir WO'ya ait değil ve şu an açık başka WO yok; yine de kayda
   geçiyorum:
   - `app/modules/content/validation.py` — §18.3 kuralı burada yaşıyor; kabul kriteri 6 bu
     dosyaya dokunmadan karşılanamıyordu (`_check_audio` bir voiceover referansını
     `media_assets`'te arıyordu).
   - `app/modules/content/service.py` — `build_context`'e tek satır (`voiceover_facts`), yoksa
     yeni kural boş bir sözlükle koşardı.
   - `app/modules/content/timeline.py` — `asset_ids`'ten voiceover kimliğini çıkardım +
     `voiceover_ids`. Bırakılsaydı render worker'ı seslendirmeyi kaynak video sanıp indirmeye
     çalışırdı.
   - `app/modules/content/policy.py` — iki yeni `ContentAction` ve eşlemeleri.
   - Ayrıca `app/infrastructure/ai/audio_probe.py` **yeni** bir dosya (ilanda `fake_tts.py` ve
     `__init__.py` vardı); ses ölçümünün adapter katmanında bir evi olması gerekiyordu.
3. **İki mevcut testi güncelledim** (ikisi de `tests/` altında, ilan edilmiş kapsam):
   `test_content_script_unit.py`'de yetki matrisi listesi (yeni iki eylem), ve
   `test_schema_debt_migration.py`'de head'i `0013_script_generation` diye **sabitleyen**
   assertion. İkincisini sürüm-bağımsız hale getirdim (head'i önce okuyup sonra
   karşılaştırıyor), yoksa her migration slice'ı ilgisiz bir testi düzenlemek zorunda kalacaktı.
4. **Bu uç senkron ve dayanıklı bir job değil**, senaryo üretimiyle aynı borç. Bir koşu birkaç
   çağrı olduğu için çağrı başına timeout'un üstüne `TTS_TOTAL_TIMEOUT_SECONDS` (varsayılan 180 s)
   koydum. Gerçek sağlayıcı takıldığında bu dayanıklı bir job'a taşınmalı (2E) — `pending`'de
   takılı satırları süpüren kurtarma taraması da orada.
5. **CTA satırı seslendirilmiyor.** §18.1'in `cta.text`'i bir segment değil; §14.8 "CTA sonunda
   net **görünür**" diyor. Bir `cta` amaçlı segment varsa o zaten segment olarak seslendiriliyor.
   Ürün tarafı başka bir şey istiyorsa bu bir karar, kod değişikliği değil.
6. **Depolama yaşam döngüsü açığı büyüyor.** Her seslendirme koşusu satır başına bir obje
   bırakıyor ve başarısız koşular da obje bırakıyor (bilerek: kayıtsız byte olmasın). STATUS'taki
   "maliyet odaklı yaşam döngüsü politikası yok" açığına seslendirme objeleri de dahil.

## Doğrulama

_(test eden oturum doldurur — özellikle: serbest metni script id'siz seslendirtme denemeleri, başka tenant'ın script'i, tavan aşımı, süre beyanı ile ffprobe ölçümünün çelişmesi)_
