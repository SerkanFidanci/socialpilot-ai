# benchmark — sağlayıcı ölçüm koşum takımı (W08)

**Sahibi:** golden set ölçüm mantığı — kabiliyet başına metrik, maliyet tavanı, sağlayıcı
kayıt şekli, sonuç raporu. **Domain modülü değildir:** sağlayıcı seçmez, veritabanına yazmaz,
iş kuralı taşımaz. `../modules/` ve `../infrastructure/` içindeki kontratları *tüketir*.

**Neden burada, `modules/media/` içinde değil:** W08 iş emri "ölçüm mantığı domain'e sızmaz"
diyor; ayrıca `modules/media/CLAUDE.md` W03'e ait ve W08'in dosya listesinde değil. Bu yüzden
harness ayrı, domain-dışı bir paket olarak yaşar.

## Değişmezler

- **Varsayılan koşu fake sağlayıcılarla çalışır** — credential yok, ağ yok, DB yok. `make
  benchmark` CI'da bu şekilde aracın kendisini test eder.
- **Tek maliyet muhasebesi:** `ProviderUsageRecord` ADR-007'nin `provider_usage` alanlarını
  birebir yansıtır (token/prompt/imzalı URL/ham yanıt taşımaz). Paralel bir maliyet modeli
  kurulmaz. (Not: `provider_usage` **tablosu henüz kodda yok** — bu kayıt, o kalıcılık
  geldiğinde benimsenecek nötr şekildir.)
- **Maliyet tavanı çağrıdan önce uygulanır:** `CostLedger.reserve` tahmini maliyetle bakar;
  tavan aşılacaksa **çağrı yapılmadan** durur, sessizce para harcamaz.
- **Provenance zorunlu:** prompt sürümü / route revizyonu olmayan örnek puanlanmaz
  (`BenchmarkProvenanceError`). PRD §17.6.
- **Veri minimizasyonu:** sağlayıcıya yalnızca proxy/kesit referansı gider, orijinal değil
  (`require_minimized_input`). §34.3.
- **Otomatik puanlanamayan boyut uydurulmaz:** marka tonu ve prozodi `auto_scored=False` ile
  manuel/LLM-judge olarak raporlanır.

## Dosyalar

| Dosya | İş |
|---|---|
| `model.py` | `Capability`, `ProviderUsageRecord`, `CostLedger`, hata sınıfları, sonuç dataclass'ları |
| `metrics.py` | Saf metrik fonksiyonları (WER, drift, jaccard, F1, yasak kelime, uydurma fiyat/tarih, timeline uyumu, süre sapması, fonem kapsamı) |
| `golden.py` | `tests/fixtures/golden/samples/` yükleme; provenance doğrulaması |
| `providers.py` | Fake sağlayıcılar + descriptor'lar (veri bölgesi, yüz/ses uygunluğu, birim maliyet) + registry; gerçek set yalnızca konfigürasyon yüzeyi |
| `runner.py` | Orkestrasyon: tavan altında çağır, ground truth'a karşı puanla, N koşu dağılımı |
| `report.py` | Makine-okunur JSON + insan-okunur karşılaştırma tablosu (veri bölgesi + uygunluk sütunları) |

## Girdi ve giriş noktaları

- Golden set: `../../../tests/fixtures/golden/` (manifest + ground truth + lisans notu; büyük
  binary yok).
- Çalıştırma: `../../../scripts/run_benchmark.py` (`make benchmark`).
- Fixture üretimi: `../../../scripts/make_golden_media.py` (FFmpeg ile deterministik).

## Gereksinim, karar, mimari

- [35-ai-routing-cost.md](../../../../../docs/product/requirements/35-ai-routing-cost.md) (§17, §39) ·
  [97-engineering-standards.md](../../../../../docs/product/requirements/97-engineering-standards.md) (§40.5) ·
  [99-external-platform-facts.md](../../../../../docs/product/requirements/99-external-platform-facts.md) (maliyet, KVKK)
- [ADR-007](../../../../../docs/adr/ADR-007-media-analysis-provider-routing.md) ·
  Mimari: [ai-provider-routing.md](../../../../../docs/architecture/ai-provider-routing.md) (benchmark bölümü)

## Testler

`tests/unit/test_benchmark.py`
