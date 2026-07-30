# W08 — Golden set benchmark koşum takımı

**Dal:** `slice/1h-provider-benchmark` · **Base:** `main` · **Migration slotu:** yok · **W07 ile paralel** (dosya-ayrık)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Neden bu iş:** Gerçek ücretli AI sağlayıcısı bağlamanın **ön koşulu.** Şu an ASR ve VLM fake; Phase 1 çıkış kriteri mekanik olarak karşılandı ama transcript ve etiketlerin *içeriği* sentetik. Ölçülmeden bağlanan ilk sağlayıcı varsayılan hâline gelir ve ADR-007'nin kabiliyet routing'ini yazmanın bütün amacı kaybolur. PRD §17.2'nin aday tablosu **maliyete göre** seçilmiş; ürünün asıl çıktısı Türkçe pazarlama içeriği olduğu hâlde **Türkçe kalitesi ve veri bölgesi tartılmamış.**

Bu WO bir sağlayıcı **seçmez**. Seçimi mümkün kılan ölçüm aracını kurar.

## Okunacaklar

Router: [`docs/index.md`](../index.md) → "Yeni dış sağlayıcı entegrasyonu" satırı. Asgari set:

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/adr/ADR-007-media-analysis-provider-routing.md`](../adr/ADR-007-media-analysis-provider-routing.md) — route kaydı, maliyet tavanı, veri bölgesi alanı
3. [`docs/architecture/ai-provider-routing.md`](../architecture/ai-provider-routing.md)
4. [`docs/product/requirements/35-ai-routing-cost.md`](../product/requirements/35-ai-routing-cost.md) — §17 kabiliyetler ve aday tablosu, §39 maliyet kontrolü
5. [`docs/product/requirements/97-engineering-standards.md`](../product/requirements/97-engineering-standards.md) — **§40.5 medya golden testleri** (sabit test seti bu WO'nun girdisi)
6. [`docs/product/requirements/99-external-platform-facts.md`](../product/requirements/99-external-platform-facts.md) — sağlayıcı maliyet ve mevzuat gerçekleri
7. `services/api/app/infrastructure/media/CLAUDE.md` — mevcut fake adapter'ların şekli

## Kapsam

### 1. Golden medya seti (PRD §40.5)

Depoya **üretilebilir** bir sabit set. Büyük binary'ler commit edilmez: FFmpeg ile deterministik üreten bir script + gerçek Türkçe konuşma gereken yerler için küçük, lisansı açık örnekler (kaynağı ve lisansı dosyada yazılı).

Kapsanacak nitelikler: dikey/yatay · gürültülü ses · **Türkçe konuşma** · çoklu ürün · karanlık · titrek · insan yüzü · öncesi/sonrası · logo · küçük metin. Her örnek için **beklenen doğru cevap** (ground truth) makine-okunur biçimde yazılır — aksi hâlde ölçüm değil izlenim olur.

### 2. Kabiliyet başına ölçüm

Kabiliyetler ve her biri için metrik:

| Kabiliyet | Metrik |
|---|---|
| `asr` (Türkçe) | Kelime hata oranı (WER), zaman damgası kayması, gürültülü örnekte bozulma, marka terim sözlüğü isabeti |
| `video_understanding` | Sahne sınıflandırma isabeti, ürün/nesne tespiti, `unsafe_flags` doğruluğu, **şema sadakati** (geçersiz JSON oranı) |
| `text_strategy` / `script_generation` | **Türkçe marka tonu**, yasak kelime ihlali sayısı, uydurulmuş fiyat/tarih sayısı (sıfır olmalı), CTA'nın onaylı listeden gelmesi |
| `structured_timeline` | Katı JSON şemaya uyum oranı, sınır değerlerde bozulma |
| `tts` (Türkçe) | Prozodi/telaffuz değerlendirmesi (Türkçe'ye özgü sesler), segment süresi tahmin sapması |

Her koşu için **gerçek maliyet** (integer minor unit) ve **gecikme** kaydedilir. `provider_usage` şeması zaten bunu taşıyor (ADR-007) — benchmark aynı kaydı kullanır, paralel bir muhasebe kurmaz.

### 3. Koşum aracı

- Tek komutla çalışır (`make benchmark` veya `scripts/run_benchmark.py`), **varsayılan olarak fake adapter'larla** koşar — yani CI'da ve credential olmadan da çalışır ve aracın kendisi test edilir.
- Gerçek sağlayıcı, açık bir konfigürasyon ve **açık maliyet tavanı** ile devreye girer. Tavan aşılırsa koşu **durur**; sessizce para harcamaz.
- Çıktı: makine-okunur sonuç dosyası + insan-okunur karşılaştırma tablosu (`docs/reviews/` altına tarih-konu adlandırmasıyla yazılabilir).
- Aynı girdi + aynı sağlayıcı + aynı prompt sürümü → sonuç **tekrarlanabilir** olmalı; sağlayıcı non-deterministik ise N koşu ve dağılım raporlanır, tek koşuya bakıp karar verilmez.
- Prompt sürümü sonuca **yazılır** (PRD §17.6): hangi prompt'la ölçüldüğü bilinmeyen bir sonuç kullanılamaz.

### 4. Veri bölgesi ve gizlilik boyutu

Sonuç tablosunda her sağlayıcı için **veri bölgesi** ve **yüz/ses taşıyan girdi için uygunluk** sütunu bulunur. Gerekçe: KVKK'da yüz/ses biyometrik tartışmasına girer ve her yurt dışı sağlayıcı standart sözleşme + 5 iş günü Kurul bildirimi demek ([99-external-platform-facts.md](../product/requirements/99-external-platform-facts.md)). **En iyi skor, hukuken kullanılamıyorsa kazanan değildir** — tablo bunu görünür kılmalı.

Ayrıca §34.3 veri minimizasyonu benchmark'ta da uygulanır: sağlayıcıya orijinal değil proxy/kesit gönderilir.

## Kapsam dışı (dokunma)

- **Sağlayıcı seçme kararı.** Bu WO ölçer; kararı PM, sonuçlara bakarak ADR olarak yazar.
- **Gerçek sağlayıcı credential'ı temin etme / hesap açma.** Konfigürasyon yüzeyi hazırlanır, anahtar konmaz.
- **Prompt tasarımını iyileştirmek.** Mevcut kontratlar ölçülür; prompt optimizasyonu ayrı iş.
- **Migration.** Şema değişikliği gerekiyorsa dur ve rapora yaz (`provider_usage` yeterli olmalı).
- `compose.yaml`, `Makefile`'ın kaynak-limiti/yedekleme hedefleri → **W07'nin.** `Makefile`'a yalnızca `benchmark` hedefi eklenir; çakışma olursa dur ve rapora yaz.
- `docs/index.md`, `docs/adr/README.md` → ADR yazarsan indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/scripts/run_benchmark.py                (yeni)
services/api/scripts/make_golden_media.py            (yeni — deterministik fixture üretimi)
services/api/tests/fixtures/golden/                  (küçük örnekler + ground truth + LİSANS notu)
services/api/app/modules/media/benchmark.py          (veya uygun modül — ölçüm mantığı domain'e sızmaz)
services/api/tests/unit/                             (aracın kendi testleri)
Makefile                                             (yalnızca benchmark hedefi)
docs/architecture/ai-provider-routing.md             (benchmark bölümü)
docs/reviews/README.md                               (sonuç raporu adlandırması)
```

