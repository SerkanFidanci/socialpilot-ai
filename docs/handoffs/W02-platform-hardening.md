# W02 — Bağımlılık tazeleme, lockfile, CI güvenlik kapıları, ortam

**Dal:** `slice/0h-platform-hardening` · **Base:** `main` · **Migration slotu:** yok
**Durum:** hazır, tetiklenmedi
**Neden bu iş:** Depodaki bağımlılık pinlerinin tamamı **Eylül–Kasım 2024** sürümlerine denk düşüyor; yani canlı paket deposundan doğrulanmamış, hafızadan yazılmış bir set. Üstüne tüm pinler **üst sınırlı** (`<0.116`, `<0.31`, `<1.15`, `<5.5`, `<3.13`) — bu yapı güncellemeyi aktif olarak bloke ediyor. Lockfile yok (CI ile prod aynı byte'ları kurmuyor), Dependabot/Renovate yok, ve PRD §41.1 ile §33.5'in zorunlu kıldığı güvenlik taramalarının **hiçbiri** CI'da yok.

## Okunacaklar

1. `docs/STATUS.md`
2. `services/api/pyproject.toml`, `services/api/requirements.txt`, `services/api/Dockerfile`
3. `.github/workflows/verify.yml`, `Makefile`, `compose.yaml`
4. `docs/runbooks/local-development.md`
5. PRD §41 ve §33.5 (`docs/product/product-requirements.md`) — **yalnızca bu iki bölüm**

## Kapsam

### 1. Paket yönetimi ve lockfile
- `uv`'ye geç: `pyproject.toml` + **`uv.lock`** (commit edilir). `requirements.txt` kaldırılır; Dockerfile ve CI `uv sync --locked` kullanır.
- Üst sınır pinleri kaldırılır; tekrarlanabilirliği lockfile sağlar. İstisna: bilinçli bir uyumsuzluk varsa gerekçesi yorumla yazılır.

### 2. Sürüm tazeleme
Her birini **kurulum anında paket deposundan doğrula**, bu dosyadaki sayılara güvenme:
- FastAPI, uvicorn, Alembic, Celery, redis-py, structlog, pydantic-settings, pytest, pytest-asyncio, mypy, ruff, asyncpg → güncel kararlı sürümler.
- SQLAlchemy **2.0'da kalır** (2.1 hâlâ beta) — bu pin doğru, dokunma.
- Python: `>=3.13` (`<3.13` sınırı kalkar). Dockerfile `python:3.13-slim`, CI `python-version: "3.13"`.
- Compose ve CI servisleri: `postgres:18-alpine`. PostgreSQL 18'in `uuidv7()` fonksiyonu ileride UUID PK yerelliği için değerli; bu WO onu **kullanmaz**, yalnızca sürümü hazırlar.
- Redis imajı **7'de kalır**; Valkey geçişi ayrı bir ADR önerisi olarak `docs/adr/`'a *öneri* statüsünde yazılır, uygulanmaz.
- Kırılan her API için düzeltme aynı slice'ta yapılır; "sonra bakılır" bırakılmaz.

### 3. CI güvenlik kapıları
`verify.yml`'a eklenir, mevcut adımlar korunur:
- Bağımlılık zafiyet taraması (`uv`'nin audit yolu veya `pip-audit`).
- Secret taraması (`gitleaks`) — geçmiş commit'ler dahil.
- Container taraması (`trivy`) — build edilen API imajı üzerinde.
- Kritik bulgu = kırmızı build. Eşikler workflow içinde açıkça yazılır.

### 4. Ortam: APK build blokajı (B2)
- `JAVA_HOME` / JDK ve Android cmdline-tools gereksinimi `docs/runbooks/local-development.md`'ye **adım adım** yazılır (kurulum komutları dahil, Windows).
- Bu bir CI işi değil; CI'da APK build edilmez. Amaç: bir sonraki mobil oturumun tıkanmaması.

### 5. Otomatik güncelleme
- `renovate.json` (veya `.github/dependabot.yml`) eklenir: haftalık, gruplanmış, lockfile güncelleyen.

## Kapsam dışı (dokunma)

- **`services/api/app/**` altındaki hiçbir uygulama kodu.** Sürüm yükseltmesi kod düzeltmesi gerektiriyorsa yalnızca gereken minimum satırı değiştir ve rapora yaz.
- **`app/core/config.py` — bu dosyanın sahibi W01.** Ayar eklemen gerekiyorsa dur ve rapora yaz.
- OpenTelemetry — W05'e ayrıldı (config.py çakışması nedeniyle).
- `docs/architecture/media-upload.md` (W01), `docs/product/requirements/**` ve `AGENTS.md` / `CLAUDE.md` (W03).
- Valkey geçişini uygulama; yalnızca ADR önerisi.

## Dokunulacak dosyalar (ilan)

```
services/api/pyproject.toml
services/api/uv.lock                  (yeni)
services/api/requirements.txt         (kaldırılır)
services/api/Dockerfile
.github/workflows/verify.yml
renovate.json                         (yeni)
Makefile
compose.yaml                          (yalnızca postgres imaj etiketi)
docs/runbooks/local-development.md
docs/adr/ADR-009-<bagimlilik-ve-runtime-temeli>.md   (yeni)
docs/adr/ADR-010-<valkey-degerlendirmesi>.md         (yeni, statü: önerildi)
```

## Kabul kriterleri

1. `uv sync --locked` ile temiz makinede kurulum çalışıyor; `uv.lock` commit'li.
2. `make verify` yeşil — Python 3.13 ve PostgreSQL 18 ile, 180 testin tamamı geçiyor.
3. Alembic `upgrade head → downgrade base → upgrade head` PostgreSQL 18'de çalışıyor; head hâlâ `0009_video_understanding`.
4. CI'da zafiyet + secret + container taraması adımları var ve bilinçli olarak kırmızıya düşebiliyor (dokümante edilmiş eşikle).
5. `flutter analyze` ve `flutter test` etkilenmemiş (mobil tarafa dokunulmadı).
6. Runbook'taki JDK adımları izlenerek `flutter build apk --debug` çalışıyor — ya da çalışmıyorsa **tam hata** rapora yazılıyor.
7. ADR-009 ve ADR-010 yazıldı, `docs/index.md` + `docs/adr/README.md` güncellendi.
8. Yükseltme sırasında değiştirilen her uygulama satırı rapora tek tek yazıldı.

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum doldurur)_
