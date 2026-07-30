# W10 — Şema borcu: dört birikmiş kalem

**Dal:** `slice/0m-schema-debt` · **Base:** `main` · **Migration slotu: SENDE** (`0011`)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 4.8 / medium
**Neden bu iş:** Dört kalem, dördü de bir migration bekliyordu ve slot başkasındaydı. Her biri **bilinçli olarak** ertelendi ve gerekçesi kayıtlı; hiçbiri bugün bir şeyi bloke etmiyor ama üçü taşıma maliyeti yaratıyor. Slot boşaldı, hepsi tek slice'ta kapanır. Bu WO yeni yetenek eklemez — **var olan geçici çözümleri kaldırır ve yarım kalan sözleşmeleri tamamlar.**

## Okunacaklar

Router: [`docs/index.md`](../index.md) → "Mimari değişiklik" satırı. Asgari set:

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/handoffs/PM-NOTES.md`](PM-NOTES.md) — **"ADR-008 ekleri"** bölümü (kalem 1 ve 3'ün gerekçesi)
3. `services/api/app/modules/media/CLAUDE.md`, `services/api/app/modules/businesses/CLAUDE.md`
4. [`docs/architecture/ai-provider-routing.md`](../architecture/ai-provider-routing.md) — `provider_usage`'in **planlanan** tablo olduğu notu
5. [`docs/architecture/tenant-isolation.md`](../architecture/tenant-isolation.md) — rol matrisi (kalem 4)

## Kapsam — dört kalem

### 1. `provider_usage` tablosu

ADR-007 ve `ai-provider-routing.md` bu tabloyu tarif ediyor; **yoktu.** W08 bunu yakaladı, migration slotu olmadığı için tabloyu eklemedi ve aynı alanları taşıyan `ProviderUsageRecord` **değerini** üretti (`app/benchmark/model.py`).

- Tabloyu bu şekle göre oluştur: tenant/job/asset/run/capability/provider/model, tahmini ve gerçek **integer minor unit** maliyet, para birimi, süre, sonuç, correlation ID.
- **Dışlananlar sözleşmenin parçası:** token değeri, prompt, imzalı URL, ham yanıt — hiçbiri saklanmaz.
- Benchmark harness'ı tabloyu kullanacak şekilde bağla, ama **paralel muhasebe kurma**: `ProviderUsageRecord` şekli korunur, arkasına kalıcılık gelir.
- `ai-provider-routing.md`'deki "planlanan tablo / maliyet atfı kalıcı değil" notunu **gerçeğe göre güncelle**.

### 2. `media_upload_sessions.storage_upload_id` genişletmesi

`String(128)` gerçek AWS `UploadId` değerleri için kısa. W01 slotu olmadığı için `_control/uploads/{id}.json` yazan **sunucu sahipli bir kontrol objesi** kullandı (ADR-008'de kayıtlı): fazladan bir round-trip, fazladan bir hata modu, temizlenmesi gereken fazladan bir obje.

- Kolonu gerçek sağlayıcı değerlerini taşıyacak genişliğe çıkar.
- **Kontrol objesi katmanını kaldır** ve `create_upload`/part/complete/cancel yollarını kolona dayandır.
- Mevcut satırlar için geçiş: kolon genişletmesi veri kaybetmez, ama yarım kalmış oturumların kontrol objesi varsa geriye dönük yol bırakma — bu bir dev-only geçiş, üretim verisi yok. Yaklaşımını rapora yaz.
- ADR-008'e ek not: geçici çözüm kaldırıldı.

### 3. Fotoğraf analiz durumu (K6 ikinci yarısı için **yalnızca** enum)

W09 HEIC/HEIF'i ingest'te açık kodla reddediyor (`INGEST_ANALYSIS_UNSUPPORTED_MEDIA_TYPE`) — sessiz çıkmaz sokak yok, doğru davranış. Ama fotoğraf hattı geldiğinde bir duruma ihtiyaç var.

- **Yalnızca durum/enum genişletmesini yap**; fotoğraf analiz hattını (teknik metadata + VLM etiketleme, sahne/ASR yok) **kurma** — o ayrı bir slice ve ürün kararı gerektiriyor.
- Enum'a eklenen değerin hiçbir yol tarafından **üretilmediğini** doğrula: yeni durum şu an ulaşılamaz olmalı, ileride hat yazıldığında kullanılacak. Ulaşılamaz bir durumu eklemenin tek gerekçesi migration slotunu bir kez kullanmak — bunu rapora yaz.

### 4. `approver` rolü

`BusinessRole` enum'unda yok; PRD §4 onu tanımlıyor ve W04'ün kabul kriteri 3'ün yarısı bu yüzden test edilemedi.

- Rolü enum'a ve rol matrisine ekle. Yetkileri [`tenant-isolation.md`](../architecture/tenant-isolation.md)'deki tabloya göre: **yalnızca onay kaynaklarını görür ve onay kararı verir**; içerik/medya yazamaz, üyelik yönetemez, faturalandırmaya dokunamaz.
- W04'ün "her `BusinessRole` üyesi için marka cevabı tanımlı" testi seni eşlemeye zorlayacak — o testi **zayıflatarak geçme**, eşlemeyi yap.
- Onay kaynakları henüz yok (Phase 2 işi). Bu yüzden `approver` şu an **hiçbir şeye erişemeyen** bir rol olacak: bunu açıkça test et ve rapora yaz. Yanlış olan bir rolü var etmemek değil, var edip sessizce geniş yetki vermek olurdu.

## Kapsam dışı (dokunma)

- **Fotoğraf analiz hattının kendisi** (kalem 3'e bak).
- **Onay akışı / approval istekleri** — Phase 2.
- **Gerçek AI sağlayıcısı bağlamak** — W08 benchmark'ından sonra ve ayrı karar.
- `compose.yaml`, `Dockerfile`, `.github/workflows/**` → W06'nın.
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.
- Yeni özellik, yeni endpoint, yeni modül. Bu WO borç kapatır.

## Dokunulacak dosyalar (ilan)

```
services/api/migrations/versions/0011_*.py                  (yeni — MIGRATION SLOTU SENDE)
services/api/app/modules/operations/models.py               (provider_usage modeli — ya da uygun modül, gerekçesini yaz)
services/api/app/benchmark/model.py                         (ProviderUsageRecord → kalıcılık bağı)
services/api/app/benchmark/runner.py                        (kayıt yazımı)
services/api/app/modules/media/models.py                    (storage_upload_id genişliği + fotoğraf durumu)
services/api/app/infrastructure/storage/s3.py               (kontrol objesi katmanının kaldırılması)
services/api/app/modules/media/service.py                   (kontrol objesine bağımlılığın kaldırılması)
services/api/app/modules/businesses/models.py + policy.py   (approver rolü)
services/api/app/infrastructure/database/metadata.py        (yeni model modülü varsa kaydı)
services/api/tests/unit/ + tests/integration/
docs/architecture/ai-provider-routing.md                    (planlanan → var)
docs/adr/ADR-008-s3-compatible-storage-adapter.md           (ek not: geçici çözüm kaldırıldı)
docs/architecture/tenant-isolation.md                       (approver satırı)
```

## Kabul kriterleri

1. Migration `0011` up → down → up çalışıyor; tek head. **Downgrade veri kaybetmiyor** ve bunu gösteren bir test var (özellikle kolon genişletmesinin geri alınması).
2. `provider_usage` tablosu var; benchmark koşusu kaydı **oraya** yazıyor; token/prompt/imzalı URL/ham yanıt saklanmadığını doğrulayan test var.
3. `storage_upload_id` gerçek sağlayıcı `UploadId` uzunluğunu taşıyor; **kontrol objesi katmanı kaldırıldı**; upload → part → complete → cancel yolları MinIO'ya karşı hâlâ geçiyor; `_control/` altına artık hiçbir obje yazılmıyor (test var).
4. Fotoğraf durumu enum'da; **hiçbir kod yolu onu üretmiyor** (test var).
5. `approver` rolü enum'da ve rol matrisinde; hiçbir yazma yetkisi yok; W04'ün rol-kapsama testi zayıflatılmadan geçiyor.
6. `make verify` yeşil; test sayısı azalmıyor (şu an 392).
7. `ai-provider-routing.md` gerçeği anlatıyor; ADR-008'e geçici çözümün kaldırıldığı not düşüldü.
8. Kontrat drift yok (`make generate-docs` sonrası temiz).

## ADR numara kuralı

Numarayı **sen seçmiyorsun.** Gerçek bir karar çıkarsa `ADR-XXX-<konu>.md` yaz, başlıkta da `ADR-XXX` bırak, raporda bildir. PM numaralandırır.

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum doldurur — özellikle: migration downgrade veri kaybı, kontrol objesi kaldırıldıktan sonra yarım kalmış upload'lar, approver rolünün hiçbir yere sızmadığı)_
