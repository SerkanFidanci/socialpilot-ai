# ADR-019: Çalışma zamanı imaj temeli ve yedek runner'ının şekli

**Status:** Accepted
**Date:** 2026-08-04
**Karar veren:** W06 · **[ADR-013](ADR-013-single-server-deployment-topology.md)'ün eki**
(topolojiyi değiştirmez, üstünde çalışan imajları ve yedeğin *koşma biçimini* sabitler).
Broker/cache seçimi ayrı bir karardır: [ADR-010](ADR-010-valkey-runtime-evaluation.md).

## Context

ADR-013 tek sunucu topolojisini ve yedekleme **yordamını** yazdı. İki açık kaldı:

1. **İmajlar Phase 0'da dondurulmuş haldeydi.** `postgres:16-alpine` ve `redis:7-alpine`, aradan
   geçen aylara ve `0020_ledger_integrity`'nin getirdiği trigger + kısmi index + advisory lock
   makinesine rağmen hiç doğrulanmamıştı.
2. **Yedeği koşan bir şey yoktu.** `scripts/backup_db.py` ve `scripts/restore_check.py` vardı;
   onları çağıran hiçbir zamanlayıcı, hiçbir servis, hiçbir imaj yoktu. Üretime bu haliyle
   çıkmak, ilk gün yedeksiz olmak demekti — dağıtım kapısı **D1**.

## Decision

### 1. Sürüm hattı ve doğrulama kuralı

| Runtime | İmaj | Gerekçe |
|---|---|---|
| PostgreSQL | `postgres:18.4-alpine` | 18.4 güncel kararlı hat; 19 beta ve beta sunucu koşulmaz |
| Broker/cache | `valkey/valkey:9.1.1-alpine` | BSD-3 (ADR-010); `unstable` etiketi alınmadı |
| API / worker / beat | `python:3.13-slim`, **`runtime` hedefi** | `requires-python >=3.13` ve `uv.lock` ile aynı |
| Yedek runner'ı | `runtime` + `postgresql-client-18` + `openssl`, **`backup` hedefi** | ayrı imaj |

**Alpine 16 hattından bilinçli olarak korundu.** Major sürüm atlaması zaten yeterince değişken
getiriyor; musl'da kalmak libc collation sağlayıcısını (`datlocprovider = c`, `en_US.utf8`) aynı
bırakır, yani hiçbir metin index'i altımızdan sıralama değiştirmez. Debian'a geçmek bunu tek
adımda ikinci bir bilinmeyene çevirirdi.

**Sürüm asla hafızadan yazılmaz.** Bu tablodaki her satır yazıldığı anda registry'den doğrulandı
ve çalışan konteynerden sürüm çıktısı alındı (`SELECT version()` → `PostgreSQL 18.4 on
x86_64-pc-linux-musl`, `valkey-cli INFO server` → `valkey_version:9.1.1`). Beta/RC alınmaz.

### 2. İmaj hedefi açıkça yazılır — varsayılana bırakılmaz

`services/api/Dockerfile` artık iki aşamalı: `runtime` ve ondan türeyen `backup`. Docker'da
hedefi belirtilmemiş bir build **son** aşamayı alır. Bu yüzden `api`, `celery-worker` ve
`celery-beat` servisleri `target: runtime` yazar, CI'ın imaj taraması `--target runtime` ile
build eder. Bu kozmetik değil: W06 sırasında tam olarak bu tuzağa düşüldü ve `api` imajı bir süre
`pg_dump`/`psql` taşıdı — ADR-013'ün "durum tutmayan API imajı veritabanı istemcisi taşımaz"
kuralı sessizce ihlal edilmişti.

**İstemci PostgreSQL'in kendi apt deposundan gelir**, Debian'ınkinden değil: trixie
`postgresql-client 17` veriyor ve `pg_dump` kendisinden **yeni** bir sunucuyu reddeder. 17
istemci 18.4 sunucuya karşı sürüm uyuşmazlığıyla durur, yani yedek hiç alınmazdı.

### 3. Yedek runner'ı: profil altında **tek atımlık** konteyner, sürekli çalışan zamanlayıcı değil

`compose.yaml`'a `backup` profili altında iki servis eklendi — `backup` ve `restore-check` —
ikisi de çalışıp **çıkar**. Zamanlama host'un işidir (systemd timer / cron), konteynerin değil.

Gerekçe:

- ADR-013'ün bütün noktası 6 çekirdeklik bir kutuya sürekli açık bir bileşen daha koymamaktı.
- İçinde uyuyan bir döngü, host'un zaten sahip olduğu zamanlayıcıyı **daha kötü** hata
  semantiğiyle tekrar eder: tek atımlık konteyner script'in çıkış koduyla çıkar, yani systemd
  timer'ın `OnFailure=`'ı başarısız yedeği görür. Uyuyan bir döngü onu yutar.
