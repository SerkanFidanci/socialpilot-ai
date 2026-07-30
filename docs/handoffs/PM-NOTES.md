# PM Devir Notu

**Amaç:** PM oturumunun bağlamı sıkıştırılırsa veya yeni bir PM oturumu açılırsa, buradan devam edilebilsin. **Her PM oturumu bu dosyayı ve [STATUS.md](../STATUS.md)'yi okur.**

**Son güncelleme:** 2026-07-30

## Rol ve çalışma modeli

Claude, bu projede **proje yöneticisi ve mimar** rolündedir. Kullanıcının istediği biçim:

- PM uygulama koduna girmez; bağlamını uygulama ayrıntısıyla doldurmaz.
- PM iş emrini `docs/handoffs/W<NN>-<konu>.md` olarak yazar ve kullanıcıya dosya yolunu verir.
- Kullanıcı **yalnızca oturumu tetikler**: ilgili oturuma "bu dosyadaki iş emrini oku ve uygula" der.
- Yürüten oturum **aynı dosyaya** rapor yazar; test eden oturum (GPT Codex) doğrulama bölümünü doldurur.
- PM tüm dönüşleri bu dosyalardan okur, sıradaki iş emrini yazar.
- Aynı anda birden fazla oturum çalışabilir; **dosya-ayrıklığını ve Alembic migration slotunu PM garanti eder.**

## Tetikleme promptları (kullanıcıya verilecek metinler)

Yürütücü oturum:

```
docs/handoffs/<WO dosyası> dosyasındaki iş emrini oku ve uygula. Protokol: docs/handoffs/README.md. Başlamadan önce docs/STATUS.md oku. Sahibi olmadığın dosyaya dokunma; gerekirse dur ve raporuna yaz.
```

Test eden oturum (Codex):

```
docs/handoffs/<WO dosyası> dosyasını oku. Sen test edensin, özellik yazma. make verify çalıştır, sonra kabul kriterlerine karşı düşmanca test dene. Bulgularını aynı dosyanın "Doğrulama" bölümüne, docs/handoffs/README.md'deki tabloyla yaz.
```

Model/effort ataması [STATUS.md](../STATUS.md) WO tablosundadır. Kural: güvenlik hassas veya yeni domain işi → Opus 5 / high; mekanik ama geniş iş → Opus 4.8 / medium; tek dosyalık mekanik iş → Opus 4.7 / low.

## Neyi kendim doğruladım, neyi rapordan aldım

Bunu karıştırmamak önemli:

- **Kendim doğruladım (git üzerinden):** dal/worktree topolojisi, `c43ccad`'in `ce96771` tarafından kapsandığı (dosya bazlı diff), commit zaman damgaları, doküman byte boyutları ve token tahminleri, kod boyutu dağılımı, lockfile/Dependabot/güvenlik taraması yokluğu, `config.py` MIME listesi.
- **Yürüten oturumun raporundan aldım, kendim çalıştırmadım:** 180 pytest geçtiği, mypy strict temizliği, `flutter analyze`/45 test, compose api healthy, `0009` tek head, canlı endpoint doğrulaması. Bir çelişki şüphesi olursa **`make verify` yeniden çalıştırılmalı**.

## Bekleyen kullanıcı kararları

| # | Konu | Durum |
|---|---|---|
| P1 | **`main` origin'e push edilmemiş** (Sprint 0 itibarıyla ~19 commit, tek makinede yedeksiz). PM sordu, cevap bekliyor. Kullanıcı istemeden push edilmez. | **açık** |
| K1 | Faturalandırma modeli (IAP vs web-first) — Phase 3'ten önce | açık |
| K2 | n8n içeride mi — Phase 2 zamanlama işinden önce | açık |
| K3 | Pazar kapsamı TR / EU-global — Phase 2 render şemasından önce | açık |

K1–K3'ün gerekçeleri ve PM önerileri [STATUS.md](../STATUS.md) "Karar bekleyenler" tablosunda.

## PM kuyruğu: yazılacak iş emirleri

Sırası [STATUS.md](../STATUS.md) WO tablosunda. Henüz yazılmamış olanların amaçlanan kapsamı:

- **W04 — Marka profili + ürün/hizmet kataloğu.** PRD §11. Yeni `modules/brands`. Migration slotu ayrılmış. Tenant listelerine cursor pagination borcu bu slice'ta kapatılır. W03 kapanınca yazılır (gereksinim dosyası `20-brand-catalog.md` hazır olsun).
- **W05 — OpenTelemetry.** Trace + metric; FastAPI/SQLAlchemy/httpx/redis instrumentation, OTLP exporter env ile kapalı-varsayılan. `config.py` sahipliği nedeniyle W01 sonrası.
- **W06 — PostgreSQL 18 + Valkey imaj geçişi.** `compose.yaml` + CI servis etiketleri. W01 ve W02 kapanınca.
- **ADR kuyruğu (PM yazacak, kod işi değil):**
  1. Celery ↔ async köprüsü kararı (Celery 5.6'da native asyncio yok).
  2. Dış API sürüm yaşam döngüsü politikası (Google Ads yılda 4 major; pinleme + takvimli yükseltme + contract test).
  3. Yayın (publish) delivery yüzeyi — Instagram public URL gereksinimi ile signed-URL duruşunun çelişkisi.
  4. AI disclosure alanları (Meta otomatik etiketleme + EU AI Act Md. 50).
- **Phase 2 kapısı öncesi değerlendirme:** durable execution (DBOS/Temporal) ve LiteLLM'in kabiliyet portları altına konması.

## Öğrenilen dersler (tekrarlanmasın)

1. **Çift iş.** İki oturum `258439d` base'inden aynı slice'ı yaptı; `c43ccad` silinmek zorunda kaldı. Panzehir: WO'da "dokunulacak dosyalar" ilanı + [STATUS.md](../STATUS.md) dosya sahipliği tablosu + tetiklemeden önce durumun `tetiklenmedi` olduğunu doğrulama.
2. **İş emirleri yan yana okunmadan dağıtılmaz.** İlk üç WO'yu yazdıktan sonra `pyproject.toml`, `compose.yaml` ve `docs/index.md` üzerinde üç çakışma çıktı; W02 sıraya alındı, W06 ayrıldı, indeks sahipliği W03'e verildi.
3. **Dal isimleri içerikle uyuşmalı.** `feature/mobile-e2e-demo` medya özet API'si içeriyordu; worktree adı ile checkout edilen dal farklıydı. Kural: `slice/<faz><harf>-<domain>`, slice kapanınca merge + dal silinir.
4. **Doküman durumu git'i yansıtmıyorsa git kazanır.** `main` 16 commit gerideyken dokümanlar Phase 0'ı anlatıyordu. [STATUS.md](../STATUS.md) her slice kapanışında aynı commit'te güncellenir.
