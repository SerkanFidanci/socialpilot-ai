# planner — içerik planlayıcı: talep, sıra, takvim (PRD §13)

**Sahibi:** `content_obligation` (§13.1) ve onun kapalı durum makinesi, §13.2'nin on önceliğinin
saf ve sıralı uygulaması, §13.3 içerik karmasının **ölçümü**, sessiz saatler ve tenant saat
dilimi aritmetiği, ayakta duran talebin (Phase 3 abonelik kaleminin yerini tutan
`planner_subscription_items`) tanımı, obligation → proje dönüşümü ve `APPROVED → SCHEDULED` kenarı.
**Sahibi değil:** içerik üretiminin kendisi (→ `../content/`), kredi defteri (→ `../entitlement/`),
abonelik/plan/fatura (→ **Phase 3**, K1 beklemede), yayınlama (→ **Phase 4**), HTTP taşıma
(→ `../../api/routes/planner.py`).

## Değişmezler

- **Planlayıcı talep üretir, içerik üretmez** (PM kararı, W22). Üç ayrı iş: planlama obligation
  yazar, dönüşüm proje açar, zamanlama slot verir. `ObligationPlanningService` yapıcısında ne
  `EntitlementService` ne `ContentProjectService` vardır — planlama **para harcayamaz**, çünkü
  harcayacak bir şeyi yoktur. Ayrım şu yüzden: planlama ile üretim aynı işlem olsaydı,
  planlayıcının bir hatası doğrudan kredi yakardı; ayrı olunca hata silinebilir bir satırdır.
- **Bağımlılık tek yönlüdür: `planner` → `content`.** `content` bu modülü bilmez ve bilmemeli.
  Bu yüzden proje referansı **obligation'ın üzerindedir** (`content_projects`'te `obligation_id`
  yoktur): proje obligation'sız var olabilir — 2A–2F'nin ürettiği her proje öyle — obligation ise
  en fazla bir proje taşır. Aynı disiplin `entitlement`'ın `ReservationSourceProbe`'unda da var.
- **`APPROVED → SCHEDULED` kenarı planlayıcınındır.** Slot bir §13 sorusudur (işletme saat dilimi,
  sessiz saat penceresi, ayakta duran talep) ve cevabını `content`'in içine koymak o modülü buna
  bağımlı yapardı. Sıralayıcı `APPROVED`'ı **claim eder ve hiçbir şey yapmaz**; `waits_for_handoff`
  o durumu adım timeout'undan muaf tutar, çünkü planlayıcıyı beklemek durmuş bir iş değildir.
- **Onaylanmamış içerik takvime giremez.** Kenar yalnızca `APPROVED`'dan çizilidir;
  `WAITING_APPROVAL → SCHEDULED` yoktur. `never_within_guardrails` politikası zaten aktörsüz bir
  `auto_approved` kaydıyla `APPROVED`'a düşüyor, yani her §21.1 politikasının yolu tanımlı.
- **İdempotency iki kilitlidir.** Doğal anahtar `(subscription_item_id, period_start)` **unique**;
  ve okuma-sonra-yazma dizisini tenant advisory lock'u (`ADVISORY_LOCK_NAMESPACE = 2_0022`)
  seri hâle getirir. Lock **var olan period_start'lar okunmadan önce** alınır — sonra alınırsa
  hiçbir şeyi kilitlemez. İki mekanizmadan hiçbiri planlayıcının kontrol etmeyi hatırlamasına
  bağlı değil. Namespace `entitlement`'ınkinden ayrıdır: dönüşüm önce bunu, sonra `create_project`
  içinde onunkini alır — hep bu sırayla, asla tersi, yani döngü kurulamaz.
- **Dönüşüm anahtarı obligation'dan türetilir** (`obligation:{id}:generation`). "Proje
  oluşturuldu" ile "obligation güncellendi" arasında ölen bir süreç, ikinci bir proje satın almak
  yerine aynısını yeniden oynatır. Rastgele bir anahtar burada ikinci bir tahsilat demektir.
