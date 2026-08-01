# W15 — Phase 2C: Seslendirme (`tts` portu, fake sağlayıcı)

**Dal:** `slice/2c-tts-voiceover` · **Base:** `main` · **Migration slotu: SENDE** (`0014`)
**Durum:** hazır, tetiklenmedi
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

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum doldurur — özellikle: serbest metni script id'siz seslendirtme denemeleri, başka tenant'ın script'i, tavan aşımı, süre beyanı ile ffprobe ölçümünün çelişmesi)_
