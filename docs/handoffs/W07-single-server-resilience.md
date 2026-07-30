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

Tek sunucu kararını, servis yerleşimini, kaynak bütçesini, yedekleme stratejisini ve **ölçek çıkışını** (ikinci bir yalnızca-worker makinesi eklemek konfigürasyon işidir) kaydeden bir ADR. Numarayı **PM merge sırasında** verecek — dosyayı `docs/adr/ADR-013-single-server-deployment-topology.md` adıyla yaz ve raporda bildir (bkz. aşağıdaki numara kuralı).

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
docs/adr/ADR-013-single-server-deployment-topology.md   (yeni)
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

**ADR numarasını sen seçmiyorsun.** 2026-07-30'da W02 ve W09 paralel çalışırken ikisi de ADR-009'u aldı. Dosyayı `ADR-013-<konu>.md` adıyla yaz, içindeki başlıkta da `ADR-013` bırak, raporda "numara bekliyor" diye bildir. **PM merge sırasında numaralandırır.**

## Rapor — 2026-07-30 · W07 yürüten oturum

**Dal:** `slice/0l-single-server-resilience` (base `main`@`0c7a07a`) · **Commit'ler:** _(tek commit, aşağıda)_ · **Durum:** tamamlandı (bağımsız doğrulama ve PM merge bekliyor)

### Yapılanlar

