# Proje Durumu

**Son güncelleme:** 2026-07-30 · **Sahip:** PM oturumu

| | |
|---|---|
| `main` | `5addf69` — W01→W09 tamamı merge edildi (W06/W10 hariç), origin ile senkron |
| Alembic head | `0010_brand_catalog` (tek head) |
| Backend doğrulama | **392 pytest** (gerçek PostgreSQL + MinIO) · lint + format + mypy strict 135 dosyada temiz · py313 / mypy 2.3 / ruff 0.16 |
| Mobil doğrulama | `flutter analyze` temiz · 45 test · Flutter 3.44.8 / Dart 3.12.2 |
| Compose | api + postgres + redis + minio healthy · **servis bazlı CPU/RAM limitleri ve öncelik sırası** (ADR-013) · proje adı `COMPOSE_PROJECT_NAME` ile ayrılabilir |
| Açık dal | `main` + aktif work order dalları (başka dal bırakılmaz) |

> **Her oturum İLK bu dosyayı okur.** Bu dosya ile `git log` çelişirse **git kazanır**; çelişkiyi gören oturum bu dosyayı aynı commit'te düzeltir.
>
> **PM oturumu ayrıca [handoffs/PM-NOTES.md](handoffs/PM-NOTES.md)'yi okur** — rol, tetikleme promptları, bekleyen kararlar, yazılacak iş emirleri, öğrenilen dersler. Teknoloji/metodoloji değerlendirmesi: [reviews/2026-07-30-tech-methodology.md](reviews/2026-07-30-tech-methodology.md).

## Nerede kaldık

### Tamamlandı (`main`'de)

- **Phase 0 — Temel platform.** identity/tenant + RBAC, medya multipart upload control-plane, jobs/attempts/outbox/idempotency/audit, RFC 9457 hata kataloğu, CI verify hattı.
- **Phase 1 A–D — Medya analiz hattı.** ingest güvenlik geçidi (ADR-006), teknik analiz (ffprobe / kalite sinyalleri / dikey medya / sınırlı proxy), sahne tespiti + konuşma çözümleme, video understanding (kontratlar, job akışı, frame budget, FFmpeg frame extraction, sağlayıcı yönlendirme ADR-007), Celery worker composition + outbox publisher + beat, processing-summary API.
- **Platform temeli.** uv + lockfile, güncel bağımlılıklar (py313), CI'da zafiyet + secret + container taraması (W02). Tek sunucu dayanıklılığı: servis bazlı kaynak limitleri, worker scratch guard, sunucu dışına şifreli yedek + geri yükleme provası (W07, ADR-013).
- **Marka ve katalog.** `modules/brands` — marka profili, ürün/hizmet kataloğu ve fiyatları, kampanya kayıtları, onaylı/yasak iddia listeleri, onaylı CTA'lar, hedef kitleler. Deterministik marka sağlık skoru (tavsiye, bloke etmez). Cursor pagination primitifi `core/pagination.py` (W04).
- **Gözlemlenebilirlik.** OpenTelemetry trace + metric, varsayılan **kapalı** ve kapalıyken sıfır maliyet; correlation ID ↔ trace bağı (W05, ADR-014).
- **Sağlayıcı benchmark aracı.** `app/benchmark/` — golden set + ground truth + kabiliyet başına metrik + maliyet tavanı + veri bölgesi sütunu. Credential'sız koşar (W08). Gerçek sağlayıcı seçimi buna dayanacak.
- **Mobil uçtan uca demo.** `apps/mobile` — 24 Dart dosyası; işletme seçimi/oluşturma, video seçme, upload progress, 6 adımlı processing checklist, sonuç detayı. Material 3, Türkçe. Config yalnızca `--dart-define`; kaynak kodda token yok.

### Phase 1'den eksik

