# ADR-XXX: Tek Sunucu Dağıtım Topolojisi

> **Numara PM tarafından merge sırasında verilecek.** Bu dosya `ADR-XXX` adıyla yazıldı; W07
> paralel çalıştığı için numarayı yürüten oturum seçmez (2026-07-30'da W02 ve W09 ikisi de
> ADR-009'u almıştı). Kod içindeki `ADR-XXX` referansları (`compose.yaml`, `.env.example`,
> `Makefile`, `scripts/backup_db.py`, `scripts/restore_check.py`) tek bir find-replace ile
> numaralandırılacak.

**Status:** Accepted
**Date:** 2026-07-30
**Karar veren:** PM/mimar oturumu (kullanıcı kararı K5 doğrultusunda) · yürüten: W07

## Context

Kullanıcı kararı K5 ([STATUS.md](../STATUS.md)): ürün **tek, ucuz, dedike bir sunucuda**
çalışacak — üstünde backend + worker + PostgreSQL + Redis; frontend ve medya sunucuda
barınmaz. Düşük sabit maliyet hedefi (mertebe ayda €50–80). Asıl COGS AI sağlayıcı çağrıları;
sunucu maliyet listesinin dördüncüsü.

Bu karar mevcut mimariyle uyumlu — medya byte'ları API'den geçmiyor (ADR-002), worker'lar ayrı
süreç ve eşzamanlılığı sınırlı, `generation_deadline_at` zirve yayın saatinden ayrı (§13.1).
Ama tek makine iki şeyi zorunlu kılıyordu ve ikisi de yoktu:

1. **Kaynak izolasyonu.** Hiçbir Compose servisinde CPU/RAM limiti tanımlı değildi. Tek
   makinede ağır bir render/analiz işi API'yi açlığa sürükler; kullanıcı "uygulama dondu" der.
2. **Sunucu dışına yedek.** Tek sunucu tek arıza noktası ve üretim veritabanı git'te olmayacak.
   2026-07-30'da dev Docker'ı tümden kaybettik ve zararsızdı çünkü her şey git'teydi; üretimde
   aynı olay **veri kaybı**dır.

## Decision

### 1. Servis yerleşimi ve kaynak bütçesi

Tüm bileşenler tek makinede, `compose.yaml`'da **açık kaynak bütçesiyle**. Her servise CPU
tavanı (`cpus`), bellek tavanı (`mem_limit`) ve göreli CPU ağırlığı (`cpu_shares`) verildi.
Tavanlar bilinçli olarak **aşırı-abone** (toplamları çekirdek sayısını aşar): tavan tek bir
servisin patlamasını sınırlar, `cpu_shares` ise doygunlukta kimin fren yiyeceğine karar verir.

Ağırlık sırası **postgres > api > redis > worker'lar**. Sistem kaydı ve API asla açlığa
düşmez; render/analiz patlaması ilk fren yiyen taraftır. Hedef sunucu: 6–8 çekirdek / 32–64 GB;
rakamlar alt uca (6 çekirdek / 32 GB) göre seçildi, sabit bellek tavanı toplamı ~15.6 GB.

| Servis | cpus | mem_limit | cpu_shares | Gerekçe |
|---|---|---|---|---|
| postgres | 2.0 | 8g | 2048 | Sistem kaydı; page cache için en büyük RAM, en yüksek ağırlık |
| api | 2.0 | 2g | 1024 | Byte geçmez (ADR-002); istek işleme için 2g bol, postgres'in hemen altında |
| redis | 0.5 | 512m | 512 | Hafif broker/cache; `maxmemory 384mb`+`noeviction` cgroup OOM'undan önce reddeder |
| minio | 1.0 | 1g | 512 | Yalnız dev byte yolu; üretimde R2 (sıfır egress) |
| celery-worker | 2.0 | 4g | 256 | En düşük ağırlık; `--concurrency=2`; tmpfs 512m bu tavana dahil |
| celery-beat | 0.25 | 128m | 128 | Yalnız tick üretir |

Ek olarak worker süreci **kendini renice eder** (`os.nice(+10)`,
[`app/worker/composition.py`](../../services/api/app/worker/composition.py)); FFmpeg alt
süreçleri bu düşük CPU önceliğini miras alır. `cpu_shares` cgroup düzeyinde, `nice` süreç
düzeyinde aynı önceliği iki kez uygular. `ionice` host'a bırakıldı.

### 2. Scratch (geçici disk) sınırı

Worker scratch'i iki katmanla korunur, ikisi de sessiz değil:

- **Yumuşak uygulama sınırı** ([`app/worker/scratch.py`](../../services/api/app/worker/scratch.py)):
  drain, scratch bütçe üstündeyken yeni iş **almaz**; `WORKER_SCRATCH_BUDGET_EXCEEDED` ile
  gürültülü başarısız olur. Bütçe tmpfs boyutunun 3/4'ü — ENOSPC duvarından önce, geri
  sarılacak boşlukla tetiklenir. Çöken bir worker'dan kalan scratch süreç init'te temizlenir.
- **Sert tmpfs tavanı** (`compose.yaml` `tmpfs size=512m`): yumuşak sınırı aşan tek bir kaçak
  iş bir sonraki yazımda ENOSPC alır ve servisin normal hata yoluyla `failed` olur; host
  belleği tükenmez.

### 3. Yedekleme ve geri yükleme