## Kabul kriterleri

1. `make benchmark` credential olmadan, fake adapter'larla uçtan uca çalışıyor ve sonuç dosyası üretiyor.
2. Golden set script'le üretiliyor; depoda büyük binary yok; lisans gerektiren her örneğin kaynağı ve lisansı yazılı.
3. Her örnek için makine-okunur ground truth var; ölçüm buna karşı hesaplanıyor (izlenim değil sayı).
4. Beş kabiliyetin her biri için en az bir metrik hesaplanıyor ve sonuç tablosunda görünüyor.
5. Her koşu gerçek maliyeti ve gecikmeyi `provider_usage` üzerinden kaydediyor; paralel muhasebe yok.
6. **Maliyet tavanı çalışıyor:** tavan aşımında koşu duruyor. Bunu gösteren test var.
7. Sonuç tablosunda veri bölgesi ve "yüz/ses taşıyan girdi için uygun mu" sütunları var.
8. Sağlayıcıya orijinal medya değil proxy/kesit gidiyor; test var.
9. Sonuç kaydında prompt sürümü ve route kaydı var; hangi koşullarda ölçüldüğü belirsiz sonuç üretilemiyor.
10. Non-determinizm ele alınmış: N koşu + dağılım, ya da neden tek koşunun yeterli olduğunun gerekçesi.
11. `make verify` yeşil, Alembic head değişmemiş.

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum doldurur — özellikle: ground truth'a karşı hesaplanan metriği bağımsız doğrula ve maliyet tavanını gerçekten aşmayı dene)_
