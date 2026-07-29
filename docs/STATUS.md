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

## Karar bekleyenler

| # | Karar | Ne zaman gerekli | PM önerisi |
|---|---|---|---|
| K1 | **Faturalandırma modeli:** mağaza IAP mı, web-first + refakatçi mobil mi? Türkiye alternatif faturalandırma programlarında **yok** → %15–30 komisyon kaçınılmaz | Phase 3'ten önce | Web-first satış, mobilde satın alma yok (Apple 3.1.3(a)) |
| K2 | **n8n içeride mi?** Sustainable Use License ticari platform motoru olmayı kısıtlıyor; iş mantığı zaten yasak, geriye kalan zamanlamayı Celery Beat şu an yapıyor | Phase 2 zamanlama işinden önce | MVP'den çıkar |
| K3 | **Pazar kapsamı:** yalnız TR mi, EU/global roadmap'te mi? | Phase 2 render şeması | EU roadmap'teyse C2PA/provenance alanları şimdi şemaya girsin |

## Açık work order'lar

Protokol: [handoffs/README.md](handoffs/README.md)

| WO | Konu | Durum | Dal | Migration slotu |
|---|---|---|---|---|
| [W01](handoffs/W01-object-storage-adapter.md) | MinIO/S3 storage adapter + MIME düzeltmesi | hazır, tetiklenmedi | `slice/1e-object-storage` | — |
| [W02](handoffs/W02-platform-hardening.md) | Bağımlılık tazeleme, lockfile, CI güvenlik kapıları, ortam | hazır, tetiklenmedi | `slice/0h-platform-hardening` | — |
| [W03](handoffs/W03-docs-restructure.md) | Doküman yapısı + navigasyon katmanı | hazır, tetiklenmedi | `slice/doc-restructure` | — |
| W04 | Marka profili + ürün/hizmet kataloğu modülü | W03 kapanınca yazılacak | `slice/1f-brand-catalog` | **ayrılmış** |
| W05 | OpenTelemetry (trace + metric) | W01 kapanınca yazılacak | `slice/0i-telemetry` | — |

## Sprint 0 kaydı (2026-07-30, PM)

Depo hijyeni yapıldı: `main` 16 commit geride ve Phase 1'in tamamı merge edilmemiş durumdaydı; 5 worktree / 9 dal vardı ve isimler içerikle uyuşmuyordu.

- `main` → `7d78c6e` fast-forward (tüm commit'ler ata, lineer).
- Terk edilmiş çift iş **`c43ccad`** ("celery outbox and beat scheduling") silindi. Gerekçe: `ce96771` aynı base'den (`258439d`) çıkıp aynı 16 dosyayı kapsıyor ve süperset — özdeş `beat_schedule` anahtarları, `test_video_understanding_flow.py` birebir aynı, testlerde +129/+158 fazla kapsam. Kurtarma gerekirse SHA: `c43ccadd67783d2b781203e51b8abbc2be3c2abc` (reflog ~90 gün).
- Atıl 3 worktree ve 7 orphan dal kaldırıldı. Kalan: `main` (ana dizin) + geçici PM worktree'si.
