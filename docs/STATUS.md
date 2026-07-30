# Proje Durumu

**Son güncelleme:** 2026-07-30 · **Sahip:** PM oturumu

| | |
|---|---|
| `main` | W01 `8d055b7` üzerinde — feat: add S3-compatible object storage adapter and dev seed (+ rapor commit'i) |
| Alembic head | `0009_video_understanding` (tek head) |
| Backend doğrulama | 244 pytest (gerçek PostgreSQL + MinIO) · ruff + ruff format · mypy strict — 101 dosyada temiz |
| Mobil doğrulama | `flutter analyze` temiz · 45 test · Flutter 3.44.8 / Dart 3.12.2 |
| Compose | api + postgres + redis + minio healthy · `/health/ready` → postgresql + redis ready |
| Açık dal | `main` + aktif work order dalları (başka dal bırakılmaz) |

> **Her oturum İLK bu dosyayı okur.** Bu dosya ile `git log` çelişirse **git kazanır**; çelişkiyi gören oturum bu dosyayı aynı commit'te düzeltir.
>
> **PM oturumu ayrıca [handoffs/PM-NOTES.md](handoffs/PM-NOTES.md)'yi okur** — rol, tetikleme promptları, bekleyen kararlar, yazılacak iş emirleri, öğrenilen dersler. Teknoloji/metodoloji değerlendirmesi: [reviews/2026-07-30-tech-methodology.md](reviews/2026-07-30-tech-methodology.md).

## Nerede kaldık

### Tamamlandı (`main`'de)

- **Phase 0 — Temel platform.** identity/tenant + RBAC, medya multipart upload control-plane, jobs/attempts/outbox/idempotency/audit, RFC 9457 hata kataloğu, CI verify hattı.
- **Phase 1 A–D — Medya analiz hattı.** ingest güvenlik geçidi (ADR-006), teknik analiz (ffprobe / kalite sinyalleri / dikey medya / sınırlı proxy), sahne tespiti + konuşma çözümleme, video understanding (kontratlar, job akışı, frame budget, FFmpeg frame extraction, sağlayıcı yönlendirme ADR-007), Celery worker composition + outbox publisher + beat, processing-summary API.
- **Mobil uçtan uca demo.** `apps/mobile` — 24 Dart dosyası; işletme seçimi/oluşturma, video seçme, upload progress, 6 adımlı processing checklist, sonuç detayı. Material 3, Türkçe. Config yalnızca `--dart-define`; kaynak kodda token yok.

### Phase 1'den eksik

- Marka profili + ürün/hizmet kataloğu (PRD §11) → **W04**
- Sahne kütüphanesi arama + pgvector embedding/retrieval (PRD §16.4–16.5)
- **Phase 1 çıkış kriteri henüz karşılanmadı:** *"10 video yüklenir; sahneler, transcript ve etiketler görünür"* — yükleme yolu artık gerçek (W01), ama worker medyayı fixture'dan okuduğu için analiz gerçek byte görmüyor; bkz. **B1**.
- `video/quicktime` ve HEIC yüklemede kabul ediliyor ama analiz hattına girmiyor — `ingest.py` teknik analizi yalnızca `video/mp4` için kuyruğa alıyor (W01 raporu, madde 4: karar gerekiyor).

### Başlamadı

Phase 2 içerik üretimi · Phase 3 abonelik/entitlement · Phase 4 yayınlama · Phase 5–6 reklam · admin web paneli · n8n

## Bloke ediciler

| # | Konu | Etki | Çözüm |
|---|---|---|---|
| B1 | ~~`FakeMultipartStorage` byte kabul etmiyor~~ → **yükleme yarısı çözüldü** (`8d055b7`). Gerçek multipart PUT → complete → `uploaded` + ingest job, MinIO'ya karşı test edildi. **Kalan yarı:** worker medyayı hâlâ fixture tabanlı `FakeMediaMaterializer` ile okuyor, yani ffprobe gerçek byte görmüyor | Mobil demo 3. adımı çalışıyor; **Phase 1 çıkış kriteri hâlâ kapanmıyor** | W01 kapandı → **gerçek materializer adapter'ı için yeni WO gerekiyor** (W01 raporu, madde 3) |
| B2 | `JAVA_HOME` yok, Android cmdline-tools eksik | APK build edilemiyor (Dart kodunun derlendiği `flutter test` ile doğrulanmış) | **W02** ortam adımı |

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
| K2 | **n8n içeride mi?** Sustainable Use License ticari platform motoru olmayı kısıtlıyor; iş mantığı zaten yasak, geriye kalan zamanlamayı Celery Beat şu an yapıyor | Phase 2 zamanlama işinden önce | MVP'den çıkar |
| K3 | **Pazar kapsamı:** yalnız TR mi, EU/global roadmap'te mi? | Phase 2 render şeması | EU roadmap'teyse C2PA/provenance alanları şimdi şemaya girsin |
| K4 | **Kullanıcı düzenleme modeli:** "ufak editler" (metin taşı, sticker ekle, stil değiştir) nasıl yapılacak? PRD §18.2 bildirimsel overlay'i, §21.3 revizyonu tanımlıyor; §3.3 manuel editörü kapsam dışı bırakıyor — arada karar verilmemiş bir boşluk var | Phase 2 timeline şeması | **Parametrik düzenleme:** timeline JSON patch'i — metin içeriği (yasak-kelime doğrulamalı, fiyat/tarih yalnızca doğrulanmış kayıttan), 9'lu ızgara konum çapaları, stil token'ı, marka onaylı sticker kütüphanesi, segment sınırına snap. Serbest x/y ve kare kare montaj yok. Saf yeniden render **yeni hak tüketmez**, revizyon kotasından düşer. Platformun etkileşimli sticker'ları (anket/konum/mention) API ile eklenemez — ürün tarafında açıkça anlatılmalı |

| K6 | **iOS medya formatlarının analizi.** W01 `image/heic`, `image/heif`, `video/quicktime`'ı allowlist'e ekledi ama `ingest.py::_complete_clean` teknik analizi yalnızca `content_type == "video/mp4"` için kuyruğa alıyor → iPhone'un varsayılan `.mov` çıktısı ingest'ten sonra **sessizce duruyor**, sahne/transcript üretilmiyor. Kabul edip analiz etmemek, reddetmekten kötü: kullanıcı sessiz çıkmaz sokağa giriyor | **W09 ile birlikte** (Phase 1 çıkış kriteri iOS medyasıyla kapanmıyor) | **Analiz hattını genişlet**, istemcide transcode etme. Gerekçe: ffprobe/FFmpeg `.mov`/HEVC'yi zaten çözüyor, kapı bir eşitlik kontrolünden ibaret; telefonda transcode pil yakar ve PRD §2.5 "render/analiz sunucuda" diyor. **Ayrı açık:** HEIC bir *fotoğraf* — mevcut hat video odaklı (sahne, transcript). Fotoğraf için "analiz" ne demek (teknik metadata + VLM etiketleme, sahne/ASR yok) ayrıca tanımlanmalı |

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
| [W02](handoffs/W02-platform-hardening.md) | Bağımlılık tazeleme, lockfile, CI güvenlik kapıları | **tamamlandı** · doğrulama bekliyor (dal: `claude/platform-hardening-w02-74a021`) | `slice/0h-platform-hardening` (harness dalı farklı — rapora bkz.) | Opus 4.8 / medium | — |
| [W09](handoffs/W09-real-media-materializer.md) | **Gerçek medya materializer + `.mov`/HEVC analiz kapısı** — Phase 1 çıkış kriterinin kalan yarısı | **sıradaki** (W02 ile paralel olabilir) | `slice/1g-real-materializer` | Opus 5 / high | — |
| W04 | Marka profili + ürün/hizmet kataloğu modülü | W09 sonrası | `slice/1f-brand-catalog` | Opus 5 / high | **ayrılmış** — `storage_upload_id` kolonunu `String(128)`'den genişletme işi de bu slota bindirilir (W01'in kontrol-objesi geçici çözümünü kaldırır) |
| W05 | OpenTelemetry (trace + metric) | W01 kapanınca yazılacak | `slice/0i-telemetry` | Opus 4.8 / medium | — |
| W06 | PostgreSQL 18 + Valkey imaj geçişi | W01 + W02 kapanınca | `slice/0j-runtime-images` | Opus 4.8 / medium | — |

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
| `docs/STATUS.md` | PM (WO'lar yalnızca kendi durum satırını günceller) |

## Sprint 0 kaydı (2026-07-30, PM)

Depo hijyeni yapıldı: `main` 16 commit geride ve Phase 1'in tamamı merge edilmemiş durumdaydı; 5 worktree / 9 dal vardı ve isimler içerikle uyuşmuyordu.

- `main` → `7d78c6e` fast-forward (tüm commit'ler ata, lineer).
- Terk edilmiş çift iş **`c43ccad`** ("celery outbox and beat scheduling") silindi. Gerekçe: `ce96771` aynı base'den (`258439d`) çıkıp aynı 16 dosyayı kapsıyor ve süperset — özdeş `beat_schedule` anahtarları, `test_video_understanding_flow.py` birebir aynı, testlerde +129/+158 fazla kapsam. Kurtarma gerekirse SHA: `c43ccadd67783d2b781203e51b8abbc2be3c2abc` (reflog ~90 gün).
- Atıl 3 worktree ve 7 orphan dal kaldırıldı. Kalan: `main` (ana dizin) + geçici PM worktree'si.