- Sahne kütüphanesi arama + pgvector embedding/retrieval (PRD §16.4–16.5)
- **Phase 1 çıkış kriteri mekanik olarak karşılandı** (`5ee03d4`): gerçek video yüklenip sahne + transcript + etiket üretiyor, `.mov`/HEVC dahil. **Ama ASR ve VLM hâlâ fake** — yani transcript ve etiketlerin *içeriği* sentetik. Gerçek sağlayıcı bağlanması **W08** benchmark'ından sonra; kriteri "gerçek içerikle karşılandı" saymak için o gerekiyor.
- Fotoğraf (HEIC/HEIF/JPEG/PNG) analiz hattı yok — K6'nın ikinci yarısı. Şu an HEIC açık kodla reddediliyor (sessiz durma yok).

### Başlamadı

Phase 2 içerik üretimi · Phase 3 abonelik/entitlement · Phase 4 yayınlama · Phase 5–6 reklam · admin web paneli · n8n

## Bloke ediciler

| # | Konu | Etki | Çözüm |
|---|---|---|---|
| ~~B1~~ | **KAPANDI** (`5ee03d4`). Yükleme yolu gerçek (W01) + worker medyayı gerçekten S3'ten akıtıyor (W09). Fixture byte'ı hattın hiçbir yerinde yok | — | — |
| B2 | `JAVA_HOME` yok, Android cmdline-tools eksik | APK build edilemiyor | **kısmen kapandı:** runbook'ta Windows JDK 17 + cmdline-tools adımları var (W02) ama hiçbir ortamda uçtan uca doğrulanamadı. İlk mobil oturum adımları izleyip sonucu runbook'a yazmalı |

## Geliştirme ortamı

**2026-07-30 olayı:** Docker tamamen sıfırlandı — container, volume ve image kalmadı. **Depoda hiçbir kayıp yok**; kaybolan yalnızca tek kullanımlık dev altyapısıydı. Kurtarma tamamen otomatik:

```
docker compose up -d --build
docker compose exec -T api python -m alembic upgrade head
docker compose --profile worker up -d        # worker gerekiyorsa
```

Şema 9 migration'dan (`0001`→`0009`) birebir yeniden üretiliyor.

**Ortaya çıkan açık:** elle oluşturulmuş "Demo Isletme" + analiz edilmiş asset seed'i **yeniden üretilemedi** — seed script'i yoktu. `services/api/scripts/seed_dev.py` **W01** kapsamına eklendi. Kural: dev ortamında elle veri oluşturan hiçbir akış script'siz bırakılmaz.

## Karar bekleyenler

| # | Karar | Ne zaman gerekli | PM önerisi |
|---|---|---|---|
| K1 | **Faturalandırma modeli:** mağaza IAP mı, web-first + refakatçi mobil mi? Türkiye alternatif faturalandırma programlarında **yok** → %15–30 komisyon kaçınılmaz | Phase 3'ten önce | Web-first satış, mobilde satın alma yok (Apple 3.1.3(a)) |
| ~~K2~~ | **KARARA BAĞLANDI — [ADR-012](adr/ADR-012-remove-n8n-from-mvp.md):** n8n MVP kapsamından çıkarıldı. Gerekçe: yasaklar uygulandıktan sonra kalan iş zamanlama/bildirimden ibaret ve Celery Beat bunu zaten üretimde yapıyor; SUL lisansı ticari platform motoru olmayı kısıtlıyor; tek sunucuda her zaman açık bir bileşen + credential store + editor erişim yüzeyi. `workflows/n8n/` oluşturulmaz. Geri dönüş kolay: outbox zarfı taşıyıcıdan bağımsız | — | — |
| K3 | **Pazar kapsamı:** yalnız TR mi, EU/global roadmap'te mi? | Phase 2 render şeması | EU roadmap'teyse C2PA/provenance alanları şimdi şemaya girsin |
| K4 | **Kullanıcı düzenleme modeli:** "ufak editler" (metin taşı, sticker ekle, stil değiştir) nasıl yapılacak? PRD §18.2 bildirimsel overlay'i, §21.3 revizyonu tanımlıyor; §3.3 manuel editörü kapsam dışı bırakıyor — arada karar verilmemiş bir boşluk var | Phase 2 timeline şeması | **Parametrik düzenleme:** timeline JSON patch'i — metin içeriği (yasak-kelime doğrulamalı, fiyat/tarih yalnızca doğrulanmış kayıttan), 9'lu ızgara konum çapaları, stil token'ı, marka onaylı sticker kütüphanesi, segment sınırına snap. Serbest x/y ve kare kare montaj yok. Saf yeniden render **yeni hak tüketmez**, revizyon kotasından düşer. Platformun etkileşimli sticker'ları (anket/konum/mention) API ile eklenemez — ürün tarafında açıkça anlatılmalı |