- **Yetersiz bakiye hiçbir iz bırakmaz ama görünürdür.** `create_project` rezervasyonu projeyi
  yaratan transaction'da açar (W20), bu yüzden `402` proje satırını da götürür. Geriye kalan,
  neden iş olamadığını **uçtan okunabilir** biçimde söyleyen bir `blocked` obligation'dır.
  `blocked` bir ölüm değil bir durumdur: bakiye yüklenince aynı pencere aynı gün dönüşür
  (`blocked → planned → blocked` kenarları bunun için var, self-loop yerine).
- **Öncelikler sıradır, ağırlıklı skor değildir** (§13.2). `rank_obligations` on bileşenli
  **leksikografik** bir anahtar üretir ve bileşenlerin her biri **küçük ayrık bir kova**dır.
  Sürekli bir ölçü (dakikasına kadar bir deadline) neredeyse tekil olurdu ve 3–9 arası öncelikler
  hiç ulaşılamayan koda dönerdi; kovalar beraberlikleri hayatta tutar. Son eşitlik bozucu
  `(planned_publish_at, obligation_id)` — "aynı girdi → aynı sıra" sıralamanın özelliğidir,
  girdinin tesadüfi düzeninin değil.
- **4 ve 10 numaralı önceliklerin alanı var, kuralı yok** (`UNIMPLEMENTED_PRIORITIES`). Geçmiş
  performans Phase 5'te doğuyor; özel günler §13.2/10'un kendi şartı olan doğrulanmış takvim
  kaynağını istiyor ve o kaynak seçilmedi. `RankContext.performance_score` ve `special_day_code`
  taşınır ve **hiçbir şey tarafından okunmaz**; ikisi de sabit katkı verir, yani §13.2'deki
  yerleri açık tutulur. Birim testi ikisine de değer vererek hiçbir sıranın değişmediğini zorlar.
- **Karma ölçülür, yargılanmaz** (§13.3, PM kararı 5). `measure_mix`'in başarısızlık modu yoktur
  ve sapmayı okuyan tek şey 3. önceliktir — o da yeniden sıralar. Hiçbir yerde "bu kategorinin
  kotası doldu" diye reddeden bir kod yoktur: sert kota, tam olarak §13.2/1'in ilk sıraya
  koyduğu işletmeyi (kampanyası olanı) cezalandırırdı.
- **Saat tenant'ındır, sunucunun değil.** Her sınır **yerel takvim gününden** kurulur ve bir kez
  çevrilir; `start + timedelta(days=1)` yazılmaz. DST günü 23 veya 25 saattir ve bu doğru gün
  uzunluğudur. `planned_publish_at` işletmenin saat diliminde anlamlıdır, depoda UTC.
- **Bilinmeyen saat dilimi UTC'ye düşmez, reddedilir** (`PLANNER_TIMEZONE_UNKNOWN`). Sessizce
  UTC'ye düşmek Türk bir kafenin akşam gönderisini üç saat erken yayınlar ve kimseye söylemez.
- **Sessiz saat kaydırır, iptal etmez** (§13.2/8). Pencereye düşen an pencerenin bittiği ana
  itilir; dışarıda olan an değişmeden döner — bu yüzden kaydırma koşulsuz uygulanabilir.
  İlkbahar geçişinde pencerenin bittiği yerel saat **var olmayabilir**; hatanın tamamı bir
  saattir, bu yüzden tek bir düzeltme yapılır ve ikinci başarısızlık döngü değil
  `PLANNER_QUIET_HOURS_UNRESOLVED` üretir.
- **Kaydırıldı mı bilgisi saklanır** (`quiet_hours_shifted`). "23:00 dedim, neden 08:00?"
  sorusunun cevabı satırdan okunmalı — o zamandan beri düzenlenmiş olabilecek bir pencereye karşı
  yeniden türetilerek değil.
