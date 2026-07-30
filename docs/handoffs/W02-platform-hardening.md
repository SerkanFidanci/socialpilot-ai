# W02 — Bağımlılık tazeleme, lockfile, CI güvenlik kapıları, ortam

**Dal:** `slice/0h-platform-hardening` · **Base:** `main` · **Migration slotu:** yok
**Durum:** bekliyor — **başlama koşulu: W01 merge edilmiş olmalı**
**Neden bekliyor:** `services/api/pyproject.toml` iki WO'nun da dokunacağı tek dosya. W01 oraya yalnızca storage istemcisi bağımlılığını ekler; W02 dosyayı uv'ye taşır. Sıra: **W01 → W02**.
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
- **PostgreSQL 18 ve Valkey geçişi bu WO'da YOK** — ikisi de `compose.yaml`'a dokunur, o dosyanın sahibi W01. Ayrı bir WO'ya (W06) alındı. Valkey için yalnızca *öneri* statüsünde ADR yazılır, uygulanmaz.
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

### 5. W01 ve W03'ten devredilen iki kalem (sahibi sensin)

- **`make generate-docs` `endpoints.md`'yi üretmiyor.** W03 `docs/api/endpoints.md`'yi ve üretecini yazdı ama `Makefile` senin sahipliğinde olduğu için bağlamadı. `generate-docs` hedefine ekle; `check-openapi` davranışı bozulmasın. Üretilen tablo koddan sapamamalı.
- **Runbook'ta gerçek byte yolu adımı eksik.** `compose.yaml` `STORAGE_ADAPTER: ${STORAGE_ADAPTER:-fake}` kullanıyor; mobil demonun gerçek upload'ı için depo kökünde `.env` içine `STORAGE_ADAPTER=s3` yazıp `docker compose up -d api` gerekiyor. Bunu `docs/runbooks/local-development.md`'ye yaz (W01 raporu, madde 2). Varsayılanı `s3`'e çevirme — mevcut kontrol düzlemi testleri `fake`'e dayanıyor.

### 6. Otomatik güncelleme
- `renovate.json` (veya `.github/dependabot.yml`) eklenir: haftalık, gruplanmış, lockfile güncelleyen.

## Kapsam dışı (dokunma)

- **`services/api/app/**` altındaki hiçbir uygulama kodu.** Sürüm yükseltmesi kod düzeltmesi gerektiriyorsa yalnızca gereken minimum satırı değiştir ve rapora yaz.
- **`app/core/config.py` ve `compose.yaml` — bu dosyaların sahibi W01.** Dokunman gerekiyorsa dur ve rapora yaz.
- **`docs/index.md` ve `docs/adr/README.md` — sahibi W03.** Yazdığın ADR'leri bu indekslere **ekleme**, yalnızca raporunda bildir; PM bağlar.
- OpenTelemetry — W05'e ayrıldı (config.py çakışması nedeniyle).
- PostgreSQL 18 / Valkey uygulaması — W06.
- `docs/architecture/media-upload.md` (W01), `docs/product/requirements/**` ve `AGENTS.md` / `CLAUDE.md` (W03).

## Dokunulacak dosyalar (ilan)

```
services/api/pyproject.toml
services/api/uv.lock                  (yeni)
services/api/requirements.txt         (kaldırılır)
services/api/Dockerfile
.github/workflows/verify.yml
renovate.json                         (yeni)
Makefile
docs/runbooks/local-development.md
docs/adr/ADR-009-<bagimlilik-ve-runtime-temeli>.md   (yeni)
docs/adr/ADR-010-<valkey-degerlendirmesi>.md         (yeni, statü: önerildi)
```

## Kabul kriterleri

