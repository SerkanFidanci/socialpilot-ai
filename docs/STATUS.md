# Proje Durumu

**Son güncelleme:** 2026-08-02 · **Sahip:** PM oturumu

| | |
|---|---|
| `main` | W01→W23 merge edildi — **PHASE 2 TAMAM**, defter bütünlüğü artık şemada (ADR-018). **W06 dalda tamamlandı** (PG 18.4 + Valkey 9.1.1 + çalışan yedek runner'ı, D1 kapanıyor) → sırada **Phase 3** (K1 kararı gerekiyor) |
| Alembic head | `0020_ledger_integrity` (tek head; zincir 0001→0020, up/down/up doğrulandı) |
| Backend doğrulama | **1474 pytest** (gerçek PostgreSQL + MinIO + FFmpeg; merge sonrası PM koşusu) · lint + format + mypy strict temiz · py313 / mypy 2.3 / ruff 0.16 |
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

### Sırada

**Phase 2 — içerik üretimi.** Planı yazıldı: [plans/active/phase-2-content-generation.md](plans/active/phase-2-content-generation.md). Yedi slice (2A→2G), girişte alınmış kararlarla. **Hiçbir bekleyen karar fazı bloke etmiyor.** 2A–2E kapandı ve merge edildi; **2F dalda tamamlandı** (W21 — merge bekliyor); sırada **2G (planlayıcı)**.

> **2E birinci yarı tamam (dalda, W19):** artık bir "içerik projesi" var. `PLANNED`'dan
> `PREVIEW_READY`'ye giden yol uçtan uca çalışıyor — senaryo, seslendirme, timeline, render, QC
> sırayla ve her geçiş kaydedilerek. Üç devralınan borç kapandı (voiceover miksajı, QC kuyruk
> olayı + sorgu yeniden şekillendirmesi, `pending` süpürücü). **Hak tüketimi bu slice'ta yok**;
> W20 tüketim noktalarını buraya takacak.
>
> **2E ikinci yarı tamam (dalda, W20):** artık **sayıyor**. Proje açılışı hakkı aynı
> transaction'da rezerve ediyor (yetersizse `402` ve proje hiç oluşmuyor), projeyi terminal yapan
> transaction hakkı sonuçlandırıyor ya da iade ediyor. Bakiye hiçbir yerde saklanmıyor —
> append-only defterin toplamı, ve hem "append-only" hem "negatif olamaz" **veritabanı
> trigger'ı**. Puan tablosu (§12.4) sürümlü ve her tahsilat sürümünü taşıyor.
> **Ödeme/mağaza yok** (K1, Phase 3): tek kredi kaynağı `owner`'ın manuel grant'i.
> Ayrıntı: [entitlement.md](architecture/entitlement.md), ADR-017.
>
> **W20'nin açık bıraktıkları (2F/Phase 3):** proje iptali yok, yani `WAITING_MEDIA`'da park eden
> proje kredisini süresiz tutuyor (2F); tekil uçlar (proje bağlamı olmayan senaryo/seslendirme/
> timeline/render) bilinçli olarak **ücretsiz** (Phase 3); §12.7'nin `CONSUMED → REFUNDED` yolu
> destek yüzeyi istiyor (Phase 3); §12.6/§12.9'un hak penceresi ve devir kuralları yok.

> **2F tamam (dalda, W21):** proje artık `PREVIEW_READY`'de **durmuyor**. Sıralayıcı oradan
> geçerken §21.1'in yedi politikasını uyguluyor; onay gerekiyorsa `WAITING_APPROVAL`, gerekmiyorsa
> aktörsüz bir `auto_approved` kaydıyla `APPROVED`. Ret kapalı on nedenden biri + isteğe bağlı
> serbest not (`other` ise zorunlu); revizyonun **sınıfı ve yeniden başlangıç noktası değişen
> alandan türetiliyor** ve hat gerçekten oradan yeniden başlıyor. **W20'nin açığı kapandı:**
> terminal olmayan her durumdan iptal + iade, ve `WAITING_MEDIA`'da yaşlanan projeler için
> `content.project.sweep`. Ayrıntı: [content-render.md](architecture/content-render.md).
>
> **W21'in yükselttiği üç şey (PM kararı bekliyor):** (1) §20'ye iki durum eklendi — `APPROVED` ve
> `CANCELLED`; **2G'nin kenarı `WAITING_APPROVAL → SCHEDULED` değil `APPROVED → SCHEDULED` olmalı**.
> (2) PM kararı 4'ün "küçük revizyon senaryo yeniden üretmez" ifadesi **CTA ve başlık için doğru
> değil** (metinleri senaryo dokümanında ve seslendirmede); uygulamada sınıf küçük kaldı, yeniden
> başlangıç senaryo — tek satırlık veri tablosu. (3) Onay politikası **proje** bazında saklanıyor;
> "işletme başına" katmanı §12.2'nin abonelik kalemiyle Phase 3'te geliyor.
>
> **W21'in açık bıraktıkları:** `ads_only` hiçbir şeye onay istemiyor (§14 reklam senaryosu
> açmadı, tablo import anında totallik zorluyor); `PROJECT_CANCELLED`/`PROJECT_ABANDONED` defterde
> `UNCLASSIFIED` (iade ediyor — bugün doğru, sınıflandırma `entitlement/ledger.py`'de tek satır);
> büyük revizyonun 2 kotası **tahmin**, W08 ölçünce yeniden değerlendirilecek; `docs/index.md` ve
> `docs/adr/README.md` indekslerine eklenmedi.

> **2D tamam (dalda):** takip 1 ile Celery bağlantısı da yapıldı — `content.qc.drain`, beat
> girdisi `drain-content-qc`, worker composition. QC artık uçtan uca akıyor.
>
> **2E'ye devredilen ölçüm — KAPANDI (W19).** W18 QC claim'ini "raporu olmayan `succeeded`
> render" taraması olarak bırakmıştı (200 bin render'da tick başına 134 ms) ve sonucu **"index
> tek başına çözmüyor, sorgunun korelasyonu ifade etmesi gerek"** idi. W19 ikisini de yaptı:
> render'ı başarılı yapan transaction'da `content.qc.requested` yazılıyor, ve claim'in yordamı
> `render_outputs.qc_claimed_at` ile kendi satırına taşındı (kısmi index; durağan durumda **boş
> küme**). Yeniden ölçüm: **199 ms → 3,6 ms**, bekleyen yokken **0,05 ms**, ve plan gerçekten
> `ix_render_outputs_awaiting_qc` index scan'ine döndü. Beat tick'i 30 s'den 900 s'lik seyrek
> süpürmeye indi. Ayrıntı: [background-jobs.md](architecture/background-jobs.md).
>
> **2D'nin ürün tarafına söylediği:** gerçek VLM sağlayıcısı bağlanana kadar (W08 sonrası) hiçbir
> render otomatik `passed` olmuyor. Model kontrolleri `unknown`, karar `needs_review`. Bu
> fail-closed kuralının doğru sonucu, eksiklik değil.

> ~~**2E'ye taşınan açık (W15):**~~ **KAPANDI (W19).** Her iki render adapter'ı artık `voiceover`
> ses kaynağını bildiriyor; satır başına WAV'lar tek track'e birleşiyor ve altlıkla mikseniyor,
> `duck_under_voice` varsa sidechain kompresörüyle. Uçtan uca kanıt: aynı kesitin üç render'ı
> (altlık / +ses / +ducking) **çözülmüş PCM hash'iyle** karşılaştırıldı ve üçü de farklı.
> `TIMELINE_UNSUPPORTED_AUDIO_SOURCE` artık yalnızca `music` için düşüyor — müzik lisans kaydı
> ister (§18.3) ve kabiliyeti bildirmek eksik kaydı yarım kalan bir render'a çevirirdi.

### Başlamadı

Phase 3 abonelik/entitlement · Phase 4 yayınlama · Phase 5–6 reklam · admin web paneli · n8n

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
| K3 | **Pazar kapsamı:** yalnız TR mi, EU/global mi? **Çerçeveleme düzeltildi:** bunu Phase 2 render şemasını bloke eden karar olarak sunmuştum — yanlıştı. AI disclosure alanı Meta'nın Temmuz 2026 zorunluluğu nedeniyle **TR-only kapsamda da** gerekli, ve C2PA kancası 2A'da açılıyor. K3 yalnızca **işaretlemenin katılığını** belirliyor. | Phase 2 sonu / EU'ya girmeden | EU roadmap'teyse makine-okunur işaretlemeyi katılaştır; alan zaten var |
| ~~K4~~ | **KARARA BAĞLANDI (PM/mimar):** parametrik düzenleme. Timeline JSON patch'i; metin içeriği yasak-kelime doğrulamalı, fiyat/tarih yalnızca doğrulanmış kayıttan, 9'lu ızgara konum çapaları, stil token'ı, marka onaylı sticker kütüphanesi, segment sınırına snap. Serbest x/y ve kare kare montaj yok. **Saf yeniden render yeni hak tüketmez**, revizyon kotasından düşer. Platformun etkileşimli sticker'ları (anket/konum/mention) API ile eklenemez — ürün tarafında anlatılmalı. Gerekçe ve ADR: [Phase 2 planı](plans/active/phase-2-content-generation.md) §2, ADR'ı slice 2A yazar | — | — |

| K6 | **iOS medya formatlarının analizi.** ~~`.mov` sessizce duruyor~~ **video yarısı W09'da çözüldü** (`5ee03d4` merge edildi): `.mov`/`video/quicktime` artık analiz hattına giriyor, codec ffprobe'dan doğrulanıyor, desteklenmeyen codec `rejected`. HEIC/HEIF ingest'te **açıkça reddediliyor** (`INGEST_ANALYSIS_UNSUPPORTED_MEDIA_TYPE`) — sessiz çıkmaz sokak yok. **Kalan (ikinci yarı):** HEIC/HEIF *fotoğraf* analiz hattı (teknik metadata + VLM etiketleme; sahne/ASR yok) tanımlanıp inşa edilmeli — ayrı slice, enum durumu için migration slotu ister | fotoğraf hattı Phase 2'den önce | **Video yarısı uygulandı** (ADR-011). Fotoğraf hattı: bir "fotoğraf hazır/analiz" durumu + VLM etiketleme; landing'de HEIC→JPEG transcode gerekir (platform uyumu). Bu geldiğinde W09'un geçici HEIC reddi kalkar |

### W13 kural onayları (PM, 2026-07-31)

W13'ün yükselttiği kararların tamamı karara bağlandı:

1. **Üretimde fake AI davranışı — ONAYLANDI ve genelleştirildi.** `script_generation` üretimde `Settings` reddi yerine `DisabledScriptGenerationAdapter` + `503 SCRIPT_GENERATION_NOT_CONFIGURED` alıyor; boot çökmüyor. Gerekçe doğru: fake render açıkça yer tutucu dosya yazar, fake senaryo ise **insanın onaylayıp yayınlayabileceği akıcı metin** üretir — sessizce üretime sızması asıl tehlike, ama bir kabiliyet için tüm uygulamayı düşürmek yanlış takas. **Genel kural:** çıktısı insan-onaylanabilir olan AI kabiliyetleri üretimde `disabled` durumuna düşer ve dokümante hata döner; altyapı adapter'ları (storage/identity/materializer/render) `Settings` doğrulamasında reddedilmeye devam eder. 2C (TTS) ve sonrası bu kurala uyar.
2. **`Permission.CONTENT_GENERATE` — ONAYLANDI.** PRD §4 açık: editor içerik üretir. W11'in timeline'ı `BUSINESS_UPDATE`'e bağlaması tutarsızdı; hizalama **W14 kalem 4**.
3. **Kampanya bitişi kapsayıcı son gün olarak basılır — ONAYLANDI.** Pencere teknik olarak `[starts_at, ends_at)`; reklam metninde son kapsayıcı an işletme saat diliminde biçimlendirilir. Bir gün fazla vaat etmemek ücretli gönderide doğru taraf.
4. **Yüzde işareti fiyat sayılır — ONAYLANDI.** Oran ya doğrulanmış indirimdir ya iddiadır; ikisi de modelin yazacağı şey değil.

Ayrıca kayda geçen: `SCRIPT_GENERATION_MAX_COST_MINOR=0` güvenli varsayılanı doğru (gerçek sağlayıcı bütçe açıkça verilmeden çalışamaz); telefon tespiti marka iletişim kaydı gelince slot+kalıp birlikte eklenir.

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
| [W07](handoffs/W07-single-server-resilience.md) | **Tek sunucu dayanıklılığı** | **kapandı** · merge (`c199b86`) + **Codex doğrulaması geçti** (gerçek restore: satır sayıları 1/1/4 birebir, head doğru; CPU doygunluğunda `/health/ready` 10/10 × 200) | dal silindi | Opus 4.8 / medium | — |
| [W08](handoffs/W08-provider-benchmark-harness.md) | **Golden set benchmark** | **kapandı** · merge (`aea6a18`) + **Codex doğrulaması geçti** (metrikler bağımsız stdlib hesabıyla birebir; maliyet tavanı koşuyu çağrıdan önce durdurdu, exit 2) | dal silindi | Opus 5 / high | — |
| [W04](handoffs/W04-brand-catalog.md) | **Marka + katalog** | **kapandı** · merge (`5addf69`) + Codex doğrulaması: 4/5 geçti, **1 yüksek bulgu açık** (integral float parasal alanda kabul ediliyor) → **W12** | dal silindi | Opus 5 / high | `0010` |
| [W05](handoffs/W05-opentelemetry.md) | **OpenTelemetry** | **kapandı** · merge (`5addf69`) + Codex doğrulaması: 3/4 geçti (kapalıyken sıfır maliyet, sentinel sızıntısı yok, düşük kardinalite), **trace zinciri worker'a geçmiyor** → **W12** | dal silindi | Opus 4.8 / medium | — |
| [W06](handoffs/W06-runtime-images-and-backup.md) | **Çalışma zamanı imajları + yedek runner'ı** · **D1 kapısını kapatır** | **tamamlandı, dalda** · `postgres:18.4-alpine` + `valkey/valkey:9.1.1-alpine` (19beta2 ve `unstable` bilinçli alınmadı; Alpine'da kalındı ki collation sağlayıcısı major atlamayla birlikte değişmesin) · **ADR-010 kabul edildi** (uyumsuzluk yok: broker + sonuç backend'i + beat + outbox publisher'ı uçtan uca koştu, outbox satırı `pending→published` gerçek drain mesajıyla) · **1474 test** yeni imajlarda, hiçbiri düzenlenmeden · `0001→0020` boş volume'de up/down/up · OpenAPI byte özdeş · **yedek runner'ı `--profile backup`**: iki tek atımlık servis (sürekli açık zamanlayıcı yok — çıkış kodu systemd `OnFailure=`'a gitsin diye), gerçekten koştu ve **kendi aldığı yedekten geri yükledi**: 6 tablonun satır sayısı kaynakla birebir, head `0020`, ciphertext `Salted__` (düz SQL yok) · geri yüklenen defterde `0020`'nin **muhafızları da ayakta** (append-only + insert guard ham SQL saldırısını reddetti) · **kendi işimde bulduğum hata:** ikinci Dockerfile aşaması `api/worker/beat`'i sessizce onu build ettirdi ve API imajı `pg_dump` taşıdı (ADR-013 ihlali) → `target: runtime` pinlendi, süit doğru imajda yeniden koşuldu · **ADR-019 yazıldı, numara PM teyidi bekliyor** · yeni runbook: üretim major sürüm yükseltmesi · merge edilmedi | `slice/0j-runtime-images` | Opus 5 / high | — |
| [W10](handoffs/W10-schema-debt.md) | **Şema borcu** (4 kalem) | **tamamlandı** · merge (`0a44f22`) · `0011` | dal silindi | Opus 4.8 / medium | kullanıldı |
| [W11](handoffs/W11-timeline-and-render.md) | **Phase 2A** — timeline + RenderPort + AI'sız render | **tamamlandı** · merge (`258ddc3`) · `0012` yeniden zincirlendi · ADR-015/016 | dal silindi | Opus 5 / high | kullanıldı |

| [W13](handoffs/W13-script-generation.md) | **Phase 2B** — senaryo üretimi | **kapandı** · Codex turu döndü (2026-08-01): **1 kritik açık** — Unicode görünmez/normalizasyon varyantları dedektörü atlatıyor → **W16**; 2 yanlış pozitif bilinçli politika olarak pinlenecek | dal + worktree silindi | Opus 5 / high | kullanıldı |
| [W14](handoffs/W14-verification-followups-2.md) | **Doğrulama bulguları 2. tur** | **kapandı** · Codex turu döndü (2026-08-01): **1 kritik açık** — `extra` alanları redakte edilmiyor → **W16**; ek: `GoogleAccessId` maskelenmiyor (rapor iddiası hatalıydı) | dal silindi | Opus 5 / high | — |
| [W15](handoffs/W15-tts-voiceover.md) | **Phase 2C** — seslendirme: `TTSPort` (fake), ffprobe ile ölçülmüş segment süreleri, timeline hizalaması | **KAPANDI** · merge · `0014` · **Codex doğrulaması geçti (6/6)** — serbest metin yolu yok, tenant sızıntısı yok, tavan çağrı öncesi duruyor, beyan ölçümü ezemiyor, idempotency kanonik, imza sızmıyor · **açık:** render adapter'ları `voiceover` kaynağını bildirmiyor → 2E | dal + worktree silindi | Opus 5 / high | kullanıldı |
| [W16](handoffs/W16-verification-followups-3.md) | **Doğrulama bulguları 3. tur** — log `extra` yüzeyi + `GoogleAccessId`, dedektör normalizasyonu | **iki tur da merge edildi** (2. tur: Latin dışı alfabe kısıtı `SCRIPT_UNSUPPORTED_CHARACTER`, görünmezler `Cf`/`Cn`/`Co`/`Cs` kategorisiyle, redaksiyon yüzde-kodlu adları görüyor) · **kapanış birleşik Codex turuna bağlı** (W17 sonrası) | `fix/verification-followups-3` (worktree duruyor, birleşik tur bitene kadar) | Opus 5 / high | — |
| [W17](handoffs/W17-latin-fold-pattern-grammar.md) | **Latin katlaması + kalıp grameri + Türkçe çekim + yazılı sayı grameri** | **kapandı** · üç tur merge edildi · sayı sözcükleri liste ama **birleşimleri gramer** (bitişik/tireli dahil), `T Lye` kapalı Türkçe ek kümesiyle, `Şef T. Lezzetli` pini korundu · **111.129 varyant / 0 kaçış**, jeneratif regresyon testi · **takip düzeltmesi 3 (ondalık kesir: `bir tam onda bes lira`) `fix/verification-followups-4` dalında uygulandı** — 0/81 ve 0/243, merge bekliyor | `fix/w17-latin-fold` (worktree duruyor) · takip 3: `fix/verification-followups-4` | Opus 5 / high | — |
| [W18](handoffs/W18-automatic-qc.md) | **Phase 2D — otomatik QC** (§19.4) · fail-closed · karar verir eylem yapmaz · `forbidden_matcher` birleştirmesi | **kapandı** · merge · `0015` · 13 kontrol raporda ve atlanması ifade edilemiyor; gerçek bozuk medya fixture'ları; karar tablosu permütasyonlarla tüketildi; `content.qc.drain` + beat bağlantısı yapıldı · **takip 2 (yinelenen sonuç birleştirmesi) `fix/verification-followups-4` dalında uygulandı** — birleştirme kommutatif, en kötü kazanır, merge bekliyor | `slice/2d-automatic-qc` (merge edildi) · takip 2: `fix/verification-followups-4` | Opus 5 / high | kullanıldı |
| [W19](handoffs/W19-content-lifecycle.md) | **Phase 2E (birinci yarı) — içerik projesi yaşam döngüsü** (§20): kapalı durum makinesi + geçiş kaydı, QC kararının sınırlı eyleme dönmesi (deneme sınırı zorunlu), üç devralınan borç (voiceover miksajı, QC kuyruk olayı, `pending` süpürücü) | **tamamlandı, dalda** · `0016` · uçtan uca `PLANNED`→`PREVIEW_READY` gerçek PostgreSQL/MinIO/FFmpeg üzerinde; voiceover miksajı + ducking PCM hash'iyle kanıtlandı; döngü tam 2 render'da duruyor; QC claim'i 199 ms → 3,6 ms (durağan durumda 0,05 ms), plan gerçekten değişti · **Codex turu TEMİZ döndü** (kaçak geçiş, sayaç taşması, tenant, idempotency, ses-QC, olay kaybı — bulgu yok) | dal silinebilir | Opus 5 / high | kullanıldı |
| düzeltme turu 4 | **QC birleştirmesi + ondalık kesirler** — `merge_check_results` (failed>unknown>passed, tam sıralama → byte-özdeş rapor), `_FRACTION_CONNECTIVE` (`tam`/`onda`/`binde`/`yuzde`, tutar başlatamaz) | **kapandı** · merge · 2.000 küme × 4 karıştırma kommutatif; `bir tam onda bes lira` 0/81, `iki tam yuzde yirmi bes lira` 0/243 (Codex'te 45/81 ve 75/243 kaçıyordu) | `fix/verification-followups-4` (silinebilir) | Opus 5 / high | — |
| [W20](handoffs/W20-entitlement-ledger.md) | **Phase 2E (ikinci yarı) — kredi defteri ve hak tüketimi**: append-only ledger, rezervasyon→sonuçlandırma/iade, sürümlü puan tablosu (§12.4), proje başlatmada kontrol · **ödeme/mağaza YOK** (K1, Phase 3) | **kapandı** · merge · `0017` · yeni modül `modules/entitlement/**` · bakiye `SUM(delta_credits)`, hiçbir yerde saklanmıyor (şema taramasıyla testli) · append-only ve negatif bakiye **trigger'la** zorlanıyor · yarış gerçek paralel transaction'la kanıtlandı (son kredi: 2→1, 3 kredilik bakiye: 10→3) · K4 uçtan uca kanıtlı (2 render, 1 `consume`) · **ADR-017 yazıldı, numara PM teyidi bekliyor** · merge edilmedi | `slice/2e-entitlement` (worktree duruyor, Codex turu bekliyor) | Opus 5 / high | kullanıldı |
| [W21](handoffs/W21-approval-revision.md) | **Phase 2F — onay sistemi ve revizyon** (§21) | **tamamlandı, dalda** (`3ddbee9`) · `0018` · yeni `approval.py` + `approval_service.py` + `content_approvals`/`content_revisions` · uçtan uca ret → revizyon → **ikinci render** → onay gerçek PostgreSQL/MinIO/FFmpeg üzerinde · politika 896 kombinasyonla, revizyon sınıflandırması **1024 alt kümenin tamamıyla** tüketildi · not sızıntısı sentinel + AST + tokenize ile kapalı · iptal/iade defterde `consume`+`refund` toplam 0 · **`approver` rolü W10'dan beri boş duran yetki kümesini aldı** (`Permission.CONTENT_APPROVE`, ilan dışı dokunuş — gerekçe raporda) · merge edilmedi | `slice/2f-approval-revision` | Opus 5 / high | kullanıldı |
| [W22](handoffs/W22-content-planner.md) | **Phase 2G — içerik planlayıcı** (§13, Phase 2'nin son dilimi) | **tamamlandı, dalda** (`3e62cc3`) · `0019` · yeni modül `modules/planner/**` (üç servis: planlama **para harcayamaz**, dönüşüm `create_project` üzerinden rezerve eder, zamanlama `APPROVED → SCHEDULED`) · **`approved` terminal olmaktan çıktı** (2F'nin `preview_ready`'ye yaptığının aynısı) · idempotency doğal anahtar + tenant advisory lock, **gerçek paralel transaction'la** kanıtlı · yetersiz bakiye → `blocked`, proje/rezervasyon/defter satırı **yok**, uçtan okunuyor · §13.2 on önceliğin **her ardışık çifti** dominance testinde, 4 ve 10 "alan var kural yok" olarak testli · `Europe/Berlin` DST + yerel gece yarısı testte · uçtan uca gerçek PostgreSQL/MinIO/FFmpeg · **1459 test** · **9 yeni uç** (50 → 61 endpoint) · merge edilmedi | `slice/2g-content-planner` | Opus 5 / high | kullanıldı |
| [W23](handoffs/W23-ledger-integrity.md) | **Defter bütünlüğü** — doğrulama turunun W20'de bulduğu üç açık: negatif bakiye trigger'ı eşzamanlı ham yazarla aşılıyor, aynı rezervasyona ikinci iade yazılabiliyor (para yaratılıyor), `reserve` kaynak başına tekilleştirmiyor. Koruma **şemaya** taşındı | **tamamlandı, dalda** · `0020` · muhafız trigger'ı tahsilat başına (a) `lock_tenant`'ın **aynı** advisory lock'unu alıyor, (b) yeni `entitlement_ledger_anchors` satırını damgalıyor, (c) toplamı kontrol ediyor · **kilit tek başına yetmedi ve bunu kendi saldırım buldu:** beklemek snapshot'ı ilerletmiyor, `REPEATABLE READ` bir yazar kuyrukta bekleyip kazananı içermeyen toplamı okuyordu — anchor bu yüzden var, ve bakiye **tutmuyor** (ADR-017 duruyor) · rezervasyon başına her tipten bir satır + iade tam olarak tutulanı geri verir + iş birimi başına ayakta bir hak + defter satırı komşunun rezervasyonunu gösteremez · `409 ENTITLEMENT_SOURCE_ALREADY_RESERVED` · süpürücü kilit sırasına uyduruldu (10 turluk süpürücü↔sonuçlandırma yarışında **kilitlenme yok**) · 16 saldırılık kendi-düzeltmene-saldır tablosu (`COPY`, savepoint, toplu yazım, RR/SERIALIZABLE) · **ölçüm:** rezervasyon başına +0,5–0,8 ms (%8–27), tek tenant 50 paralel parti `207 → 255 ms` · **1474 test** (taban 1459, +15; hiçbiri düzenlenmedi) · OpenAPI byte özdeş · **ADR-XXX yazıldı, numara PM'de** · **bilinen sınır:** superuser `session_replication_role = replica` ile trigger'ı kapatabiliyor (kısıtlar etkilenmiyor) → uygulama rolü üretimde superuser olmamalı · merge edilmedi | `fix/ledger-integrity` | Opus 5 / high | kullanıldı |

### Dosya sahipliği (çakışma önleme)

Paralel çalışan WO'lar aşağıdaki dosyalara **yalnızca sahibi** dokunur. Sahibi olmadığın bir dosyaya dokunman gerekiyorsa dur ve raporuna yaz. (Tablo yalnızca **uçuştaki** işleri listeler; kapanan işlerin sahiplikleri WO dosyalarında kayıtlı.)

| Dosya | Sahibi |
|---|---|
| `modules/content/approval*.py`, `migrations/0018_*` | **W21** (dalda tamam; ayrıca `modules/businesses/policy.py` + iki `CLAUDE.md`'ye ilan dışı dokunuş — gerekçe W21 raporunda: kabul kriteri 8 `Permission` enum'una satır eklemeden karşılanamıyor) |
| `docs/STATUS.md` | PM (WO'lar yalnızca kendi durum satırını günceller) |

## Sprint 0 kaydı (2026-07-30, PM)

Depo hijyeni yapıldı: `main` 16 commit geride ve Phase 1'in tamamı merge edilmemiş durumdaydı; 5 worktree / 9 dal vardı ve isimler içerikle uyuşmuyordu.

- `main` → `7d78c6e` fast-forward (tüm commit'ler ata, lineer).
- Terk edilmiş çift iş **`c43ccad`** ("celery outbox and beat scheduling") silindi. Gerekçe: `ce96771` aynı base'den (`258439d`) çıkıp aynı 16 dosyayı kapsıyor ve süperset — özdeş `beat_schedule` anahtarları, `test_video_understanding_flow.py` birebir aynı, testlerde +129/+158 fazla kapsam. Kurtarma gerekirse SHA: `c43ccadd67783d2b781203e51b8abbc2be3c2abc` (reflog ~90 gün).
- Atıl 3 worktree ve 7 orphan dal kaldırıldı. Kalan: `main` (ana dizin) + geçici PM worktree'si.
