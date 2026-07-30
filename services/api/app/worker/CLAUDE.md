# worker — Celery süreç composition'ı

**Sahibi:** her Celery worker sürecinin kendi composition root'u ve PostgreSQL'deki
dayanıklı işleri boşaltan (drain) uyandırma task'ları.
**Sahibi değil:** iş kuralı ve durum geçişi (→ `../modules/operations/`, `../modules/media/`),
Celery uygulama nesnesi (→ `../infrastructure/celery_app.py`), HTTP katmanı.

## Değişmezler

- **Mesaj payload'ındaki ID'ye güvenilmez.** Task'lar payload'dan iş almaz; veritabanındaki uygun işi kendileri seçip boşaltır. Bu, tekrarlanan mesajı zararsız kılar.
- Composition **süreç başınadır.** `build_worker_context` worker süreci başlarken kurulur, `worker_process_init`/`worker_process_shutdown` sinyallerine bağlıdır; global paylaşılan bağlantı taşınmaz.
- Her task bir queue'ya ve timeout'a bağlıdır; süresi geçmiş işler `operations.recovery.drain` ile geri alınır.
- Task adları (`media.*.drain`, `operations.*`) kontrattır; yeniden adlandırmak kuyruktaki mesajları düşürür.
- **Tek sunucu dayanıklılığı (ADR-XXX).** Drain, scratch bütçe üstündeyken yeni iş almaz (`WorkerScratchGuard.ensure_within_budget` → `WORKER_SCRATCH_BUDGET_EXCEEDED`); süreç init'te orphan scratch temizlenir ve süreç kendini renice eder (`os.nice(+10)`) → FFmpeg alt süreçleri düşük CPU önceliği miras alır. Bütçe tmpfs boyutundan türetilir; ENOSPC sert duvarı `compose.yaml` tmpfs tavanıdır.

## Dosyalar

| Dosya | İş |
|---|---|
| `composition.py` | `WorkerContext`, `build_worker_context`, `get_worker_context`, `start_worker_process` — süreç sahipli composition root; init'te renice + scratch reclaim |
| `tasks.py` | Drain task'ları: `media.ingest`, `media.technical_analysis`, `media.scene_speech_analysis`, `media.video_understanding`, `operations.recovery`, `operations.outbox.dispatch` + süreç init/shutdown sinyalleri; her drain scratch bütçesini kontrol eder |
| `scratch.py` | `WorkerScratchGuard`, `WorkerScratchExhausted` — tek sunucuda scratch bütçe/orphan temizliği (ADR-XXX) |
| `__init__.py` | Paket |

## Gereksinim, karar, mimari

- [85-orchestration-events.md](../../../../docs/product/requirements/85-orchestration-events.md) (PRD §26, §27) · [95-observability.md](../../../../docs/product/requirements/95-observability.md) (§38.2 queue'lar, §38.3 backpressure) · [40b-scenario-render-lifecycle.md](../../../../docs/product/requirements/40b-scenario-render-lifecycle.md) (§19.3 worker izolasyonu)
- [ADR-005](../../../../docs/adr/ADR-005-transactional-outbox.md) · [ADR-003](../../../../docs/adr/ADR-003-n8n-orchestration-boundary.md)
- Mimari: [background-jobs.md](../../../../docs/architecture/background-jobs.md)

## Testler

`tests/unit/test_worker_composition.py` · `tests/unit/test_worker_scratch.py` · `tests/integration/test_celery_orchestration.py`
