# W07 — Tek sunucu dayanıklılığı: kaynak limitleri + yedekleme

**Dal:** `slice/0l-single-server-resilience` · **Base:** `main` · **Migration slotu:** yok · **W08 ile paralel** (dosya-ayrık)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 4.8 / medium
**Neden bu iş:** K5 kararının doğrudan sonucu. Ürün **tek, ucuz, dedike bir sunucuda** çalışacak. Bu iki şeyi zorunlu kılıyor ve ikisi de şu an **yok**: (1) hiçbir Compose servisinin CPU/RAM limiti tanımlı değil — tek makinede ağır bir render veya analiz API'yi açlığa sürükler ve kullanıcı "uygulama dondu" der; (2) yedekleme yok — tek sunucu tek arıza noktası ve **üretim veritabanı git'te olmayacak.** 2026-07-30'da Docker'ı kaybettik ve zararsızdı çünkü her şey git'teydi; üretimde aynı olay veri kaybıdır.

## Okunacaklar

Router: [`docs/index.md`](../index.md) → "Mimari değişiklik" satırı. Asgari set:

1. [`docs/STATUS.md`](../STATUS.md) — özellikle **K5** bölümünün tamamı
2. `compose.yaml` ve [`docs/runbooks/local-development.md`](../runbooks/local-development.md)
3. [`docs/product/requirements/95-observability.md`](../product/requirements/95-observability.md) — §38.3 backpressure, kuyruk başına kaynak profili
4. [`docs/product/requirements/40b-scenario-render-lifecycle.md`](../product/requirements/40b-scenario-render-lifecycle.md) — §19.3 worker izolasyonu (disk kotası, geçici dizin temizliği, timeout)
5. `services/api/app/worker/CLAUDE.md` ve `services/api/app/infrastructure/CLAUDE.md`

## Kapsam

### 1. Kaynak limitleri (tek makinede komşu açlığını engelle)

- `compose.yaml`'daki her servise **açık CPU ve bellek limiti**. Rakamlar hedef sunucuyu (6–8 çekirdek, 32–64 GB) varsayarak belirlenir ve **yorumla gerekçelendirilir**; sihirli sayı bırakılmaz.
- **Öncelik sırası:** API asla açlığa düşmez. Sıra: `postgres` > `api` > `redis` > worker'lar. Worker'lar artan yükte ilk fren yiyen taraftır.
- Worker eşzamanlılığı ağır kuyruklarda **1–2** ile sınırlanır; hafif kuyruklar (outbox dispatch, bildirim) ayrı ve daha yüksek olabilir. PRD §38.2'deki kuyruk listesi kaynak profiline eşlenir.
- FFmpeg alt süreçleri **düşük öncelikle** çalışır (`nice`/`ionice` eşdeğeri). CPU'yu API'den önce bırakmalı.
- **Disk kotası ve geçici dizin temizliği zorlanır.** §19.3 zaten istiyor; tek sunucuda ihlali makineyi öldürür. Scratch alanı için bir üst sınır ve sınır aşımında işi `failed` yapan bir kontrol gerekiyor — sessizce diski doldurmak yasak.
- Sağlık kontrolleri ve `restart` politikaları limitlerle tutarlı olmalı: OOM-kill döngüsüne giren bir servis sonsuz yeniden başlamamalı.

### 2. Yedekleme (test edilmeyen yedek yedek değildir)

- **Sunucu dışına** otomatik günlük `pg_dump` — hedef object storage (R2/S3, `S3_*` konfigürasyonu zaten var). Yedek aynı diskte tutulmaz.
- Saklama politikası: günlük N gün + haftalık M hafta (rakamlar gerekçeli).
- **Geri yükleme provası zorunlu** ve script'lenmiş: boş bir veritabanına yedeği geri yükle, Alembic head'i doğrula, birkaç tablo için satır sayısı kontrolü yap. Bu bir kabul kriteri, dokümante bir öneri değil.
- Yedek **şifrelenir** ve içinde credential/token bulunmadığı doğrulanır (`oauth_credentials` tablosu envelope encryption'lı; dump'ta düz metin token olmamalı).
- Yedek başarısızlığı **sessiz kalamaz**: başarısız işten sonra bir operasyonel event/log üretilir. Metrik W05'e (OTel) bırakılabilir ama log zorunlu.

