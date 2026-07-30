# Proje Durumu

**Son güncelleme:** 2026-07-30 · **Sahip:** PM oturumu

| | |
|---|---|
| `main` | `7d78c6e` — feat: add flutter media analysis demo |
| Alembic head | `0009_video_understanding` (tek head) |
| Backend doğrulama | 180 pytest (gerçek PostgreSQL) · ruff + ruff format · mypy strict — 95 dosyada temiz |
| Mobil doğrulama | `flutter analyze` temiz · 45 test · Flutter 3.44.8 / Dart 3.12.2 |
| Compose | api healthy · `/health/ready` → postgresql + redis ready |
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
- **Phase 1 çıkış kriteri henüz karşılanmadı:** *"10 video yüklenir; sahneler, transcript ve etiketler görünür"* — upload byte yolu bloke, bkz. **B1**.

### Başlamadı

Phase 2 içerik üretimi · Phase 3 abonelik/entitlement · Phase 4 yayınlama · Phase 5–6 reklam · admin web paneli · n8n

## Bloke ediciler

| # | Konu | Etki | Çözüm |
|---|---|---|---|
| B1 | `FakeMultipartStorage` byte kabul etmiyor; part URL'leri kasıtlı olarak erişilemez `fake-storage.invalid` host'una gidiyor, `complete_upload` yalnızca in-process test hook'uyla çalışıyor | Gerçek PUT çalışmıyor → mobil demo 3. adımı ve Phase 1 çıkış kriteri kapanamıyor | **W01** — MinIO/S3 adapter |
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

## Açık work order'lar

Protokol: [handoffs/README.md](handoffs/README.md)

| WO | Konu | Durum | Dal | Model / effort | Migration slotu |
|---|---|---|---|---|---|
| [W01](handoffs/W01-object-storage-adapter.md) | MinIO/S3 storage adapter + iOS MIME düzeltmesi | **şimdi** | `slice/1e-object-storage` | Opus 5 / high | — |
| [W03](handoffs/W03-docs-restructure.md) | Doküman yapısı + navigasyon katmanı | **şimdi** (W01 ile paralel) | `slice/doc-restructure` | Opus 4.8 / medium | — |
| [W02](handoffs/W02-platform-hardening.md) | Bağımlılık tazeleme, lockfile, CI güvenlik kapıları | **W01 merge sonrası** | `slice/0h-platform-hardening` | Opus 4.8 / medium | — |
| W04 | Marka profili + ürün/hizmet kataloğu modülü | W03 kapanınca yazılacak | `slice/1f-brand-catalog` | Opus 5 / high | **ayrılmış** |
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