- Rehearsal `depends_on: backup: service_completed_successfully` ile bağlı, yani
  `docker compose --profile backup up` **yedek al → o yedeği geri yükle** sırasını tek komutta
  koşar. `--no-deps` ile rehearsal depodaki en yeni nesneyi provalar.

**Scratch veritabanı `DROP DATABASE`'i compose'un entrypoint'inde durur, script'in içinde
değil.** Veritabanı **adı** literal; yalnız hangi sunucuda yaratılacağı DSN'den gelir. Bir ortam
değişkeniyle üretime yönlendirilebilen bir `--recreate` bayrağı, gece 2'de yanlış tarafa
bakabilecek bir silme fiilidir.

### 4. Geri yükleme provası defteri de sayar

`_ROW_COUNT_TABLES` `credit_ledger`, `usage_reservations` ve `entitlement_ledger_anchors` ile
genişletildi. Sebep mekanik: düz bir dump'ta `usage_reservations`, `credit_ledger`'dan **sonra**
gelir, ve `0020`'nin insert guard'ı gördüğü rezervasyonu bulamayan bir defter satırını reddeder.
Restore'un çalışması `pg_dump`'ın trigger'ları veri yüklemesinden sonraki *post-data* bölümünde
üretmesine dayanıyor. Bu tabloları saymak, o gerçeği birinin bir kez kontrol ettiği bir şey
olmaktan çıkarıp her provada sınanan bir şeye çevirir.

## Consequences

- D1 kapandı: yedek artık koşulabilir bir şey, ve koşuldu — 18 KB ciphertext depoya yazıldı,
  `Salted__` başlıklı, düz metin SQL içermiyor; o nesneden gerçek bir geri yükleme yapıldı ve
  altı tablonun satır sayısı kaynakla birebir çıktı, Alembic head'i `0020_ledger_integrity`.
- Geri yüklenen veritabanı `0020`'nin bütün makinesini taşıyor: append-only trigger'ı ve insert
  guard'ı ikisi de PostgreSQL 18.4'te ateşliyor (ham SQL ile hem mutasyon hem taşma denendi,
  ikisi de reddedildi, bakiye değişmedi).
- İki imaj bakımı gerekiyor (`runtime`, `backup`). CI ikisini de build eder ve ikisini de tarar.
- PGDATA yolu değişti; eski yola bağlı bir volume **hata vermez**, sessizce boş bir veritabanı
  verir. Üretim yükseltme yordamı [runbook](../runbooks/postgres-major-upgrade.md)'ta.
- **Bu ADR yalnız depo tarafını üretir.** Host'ta systemd timer kurulumu, secret manager'dan
  `BACKUP_ENCRYPTION_KEY` beslenmesi ve R2 kovası operasyon işidir (D2/D3).

## Rejected alternatives

- **PostgreSQL 19 (beta).** Reddedildi. Sistem kaydı beta sunucuda çalışmaz.
- **Python 3.14'e geçmek.** Ertelendi. `requires-python >=3.13` izin verirdi ama bu, kilit
  dosyasının yeniden çözülmesi ve tüm bağımlılık kümesinin yeniden doğrulanması demek — altyapı
  imajı işine sıkıştırılacak bir karar değil, kendi işi.
- **Debian tabanlı postgres imajı.** Reddedildi: collation sağlayıcısını major sürüm atlamasıyla
  aynı anda değiştirmek, bir sorun çıktığında hangisinin sebep olduğunu bilinemez yapar.
- **Her zaman açık bir zamanlayıcı konteyneri (ofelia/supercronic).** Reddedildi; §3'teki gerekçe.
- **`pg_dump`'ı API imajına koymak.** Reddedildi: ADR-013 durum tutmayan imajın veritabanı
  istemcisi taşımamasını açıkça istiyor, ve tek bir Dockerfile'da ayrı hedef bunu bedelsiz verir.
- **`postgres:18.4-trixie`'yi runner imajı yapmak** (pg_dump + psql + openssl hepsi hazır).
  Reddedildi: script'ler `Settings`, `structlog`, `httpx` ve `alembic` istiyor, yani uygulama
  ortamı gerekiyor; onu postgres imajına taşımak kilit dosyası disiplinini kırardı.
- **Rezervasyonla dolu defteri geri yüklemeyi denemeden kabul etmek.** Reddedildi: W07'nin
  provası `0009` başındayken geçmişti ve defter o zaman yoktu.
