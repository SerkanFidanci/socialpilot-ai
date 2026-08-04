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

## Rapor — 2026-08-04 · Claude Opus 5 (high)

**Dal:** `slice/0j-runtime-images` · **Durum:** tamamlandı, dalda (merge edilmedi)
**Doğrulama ortamı:** `COMPOSE_PROJECT_NAME=sp-w06`, worktree kökünden, ayrı port bloğu
(API 8006 · postgres 55506 · valkey 56506 · minio 59006/59007) — paylaşılan konteyner ele
geçirilmedi.

### 1. Sürüm kanıtı (kabul kriteri 1)

Her satır registry'den yazıldığı anda doğrulandı; hiçbiri hafızadan yazılmadı.

| Runtime | Etiket | Çalışan konteynerden alınan kanıt |
|---|---|---|
| PostgreSQL | `postgres:18.4-alpine` | `PostgreSQL 18.4 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit` |
| Broker/cache | `valkey/valkey:9.1.1-alpine` | `valkey_version:9.1.1` · `server_name:valkey` (uyumluluk için `redis_version:7.2.4` bildiriyor) |
| API/worker/beat | `python:3.13-slim`, `runtime` hedefi | `Python 3.13.14` (Debian 13 trixie) |
| Yedek runner'ı | `runtime` + `postgresql-client-18` + `openssl`, `backup` hedefi | `pg_dump 18.4 (Debian 18.4-1.pgdg13+1)` · `OpenSSL 3.5.6` |

**Alınmayanlar ve nedeni:** PostgreSQL **19beta2** registry'de var — beta sunucu alınmaz.
Valkey **`unstable`** etiketi var — aynı sebep. **Python 3.14.6** var ve `requires-python
>=3.13` izin verirdi; alınmadı çünkü `uv.lock`'un yeniden çözülmesi ve tüm bağımlılık kümesinin
yeniden doğrulanması demek — bir imaj işine sıkıştırılacak karar değil, kendi işi (PM kuyruğuna).

**Alpine'da kalındı, Debian'a geçilmedi.** Major sürüm atlaması zaten yeterli değişken getiriyor;
musl'da kalmak libc collation sağlayıcısını aynı bırakıyor (`datlocprovider = c`,
`datcollate = en_US.utf8` — restore sonrası doğrulandı), yani hiçbir metin index'i altımızdan
sıralama değiştirmiyor.

### 2. Şema sıfırdan üretiliyor (kabul kriteri 2)

Boş volume üzerinde, `postgres:18.4-alpine`'da: **20 upgrade** → `0020_ledger_integrity (head)`
→ **20 downgrade** → base → **20 upgrade** → `0020_ledger_integrity (head)`. Zincirde
düzenlenen hiçbir migration yok.

### 3. Tam regresyon (kabul kriteri 3)

