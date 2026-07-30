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
| ~~P1~~ | **`main` push edildi** (2026-07-30, `5aabf2e` → `origin/main`). Kullanıcı 'en mantıklısı ve en doğrusuyla devam et' diyerek genel yetki verdi; 33 commit'i tek makinede yedeksiz bırakmak o yetkinin altında kalmıyordu. Bundan sonra slice kapanışlarında push PM'in rutini. | kapandı |
| K1 | Faturalandırma modeli (IAP vs web-first) — Phase 3'ten önce | açık |
| ~~K2~~ | n8n → **ADR-012 ile MVP'den çıkarıldı** (PM/mimar kararı, genel yetki kapsamında) | kapandı |
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
  5. **Sağlayıcı route politikasının içeriği.** ADR-007 mekanizmayı kurdu ama sağlayıcı seçimini bilinçli olarak dışarıda bıraktı; route kaydında `data-region requirement` alanı var, politikanın içeriği yok. Yazılacak kural: *yüz/ses taşıyan girdi sınıfı hangi sağlayıcılara gidebilir* (KVKK: biyometrik tartışması + her sağlayıcı için standart sözleşme ve 5 iş günü Kurul bildirimi), ve QC'nin üreten sağlayıcıdan farklı olma zorunluluğunun routing'e nasıl bağlandığı.

