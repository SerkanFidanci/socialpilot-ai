# W20–W22 bağımsız doğrulama planı

**Tarih:** 2026-08-02 · **Rol:** bağımsız test eden oturum

## Amaç

`main` üzerindeki W20 entitlement ledger, W21 approval/revision ve W22 content planner
dilimlerini, mevcut test girdilerini tekrar kullanmadan ve `COMPOSE_PROJECT_NAME=sp-verify`
ile ayrılmış gerçek altyapıda düşmanca doğrulamak.

## Sınır

- Özellik ve üretim kodu değiştirilmeyecek.
- Kalıcı test dosyası eklenmeyecek; bağımsız girdiler geçici harness/SQL/API çağrılarıyla
  üretilecek.
- Bulgular yalnızca ilgili work order'ın `Doğrulama` bölümüne yazılacak.

## Beklenen dosya değişiklikleri

- `docs/handoffs/W20-entitlement-ledger.md`
- `docs/handoffs/W21-approval-revision.md`
- `docs/handoffs/W22-content-planner.md`
- `docs/STATUS.md` — “bağımsız doğrulanmadı” özeti sonuçlarla çeliştiği için.
- Bu plan; bitince `docs/plans/completed/phase-2/` altına taşınacak.

## Adımlar

1. [x] Güncel `main`, work order saldırı listeleri, modül sınırları ve ilgili
   gereksinim/mimari kararları doğrula.
2. [x] `sp-verify` compose ortamını kur; migration head ve araç zinciri sürümlerini kaydet.
3. [x] W20 saldırılarını bağımsız veriyle uygula: yarış, çift iade, negatif bakiye,
   cross-tenant yazım, yeniden render ücretlendirmesi, puan sürümü geçmişi.
4. [x] W21 saldırılarını bağımsız veriyle uygula: kota, saf yeniden render, iptal tekrarı ve
   ilerletme, çift iade, roller/tenant, gizli not sızıntısı, revizyon sınıfı/başlangıcı.
5. [x] W22 saldırılarını bağımsız veriyle uygula: çift obligation, sessiz saat, yetersiz bakiye,
   onaysız schedule, cross-tenant planlama, sınırsız üretim, gece yarısı/DST.
6. [x] `make verify` kapısını çalıştır; sonuçları ve araç sürümlerini kaydet.
7. [x] Üç `Doğrulama` bölümünü kanıt tablosuyla doldur; diff'i denetle ve planı tamamlananlara
   taşı.

## Sonuç

- W20: kurallı HTTP/servis akışları geçti; iç servis/DB-yazarı sınırında üç açık bulgu
  bağımsız olarak yeniden üretildi. Karar: **düzeltme gerekiyor**.
- W21: saldırı listesinin tamamı engellendi. Karar: **teslim edilebilir**.
- W22: saldırı listesinin tamamı engellendi. Karar: **teslim edilebilir**.
- Uygulama ve test kaynak kodu değiştirilmedi; geçici hostile harness repo dışından silindi.
