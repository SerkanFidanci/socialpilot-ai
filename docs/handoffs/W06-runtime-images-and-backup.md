# W06 — Çalışma zamanı imajları + çalıştırılabilir yedek runner'ı (D1 kapısı)

**Dal:** `slice/0j-runtime-images` · **Base:** `main` · **Migration slotu: YOK**
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 4.8 / medium
**Neden bu iş:** Phase 2'nin başında bekletilmişti; şimdi sırası. İki iş bir arada: (1) çalışma zamanı imajları güncel sürümlere taşınıyor, (2) W07'nin yazdığı yedekleme **çalıştırılabilir bir compose servisi** hâline geliyor. İkincisi **D1 dağıtım kapısını kapatır** — bugün yedek alma yordamı var ama onu koşan bir şey yok, yani üretime çıkarsak ilk gün yedeksiz oluruz.

## Okunacaklar

1. [`docs/STATUS.md`](../STATUS.md) — dağıtım kapıları D1–D3
2. [W07](W07-single-server-resilience.md) — `pg_dump` + geri yükleme provası, ADR-013 (tek sunucu topolojisi, kaynak limitleri, öncelik sırası)
3. `compose.yaml`, `.github/workflows/verify.yml`, `services/api/Dockerfile`
4. `services/api/scripts/backup_db.py` ve `restore_check.py` (W07)
5. `docs/runbooks/local-development.md`

## PM kararları

### 1. Sürümü **varsayma, doğrula**

İş emrini yazarken PostgreSQL 18 ve Valkey'i hedef olarak koyuyorum, ama **imaj etiketlerini ve güncel kararlı sürümleri kendin doğrula** — bu WO Phase 2 boyunca bekledi, aradan aylar geçti. Daha yeni bir kararlı sürüm varsa onu al ve gerekçesini yaz; beta/RC alma. Seçtiğin sürümü raporda **kanıtıyla** yaz (imaj etiketi + `SELECT version()` çıktısı).

### 2. PostgreSQL geçişi: dev'de temiz kurulum, üretim için **yazılı yordam**

Dev ortamında volume silinip şema `0001→0020` zincirinden yeniden üretilebilir — veri taşımaya gerek yok. **Ama üretim için yordam yazılmalı** (runbook): major sürüm yükseltmesi `pg_upgrade` mi, dump/restore mu, kesinti süresi ne. Bu bir doküman işi, kod değil; W07'nin restore provasıyla tutarlı olsun.

**Uyarı:** `0020_ledger_integrity` trigger, kısmi index ve advisory lock kullanıyor; yeni sürümde davranışın aynı olduğunu **testle** doğrula (1474 testin tamamı yeni imajda geçmeli, özellikle eşzamanlılık testleri).

### 3. Redis → Valkey: broker davranışı **kanıtlanmalı**

Valkey protokol uyumlu ama "uyumlu olması gerekir" yetmez. Celery broker, sonuç backend'i, beat kilidi ve outbox publisher'ın kullandığı her yol yeni imajda **gerçekten** koşulmalı. Uyumsuzluk çıkarsa **dur ve bildir** — Redis'te kalmak da geçerli bir sonuçtur, gerekçesini yazarsın.

### 4. Yedek runner'ı **profil servisi**, her zaman açık değil

`compose.yaml`'a `--profile backup` ile çalışan bir servis: `backup_db.py`'yi zamanlanmış olarak koşar, çıktıyı **sunucu dışına** yazar (W07'nin şifreli hedefi), ve `restore_check.py` ile periyodik prova yapar. Tek sunucu topolojisinde (ADR-013) her zaman açık bir konteyner daha istemiyoruz; profil + host cron/systemd timer da kabul edilebilir — **hangisini seçtiğini ve neden** raporda yaz.

**Yedeğin kendisi test edilmeden yedek sayılmaz** (W07'nin kuralı): runner en az bir kez gerçekten çalıştırılıp, aldığı dosyadan **gerçek bir geri yükleme** yapılmalı ve satır sayıları karşılaştırılmalı.

### 5. CI ve runbook birlikte güncellenir

`.github/workflows/verify.yml`'deki servis imajları, `docs/runbooks/local-development.md`, `.env.example` — üçü de yeni sürümlere ve yeni profile göre güncel olmalı. Bir geliştirici runbook'u izleyerek sıfırdan kurabilmeli.

## Kapsam dışı (dokunma)

- Uygulama kodu, migration'lar, iş mantığı — bu WO **altyapı** işidir. `services/api/app/**` altında değişiklik beklenmiyor; gerekirse dur ve bildir.
- Üretim sunucusu kurulumu, domain, TLS, CDN → ayrı iş (D2/D3).
- Gerçek AI sağlayıcıları, Phase 3.

## Dokunulacak dosyalar (ilan)

```
compose.yaml                                   (imaj sürümleri + backup profili)
.github/workflows/verify.yml                   (servis imajları)
services/api/Dockerfile                        (temel imaj güncelse)
services/api/scripts/backup_db.py · restore_check.py   (runner'a bağlanması gerekiyorsa)
.env.example
docs/runbooks/local-development.md             (+ üretim yükseltme yordamı; yeni runbook dosyası açabilirsin)
docs/architecture/ (dağıtım topolojisi bölümü — hangi dosyaya yazdığını bildir)
```

## Kabul kriterleri

1. **Sürüm kanıtı:** seçilen PostgreSQL ve Valkey/Redis sürümleri, imaj etiketi + çalışan konteynerden alınmış sürüm çıktısıyla raporda.
2. **Şema sıfırdan üretiliyor:** boş volume üzerinde `0001→0020` zinciri temiz koşuyor; `downgrade base` → `upgrade head` de temiz.
3. **Tam regresyon yeni imajlarda:** `make verify` yeşil; **1474 testin tamamı** geçiyor, hiçbiri düzenlenmeden. Eşzamanlılık ve defter testleri ayrıca vurgulanır (W23'ün trigger/kilit yapısı yeni sürümde aynı davranıyor).
4. **Valkey kanıtı:** Celery broker + beat + outbox publisher yeni imajda gerçekten çalışıyor (worker profili ayağa kalkıyor, bir job uçtan uca akıyor). Uyumsuzluk varsa Redis'te kalma kararı gerekçeli.
5. **Yedek runner'ı gerçekten koştu:** en az bir yedek alındı, **o yedekten geri yükleme yapıldı**, satır sayıları karşılaştırıldı (W07'nin provası). Çıktı sunucu dışına yazılıyor ve **şifreli**.
6. **Kaynak limitleri korundu** (ADR-013): yeni servis öncelik sırasını bozmuyor; `docker stats` ile ölçüm raporda.
7. **Runbook izlenebilir:** sıfırdan kurulum adımları güncel; üretim major-sürüm yükseltme yordamı yazılı.
8. `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.
9. Rapor + araç zinciri sürümleri. **Merge etme, dalda bırak.**

## ADR numara kuralı

Sürüm/topoloji kararı ADR-013'e **ek** olarak yazılabilir; gerçek bir karar çıkarsa `ADR-XXX-<konu>.md`, numarayı PM verir.

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum: **kendi girdilerini üret.** Yedeği bozup geri yüklemeyi denet, yedek dosyası şifreli mi gerçekten kontrol et, runner'ı iki kez koşup çakışma üret, Valkey'i durdurup Celery'nin davranışını gör, disk dolu senaryosunda yedeğin sessizce başarısız olup olmadığını sına)_
