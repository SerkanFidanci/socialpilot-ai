# W14 — Doğrulama bulgularının kapatılması, 2. tur

**Dal:** `slice/0p-verification-followups-2` · **Base:** `main` · **Migration slotu: YOK** (yeni revizyon yok; yalnızca `0011`'in downgrade'ine koruma eklenir)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high — biri yüksek şiddetli güvenlik bulgusu
**Neden bu iş:** Codex'in W10/W11 doğrulamasından üç açık bulgu + W13'ün bildirdiği iki tutarlılık borcu. Kaynaklar: [W10 Doğrulama bulgu 1](W10-schema-debt.md), [W11 Doğrulama bulgu 2 ve 3](W11-timeline-and-render.md), [W13 raporu](W13-script-generation.md).

## Okunacaklar

1. [`docs/STATUS.md`](../STATUS.md)
2. [`W11-timeline-and-render.md`](W11-timeline-and-render.md) — **Doğrulama bulgu 2 ve 3**
3. [`W10-schema-debt.md`](W10-schema-debt.md) — **Doğrulama bulgu 1**
4. `services/api/app/core/CLAUDE.md` (logging/telemetry), `services/api/app/modules/content/CLAUDE.md`
5. [`docs/architecture/error-handling.md`](../architecture/error-handling.md)

## Kalem 1 — Presigned URL log sızıntısı (YÜKSEK)