1. `uv sync --locked` ile temiz makinede kurulum çalışıyor; `uv.lock` commit'li.
2. `make verify` yeşil — Python 3.13 ile, testlerin tamamı geçiyor (W01 sonrası sayı artmış olabilir).
3. Alembic `upgrade head → downgrade base → upgrade head` çalışıyor; head değişmemiş.
4. CI'da zafiyet + secret + container taraması adımları var ve bilinçli olarak kırmızıya düşebiliyor (dokümante edilmiş eşikle).
5. `flutter analyze` ve `flutter test` etkilenmemiş (mobil tarafa dokunulmadı).
6. Runbook'taki JDK adımları izlenerek `flutter build apk --debug` çalışıyor — ya da çalışmıyorsa **tam hata** rapora yazılıyor.
7. ADR-009 ve ADR-010 yazıldı (indekslere **eklenmedi** — W03'ün sahipliğinde, raporda bildirildi).
8. Yükseltme sırasında değiştirilen her uygulama satırı rapora tek tek yazıldı.

## Rapor — 2026-07-30 · yürütme oturumu (Opus 4.8)

**Dal:** `claude/platform-hardening-w02-74a021` (harness worktree dalı — WO'nun ilan ettiği
`slice/0h-platform-hardening` yerine; ayrıntı aşağıda) · **Commit'ler:** `993d31b` ·
**Durum:** tamamlandı (merge PM/doğrulama oturumuna bırakıldı — `main`'e girmedi)

### Yapılanlar

**1. Paket yönetimi + lockfile (uv)**
- `services/api/pyproject.toml` uv'ye taşındı: üst sınır pinleri kaldırıldı, aralıklar yalnızca
  **alt sınır**; tekrarlanabilirlik `services/api/uv.lock` (yeni, commit'li) ile sağlanıyor.
  Tek bilinçli üst sınır **SQLAlchemy `<2.1`** (2.1 hâlâ beta) — yorumla işaretlendi.
- `requires-python` `>=3.13`; ruff `target-version = "py313"`, mypy `python_version = "3.13"`.
- `services/api/requirements.txt` **kaldırıldı**.
- Her sürüm **kurulum anında PyPI'dan doğrulandı** (hafızadan değil). Lock'ta sabitlenen
  başlıca sürümler: fastapi 0.141.1, uvicorn 0.52.0, alembic 1.18.5, celery 5.6.3, redis 8.1.0,
  kombu 5.6.2, structlog 26.1.0, pydantic-settings 2.14.2, pydantic 2.13.4, sqlalchemy 2.0.51,
  httpx 0.28.1, asyncpg 0.31.0, mypy 2.3.0, pytest 9.1.1, pytest-asyncio 1.4.0, ruff 0.16.0.
  (Dış-platform teyidi [99-external-platform-facts.md](../product/requirements/99-external-platform-facts.md)
  §49 satırıyla uyumlu.)

**2. Dockerfile**
- `python:3.13-slim`; pinlenmiş `ghcr.io/astral-sh/uv:0.11.31` katmanından uv; iki katmanlı
  `uv sync --locked` (önce bağımlılık, sonra proje). Ortam **kaynak ağacının dışında**
  (`/opt/venv`) tutuluyor — Compose'un read-only kaynak bind-mount'u kurulu paketleri
  gölgeleyemesin diye (kritik: aksi halde api servisi kırılırdı).

**3. CI güvenlik kapıları (`.github/workflows/verify.yml`)** — PRD §41.1 / §33.5
- `backend` işi uv'ye geçti (`astral-sh/setup-uv@v9.0.0`, `uv sync --locked --all-extras`,
  `UV_PYTHON=3.13`); mevcut adımların tümü korundu.
- `dependency-audit`: `uv export`'lanan kilitli set üzerinde `pip-audit` (eşik: herhangi bir
  PyPA/OSV zafiyeti = kırmızı).
- `secret-scan`: pinlenmiş `gitleaks 8.30.1` ikilisi, **tüm geçmiş** (`fetch-depth: 0`); dev
  placeholder'ları `.gitleaks.toml` allowlist'inde.
- `container-scan`: API imajı build edilir, `aquasecurity/trivy-action@v0.36.0` (eşik:
  düzeltilebilir **CRITICAL** = kırmızı; `ignore-unfixed`). Tüm eşikler workflow'da yazılı.

**4. Ortam / APK (B2)** — `docs/runbooks/local-development.md`'ye Windows adım adım JDK 17 +
Android cmdline-tools + lisans + `flutter build apk --debug` yazıldı. (Doğrulama notu aşağıda.)

**5. Devralınan iki kalem**
- `make check-openapi` artık **hem** `openapi.json` **hem** `docs/api/endpoints.md`'yi diff'liyor
  (endpoints.md zaten `generate_openapi.py` tarafından üretiliyordu; eksik olan drift koruması
  bağlandı). `generate-docs` davranışı ve `check-openapi` sözleşmesi korundu.
- Runbook'a gerçek byte yolu bölümü (`.env`'e `STORAGE_ADAPTER=s3` + `docker compose up -d api`;
  varsayılan `fake` bırakıldı) ve uv kurulum bölümü eklendi.