- **ADR-008 ekleri (W01 sonrası, PM kuyruğunda).**
  1. **Completion checksum stratejisini K5 ışığında yeniden değerlendir.** W01 tek ve her zaman doğru yolu seçti: completion, SHA-256'yı **depodaki byte'ları tek seferlik akışlı okuyarak** hesaplıyor. MinIO yerelde ucuz, ama üretimde R2/S3 ile bu **her upload'da dosya boyutu kadar indirme** demek — K5'in (tek ucuz sunucu, egress maliyeti) doğrudan karşısında. W01, K5 kararından **önce** tasarlandı, bu yüzden maliyet boyutu girdisinde yoktu. Değerlendirilecek seçenekler: sağlayıcı tarafı `ChecksumSHA256` (`FULL_OBJECT`) desteklendiğinde kullanma, part başına checksum'ı PUT sırasında sağlayıcıya doğrulatma, doğrulamayı API isteğinden worker'a taşıma, boyut eşiği. Karar ADR-008 eki olarak yazılır.
  2. **Hand-rolled SigV4 riski.** W01 boto3 eklemek yerine imzalamayı `httpx` üzerinde elle yaptı (async yola senkron SDK sokmamak için — gerekçe ADR-008'de, savunulabilir). Ama SigV4 güvenlik-hassas ve **yalnızca MinIO'ya karşı doğrulandı**; MinIO S3-uyumlu ama birebir aynı değil. Üretim sağlayıcısı seçilirken (R2/S3) imzalama gerçek sağlayıcıya karşı yeniden doğrulanmalı: özel karakterli anahtarlar, bölge/servis kapsamı, saat kayması, çok büyük part sayısı. Codex doğrulamasında bu yüzeye özel olarak saldırılmalı.
  3. **Kontrol objesi geçici çözümü.** `media_upload_sessions.storage_upload_id` `String(128)` gerçek AWS `UploadId` için kısa; W01 migration slotu olmadığı için `_control/uploads/{id}.json` yazan bir sunucu sahipli kontrol objesi kullandı. Kolon genişletildiğinde (W04 slotu) bu katman kaldırılıp sadeleştirilir.

- **W07 — YAZILDI** → [W07-single-server-resilience.md](W07-single-server-resilience.md). Özet: iki kalem — (1) `compose.yaml`'a servis bazlı CPU/RAM limitleri, render worker'ına düşük öncelik ve concurrency sınırı, geçici dizin temizliğinin zorlanması; (2) sunucu dışına otomatik günlük `pg_dump` + geri yükleme provası (yedek test edilmeden yedek sayılmaz). **Gerekçe:** tek sunucu tek arıza noktası ve üretim veritabanı git'te olmayacak. `compose.yaml` sahibi W01, o yüzden W01 merge sonrası. Deployment topolojisi ADR'ı ile birlikte yazılır.

- **W08 — YAZILDI** → [W08-provider-benchmark-harness.md](W08-provider-benchmark-harness.md). Özet: gerçek sağlayıcı bağlanmadan ÖNCE — PRD §40.5'teki sabit medya seti (dikey/yatay, gürültülü, Türkçe konuşma, karanlık, titrek, insan yüzü, öncesi/sonrası, logo, küçük metin) üzerinde kabiliyet başına sağlayıcı karşılaştırması: Türkçe ASR doğruluğu, VLM sahne isabeti, Türkçe TTS prozodisi, marka tonu ve yasak kelime uyumu, katı JSON şema sadakati, kabiliyet başına gerçek maliyet. **Gerekçe:** ölçülmeden bağlanan ilk sağlayıcı varsayılan hâline gelir ve kabiliyet routing'inin amacı kaybolur. PRD §17.2'nin aday tablosu maliyete göre seçilmiş; Türkçe kalitesi ve veri bölgesi tartılmamış.
- **Phase 2 kapısı öncesi değerlendirme:** durable execution (DBOS/Temporal) ve LiteLLM'in kabiliyet portları altına konması.

## Yetki sınırı (2026-07-30 itibarıyla)

Kullanıcı "en mantıklısı ve en doğrusuyla devam et her zaman" dedi. Bunun pratik anlamı:

- **PM karara bağlar:** mimari kararlar (ADR), iş emri sırası ve kapsamı, merge, ADR numaralandırma, slice kapanışında push, geri dönüşü kolay teknik tercihler.
- **Kullanıcıya kalır:** para ve hukuk sonucu doğuran, geri dönüşü pahalı olanlar — **K1** (faturalandırma modeli: mağaza komisyonu ve store ilişkisi), **K3** (pazar kapsamı: EU'ya girmek AI Act Md. 50 yükümlülüğü doğurur). Bunlar için karar hazırlanır, önerilir, ama tek başına alınmaz.
- **K4** (kullanıcı düzenleme modeli) Phase 2 timeline şemasıyla birlikte kararlaştırılır — önerisi hazır, ama önünde timeline işi yokken karara bağlamak erken olur.
- Geri dönüşü zor veya dışa dönük her yeni işlem tipi (üretim deploy, ödeme, dış platforma içerik gönderme) yine ayrıca sorulur.

## Öğrenilen dersler (tekrarlanmasın)

1. **Çift iş.** İki oturum `258439d` base'inden aynı slice'ı yaptı; `c43ccad` silinmek zorunda kaldı. Panzehir: WO'da "dokunulacak dosyalar" ilanı + [STATUS.md](../STATUS.md) dosya sahipliği tablosu + tetiklemeden önce durumun `tetiklenmedi` olduğunu doğrulama.
2. **İş emirleri yan yana okunmadan dağıtılmaz.** İlk üç WO'yu yazdıktan sonra `pyproject.toml`, `compose.yaml` ve `docs/index.md` üzerinde üç çakışma çıktı; W02 sıraya alındı, W06 ayrıldı, indeks sahipliği W03'e verildi.
3. **Dal isimleri içerikle uyuşmalı.** `feature/mobile-e2e-demo` medya özet API'si içeriyordu; worktree adı ile checkout edilen dal farklıydı. Kural: `slice/<faz><harf>-<domain>`, slice kapanınca merge + dal silinir.
4. **Compose proje adı paylaşılıyorsa paralel doğrulama güvenilmez.** W02 kendi worktree'sinde `--build` yapınca `main`'in konteynerini kendi imajıyla değiştirdi; Codex de `main`'in kaynağını W02'nin araç zincirinden geçirip W01'e ait olmayan 21 hata bildirdi. Düzeltme: `compose.yaml` artık `${COMPOSE_PROJECT_NAME:-socialpilot-ai}`, kural [README.md](README.md)'de. **Ders:** bir doğrulama raporu, hangi araç zinciri sürümleriyle koştuğunu yazmıyorsa yeniden üretilemez.

5. **Paralel WO'lar aynı ADR numarasını alır.** W02 ve W09 ikisi de ADR-009'u aldı; W09'un dosyası merge sırasında ADR-011'e taşındı. WO'daki "dizini tara" uyarısı yetmiyor çünkü paralel dallar birbirinin dosyasını göremiyor. **Ders:** ADR numarasını **PM merge sırasında** verir; WO'lar dosyayı geçici bir adla yazıp raporda bildirir.

6. **Araç zinciri yükseltmesi ile kod slice'ı paralel çalışırsa birleşimi kimse doğrulamamış olur.** W09 py312/mypy 1.13/ruff 0.8'de yazıldı, W02 py313/mypy 2.3/ruff 0.16'ya taşıdı; ikisi de kendi dalında yeşildi ama birleşik durum kırmızıydı. **Ders:** platform yükseltmesi ile aynı anda kod slice'ı koşturuluyorsa merge sonrası uzlaştırma turu plana baştan yazılır — ya da yükseltme tek başına koşar.

7. **Kapı kapsamı tek tip olmalı.** `mypy .` `scripts/` dizinini denetliyordu ama `ruff check app tests migrations` denetlemiyordu; `scripts/` sessizce sapabiliyordu. Makefile'ın lint/format hedeflerine `scripts` eklendi. **Ders:** yeni bir üst düzey dizin eklendiğinde tüm kapıların kapsamı aynı anda güncellenir.

8. **Doküman durumu git'i yansıtmıyorsa git kazanır.** `main` 16 commit gerideyken dokümanlar Phase 0'ı anlatıyordu. [STATUS.md](../STATUS.md) her slice kapanışında aynı commit'te güncellenir.