**1474 passed** (`RUN_INTEGRATION_TESTS=1`, gerçek PostgreSQL 18.4 + Valkey 9.1.1 + MinIO +
FFmpeg), hiçbir test düzenlenmeden — taban 1474 ile birebir. `ruff check` temiz ·
`ruff format --check` 234 dosya · `mypy` 220 dosyada temiz · `check-openapi` **byte özdeş**
(kontrat konteyner içinde yeniden üretilip diff'lendi).

W23'ün trigger/kilit/kısmi index makinesi 18.4'te aynı davranıyor — eşzamanlılık ve defter
testlerinin tamamı süitin içinde geçti, ayrıca §5'te geri yüklenmiş veritabanına karşı elle
saldırıldı.

### 4. Valkey kanıtı (kabul kriteri 4) — ADR-010 kabul edildi

Uyumsuzluk çıkmadı, Redis'te kalma gerekçesi gerekmedi. Kanıtlar:

- **Broker + sonuç backend'i:** `send_task` → `AsyncResult.get()` gidiş-dönüş
  (`redis://valkey:6379/1` ve `/2`).
- **Beat:** `Sending due task dispatch-outbox` tick'leri, worker `Connected to
  redis://valkey:6379/1` → `celery@... ready` → görev `succeeded`.
- **Outbox publisher'ı uçtan uca:** PostgreSQL'e gerçek bir `media.technical_analysis.requested`
  satırı yazıldı → beat tick'i → `operations.outbox.dispatch` **`processed: 1`** →
  publisher Valkey'e `media.technical_analysis.drain` yolladı → worker onu tüketip koştu →
  outbox satırı `published`. Tam zincir yeni imajda, iki kez (ilk volume ve sıfırlanmış volume).
- **15 görevin tamamı** worker'da kayıtlı (`inspect registered` broker üstünden döndü).

`redis-py 8.1.0` / `celery 5.6.3` / kombu değişmedi. Uygulama tarafı isimler (`REDIS_URL`,
`REDIS_PORT`, `redis://`) **bilinçli olarak** korundu: istemci gerçekten `redis-py`, şema
gerçekten `redis://`, ve `app/core/config.py` bu WO'nun kapsamı dışında. Yalnızca **sunucu olan
şey** — compose servisi — `valkey` adını aldı.

### 5. Yedek runner'ı gerçekten koştu (kabul kriteri 5)

Sıfırlanmış volume üzerinde, seed + gerçek defter verisiyle (grant +10, rezervasyon −5):

| Adım | Sonuç |
|---|---|
| `docker compose --profile backup up -d` | `backup` exit **0**, `restore-check` exit **0** |
| Yedek | `db_backup_succeeded` · 18 144 bayt ciphertext · sha256 kayıtlı · sunucu diskinde kopya yok |
| **Şifreli mi** | İlk 8 bayt `Salted__` · gzip magic **yok** · `CREATE TABLE` **yok** · `Demo Isletme` **yok** |
| Geri yükleme | `db_restore_check_succeeded` · **aynı nesneden** · head `0020_ledger_integrity` |
| **Satır sayıları** | kaynak `businesses=1 media_assets=1 jobs=4 credit_ledger=2 usage_reservations=1 anchors=1` → geri yüklenen **birebir aynısı** |
| İki kez koşma | Aynı sonuç; scratch veritabanı her koşuda düşürülüp yeniden yaratılıyor |

**Geri yüklenen veritabanı `0020`'nin makinesini de taşıyor** — satırları değil, muhafızları:
`trg_credit_ledger_append_only` + `trg_credit_ledger_insert_guard` ikisi de kurulu, dört `uq_*`
index'i yerinde. Ham SQL ile saldırıldı: `UPDATE credit_ledger` → *"append-only; write a
compensating entry instead"*, `consume -9999` → *"balance would go negative"*, bakiye `5`'te
kaldı.

**Bulunan ve kapatılan gerçek bir açık:** düz bir dump'ta `usage_reservations`,
`credit_ledger`'dan **sonra** gelir ve `0020`'nin insert guard'ı gördüğü rezervasyonu bulamayan
satırı reddeder — yani rezervasyon taşıyan bir defterin restore'u prensipte patlayabilirdi.
Patlamıyor, çünkü `pg_dump` trigger'ları veriden **sonraki** post-data bölümünde üretiyor.
W07'nin provası `0009` başındayken geçmişti ve defter o zaman yoktu, yani bu yol bugüne kadar hiç
sınanmamıştı. `_ROW_COUNT_TABLES` üç defter tablosuyla genişletildi ki bu, birinin bir kez
kontrol ettiği bir gerçek olmaktan çıkıp her provada sınanan bir şey olsun.

**Seçim: profil + host systemd timer'ı, sürekli çalışan konteyner değil.** İki servis de tek
atımlık, çıkış kodu sonucun kendisi. Gerekçe: ADR-013'ün bütün noktası 6 çekirdeklik kutuya
sürekli açık bir bileşen daha koymamak, ve içinde uyuyan bir döngü host'un zaten sahip olduğu
zamanlayıcıyı **daha kötü** hata semantiğiyle tekrar eder — tek atımlık konteynerde systemd'nin
`OnFailure=`'ı başarısız yedeği görür, uyuyan döngü onu yutar. Timer/`.service` üniteleri ve
`Persistent=true` gerekçesi runbook'ta.

`DROP DATABASE` compose entrypoint'inde, script'te değil; veritabanı **adı** literal, yalnız
hangi sunucuda yaratılacağı DSN'den geliyor. Bir ortam değişkeniyle üretime yöneltilebilen
`--recreate` bayrağı, gece 2'de yanlış tarafa bakabilecek bir silme fiili olurdu.

### 6. Kaynak limitleri korundu (kabul kriteri 6)

Canlı konteynerlerden (`docker inspect` + `docker stats`), ADR-013 sırası bozulmadı:

| Servis | cpus | mem | cpu_shares |
|---|---|---|---|
| postgres | 2.0 | 8 GB | **2048** |
| api | 2.0 | 2 GB | **1024** |
| valkey | 0.5 | 512 MB | **512** |
| minio | 1.0 | 1 GB | **512** |
| celery-worker | 2.0 | 4 GB | **256** |
| celery-beat | 0.25 | 128 MB | **128** |
| **backup / restore-check** | **1.0** | **512 MB** | **128** (en alt kademe) |

Yedek runner'ı ek olarak `pids_limit: 64`, `read_only: true`, `restart: "no"`, `tmpfs /tmp 1g`.
Tavanın gerçekten bağladığı ölçüldü: CPU yakan bir koşuda `docker stats` **%96–98** gösterdi,
%100'ü hiç aşmadı; bellek `9.23MiB / 512MiB`.

### 7. Runbook izlenebilir (kabul kriteri 7)

- [`docs/runbooks/local-development.md`](../runbooks/local-development.md) — imaj tablosu,
  `valkey` servis adı, `target:` kuralı, PGDATA tuzağı, yedek/prova komutları.
- [`docs/runbooks/operations.md`](../runbooks/operations.md) — runner, systemd timer + `.service`,
  kaynak tablosu, prova ve trigger doğrulaması.
- **Yeni:** [`docs/runbooks/postgres-major-upgrade.md`](../runbooks/postgres-major-upgrade.md) —
  üretim major sürüm yordamı: neden `pg_upgrade` değil dump/restore, kesinti penceresi nasıl
  **ölçülür** (tahmin edilmez), 8 adım, satır sayısı **ve** trigger doğrulaması, geri dönüş.

### 8. İndeksler (kabul kriteri 8)

`docs/index.md` (ADR-010 durumu, ADR-019, yeni runbook satırı) ve `docs/adr/README.md`
(ADR-010 → Accepted, ADR-019 satırı) güncellendi. `docs/architecture/overview.md`'ye **"Runtime
images and deployment topology"** bölümü eklendi — dağıtım topolojisi bölümünü buraya yazdım.

### 9. Araç zinciri (kabul kriteri 9)

Python 3.13.14 · ruff 0.16.0 · mypy 2.3.0 · alembic 1.18.5 · celery 5.6.3 · redis-py 8.1.0 ·
SQLAlchemy 2.0.51 · Docker 25.0.3 · Compose 2.24.6-desktop.1 · PostgreSQL 18.4 · Valkey 9.1.1.

## Kendi işimde bulduğum hata (rapora yazılması gereken)

`Dockerfile`'a ikinci aşama ekleyince `api`, `celery-worker` ve `celery-beat` **sessizce onu
build etmeye başladı** — Docker hedef verilmediğinde *son* aşamayı alır. API imajı bir süre
`pg_dump`/`psql` taşıdı, yani ADR-013'ün "durum tutmayan API imajı veritabanı istemcisi taşımaz"
kuralı ihlal edilmişti ve hiçbir test bunu görmezdi. Üç servise `target: runtime` yazıldı, CI'ın
imaj taraması `--target runtime` aldı, ve **tam süit doğru imajda yeniden koşuldu** (1474, tekrar).
Kural ADR-019'a yazıldı; doğrulama komutu: `docker compose exec api sh -c "which pg_dump psql"`
hiçbir şey döndürmemeli.

## Kapsam dışı bıraktıklarım ve nedeni

- **`Makefile`** — `backup` hedefinin yorumunda W07'den kalma `ADR-XXX` var, artık ADR-013.
  Tek kelimelik düzeltme ama dosya ilan listemde yok; **PM'e bırakıldı** (protokol bağlayıcı).
- **`docs/product/requirements/96-stack-and-topology.md`** — diyagramı ve yığın listesi hâlâ
  "Redis" diyor. Requirement dosyası, ilan listemde yok. **PM'e bırakıldı.**
- **`app/core/config.py` ve `REDIS_*` değişken adları** — uygulama kodu, WO açıkça kapsam dışı
  bırakıyor. Zaten doğru: istemci `redis-py`, şema `redis://`.
- **Python 3.14** — §1'deki gerekçe.
- **Üretim sunucusu, systemd ünitelerinin kurulumu, R2 kovası, `BACKUP_ENCRYPTION_KEY`'in secret
  manager'a konması** — D2/D3, operasyon işi. Depo tarafı hazır.

## Açıkça belirtmem gerekenler

1. **ADR numarası PM teyidi bekliyor.** `ADR-019-runtime-image-baseline-and-backup-runner.md`
   yazıldı; dizinde 019 boştu ve README'nin kuralı "bir sonraki kullanılmamış numarayı seç"
   diyor. Kataloga girmediği için yeniden numaralandırma hâlâ meşru.
2. **`docs/STATUS.md`'de D1–D3 dağıtım kapıları yok.** PM-NOTES üçüne de STATUS'ta diye atıf
   yapıyor (`satır 173`), ama STATUS'ta yalnız B1/B2 var — bir yeniden yazımda düşmüş. W06 D1'i
   kapatıyor; **kapının kendisi kayıtlı değil.** Yalnız kendi satırımı güncelledim (dosya PM'in).
3. **PostgreSQL 18 PGDATA tuzağı sessiz.** Eski yola bağlı bir volume hata vermez, boş bir
   veritabanı verir ve her sağlık kontrolü geçer. Bu, üretim yükseltmesinde veri kaybı gibi
   görünen ama aslında yanlış dizin olan bir olayın reçetesi. Runbook'un ilk bölümü bu.
4. **Docker Desktop ağ ekleme kararsızlığı (ortamsal, benim değişikliğim değil).** Taze bir
   `up`'tan sonra `[backend, edge]` ilan eden bir servis bazen yalnız birine bağlanıyor; API iki
   ağda olduğu için gizliyor, `backend`-only worker/beat `Temporary failure in name resolution`
   ile patlıyor. Bu oturumda önce `valkey`'e, sonra `postgres`'e vurdu. Çözüm
   `up -d --force-recreate <servis>`; teşhis + çözüm local-development runbook'una yazıldı.
   **Doğrulayan oturum bunu bilsin — Valkey'e ait bir uyumsuzluk sanılabilir, değil.**
5. **`BACKUP_ENCRYPTION_KEY`'in compose varsayılanı geliştirme içindir.** `socialpilot_local_only`
   önekli, gitleaks allowlist'ine bilinçli olarak uyuyor. Üretimde secret manager'dan gelmeli;
   `Settings` bunu doğrulamıyor çünkü uygulama ayarı değil — runbook'ta iki yerde yazılı.
6. **gitleaks (çalışma ağacı modu) 4 bulgu veriyor, hiçbiri bu WO'nun dosyalarında değil:**
   `96-stack-and-topology.md:229`, `test_backup_db.py:142`, `test_restore_check.py:58,66`.
   Değiştirdiğim hiçbir dosya bulgu üretmiyor.
7. **Doğrulama ortamı ayakta bırakıldı** (`sp-w06`, yukarıdaki port bloğu), worktree duruyor.

## Doğrulama

_(test eden oturum: **kendi girdilerini üret.** Yedeği bozup geri yüklemeyi denet, yedek dosyası şifreli mi gerçekten kontrol et, runner'ı iki kez koşup çakışma üret, Valkey'i durdurup Celery'nin davranışını gör, disk dolu senaryosunda yedeğin sessizce başarısız olup olmadığını sına)_