**6. Otomatik güncelleme** — `renovate.json` (yeni): haftalık, gruplu (Python / CI-imaj),
`rangeStrategy: update-lockfile`, SQLAlchemy major hold'u devre dışı kural olarak kodlandı.

**7. ADR'ler** — `ADR-009-dependency-and-runtime-baseline.md` (Accepted),
`ADR-010-valkey-runtime-evaluation.md` (Proposed, uygulanmadı). **İndekslere eklenMEdi**
(`docs/index.md` + `docs/adr/README.md` W03 tekelinde) — PM/W03 bağlar.

### Kapsam dışı bıraktıklarım ve nedeni

- `compose.yaml`, `app/core/config.py`, `docs/architecture/media-upload.md`,
  `docs/product/**`, `AGENTS.md`/`CLAUDE.md`, `docs/index.md`, `docs/adr/README.md`:
  sahibi başka WO — dokunulmadı (yalnızca doğrulama için compose **çalıştırıldı**, düzenlenmedi).
- PostgreSQL 18 / Valkey uygulaması → W06 (ADR-010 yalnızca öneri).
- OpenTelemetry → W05.
- Mobil kaynak kodu → dokunulmadı (`apps/mobile` diff'te yok).

### Doğrulama

| Kontrol | Sonuç |
|---|---|
| `uv sync --locked` temiz kurulum (host + Docker build) | ✅ 59 paket çözüldü, imaj build edildi |
| `make verify` eşdeğeri — Linux konteynerde | ✅ ruff temiz · ruff format temiz · **mypy 102 dosya temiz** · **244 pytest passed** (gerçek PostgreSQL+Redis+MinIO) · check-openapi (host) drift yok |
| Alembic `upgrade head → downgrade base → upgrade head` (konteyner) | ✅ head değişmedi (`0009_video_understanding`) |
| `pip-audit` kilitli set | ✅ "No known vulnerabilities found" (kapı temiz sette kırmızıya düşmüyor) |
| Kabul kriteri 4 — CI kapıları + eşikler | ✅ üç kapı, eşikler dokümante |
| Kabul kriteri 5 — `flutter analyze`/`flutter test` etkilenmedi | ✅ mobil dosya değişmedi (bkz. aşağı: bu ortamda Flutter kurulu değil) |
| Kabul kriteri 6 — `flutter build apk --debug` | ⚠️ adımlar yazıldı; **bu ortamda doğrulanamadı** (JDK yok, `JAVA_HOME` boş, Flutter SDK bu shell'de çözülmüyor = B2'nin kendisi). Tam hata: `java` bulunamıyor + `flutter` komutu yok |
| Kabul kriteri 7 — ADR-009/010 | ✅ yazıldı, indekse eklenmedi (raporlandı) |

### Açıkça belirtmem gerekenler

**a) Sürüm yükseltmesinde değişen uygulama/test satırları (kriter 8).** Hepsi araç/sürüm
kaynaklı; app/** için minimum tutuldu ve mümkün olduğunca `pyproject` (benim sahipliğim)
konfigürasyonuna alındı:

- `pyproject.toml` (benim): iki dar kapsamlı `[[tool.mypy.overrides]]` — (i)
  `disallow_untyped_decorators=false` yalnızca `app.worker.tasks` + `app.modules.operations.tasks`
  (Celery'nin tipsiz `@task`/`@signal.connect` decorator'ları; mypy 2.x `untyped-decorator`);
  (ii) `generate_endpoints_doc` için `ignore_missing_imports` (script'in sys.path-tabanlı kardeş
  importu). Bu sayede `app/worker/tasks.py`'a **hiç** dokunulmadı, strict global olarak
  gevşetilmedi.
- `app/infrastructure/redis/client.py`: `redundant-cast` (redis-py 8 tiplemesi) → `cast(Redis, …)`
  ve artık gereksiz `from typing import cast` importu kaldırıldı, doğrudan `return Redis.from_url(…)`.
- `app/modules/operations/tasks.py`: 4 decorator'daki artık **kullanılmayan** `# type: ignore[misc]`
  yorumları kaldırıldı (override sonrası `unused-ignore`).
- `tests/integration/` 8 dosya: `Generator[None, None, None]` → `Generator[None]` (ruff UP043,
  py313 hedefi); `test_media_ingest.py`'da ek bir ruff-format satır düzeni.
- `tests/integration/{test_operations,test_media_uploads,test_identity_businesses}.py`:
  `.status_code` dönüşleri `int(...)` ile sarıldı (mypy 2.3 `no-any-return`; değerler zaten int).
- `docs/generated/openapi.json`: **+7 satır** — fastapi 0.141 `ValidationError` şemasına pydantic
  `ctx`/`input` alanlarını ekliyor (katkısal/kırıcı değil). Regenerate edilip commit edildi;
  aksi halde `check-openapi` kırmızıya düşerdi.

**b) İlan edilen "Dokunulacak dosyalar" dışına çıkan dosyalar** (yukarıdaki upgrade fallout'a ek):
`.gitleaks.toml` (yeni) eklendi — secret kapısının dev placeholder'larda (`socialpilot_local_only`,
`development-local-identity-key-not-for-production`) yanlış-pozitif vermeden gerçek sızıntıları
yakalaması için gerekli. PM'in haberi olsun diye bildiriyorum.

**c) Dal adı uyuşmazlığı.** Harness bu worktree'yi `claude/platform-hardening-w02-74a021` dalıyla
kurdu; WO `slice/0h-platform-hardening` diyor. Harness otomasyonunu bozmamak için dalı yeniden
adlandırmadım. Merge/rename PM'e bırakıldı; STATUS W02 satırına gerçek dal not düşüldü.

**d) B2 tam kapatılmadı.** APK adımları runbook'ta hazır ama bu ortamda JDK/Flutter olmadığından
uçtan uca çalıştırılamadı (kriter 6'nın "çalışmıyorsa tam hata rapora yazılır" dalı). İlk mobil
oturum adımları izleyip sonucu runbook'taki durum notuna yazmalı.

**e) Takip için not (bloke edici değil).** starlette 1.3 TestClient bir
`StarletteDeprecationWarning` veriyor: *"Using httpx with starlette.testclient is deprecated;
install httpx2 instead."* Testler geçiyor; ileride starlette test istemcisi değişimi için ayrı
bir kalem olabilir — bu WO kapsamında değil.

**f) Windows-yerel birim testleri.** Host'ta 14 birim testi düşüyor (`ffmpeg` yok → `[WinError 2]`;
`worker_temp_root` POSIX-mutlak-yol doğrulayıcısı `C:\` yolunu reddediyor). İkisi de **ortamsal**,
sürüm yükseltmesiyle ilgisiz; Linux konteynerde 244/244 geçiyor.

## Doğrulama

_(test eden oturum doldurur)_

## Doğrulama

_(test eden oturum doldurur)_