| K6 | **iOS medya formatlarının analizi.** ~~`.mov` sessizce duruyor~~ **video yarısı W09'da çözüldü** (`5ee03d4` merge edildi): `.mov`/`video/quicktime` artık analiz hattına giriyor, codec ffprobe'dan doğrulanıyor, desteklenmeyen codec `rejected`. HEIC/HEIF ingest'te **açıkça reddediliyor** (`INGEST_ANALYSIS_UNSUPPORTED_MEDIA_TYPE`) — sessiz çıkmaz sokak yok. **Kalan (ikinci yarı):** HEIC/HEIF *fotoğraf* analiz hattı (teknik metadata + VLM etiketleme; sahne/ASR yok) tanımlanıp inşa edilmeli — ayrı slice, enum durumu için migration slotu ister | fotoğraf hattı Phase 2'den önce | **Video yarısı uygulandı** (ADR-011). Fotoğraf hattı: bir "fotoğraf hazır/analiz" durumu + VLM etiketleme; landing'de HEIC→JPEG transcode gerekir (platform uyumu). Bu geldiğinde W09'un geçici HEIC reddi kalkar |

### K5 — Dağıtım ve maliyet modeli

**Kısıt (kullanıcı, 2026-07-30):** kendi altyapısına ağır sabit maliyet istemiyor; ürün bir SaaS aracı olacak, kullanıcılardan ücret alınacak, kendi makinesi yüklenmeyecek.

**Maliyet sıralaması (büyükten küçüğe):** AI sağlayıcı çağrıları → egress → depolama birikimi → render/transcode CPU → veritabanı/API → mağaza komisyonu (her şeyin üstünden %15-30, bkz. K1). **Sunucu bu listenin dördüncüsü; asıl COGS AI çağrıları.**

**Şimdi alınacak karar:** `RenderPort` / `TranscodePort` birinci sınıf kabiliyet portu olmalı — AI sağlayıcılarıyla aynı muamele (ADR-004). FFmpeg çağrıları render worker'ının içine gömülürse seçenek kaybedilir. Port arkasında üç dağıtım seçeneği konfigürasyonla değişebilir: yönetilen render servisi (sıfır idle) → sıfıra ölçeklenen burst compute (sıfır idle) → ucuz dedike/spot CPU (hacim eşiği sonrası). PRD §17.2 bunu zaten öngörüyor ("Montaj: FFmpeg | alternatif: Yönetilen render servisi").

**Kullanıcı kararı (2026-07-30):** **tek sunucu**, üzerinde backend + worker + veritabanı; düşük sabit maliyet. Frontend ve medya sunucuda barınmaz.

**Bu karar mimariyle uyumlu — değişiklik gerekmiyor.** Tek sunucuyu mümkün kılan üç mekanizma tasarımda zaten var: (1) medya byte'ları API'den geçmiyor (ADR-002), (2) worker'lar ayrı süreç ve eşzamanlılığı sınırlı (§38.3 backpressure/tenant limiti), (3) `generation_deadline_at` `planned_publish_at`'ten ayrı (§13.1) → zirve yayın saatine değil, gece kuyruğuna dağılıyor.