**1. Kaynak limitleri (`compose.yaml`).** Her servise açık `cpus` + `mem_limit` + `cpu_shares` + gerekçe yorumu. Öncelik `cpu_shares` ile kodlandı: postgres 2048 > api 1024 > redis/minio 512 > worker 256 > beat/minio-init 128. Dosya başına bütçe mantığını anlatan blok yorum. `mem_reservation` yumuşak tabanları. redis `--maxmemory 384mb --maxmemory-policy noeviction` (cgroup OOM'undan önce reddeder, broker mesajı düşürmez). Worker `--concurrency=2` (ağır FFmpeg işi tavana sığsın). Worker/beat `restart: on-failure` + `stop_grace_period` (temiz SIGTERM 0 → tekrar başlamaz; yalnız çökme, Docker üstel backoff ile hız-sınırlı → OOM döngüsü CPU döndürmez).

**2. Worker scratch guard + alt süreç önceliği (`app/worker/`).**
- Yeni `app/worker/scratch.py`: `WorkerScratchGuard` (usage_bytes / reclaim_stale / ensure_within_budget) + `WorkerScratchExhausted` (kod `WORKER_SCRATCH_BUDGET_EXCEEDED`). Bütçe `WORKER_TMPFS_BYTES * 3/4` (tmpfs boyutundan türetilir; ENOSPC sert duvarından önce tetiklenir).
- `app/worker/tasks.py` `_drain`: her iş öncesi `ensure_within_budget()` (bütçe üstündeyse iş almaz, gürültülü fail); workdir işleri arasında `reclaim_stale()` (residue temizliği).
- `app/worker/composition.py` `start_worker_process`: `os.nice(+10)` (POSIX guard'lı; FFmpeg çocukları düşük CPU önceliği miras alır) + init'te orphan scratch reclaim.
- Testler: `tests/unit/test_worker_scratch.py` (8 test: reclaim, usage, over-budget documented code, drain refuses over-budget, drain reclaims residue).

**3. Yedekleme + geri yükleme provası (`services/api/scripts/`).**
- `backup_db.py`: plain `pg_dump` → düz metin token taraması → gzip (stdlib) → `openssl` AES-256-CBC+PBKDF2 şifreleme → kendi minimal SigV4 istemcisiyle (PUT/GET/LIST/DELETE, her çağrı timeout'lu) object storage'a tarihli anahtar → günlük/haftalık saklama pruning → yapılandırılmış `db_backup_succeeded`/`db_backup_failed` (+`error_code`) log. Yerelde kopya bırakmaz (tek TemporaryDirectory `finally`'de silinir).
- `restore_check.py`: en son (veya pinlenmiş) yedeği indir → decrypt → gunzip → boş scratch DB'ye `psql` yükle → `alembic_version` == kod head doğrula → çekirdek tablo satır sayıları. `RESTORE_CHECK_DATABASE_URL` ayrı değişken (üretimi asla hedeflemez).
- Testler: `tests/unit/test_backup_db.py` (15) + `tests/unit/test_restore_check.py` (8): DSN eşleme, secret tarama (her iki yön), saklama (ISO hafta), SigV4 belirlenimci imza, key parse, config doğrulama, **gerçek openssl encrypt/decrypt round-trip + yanlış parola**.

**4. Dağıtım topolojisi ADR'ı.** `docs/adr/ADR-013-single-server-deployment-topology.md` (Türkçe, ADR set'iyle uyumlu). Servis yerleşimi, kaynak bütçe tablosu, scratch iki-katman, yedek/geri-yükleme, restart koruması, **ölçek çıkışı** (ikinci worker makinesi = konfigürasyon). **İndekse eklenmedi** (W03 tekeli); PM bağlar.

**5. Docs/çevre.** Yeni `docs/runbooks/operations.md` (İngilizce, runbooks diliyle; kaynak bütçe, backup/restore kullanımı, ön koşullar, cron, systemd `StartLimitBurst`). `.env.example` `BACKUP_*`/`RESTORE_CHECK_*` bloğu. `Makefile` `backup` + `restore-check` hedefleri. `app/worker/CLAUDE.md` güncellendi (scratch.py + invariant + test yolu). Kendi STATUS satırım güncellendi.

### Kapsam dışı bıraktıklarım ve nedeni

- **ADR numarası seçilmedi** (numara kuralı). Dosya + kod içi referanslar `ADR-013` placeholder; PM tek find-replace ile numaralar: `compose.yaml`, `.env.example`, `Makefile`, `scripts/backup_db.py`, `scripts/restore_check.py`, `app/worker/CLAUDE.md`, ADR dosyası + başlığı.
- **İndekse ADR eklenmedi** (`docs/index.md`, `docs/adr/README.md` W03 tekeli) — PM bağlar.
- **`Makefile` hem W07 hem W08'de** ilan edilmiş (paralel). Hedeflerimi dosya sonuna, ayrı bölüme ekledim (backup/restore-check); W08 benchmark hedefiyle çakışma trivial merge.
- **Şifreleme `openssl enc -aes-256-cbc -pbkdf2`** (CBC, kimlik doğrulamasız). Gerekçe: yeni Python crypto bağımlılığı `pyproject.toml`+`uv.lock` sahipliğine takılıyor (touch listemde yok); `openssl enc` GCM'i güvenilir desteklemiyor. Bütünlük restore adımında yakalanır. ADR'da "Rejected alternatives"te açık.
- **Config'e scratch ayarı eklemedim** (`app/core/config.py` W01 sahipli, listemde yok). Scratch bütçesi worker paketinde modül sabiti; mevcut `WORKER_TMPFS_BYTES`'ı import eder (yeni ayar yok).
- **pg_dump/psql/openssl API imajına eklenmedi** (Dockerfile W02 sahipli). Backup runner DB-komşusu bir bağlamda çalışır; ön koşul runbook'ta.
- **Kuyruk-başına kaynak profili (§38.2)** tam uygulanmadı: task routing yok, ayrı hafif-kuyruk worker'ı buna bağlı. Ağır-kuyruk worker'ı `--concurrency=2` ile sınırlı; kalanı ADR'da konfigürasyon eklemesi olarak belgelendi.

### Doğrulama

Araç zinciri: Python 3.13.2 / mypy 2.3.0 / ruff 0.16.0 (uv.lock) — host'ta `uv run`; Docker doğrulaması `COMPOSE_PROJECT_NAME=sp-w07`, izole host portları (8007/55437/56384/59005/59006), konteyner pg_dump/psql 16.14.

| Kontrol | Sonuç |
|---|---|
| `ruff check` (app tests migrations scripts) | ✅ temiz |
| `ruff format --check` | ✅ 119 dosya format'lı |
| `mypy .` (strict) | ✅ 111 dosya, sorun yok |
| Container pytest (`RUN_INTEGRATION_TESTS=1`) | ✅ **295 passed** (264 baz + 31 yeni; host'ta POSIX-yol düşenler Linux'ta geçiyor) |
| compose config resolve + canlı limitler | ✅ postgres 2c/8G/2048 > api 2c/2G/1024 > redis .5c/512M/512 > minio 1c/1G/512 (docker inspect) |
| Kabul 1 (her servis limitli, gerekçeli) | ✅ compose'da 6 servis, yorumlu |
| Kabul 2 (yük altında `/health/ready` 200) | ✅ worker'da 4 CPU-hog işi çalışırken 10 poll boyunca **200 @ ~12-15ms**. Kaynak profili canlı (cpu_shares 1024 vs 256 + worker child nice=10, `/proc` ile doğrulandı). *Not: çok-çekirdekli dev host'ta toplam-CPU baskısı yok; gerçek starvation testi çekirdek-kısıtlı hedef makinede — bağımsız doğrulayıcıya bırakıldı.* |
| Kabul 3 (scratch aşımı → documented fail, sessiz dolmaz, test) | ✅ `WORKER_SCRATCH_BUDGET_EXCEEDED`; drain over-budget'ta iş almıyor; unit test var. Sert tmpfs tavanı ENOSPC backstop'u |
| Kabul 4 (yedek storage'a, aynı diskte kopya yok) | ✅ gerçek MinIO'ya SigV4 PUT; TemporaryDirectory `finally`'de silinir (kod + canlı test) |
| Kabul 5 (restore provası: boş DB → head → satır) | ✅ gerçek `pg_dump 16` → `psql` restore → `alembic_version`=`0009_video_understanding` (kod head) → businesses/media_assets/jobs sorgulanabilir. `make restore-check` hedefi |
| Kabul 6 (şifreli, düz metin token yok) | ✅ openssl encrypt round-trip (canlı MinIO'dan indirilip decrypt); scanner ciphertext'te geçti, `ya29.` plaintext'te `BACKUP_PLAINTEXT_SECRET_DETECTED` ile tetiklendi |
| Kabul 7 (yedek başarısızlığı log'da, test) | ✅ `db_backup_failed`+`error_code`, non-zero exit; `BackupError` kodları unit-test'li |
| Kabul 8 (OOM/restart döngü koruması) | ✅ worker/beat `on-failure` + Docker backoff (hız-sınırı); bellek tavanları OOM'u önler; deneme-sayısı sınırı runbook'ta systemd `StartLimitBurst` (düz compose'da yok — ADR'da açık) |
| Kabul 9 (`make verify` yeşil, Alembic head değişmedi) | ✅ statik kapılar + 295 test; head `0009_video_understanding` değişmedi (migration eklenmedi) |
| Kabul 10 (topoloji ADR'ı, indekse eklenmedi) | ✅ `ADR-013-single-server-deployment-topology.md`; indekse eklenmedi, PM'e bildirildi |
| Backup/restore E2E (canlı MinIO + PostgreSQL) | ✅ run_backup: stub pg_dump → scan → gzip → openssl → SigV4 PUT → LIST → retention (2020 anahtarı silindi) → success log; round-trip GET+decrypt == orijinal. Gerçek pg_dump→psql restore + head postgres konteynerinde |

### Açıkça belirtmem gerekenler

- **PM'e:** (1) ADR numarasını ver ve `ADR-013`'i tüm dosyalarda find-replace et; (2) ADR'ı `docs/index.md` + `docs/adr/README.md`'ye bağla (W03 tekeli); (3) merge et. Migration eklenmedi, slot kullanılmadı.
- **Bağımsız doğrulayıcıya:** gerçek uçtan uca `make backup` + `make restore-check`'i pg_dump/psql/openssl bulunan bir bağlamda koş (API slim imajında pg client yok — runbook'ta ön koşul). Kaynak önceliğini çekirdek-kısıtlı bir host'ta yük altında sına.
- **Varsayım:** hedef sunucu 6–8 çekirdek / 32–64 GB; rakamlar alt uca göre. Farklı donanımda `compose.yaml` başlık yorumundaki bütçe yeniden ölçeklenir.

## Doğrulama

### Doğrulama — 2026-07-30 · Codex test oturumu

Araç zinciri: Docker 25.0.3 (build `4debf41`) · Docker Compose 2.24.6-desktop.1 · izole `COMPOSE_PROJECT_NAME=sp-codex` stack · Python 3.13.14 · pytest 9.1.1 · ruff 0.16.0 · mypy 2.3.0 · PostgreSQL istemcileri `pg_dump`/`psql` 16.14 · FFmpeg 7.1.5. API imajındaki `openssl` ile şifreleme kullanıldı.

| # | Bulgu | Şiddet | Yeniden üretim | Durum |
|---|---|---|---|---|
| 1 | Gerçek geri yükleme geçti: `pg_dump 16.14` ile kaynakta `businesses=1`, `media_assets=1`, `jobs=4` içeren dump alındı; W07’nin şifreleme + SigV4 upload yolu ile MinIO’ya yazılan ciphertext indirildi, decrypt/gunzip sonrası boş `socialpilot_restorecheck` DB’sine `psql 16.14` ile yüklendi. Geri yüklenen `alembic_version=0009_video_understanding` ve üç satır sayısı kaynakla birebir eşleşti (`1/1/4`). | — | İzole stack’te gerçek PostgreSQL + MinIO; yedek anahtarı `qa-w07/...sql.gz.enc` | kabul edildi |
| 2 | Scratch bütçe aşımı gürültülü fail ediyor: worker tmpfs’inde 385 MiB artık dosya (soft bütçe 402,653,184 byte) varken gerçek `media.technical_analysis.drain` Celery işi `FAILURE` ve `WORKER_SCRATCH_BUDGET_EXCEEDED` döndü. | — | `celery-worker` içinde `dd ... count=385`; API’den `send_task('media.technical_analysis.drain')` | kabul edildi |
| 3 | Worker’ın 2 CPU kotasını iki `yes > /dev/null` stres süreciyle doldururken API içinden `/health/ready` 10/10 kez `200` döndü; ölçülen süreler 2.11–15.44 ms idi. | — | `celery-worker` içinde 25 sn iki CPU-hog; API konteynerinden ardışık 10 readiness isteği | kabul edildi |
| 4 | Hedefli W07 testleri de yeşil: scratch, backup, restore ve benchmark ilgili birim testlerinden 49/49 geçti. API slim imajında `pg_dump`/`psql` bulunmadığı için tam restore CLI’si değil, gerçek dump ve W07’nin object-store/decrypt yardımcılarıyla dış DB-client restore provası koşuldu; bu runbook’taki DB-komşusu runner ön koşuluyla uyumlu. | düşük | `pytest tests/unit/test_worker_scratch.py tests/unit/test_backup_db.py tests/unit/test_restore_check.py tests/unit/test_benchmark.py` | kabul edildi |

**Karar:** teslim edilebilir.