**Bulgu (Codex, W11 #3):** gerçek MinIO multipart akışında HTTP istemci `INFO` kaydı **tam imzalı URL'yi** (`X-Amz-Credential` + imza query parametreleriyle) stdout'a yazdı. W01'in sentinel testi uygulama loglarını ve DB satırlarını tarıyordu; **kütüphane logger'larını** (httpx/httpcore) taramıyordu — sızıntı oradan.

**Yapılacak:**
- Redaksiyon **logging altyapısı seviyesinde**: hangi logger yazarsa yazsın, kayıt içindeki imzalı query parametreleri (`X-Amz-Signature`, `X-Amz-Credential`, genel imza kalıpları) hiçbir handler çıktısına ulaşmadan maskelensin. Tek logger'ı susturmak yetmez — yarın başka bir kütüphane aynı şeyi yapar; **filtre merkezi olmalı** (`core/logging.py`).
- Ek olarak httpx/httpcore log seviyesi bilinçli bir değere çekilebilir (gerekçesiyle) — ama bu, filtrenin **yedeği** olur, yerine geçmez.
- Worker süreci de aynı filtreyi almalı (`worker/composition.py` logging kurulumunu kullanıyorsa doğrula).
- **Test, sızıntının bulunduğu yolda:** gerçek MinIO multipart upload/complete sırasında **tüm logging handler çıktısı** yakalanır; sentinel imza değeri hiçbir kayıtta geçmez. W01'in testinin kapsamadığı yüzey buydu — test artık kütüphane logger'larını da kapsıyor.

## Kalem 2 — Patch idempotency fingerprint'i gövdeyi kapsamıyor (orta)

**Bulgu (Codex, W11 #2):** aynı `Idempotency-Key` + aynı operasyon sayısı + **farklı metin** → `409 IDEMPOTENCY_CONFLICT` yerine `201` ve ilk revizyon dönüyor. Fingerprint yalnızca `operations` **sayısını** saklıyor.

**Yapılacak:**
- Fingerprint **kanonik istek gövdesinin tamamından** türetilir (kararlı serileştirme + hash). Aynı key + farklı gövde → `409`; aynı key + aynı gövde → saklanan sonuç.
- **Envanter çıkar:** idempotency kullanan tüm uçlar hangi fingerprint'i sağlıyor? Aynı kısayolu kullanan başka uç var mı? Ortak yardımcı varsa neden burada kullanılmamış? Listeyi rapora yaz; tespit edilen diğer eksikler de bu kalemde düzeltilir.
- Test sayılı girdilerle: aynı key + farklı metin → `409` · aynı key + aynı gövde → replay · farklı key + aynı gövde → yeni sonuç · alan sırası değişmiş ama eşdeğer gövde → replay (kanoniklik).

## Kalem 3 — `0011` downgrade'i uzun `UploadId` verisinde çöküyor (orta)

**Bulgu (Codex, W10 #1):** 288 karakterlik gerçek `UploadId` varken `alembic downgrade` `varchar(128)`'e daraltmada sürücü hatasıyla (`StringDataRightTruncationError`) duruyor. Veri kaybolmuyor ama hata anlaşılmaz ve kabul kriterinin "up→down→up" vaadi uzun veride tutmuyor.

**Yapılacak:**
- Kolon daraltmanın >128 karakterlik veriyi **koruyamayacağı** matematiksel gerçek — hedef veri kaybetmek değil, **anlaşılır şekilde durmak**: downgrade başında ön koşul kontrolü, sığmayan satır sayısını/örneğini adlandıran açık ve dokümante bir hata ile durur; sürücü hatası kullanıcıya ulaşmaz.
- Migration docstring'ine ve [ADR-008](../adr/ADR-008-s3-compatible-storage-adapter.md) notuna işle: bu downgrade yalnızca eski şekle sığan veriyle çalışır (dev-only kabul, üretim verisi yok).
- Test: 288 karakterlik ID ekle → downgrade → **açık hata mesajı** + verinin bozulmadığı doğrulanır; kısa veriyle downgrade tam çalışır.

## Kalem 4 — İzin hizalaması: timeline `BUSINESS_UPDATE`'e bağlı (W13 bulgusu)

W13, PRD §4 gereği (`editor` içerik üretir) `Permission.CONTENT_GENERATE` iznini ekledi — **PM onayladı.** Ama W11 timeline mutation'larını `BUSINESS_UPDATE`'e bağlamıştı; sonuç tutarsız: **editor senaryo üretebiliyor ama timeline oluşturamıyor.**

**Yapılacak:** timeline oluşturma/patch uçlarını `CONTENT_GENERATE`'e (veya uygun content iznine) geçir; rol matrisi testlerini güncelle (editor artık timeline da oluşturabiliyor; viewer/approver hâlâ hayır). [`tenant-isolation.md`](../architecture/tenant-isolation.md) tablosunu gerçeğe eşle.

## Kalem 5 — Küçük doküman borçları süpürmesi (W13 raporundan)

- `error-handling.md`'ye **W11'in `TIMELINE_*` kodları** eklenir (W13 kendi kodlarını ekledi, W11'inkiler eksik).
- `infrastructure/CLAUDE.md` bayat satırlar: `render/fake.py`, `render/ffmpeg.py`, `storage/s3.py` eklenir.
- `.env.example`'a `SCRIPT_GENERATION_*` anahtarları (güvenli varsayılanlarıyla, yorumlu).
- `forbidden_matcher` birleştirmesi **bu WO'da YOK** — timeline tarafını Türkçe `İ/I` katlamasına geçirmek davranış değişikliği; 2D (QC) slice'ına not düşüldü.

## Kapsam dışı (dokunma)

- **Migration revizyonu.** Yeni revizyon yok; yalnızca `0011` downgrade fonksiyonuna koruma. Yeni revizyon gerekiyorsa dur ve bildir.
- 2C (TTS) ve sonrası; gerçek sağlayıcı; `compose.yaml` (W06).
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.
- **`W13-script-generation.md`** — Codex W13 doğrulamasını paralel yazıyor olabilir; dokunma.

## Dokunulacak dosyalar (ilan)

```
services/api/app/core/logging.py                        (merkezi imza redaksiyon filtresi)
services/api/app/worker/composition.py                  (filtre worker'da da — minimum)
services/api/app/modules/content/service*.py            (patch fingerprint)
services/api/migrations/versions/0011_schema_debt.py    (downgrade ön koşul koruması)
services/api/app/modules/content/policy.py + api/routes/content.py   (izin hizalaması)
services/api/app/modules/businesses/policy.py           (yalnızca gerekiyorsa)
services/api/tests/unit/ + tests/integration/
docs/architecture/error-handling.md · tenant-isolation.md
services/api/app/infrastructure/CLAUDE.md · .env.example
docs/adr/ADR-008-s3-compatible-storage-adapter.md       (downgrade notu)
```

## Kabul kriterleri

1. **Sızıntı kapandı:** gerçek MinIO multipart akışında tüm logging handler çıktısı yakalanıyor ve sentinel imza hiçbir kayıtta yok; filtre logger-bağımsız (httpx dışında sentetik bir logger'dan da denenmiş); worker tarafı dahil.
2. Fingerprint kanonik gövdeden: 4 sayılı girdi (farklı metin → `409`, aynı gövde → replay, farklı key → yeni, eşdeğer-sıralı gövde → replay) ayrı ayrı test edildi; idempotency envanteri raporda.
3. `0011` downgrade: uzun veride açık dokümante hata + veri bozulmadı; kısa veride tam çalışıyor; ADR-008 notu düşüldü.
4. Editor timeline oluşturabiliyor/patch'leyebiliyor; viewer/approver hayır; matris dokümanı gerçekle eşleşiyor.
5. Doküman borçları kapandı (TIMELINE_* katalogda, CLAUDE.md güncel, .env.example tam).
6. `make verify` yeşil; test sayısı azalmıyor (şu an **591**); Alembic head değişmedi (`0013_script_generation`); kontrat drift yoksa dokunulmadı, varsa yeniden üretildi.

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum doldurur — özellikle: imza redaksiyonunu başka bir logger üzerinden atlatma, fingerprint kanonikliğini alan sırası/whitespace ile atlatma, downgrade korumasını boş tabloda yanlış tetikleme)_