- **Ayakta duran talep bir yer tutucudur.** `planner_subscription_items` §12.2'nin gerçek
  abonelik kalemi değildir (o Phase 3, K1'in arkasında); §13.1'in ihtiyaç duyduğu küçük şeydir ve
  W20'nin manuel grant'i gibi elle kurulur. Obligation'daki sütun adı **§13.1'in adıdır**
  (`subscription_item_id`), böylece Phase 3 bir foreign key'i yeniden hedefler, PRD'nin adını
  verdiği bir alanı yeniden adlandırmaz.
- **`ContentType` `RenderProfile`'ın ikinci yazımı değildir.** Profil bir geometridir; içerik tipi
  bir abonelik yüzeyidir. `surface_for` ikisini bağlar ve **import anında totallik** kontrolünden
  geçer. `preview_540x960`'ın üyesi yoktur: bir inceleme proxy'si kimsenin yayınladığı bir yüzey
  değil, birinin projeye yaptığı bir şeydir.
- **Kategori obligation'a kopyalanır, join edilmez.** Ayakta duran talebi gelecek ay yeniden
  sınıflandırmak, çoktan olmuş haftaların §13.3 dağılımını yeniden yazmamalı.
- **Dönüşüm, talebi kuran kişi olarak yetkilendirilir.** `requested_by_user_id` NOT NULL ve
  `create_project` onun adına `content.generate` kontrolünden geçer: `content.generate`'ini
  kaybeden bir üyenin ayakta duran talebi içerik üretmeyi **durdurur**, ki bu sahipsiz bir arka
  plan işinden doğru olan cevaptır. `audit_logs` da aynı kuralı sürdürür (W20): arka planda
  yazılan satır bile işi hareket eden kişiyi adlandırır.
- **Planlayıcıyı yapılandırmak içerik üretmek değildir.** Yazma `business.update`, okuma
  `business.read` (PRD §4). `editor` üretir ve tüm tenant'ın çalıştığı takvimi **yeniden
  yazamaz**. "planner.generate" diye bir yetki yoktur ve olmayacak: aynı tabloya giden ikinci bir
  yol daha zayıf bir yol olurdu.
- **Her sorgu `business_id` ister** — iki belgelenmiş istisna dışında: `claim_next_plannable_item`
  ve `claim_schedulable_projects`/`claim_settled_obligations`/`claim_expired_obligations`. Bunlar
  arkasında kullanıcı ve işletme olmayan bakım süpürmeleridir; tenant'a kapsamak onları yalnızca
  birinin adını verdiği tenant'ı düzeltebilir hâle getirirdi. Aldıkları her satır, hakkında bir
  şey yazılmadan önce kendi tenant'ı altında yeniden okunur.
- **Kısıt ile claim index'i aynı kümeyle yazılır.** `ck_content_obligation_due_matches_status`
  "yalnızca dönüştürülebilir durumlar due time taşır" der ve `ix_content_obligations_due` aynı
  kümenin üzerine kısmi. `in_progress` terminal **değildir** ve yine de due time taşımaz: proje
  artık dayanıklı iştir, kuyruk kaydını da yoklamak aynı iş üzerinde ikinci bir saat olurdu.

## Bu slice'ın olayı yok

Üç drain de **yalnızca beat tick'iyle** uyanır. `planner.obligations.plan` için bu ilkesel:
"yeni bir dönem başladı" diye olay yazan hiçbir şey yok ve bir tarihin gelişini gözleyebilen tek
şey bir tick'tir — `content.pending.sweep` ve `content.project.sweep` ile aynı gerekçe.
`planner.obligations.dispatch` ve `planner.projects.schedule` için **değil**: ikisinin de
üreticisi olabilirdi (planlanan bir obligation dispatcher'ı, bir onay zamanlayıcıyı uyandırabilir)
ama outbox zarfı (`infrastructure/celery_publisher.py`) bu iş emrinin dosyası değildi. Bunun
bedeli gecikmedir, doğruluk değil; bu yüzden iki tick de kısa (60 s / 120 s) ve
`test_the_planner_drains_run_on_ticks_alone_and_the_first_one_has_to` iddiayı sabitliyor.

## Dosyalar

| Dosya | İş |
|---|---|
| `obligation.py` | §13'ün **saf** yarısı: `ContentType` (+ `surface_for`, import anında total), `ContentCategory`, `PlanPeriod`, `PlanItemStatus`, `ObligationStatus`/`ObligationEvent` + kapalı ve total geçiş tablosu, `QuietHours` + `shift_out_of_quiet_hours` (DST düzeltmesi dahil), `period_bounds`/`period_days`/`build_window` (yerel takvim aritmetiği), `MixTargets` + `measure_mix`, `PlannerPriority` + on kuralın tamamı + `rank_obligations`, `UNIMPLEMENTED_PRIORITIES`, dokümante hata kodları. Session/saat/sağlayıcı yok |
| `models.py` | `PlannerSettings` (tenant başına tek satır: sessiz pencere yerel dakika olarak, §13.3 hedefleri JSONB, ufuk) + `PlannerSubscriptionItem` (ayakta duran talep + `next_plan_at` lease'i) + `ContentObligation` (§13.1 satırı + `project_id`, `reason_code`, `attempts`, `next_attempt_at`; doğal anahtar ve dört kısıt) |
| `repository.py` | `PlannerRepository` — tenant-kapsamlı okuma/yazma, `lock_business` (advisory lock), `planned_period_starts`, `due_obligations`, `category_counts` (§13.3 ölçümü), `recent_product_uses` (§13.2/6), dört claim (`SKIP LOCKED`) |
| `policy.py` | `PlannerAction` → merkezî `Permission` eşlemesi (yazma `business.update`, okuma `business.read`); import anında totallik |
| `service.py` | `PlannerConfigService` (API: ayarlar, kalemler, obligation okuma/iptal, sıralı plan, karma raporu) · `ObligationPlanningService` (bir kalem → pencerelerin obligation'ları, tek transaction) · `ObligationDispatchService` (claim + `create_project` + settle, üç transaction) · `ProjectSchedulingService` (`APPROVED → SCHEDULED`, biten projelerin obligation'larını uzlaştırma, kapanan pencerelerin süresini doldurma) · `PlanningProfile`/`build_profile` · `_RankContextReader` (§13.2'nin baktığı her gerçek, beş sorguda; dispatcher ve plan ucu **aynısını** kullanır) |

## Gereksinim, karar, mimari

- [40a-content-planning-scenarios.md](../../../../../docs/product/requirements/40a-content-planning-scenarios.md)
  (§13.1 obligation, §13.2 on öncelik, §13.3 haftalık karma; §14.1'in "kritik medya eksikse
  görevi beklet" fallback'i) ·
  [40b-scenario-render-lifecycle.md](../../../../../docs/product/requirements/40b-scenario-render-lifecycle.md)
  (§20 durum makinesi, `SCHEDULED`) ·
  [90a-database-design.md](../../../../../docs/product/requirements/90a-database-design.md)
  (§28.5 `content_obligations`, §28.9 `content_obligations(business_id, planned_publish_at, status)`) ·
  [50-subscription-entitlement.md](../../../../../docs/product/requirements/50-subscription-entitlement.md)
  (§12.2 abonelik kalemi — Phase 3, §12.8 tüketim)
- `ADR-017-entitlement-ledger.md` (rezervasyon disiplini; dönüşüm onu çağırır)
- Mimari: [content-planner.md](../../../../../docs/architecture/content-planner.md) ·
  [content-render.md](../../../../../docs/architecture/content-render.md) ·
  [entitlement.md](../../../../../docs/architecture/entitlement.md) ·
  [background-jobs.md](../../../../../docs/architecture/background-jobs.md) ·
  [error-handling.md](../../../../../docs/architecture/error-handling.md) (`PLANNER_*` kataloğu) ·
  [Phase 2 planı](../../../../../docs/plans/active/phase-2-content-generation.md) §3 (slice 2G)

## Testler

`tests/unit/test_content_planner_unit.py` · `tests/integration/test_content_planner.py` ·
`tests/unit/test_content_lifecycle_unit.py` (`SCHEDULED` kenarı ve yeni terminal küme) ·
`tests/unit/test_celery_publisher.py` (üç beat girdisi, üreticisiz olduklarının kaydı)