### 3. Dağıtım topolojisi ADR'ı

Tek sunucu kararını, servis yerleşimini, kaynak bütçesini, yedekleme stratejisini ve **ölçek çıkışını** (ikinci bir yalnızca-worker makinesi eklemek konfigürasyon işidir) kaydeden bir ADR. Numarayı **PM merge sırasında** verecek — dosyayı `docs/adr/ADR-XXX-single-server-deployment-topology.md` adıyla yaz ve raporda bildir (bkz. aşağıdaki numara kuralı).

## Kapsam dışı (dokunma)

- **Üretim sunucusu satın alma, provisioning, DNS, TLS, gerçek deploy.** Bu WO yalnızca depo tarafındaki yapılandırmayı ve script'leri üretir.
- **PostgreSQL 18 / Valkey imaj geçişi** → W06.
- **OpenTelemetry metrikleri** → W05. Bu WO yalnızca log üretir.
- **Migration.** Şema değişikliği gerekiyorsa dur ve rapora yaz.
- `services/api/app/**` altındaki uygulama mantığı — yalnızca alt süreç önceliği ve scratch sınırı için gereken minimum dokunuş; her satırı rapora yaz.
- `docs/index.md`, `docs/adr/README.md` → **ADR'ını indekse ekleme**, raporda bildir (PM bağlar).

## Dokunulacak dosyalar (ilan)

```
compose.yaml
.env.example
services/api/scripts/backup_db.py            (veya .sh — gerekçesini yaz)
services/api/scripts/restore_check.py        (geri yükleme provası)
services/api/app/worker/                     (scratch sınırı / alt süreç önceliği, minimum)
services/api/tests/unit/                     (scratch sınırı ve yedek script birim testleri)
Makefile                                     (backup / restore-check hedefleri)
docs/runbooks/local-development.md           (veya yeni docs/runbooks/operations.md)
docs/adr/ADR-XXX-single-server-deployment-topology.md   (yeni)
```

## Kabul kriterleri

1. Her Compose servisinin açık CPU/RAM limiti var ve her rakam yorumla gerekçelendirilmiş.
2. Ağır bir worker işi çalışırken `/health/ready` **200 dönmeye devam ediyor** ve yanıt süresi kabul edilebilir kalıyor. Bunu gösteren tekrarlanabilir bir ölçüm raporda var (yükü nasıl ürettiğin dahil).
3. Scratch alanı üst sınırı aşıldığında iş dokümante bir hata koduyla `failed` oluyor; disk sessizce dolmuyor; test var.
4. `pg_dump` yedeği object storage'a yazılıyor; aynı diskte kopya bırakılmıyor.
5. **Geri yükleme provası çalışıyor:** boş veritabanına restore → Alembic head doğru → satır sayısı kontrolü geçiyor. Tek komutla koşuyor ve `make` hedefi var.
6. Yedek şifreli ve dump'ta düz metin OAuth token'ı bulunmadığı doğrulanmış.
7. Yedek başarısızlığı log'da açıkça görünüyor (sessiz başarısızlık yok); test var.
8. OOM/restart döngüsü koruması var: sürekli yeniden başlayan servis sonsuz döngüye girmiyor.
9. `make verify` yeşil, Alembic head değişmemiş.
10. Deployment topolojisi ADR'ı yazıldı (indekse eklenmedi, raporda bildirildi).

## Numara kuralı (yeni)

**ADR numarasını sen seçmiyorsun.** 2026-07-30'da W02 ve W09 paralel çalışırken ikisi de ADR-009'u aldı. Dosyayı `ADR-XXX-<konu>.md` adıyla yaz, içindeki başlıkta da `ADR-XXX` bırak, raporda "numara bekliyor" diye bildir. **PM merge sırasında numaralandırır.**

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum doldurur — özellikle: yedeği gerçekten geri yükleyip head ve satır sayılarını bağımsız doğrula; kaynak limitlerini yük altında sına)_
