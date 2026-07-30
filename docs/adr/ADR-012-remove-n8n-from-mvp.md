# ADR-012: n8n'i MVP Kapsamından Çıkarma

**Status:** Accepted — ADR-003'ün yerini alır (supersedes [ADR-003](ADR-003-n8n-orchestration-boundary.md))
**Date:** 2026-07-30
**Karar veren:** PM/mimar oturumu (kullanıcı kararı K5 ve genel yetkilendirme doğrultusunda)

## Context

[ADR-003](ADR-003-n8n-orchestration-boundary.md) n8n'i bir orkestrasyon sınırında tutmayı kabul etti ve n8n'in **yapamayacağı** işleri saydı: domain state sahipliği, yetkilendirme, entitlement hesabı, reklam bütçesi kararı, kritik transaction yönetimi, OAuth token'ını düz metin tutma, büyük medya taşıma, model prompt'larının tek kaynağı olma.

Bu sınır doğruydu ama bir sonucu vardı: yasaklar uygulandıktan sonra n8n'e kalan iş **zamanlama ve bildirim koordinasyonundan** ibaret. Üç gelişme kararı olgunlaştırdı:

1. **Zamanlama zaten backend'de çalışıyor.** Celery Beat, outbox dispatch, medya kuyruk drenajı ve bayat iş kurtarma için üretimde. PRD §26.3'ün workflow kataloğundaki tetikleyicilerin tamamı aynı mekanizmayla ifade edilebilir.
2. **Lisans riski.** n8n Sustainable Use License, ürünü ticari bir platformun motoru olarak kullanmayı ve harici kullanıcıların workflow tetiklemesini kısıtlıyor; bu senaryo için ayrı bir Embed anlaşması isteniyor. Bizim kullanımımız — ücretli bir SaaS'ın içerik üretim ve yayın hattında zamanlayıcı olmak — tam olarak o gri alana düşüyor.
3. **Tek sunucu kararı (K5).** Kullanıcı tek, ucuz, dedike bir sunucuda çalışacak. n8n her zaman açık bir süreç: RAM tüketir, kendi credential şifreleme anahtarını yönetmeyi gerektirir, editor erişimi için ayrı bir kimlik doğrulama yüzeyi açar.

## Decision

**n8n MVP kapsamından çıkarılır.** Zamanlama, bildirim ve iç koordinasyon backend'in kendi işidir: Celery Beat + kuyruklar + transactional outbox + bildirim adapter'ları.

`workflows/n8n/` dizini oluşturulmaz. PRD §26.3'ün workflow kataloğu (ACC-01…OPS-06) **iptal edilmez, yeri değişir**: her kalem bir zamanlanmış görev, bir outbox tüketicisi veya bir bildirim işi olarak backend'de karşılığını bulur. Katalog bu eşlemeyle korunur.

n8n yeniden değerlendirilir **ancak** şu koşul oluşursa: backend'in sahiplenmemesi gereken, gerçek anlamda harici sistem koreografisi (müşterinin kendi CRM'i, üçüncü taraf otomasyonları, müşteriye özel entegrasyon akışları) ürün gereksinimi hâline gelirse. O noktada karar, Embed lisansı maliyeti ve barındırma yükü hesaplanarak yeni bir ADR ile alınır.

## Consequences

- **Bir bileşen, bir lisans riski ve bir güvenlik yüzeyi eksilir.** Tek sunucuda ölçülebilir RAM ve operasyon kazancı.
- **PRD §26 ve [85-orchestration-events.md](../product/requirements/85-orchestration-events.md) artık uygulanmayan bir taşıyıcıyı anlatıyor.** Event-driven tasarım (§27: outbox, idempotency, event zarfı) **aynen geçerli**; değişen tek şey tetikleyicinin n8n değil Celery Beat olması. İlgili gereksinim dosyasına bu ADR'a işaret eden bir not düşülür.
- Zamanlanmış işlerin tamamı artık `make verify` kapılarının, tip denetiminin ve testlerin içinde. n8n'de olsalardı test edilemez ve sürümlenemez olurlardı — ADR-003'ün zaten kaygılandığı şey.
- Bildirim kanalları (push, e-posta, in-app inbox) adapter olarak backend'de yazılacak; n8n'in hazır node'ları kaybedilir. Bu, kabul edilen maliyettir: PRD §31'deki kanal seti sınırlı ve zaten adapter arkasında olması gerekiyordu.
- **Geri dönüş kolaydır.** Outbox event zarfı (§26.4) taşıyıcıdan bağımsız tanımlı; n8n ileride eklenirse bir outbox tüketicisi olarak bağlanır, domain'e girmeden.

## Rejected alternatives

- **n8n'i sınırda tutmaya devam etmek (ADR-003'ün aynen sürmesi):** reddedildi. Yasaklar uygulandıktan sonra kalan değer, taşıdığı lisans + barındırma + güvenlik maliyetini karşılamıyor.
- **n8n Embed lisansı almak:** reddedildi. MVP'de çözdüğü sorun Celery Beat ile zaten çözülmüş durumda; ücretli bir lisansı gerekçelendirecek bir yetenek farkı yok.
- **Zamanlamayı harici bir cron servisine vermek:** reddedildi. Tetikleyicinin domain state'ine ve tenant bağlamına erişmesi gerekiyor; backend içindeki Beat bunu tip güvenli ve test edilebilir şekilde yapıyor.
- **n8n'i yalnızca operasyon bildirimleri için tutmak:** reddedildi. Tek bir kullanım için her zaman açık bir bileşen ve bir credential store taşımak, tek sunucu kısıtıyla çelişiyor.
