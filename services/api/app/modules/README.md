# Domain Modules

Her modülün sınırı, değişmezleri, dosya listesi ve testleri kendi `CLAUDE.md`'sinde
yazılıdır. Bu dosya yalnızca yönlendirir; modül bilgisi burada tutulmaz.

| Modül | Sorumluluk | Harita |
|---|---|---|
| `identity/` | Global kullanıcı kimliği ve dış kimlik eşlemesi (tenant-farkında değil) | [CLAUDE.md](identity/CLAUDE.md) |
| `businesses/` | Tenant kaydı, üyelik, rol→yetki politikası | [CLAUDE.md](businesses/CLAUDE.md) |
| `media/` | Yükleme control-plane'i, ingest geçidi, teknik/sahne/video analizi | [CLAUDE.md](media/CLAUDE.md) |
| `operations/` | Job, deneme, outbox, idempotency, audit | [CLAUDE.md](operations/CLAUDE.md) |

Komşu katmanlar: [`../core/`](../core/CLAUDE.md) ·
[`../infrastructure/`](../infrastructure/CLAUDE.md) · [`../worker/`](../worker/CLAUDE.md).
Modül sınırı kuralları: [backend-modules.md](../../../../docs/architecture/backend-modules.md).