**Uygulama şekli:** ucuz **dedike** sunucu (bulut VPS değil — FFmpeg gerçek çekirdek ister, vCPU başına 3-5 kat pahalı). Sabit maliyet mertebesi ayda €50-80 (sunucu + R2 + alan adı; sağlayıcıdan teyit edilmeli). Admin paneli sunucuda değil statik hosting'de (bedava, sıfır yük). n8n çıkarılırsa (K2) bir container daha eksilir.

**PM önerisi (render):** MVP'de yönetilen render servisi veya tek sunucuda sınırlı-eşzamanlılıklı FFmpeg; timeline JSON'u (§18.2) yönetilen servislerin girdi formatıyla büyük ölçüde örtüşüyor. Hacim eşiği geçince ikinci bir *yalnızca-worker* makinesi eklenir — mimari değişiklik değil, konfigürasyon.

**Tek sunucu kararının doğurduğu iki kritik eksik (şu an yok):**
1. **Kaynak limiti yok.** `compose.yaml`'da hiçbir servisin CPU/RAM limiti tanımlı değil. Tek makinede ağır render API'yi açlığa sürükler. Gerekli: worker CPU limiti, FFmpeg süreçlerine düşük öncelik, Postgres'e ayrılmış RAM, render concurrency 1-2, geçici dizin temizliğinin sıkı uygulanması (§19.3).
2. **Yedekleme yok.** Tek sunucu = tek arıza noktası ve üretim veritabanı git'te olmayacak. Gerekli: sunucu dışına (R2) otomatik günlük `pg_dump` + tercihen WAL arşivi. Pazarlık konusu değil.

**Bağlı açıklar:**
- **Egress:** Instagram videoyu bizim URL'imizden kendisi çekiyor → her yayın egress. Egress'i sıfır olan object storage (R2 tipi) seçilmeli; PRD zaten listelemiş.
- **Maliyet odaklı yaşam döngüsü politikası yok.** Orijinaller süresiz saklanıyor → tenant başına sınırsız büyüyen maliyet. Gerekli: render sonrası proxy silme, orijinali N gün sonra soğuk katmana indirme, plan bazlı depolama kotası. PRD §34'te imha politikası var ama maliyet boyutu yok.
- **Kredi puan tablosu (§12.4) ölçülmüş sağlayıcı maliyetine kalibre edilmemiş.** W08 benchmark'ı bu yüzden aynı zamanda fiyatlandırma girdisi.
- **Üretim kendi makinesinde barındırılamaz:** Instagram'ın çekebileceği genel erişilebilir, sürekli ayakta adres gerekiyor.

## Açık work order'lar

Protokol: [handoffs/README.md](handoffs/README.md)

