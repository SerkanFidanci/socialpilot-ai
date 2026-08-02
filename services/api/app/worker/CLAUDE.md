# worker — Celery süreç composition'ı

**Sahibi:** her Celery worker sürecinin kendi composition root'u ve PostgreSQL'deki
dayanıklı işleri boşaltan (drain) uyandırma task'ları.
**Sahibi değil:** iş kuralı ve durum geçişi (→ `../modules/operations/`, `../modules/media/`),
Celery uygulama nesnesi (→ `../infrastructure/celery_app.py`), HTTP katmanı.

## Değişmezler

- **Mesaj payload'ındaki ID'ye güvenilmez.** Task'lar payload'dan iş almaz; veritabanındaki uygun işi kendileri seçip boşaltır. Bu, tekrarlanan mesajı zararsız kılar.
- **`content.qc.drain` arkasında outbox olayı olmayan tek drain'dir** (W18 takip 1). Diğerleri iki kez uyandırılır: üreticisinin yazdığı olayla ve broker'ın kaybettiğini süpüren beat tick'iyle. QC'nin üreticisi yok — render yolunda `content.qc.requested` yazan bir yer yok — bu yüzden claim "raporu olmayan `succeeded` render" taraması yapıyor ve tick tetiklemenin tamamı. `test_automatic_qc_is_the_one_drain_with_no_event_behind_it` bu iddiayı sabitliyor: bir gün olay eklenirse, taramanın kalıp kalmayacağını soran şey o test olur.
- Composition **süreç başınadır.** `build_worker_context` worker süreci başlarken kurulur, `worker_process_init`/`worker_process_shutdown` sinyallerine bağlıdır; global paylaşılan bağlantı taşınmaz.
- Her task bir queue'ya ve timeout'a bağlıdır; süresi geçmiş işler `operations.recovery.drain` ile geri alınır.
- Task adları (`media.*.drain`, `operations.*`) kontrattır; yeniden adlandırmak kuyruktaki mesajları düşürür.
- **Tek sunucu dayanıklılığı (ADR-013).** Drain, scratch bütçe üstündeyken yeni iş almaz (`WorkerScratchGuard.ensure_within_budget` → `WORKER_SCRATCH_BUDGET_EXCEEDED`); süreç init'te orphan scratch temizlenir ve süreç kendini renice eder (`os.nice(+10)`) → FFmpeg alt süreçleri düşük CPU önceliği miras alır. Bütçe tmpfs boyutundan türetilir; ENOSPC sert duvarı `compose.yaml` tmpfs tavanıdır.

## Dosyalar

| Dosya | İş |
|---|---|
| `composition.py` | `WorkerContext`, `build_worker_context`, `get_worker_context`, `start_worker_process` — süreç sahipli composition root; init'te renice + scratch reclaim. `qc_probe` her ortamda **gerçek** adapter (ölçümün fake'i yok), `visual_qc` üretimde **disabled** — iki zıt kuralın birlikte tutması gereken yer burası. `content_qc_service` render portu **almaz**: yeniden render 2E'nin |
| `tasks.py` | Drain task'ları: `media.ingest`, `media.technical_analysis`, `media.scene_speech_analysis`, `media.video_understanding`, `content.render`, `content.qc`, `operations.recovery`, `operations.outbox.dispatch` + süreç init/shutdown sinyalleri; her drain scratch bütçesini kontrol eder |
| `scratch.py` | `WorkerScratchGuard`, `WorkerScratchExhausted` — tek sunucuda scratch bütçe/orphan temizliği (ADR-013) |
| `__init__.py` | Paket |

## Gereksinim, karar, mimari

- [85-orchestration-events.md](../../../../docs/product/requirements/85-orchestration-events.md) (PRD §26, §27) · [95-observability.md](../../../../docs/product/requirements/95-observability.md) (§38.2 queue'lar, §38.3 backpressure) · [40b-scenario-render-lifecycle.md](../../../../docs/product/requirements/40b-scenario-render-lifecycle.md) (§19.3 worker izolasyonu)
- [ADR-005](../../../../docs/adr/ADR-005-transactional-outbox.md) · [ADR-003](../../../../docs/adr/ADR-003-n8n-orchestration-boundary.md)
- Mimari: [background-jobs.md](../../../../docs/architecture/background-jobs.md)

## Testler

`tests/unit/test_worker_composition.py` · `tests/unit/test_worker_scratch.py` ·
`tests/unit/test_celery_publisher.py` · `tests/integration/test_celery_orchestration.py` ·
`tests/integration/test_content_qc.py` (beat girdisi → task kaydı → gerçek rapor zinciri)