- **Günlük `pg_dump` sunucu dışına** ([`scripts/backup_db.py`](../../services/api/scripts/backup_db.py)):
  plain-SQL dump → düz metin token taraması → gzip → `openssl` AES-256-CBC + PBKDF2 şifreleme →
  object storage'a (R2/S3, mevcut `S3_*` konfigürasyonu) tarihli anahtarla yükleme. **Sunucunun
  kendi diskinde kopya bırakılmaz** (tek geçici dizin `finally`'de silinir).
- **Saklama:** son N gün her yedek + son M hafta için haftada bir (varsayılan 14 gün / 8 hafta).
- **Şifreleme at-rest:** yedek ciphertext olarak durur; anahtar `BACKUP_ENCRYPTION_KEY`
  sunucunun secret manager'ında, git'te değil. `oauth_credentials` token'ları zaten envelope
  encryption'lı; dump'ta düz metin token tespit edilirse yedek gürültülü başarısız olur.
- **Geri yükleme provası** ([`scripts/restore_check.py`](../../services/api/scripts/restore_check.py)):
  en son yedeği boş bir scratch veritabanına yükler, Alembic head'in kod head'iyle eşleştiğini
  ve çekirdek tabloların satır sayılarını doğrular. `make restore-check` tek komut. **Test
  edilmeyen yedek yedek değildir.**
- **Sessiz başarısızlık yok:** başarı/başarısızlık yapılandırılmış log üretir
  (`db_backup_succeeded` / `db_backup_failed` + `error_code`), süreç non-zero çıkar. Metrik
  W05'e (OTel) bırakıldı; bu iş yalnızca log üretir.

### 4. Yeniden başlama / OOM döngü koruması

Worker ve beat `restart: on-failure` (temiz SIGTERM çıkışı 0 → tekrar başlamaz; yalnız çökme
tekrar başlatır, Docker'ın üstel geri çekilmesiyle *hız* sınırlı → OOM döngüsü CPU'yu
döndürmez). Postgres/api/redis/minio ayakta kalmalı → `unless-stopped`. Bellek tavanları OOM'u
en baştan olası olmaktan çıkaracak şekilde ölçüldü. Düz `docker compose`'un deneme *sayısı*
sınırı yok; üretim host'u bunu systemd `StartLimitBurst` ile sınırlar (aşağıya bakınız).

## Ölçek çıkışı (konfigürasyon işi, mimari değişiklik değil)

Hacim eşiği geçtiğinde **ikinci bir yalnızca-worker makinesi** eklenir: aynı imaj, aynı
Redis/PostgreSQL'e bağlanır, yalnız worker profili çalışır. Kod değişmez — worker'lar zaten
ayrı süreç ve broker üstünden konuşuyor. §38.2'nin kuyruk-başına kaynak profilleri (hafif
kuyruklar için ayrı, yüksek-eşzamanlılıklı bir worker) task routing landing'inde bir
konfigürasyon eklemesidir; bu ADR yalnız ağır-kuyruk worker'ını `--concurrency=2` ile sınırlar.

## Consequences

- Tek makinede komşu-açlığı önlendi: ağır iş altında `/health/ready` yanıt vermeye devam eder,
  çünkü API postgres'ten sonra en yüksek ağırlıkta ve worker'lar renice + düşük `cpu_shares`.
- Üretim veritabanı artık sunucu dışında, şifreli, ve geri yüklenebilirliği script'li olarak
  kanıtlanıyor. Tek arıza noktası hâlâ var (donanım) ama veri kaybı riski kapandı.
- **Üretim ön koşulları:** yedek/geri yükleme host'unda `pg_dump`, `psql`, `openssl` gerekir.
  Stateless API imajı bunları taşımaz (bilinçli); backup runner DB-komşusu bir bağlamda çalışır.
- **Bu ADR yalnız depo tarafını üretir.** Sunucu satın alma, provisioning, DNS, TLS, gerçek
  deploy, cron kurulumu ve `StartLimitBurst` ünitesi kapsam dışı (operasyon işi); yönlendirme
  [operations runbook](../runbooks/operations.md)'ta.
- Şifreleme `openssl enc -aes-256-cbc -pbkdf2` (CBC, kimlik doğrulamasız). Bütünlük restore
  adımında yakalanır (bozulma pg yüklemesinde patlar). Yeni bir Python bağımlılığı eklemekten
  (pyproject sahipliği + lockfile) kaçınmak için CLI aracı tercih edildi.

## Rejected alternatives

- **Yönetilen render servisi / burst compute (K5'te tartışıldı):** MVP'de reddedilmedi, ertelendi.
  `RenderPort` birinci sınıf port olarak kalır; hacim eşiğinde konfigürasyonla seçilir. Bu ADR
  render worker'ını gömmez, yalnızca tek sunucuda sınırlı-eşzamanlılıkla çalıştırır.
- **`deploy.resources` + `deploy.restart_policy.max_attempts` (swarm):** reddedildi. Düz
  `docker compose up` swarm `deploy` bloğunu kaynak için uygular ama `restart_policy.max_attempts`'i
  yok sayar; yanıltıcı ölü konfigürasyon bırakmamak için top-level `cpus`/`mem_limit`/`restart`
  kullanıldı, deneme sınırı runbook'ta systemd'ye bırakıldı.
- **AES-GCM (kimlik doğrulamalı) şifreleme:** reddedildi. `openssl enc` GCM'i güvenilir
  desteklemiyor; yeni bir Python crypto bağımlılığı eklemek dosya sahipliği kısıtına takılıyor.
  CBC+PBKDF2 + restore-time doğrulama kabul edilen dengedir.
- **Yedeği aynı diskte tutup ayrıca kopyalamak:** reddedildi. Tek sunucuda aynı-disk kopya
  disk arızasında beraber gider; script yerelde hiç kopya bırakmaz.