| WO | Konu | Durum | Dal | Model / effort | Migration slotu |
|---|---|---|---|---|---|
| [W01](handoffs/W01-object-storage-adapter.md) | MinIO/S3 storage adapter + iOS MIME düzeltmesi | **tamamlandı** (`8d055b7`) · Codex doğrulaması bekliyor | merge edildi, dal silindi | Opus 5 / high | — |
| [W03](handoffs/W03-docs-restructure.md) | Doküman yapısı + navigasyon katmanı | **tamamlandı** (`8b74f5c`) · merge edildi | merge edildi, dal silindi | Opus 4.8 / medium | — |
| [W02](handoffs/W02-platform-hardening.md) | Bağımlılık tazeleme, lockfile, CI güvenlik kapıları | **tamamlandı** (`3aad208`) · merge edildi | merge edildi, dal silindi | Opus 4.8 / medium | — |
| [W09](handoffs/W09-real-media-materializer.md) | **Gerçek medya materializer + `.mov`/HEVC analiz kapısı** | **tamamlandı** · merge edildi · ADR **011**'e numaralandırıldı (009 W02'de) | merge edildi, dal silindi | Opus 4.8 / high | — |
| [W07](handoffs/W07-single-server-resilience.md) | **Tek sunucu dayanıklılığı** — kaynak limitleri + scratch guard + sunucu dışına yedek + geri yükleme provası | **tamamlandı** (`c199b86`) · merge edildi · ADR-013 | merge edildi, dal silindi | Opus 4.8 / medium | — |
| [W08](handoffs/W08-provider-benchmark-harness.md) | **Golden set benchmark koşum takımı** | **tamamlandı** · merge edildi · `provider_usage` bulgusu W04 slotuna alındı | merge edildi, dal silindi | Opus 5 / high | — |
| [W04](handoffs/W04-brand-catalog.md) | **Marka profili + ürün/hizmet kataloğu** — Phase 2'nin ön koşulu | **tamamlandı** · merge edildi | merge edildi, dal silindi | Opus 5 / high | **kullanıldı** (`0010`) |
| [W05](handoffs/W05-opentelemetry.md) | **OpenTelemetry** trace + metric, varsayılan kapalı | **tamamlandı** · merge edildi · ADR-014 | merge edildi, dal silindi | Opus 4.8 / medium | — |
| W06 | PostgreSQL 18 + Valkey imaj geçişi | **sıradaki** (`compose.yaml` serbest) | `slice/0j-runtime-images` | Opus 4.8 / medium | — |
| W10 | **Şema borcu:** `provider_usage` tablosu · `storage_upload_id` genişletmesi · fotoğraf analiz enum'u · **`approver` rolü** (`BusinessRole` enum'unda yok, W04 bulgusu) | **sıradaki** (slot boşaldı) | `slice/0m-schema-debt` | Opus 4.8 / medium | **SLOT SERBEST** |

### Dosya sahipliği (çakışma önleme)

Paralel çalışan WO'lar aşağıdaki dosyalara **yalnızca sahibi** dokunur. Sahibi olmadığın bir dosyaya dokunman gerekiyorsa dur ve raporuna yaz.

| Dosya | Sahibi |
|---|---|
| `services/api/app/core/config.py` | W01 |
| `compose.yaml`, `.env.example` | W01 |
| `services/api/pyproject.toml` | W01 (yalnızca storage bağımlılığı) → sonra W02 (uv geçişi) |
| `docs/architecture/media-upload.md` | W01 |
| `services/api/Dockerfile`, `.github/workflows/verify.yml`, `Makefile` | W02 |
| `docs/runbooks/local-development.md` | W02 |
| `docs/index.md`, `docs/adr/README.md` | **W03 tekel** — W01/W02 ADR dosyasını yazar, indekse eklemez |
| `AGENTS.md`, `CLAUDE.md`, `docs/product/**`, modül `CLAUDE.md`'leri | W03 |
| `app/main.py`, `app/core/config.py`, `app/core/logging.py`, `pyproject.toml`, `uv.lock` | W05 |
| `app/api/routes/__init__.py` (router dikişi), `migrations/` | W04 |
| `docs/STATUS.md` | PM (WO'lar yalnızca kendi durum satırını günceller) |

## Sprint 0 kaydı (2026-07-30, PM)

Depo hijyeni yapıldı: `main` 16 commit geride ve Phase 1'in tamamı merge edilmemiş durumdaydı; 5 worktree / 9 dal vardı ve isimler içerikle uyuşmuyordu.

- `main` → `7d78c6e` fast-forward (tüm commit'ler ata, lineer).
- Terk edilmiş çift iş **`c43ccad`** ("celery outbox and beat scheduling") silindi. Gerekçe: `ce96771` aynı base'den (`258439d`) çıkıp aynı 16 dosyayı kapsıyor ve süperset — özdeş `beat_schedule` anahtarları, `test_video_understanding_flow.py` birebir aynı, testlerde +129/+158 fazla kapsam. Kurtarma gerekirse SHA: `c43ccadd67783d2b781203e51b8abbc2be3c2abc` (reflog ~90 gün).
- Atıl 3 worktree ve 7 orphan dal kaldırıldı. Kalan: `main` (ana dizin) + geçici PM worktree'si.
