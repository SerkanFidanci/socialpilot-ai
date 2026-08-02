# W22 — Phase 2G: İçerik planlayıcı (§13)

**Dal:** `slice/2g-content-planner` · **Base:** `main` · **Migration slotu: SENDE** (`0019`)
**Durum:** hazır, tetiklenmedi
**Model/effort:** Opus 5 / high
**Plan:** [Phase 2 planı](../plans/active/phase-2-content-generation.md) — slice 2G (**Phase 2'nin son dilimi**)
**Neden bu iş:** 2A–2F bir içeriği baştan sona üretip onaylatabiliyor — ama **her zaman biri elle tetiklediği için.** Ürünün vaadi bu değil: "işletme uyurken içerik hazır olsun." §13 bunu `content_obligation` üzerinden tanımlıyor: abonelikten talep türer, talep projeye dönüşür. Bu dilim kapanınca Phase 2 biter.

## Okunacaklar

1. [`docs/STATUS.md`](../STATUS.md)
2. [`docs/product/requirements/40a-content-planning-scenarios.md`](../product/requirements/40a-content-planning-scenarios.md) — **§13.1 obligation şekli, §13.2 on öncelik, §13.3 içerik karması**
3. [`docs/product/requirements/90a-database-design.md`](../product/requirements/90a-database-design.md) — `content_obligations` satırı ve index'i
4. `services/api/app/modules/content/CLAUDE.md` — W19'un durum makinesi, W21'in onay kenarları
5. `services/api/app/modules/entitlement/CLAUDE.md` — planlayıcı **kredi harcatacak**, rezervasyon disiplinini oradan al

## PM kararları

### 1. `SCHEDULED` kenarı `APPROVED`'dan çıkar (W21'in yükselttiği karar — kabul edildi)

W21 §20'ye `APPROVED` ve `CANCELLED` durumlarını ekledi ve haklı olarak sordu. Karar: **`APPROVED → SCHEDULED`**, `WAITING_APPROVAL → SCHEDULED` değil. Onaylanmamış içerik takvime giremez; `never_within_guardrails` politikasında zaten aktörsüz `auto_approved` kaydıyla `APPROVED`'a düşüyor, yani yol her politikada tanımlı.

**Bu slice `SCHEDULED`'ı ekler ve orada durur.** `PUBLISHING`/`PUBLISHED` → Phase 4.

### 2. Planlayıcı **talep üretir, içerik üretmez**

`content_obligations` bir **sıraya alma** kaydıdır: hangi işletme, hangi içerik tipi, hangi pencerede, ne zaman yayınlanmalı, üretimin son teslim anı ne. Obligation'dan projeye geçiş **ayrı ve idempotent** bir adımdır. Gerekçe: planlama ile üretim aynı işlem olursa, planlayıcının bir hatası doğrudan para harcar; ayrı olunca plan gözden geçirilebilir ve iptal edilebilir.

**Obligation → proje dönüşümünde kredi rezerve edilir** (W20'nin yolu). Yetersiz bakiye → obligation `blocked` olur, **sessizce kaybolmaz** ve kullanıcıya görünür.

### 3. Öncelikler **saf ve sıralı**, ağırlıklı skor değil

§13.2'nin on maddesi bir **sıra** — ağırlıklı bir puan değil. Deterministik ve açıklanabilir bir sıralayıcı yaz: girdi = aday obligation kümesi + bağlam (aktif kampanya, marka dengesi, medya yeterliliği, geçmiş tekrar), çıktı = sıralanmış liste **ve her adayın hangi kuralla o sıraya girdiği** (açıklama alanı — kullanıcı "neden bu içerik?" diye sorabilmeli).

**Bu slice'ta 1–3, 6–9 uygulanır** (kampanya, abonelik, marka dengesi, ürün tekrarı, platform uygunluğu, sessiz saatler, kullanıcı tercihleri). **4 (geçmiş performans) ve 10 (özel günler) YOK:** performans verisi Phase 5'te doğuyor, özel gün takvimi doğrulanmış dış kaynak istiyor (§13.2'nin kendi şartı) ve o kaynak seçilmedi. İkisi için alan açılır, doldurulmaz — raporda bildir.

### 4. Sessiz saatler ve zaman disiplini

`planned_publish_at` **işletmenin saat diliminde** anlamlı, depoda **UTC** (AGENTS.md kuralı). Sessiz saat penceresi işletme ayarı; pencereye düşen bir zaman **pencere dışına kaydırılır**, iptal edilmez. **DST geçişi ve yerel gece yarısı testte olmalı** — Türkiye'de DST yok ama kod tenant timezone'una göre çalışmalı ve bunu varsayım olarak gömmemeli.

### 5. İçerik karması **hedef**, kota değil (§13.3)

Yüzdeler bir **dağılım hedefi**; tek bir haftada tutturulamayabilir. Planlayıcı, karma hedefinden en çok sapan kategoriyi öne alır (§13.2'nin 3. maddesi "marka içerik dengesi"). **Sert kota koyma** — "bu hafta eğitici içerik kotası doldu, kampanya üretme" davranışı kampanyası olan bir işletmeyi cezalandırır. Sapma **ölçülür ve raporlanır**, yargılanmaz (2C→2D deseninin aynısı).

### 6. Planlayıcı beat'te çalışır ve **idempotenttir**

Aynı pencere için ikinci koşu ikinci obligation üretmez (doğal anahtar: business + subscription_item + period). Beat aralığı config'de. W19/W20/W21'in job disiplini geçerli: durum, timeout, deneme, correlation, dead-letter, süpürücü.

## Kapsam dışı (dokunma)

- **Yayınlama** (`PUBLISHING` ve sonrası, platform API'leri) → Phase 4.
- **Abonelik kalemlerinin kendisi** (`subscription_items`, plan tanımı) → Phase 3. Bu slice obligation'ı **var olan bir kaynaktan** türetir; kaynak yoksa **manuel/seed** bir abonelik kalemi tanımı yeterli (W20'nin grant deseni gibi).
- Geçmiş performans (§13.2/4) ve özel günler (§13.2/10) → alan aç, doldurma.
- `script.py`, `qc.py`, `text_normalization.py`, W21'in onay mantığı → dokunma.
- `docs/index.md`, `docs/adr/README.md` → indekse ekleme, raporda bildir.

## Dokunulacak dosyalar (ilan)

```
services/api/app/modules/planner/**                       (yeni modül: obligation, öncelik sıralayıcı, karma ölçümü, sessiz saat, CLAUDE.md)
services/api/app/modules/content/{lifecycle,project_service}.py   (SCHEDULED kenarı + obligation→proje)
services/api/app/modules/content/models.py                (proje ↔ obligation bağı)
services/api/app/api/routes/planner.py + routes/__init__.py
services/api/app/core/config.py                           (PLANNER_* ayarları)
services/api/app/worker/{tasks,composition}.py + infrastructure/celery_app.py
services/api/migrations/versions/0019_*.py                (SLOT SENDE)
services/api/tests/unit/ + tests/integration/
docs/architecture/ (planlayıcı bölümü — hangi dosyaya yazdığını bildir) · error-handling.md · .env.example
```

## Kabul kriterleri

1. Migration `0019` up → down → up; tek head.
2. **Uçtan uca:** seed'lenmiş bir abonelik kaleminden obligation üretiliyor → obligation projeye dönüşüyor (kredi rezerve edilerek) → proje `APPROVED` olunca `SCHEDULED`'a geçiyor; her adım kayıtlı, gerçek PostgreSQL.
3. **İdempotency:** aynı pencere için ikinci planlayıcı koşusu ikinci obligation **üretmiyor**; eşzamanlı iki koşu da üretmiyor (gerçek paralel transaction).
4. **Sıralayıcı saf ve açıklanabilir:** aynı girdi → aynı sıra; her adayın sıra gerekçesi çıktıda var. Uygulanan yedi öncelik permütasyon/tablo testiyle kapsanıyor; uygulanmayan ikisi **açıkça** "alan var, kural yok" olarak testli.
5. **Sessiz saat:** pencereye düşen zaman dışarı kaydırılıyor, iptal edilmiyor; tenant timezone'u UTC'den türetiliyor; yerel gece yarısı ve DST'li bir timezone testte (ör. `Europe/Berlin`) — kod TR'ye gömülü değil.
6. **Yetersiz bakiye:** obligation `blocked`, proje oluşmuyor, kredi harcanmıyor, durum kullanıcıya görünür uçtan okunuyor.
7. **Karma ölçülüyor, yargılanmıyor:** sapma raporlanıyor; sert kota yok (testle: kampanyası olan işletme karma yüzünden engellenmiyor).
8. Tenant izolasyonu her sorguda; roller (planlayıcı ayarları `business.update`, okuma `business.read`); idempotency kanonik gövdeden; imzalı URL sızmıyor.
9. `make verify` yeşil; test sayısı **1375** tabanının altına düşmez; kontrat yeniden üretilip commit'li; modül `CLAUDE.md` yazıldı.
10. Rapor + araç zinciri sürümleri. **Merge etme, dalda bırak.**

## Enumerasyon kuralı

Öncelik listesi ve içerik kategorileri PRD tarafından **kapalı** — yazılabilir. Ama **"hangi bağlamda hangi aday öne geçer"** kombinatoryaldir: total fonksiyon + permütasyon testi (2D/2E/2F deseni).

## ADR numara kuralı

Gerçek karar çıkarsa `ADR-XXX-<konu>.md`; numarayı PM verir. ("Planlayıcı talep üretir, içerik üretmez" ayrımı ADR'lık.)

## Rapor

_(yürüten oturum doldurur — şablon: [README.md](README.md))_

## Doğrulama

_(test eden oturum: **kendi girdilerini üret; mevcut testleri koşmak doğrulama değildir.** Özellikle: aynı pencerede çift obligation ürettir, sessiz saate yayın kaydırt/kaydırtma, yetersiz bakiyeyle proje açtır, onaylanmamış projeyi `SCHEDULED` yaptır, başka tenant'ın obligation'ını planlat, planlayıcıyı sonsuz obligation üretmeye zorla, timezone sınırında (yerel gece yarısı, DST) yanlış pencere ürettir)_
