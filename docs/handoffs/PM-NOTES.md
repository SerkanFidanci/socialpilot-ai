# PM Devir Notu

**Amaç:** PM oturumunun bağlamı sıkıştırılırsa veya yeni bir PM oturumu açılırsa, buradan devam edilebilsin. **Her PM oturumu bu dosyayı ve [STATUS.md](../STATUS.md)'yi okur.**

**Son güncelleme:** 2026-07-31 (compact öncesi anlık durum bloğu eklendi)

## ŞU AN — hızlı devralma (2026-08-01, W15+W16 merge sonrası)

**Depo durumu:** `main` = `5505537` (Merge W16). **743 pytest** (merge sonrası PM koşusu, gerçek PG+MinIO+FFmpeg; 674 W15 + 69 W16 = tam toplam), lint+format+mypy strict yeşil, Alembic head `0014_voiceover_assets` (tek head). Kapanan işler: **W01–W16** — Phase 2A+2B+2C bitti. ADR-001…016; W15/W16 yeni ADR çıkarmadı (gerekçeleri raporlarında).

**W16 düzeltme turu 2 MERGE EDİLDİ (2026-08-02, PM koşusu 792 pytest yeşil).** Getirdikleri: Latin dışı harf → `SCRIPT_UNSUPPORTED_CHARACTER` (tablo değil sınır — Coptic/Cherokee/… tek kuralla), görünmezler `Cf`/`Cn`/`Co`/`Cs` **kategorisiyle**, redaksiyon yüzde-kodlu parametre adlarını `%(?:25)*XX` kalıbıyla ham biçimde maskeliyor. Oturum bilinçli olarak bir sınıfı düzeltmeyip PM'e sordu; **karar verildi ve W17 ona göre yazıldı:** Latin harf katlaması **iki yön** (`165 turk lirasi` + `165 ṬL` aynı katlama), ayrıştırılamayan Latin genişletmeleri küçük harita + **fail-closed** ret, `T.L.`/`T L` ve `⑴⑸` kalıp grameri, yasak terimler de katlanır, saklanan `_scene_tags` değerlerine dokunulmaz.

**W17 MERGE EDİLDİ (2026-08-02, PM koşusu 864 pytest).** Getirdikleri: iki yönlü ASCII katlaması (`turk lirasi` + `ṬL` aynı kalıba düşer), ayrıştırılamayan Latin harfler için Unicode **adından** taban çözümü + fail-closed ret, alfabe kısıtı ile katlama tek fonksiyonda (`_ascii_fold` — ayrışamazlar), `T.L.`/`T L` grameri (ayırıcı sınırsız ama kelime karakteri taşıyamaz), Unicode'un rakam saydığı her kod noktası (`⓵`,`❶`) ASCII rakama iner, `normalize_encoding` saklanan değerler için ayrı (tüm atanmış kod noktalarında 0 fark ölçüldü). Yasak terimler katlanıyor. **Dedektörde bilinen açık sınıf kalmadı.**

**2026-08-02 · W19 + düzeltme turu 4 merge edildi; Codex W19 turu TEMİZ. `main` head `0016`, dal koşusu 1237 pytest (PM tam koşusu arka planda).**

- **W19 (2E birinci yarı):** artık bir "içerik projesi" var — `PLANNED`→`PREVIEW_READY` uçtan uca. Proje satırının kendisi dayanıklı job (ayrı `jobs` satırı yok: "sıralayıcının durumu zaten sonucudur"). Üç borç kapandı: voiceover miksajı (üç render'ın PCM hash'i farklı — iddia değil kanıt), QC olayı + claim sorgusunun yeniden şekillenmesi (199 ms → 3,6 ms, durağanda 0,05 ms), `pending` süpürücü.
- **Düzeltme turu 4:** QC birleştirmesi artık sıra-bağımsız (`failed`>`unknown`>`passed`, tam sıralama sayesinde **byte-özdeş rapor**); ondalık kesir sözcükleri kapandı (`bir tam onda bes lira` 0/81, `iki tam yuzde yirmi bes lira` 0/243 — Codex'te 45/81 ve 75/243 kaçıyordu). Oturum `tam`'ın tutar **başlatamayacağı** kısıtını kendisi ekledi ve gerekçesini ölçtü (`tamamen liraya endeksli` fiyat sanılıyordu).

**Codex turunun kalitesi hakkında not (PM):** W19 turu bulgu döndürmedi ama **kendi girdilerini üretmek yerine W19'un yazdığı testleri koşup denetledi**. Bu zayıf bir doğrulama — testin yazarı ile testin denetleyicisi aynı olduğunda tur bağımsızlığını kaybeder. Bundan sonraki Codex prompt'larına açık cümle giriyor: *"mevcut testleri koşmak doğrulama değildir; kendi girdilerini üret."* (W20'nin Doğrulama bölümüne yazıldı.)

**Sıradaki tetikleme — W20 (2E ikinci yarı: kredi defteri)** ([W20-entitlement-ledger.md](W20-entitlement-ledger.md), slot `0017`, taban 1237):
```
docs/handoffs/W20-entitlement-ledger.md dosyasındaki iş emrini oku ve uygula. Protokol: docs/handoffs/README.md. Başlamadan önce docs/STATUS.md oku. Worktree kökünden ve COMPOSE_PROJECT_NAME=sp-w20 ile çalıştır. Migration slotu sende (0017). Sahibi olmadığın dosyaya dokunma. Merge etme, dalda bırak.
```

**W20'nin kapsam sınırı bilinçli:** ödeme/mağaza **yok** (K1 hâlâ kullanıcının kararı, Phase 3). Yalnızca defter + tüketim. Gerekçe: tüketim tarafı doğru kurulursa kaynak tarafı sonradan tek bir grant yazıcısı olarak takılır; tersi mümkün değil — önce ödeme alıp sonra saymaya başlamak, sayılmamış tüketimi kalıcı borca çevirir. **Ve bu, ücretli sağlayıcı bağlamadan önce kapatılması gereken açık:** bugün bir kullanıcı sınırsız render tetikleyebilir ve her render gerçek para harcar.

**Kuyruk:** W20 → 2F onay/revizyon → 2G planlayıcı → W06 → Phase 3 (mağaza/ödeme, K1 kararıyla).

<!-- arşiv -->
**~~2026-08-02 · W17 ve W18 KAPANDI (1151 pytest, head 0015).~~**

- **W18 (2D QC):** 13 kontrol raporda ve *atlanması ifade edilemiyor* (`build_results` tamamıyla `unknown` başlar, `decide` eksik kümeyi reddeder, DB check constraint aynı kuralı tekrarlar). Gerçek bozuk medya fixture'ları, karar tablosu tüm çift permütasyonlar + 3.000 rastgele atamayla tüketildi. Celery bağlantısı takip 1'de yapıldı. **Oturum index eklemeyi ölçüp reddetti:** 200 bin render'da claim 134 ms; index eklense de planlayıcı hash anti-join'i seçiyor (korelasyonu bilemiyor), zorlanan plan 0,14 ms — yani sorgunun yeniden şekillenmesi gerek, index kozmetik olurdu. Ölçüm W19'a devredildi.
- **W17:** üç tur sonunda kapandı. Son turda sayı sözcükleri liste ama **birleşimleri gramer** (bitişik/tireli dahil), `T Lye` kapalı Türkçe ek kümesiyle çözüldü ve `Şef T. Lezzetli` pini korundu. **111.129 varyant / 0 kaçış.**

**Sıradaki iki tetikleme (paralel, dosya-ayrık):**

1. **Codex turu (W18 ilk kez + W17 son teyit):**
```
docs/handoffs/W18-automatic-qc.md ve docs/handoffs/W17-latin-fold-pattern-grammar.md dosyalarını oku. Sen test edensin, özellik yazma. İkisi de main'de merge edildi (1151 pytest). Worktree kökünden ve COMPOSE_PROJECT_NAME=sp-codex ile çalış. Hedefler: (1) QC'yi kandırmaya çalış — bozuk/siyah/sessiz/donuk medyayı passed yaptırmaya, bir kontrolü rapordan düşürmeye, ölçüm hatasını sessiz geçirmeye, karar tablosunda tanımsız kombinasyon bulmaya, başka tenant'ın raporunu okumaya, imzalı URL'yi rapora/log'a sızdırmaya; timeline metninde senaryo tarafında kapalı bir atlatmanın açık kalıp kalmadığını dene; (2) yazılı sayı gramerini atlatmaya çalış — yeni birleşim biçimleri, kesirler, dağıtım ekleri, kısaltma kuyrukları, katlama+birleşim bileşimleri; yanlış pozitifleri de ölç. Bulgularını ilgili dosyaların "Doğrulama" bölümlerine tabloyla yaz; araç zinciri sürümlerini yaz.
```
2. **W19 — Phase 2E birinci yarı** ([W19-content-lifecycle.md](W19-content-lifecycle.md), slot `0016`, taban 1151):
```
docs/handoffs/W19-content-lifecycle.md dosyasındaki iş emrini oku ve uygula. Protokol: docs/handoffs/README.md. Başlamadan önce docs/STATUS.md oku. Worktree kökünden ve COMPOSE_PROJECT_NAME=sp-w19 ile çalıştır. Migration slotu sende (0016). Sahibi olmadığın dosyaya dokunma. Merge etme, dalda bırak.
```

**2E bölündü (PM kararı):** W19 = yaşam döngüsü (§20 durum makinesi + QC kararının sınırlı eyleme dönmesi + üç devralınan borç: voiceover miksajı, QC kuyruk olayı, `pending` süpürücü). **W20 = entitlement/kota**, ayrı — çünkü hak tüketimi K1'e (para modeli, kullanıcı kararı) bağlı ve tek WO'ya sığdırmak slotu ve dosya sahipliğini şişirirdi.

**Kuyruk:** W19 → W20 (entitlement) → 2F onay/revizyon → 2G planlayıcı → W06. Sonra Phase 3.

<!-- arşiv -->
**~~ŞU AN İKİ SICAK OTURUM AÇIK (2026-08-02).~~**

1. **W18 (2D QC) — takip 1: Celery bağlantısı.** İş kabul edildi (dalda **1071 pytest**, +124): 13 kontrolün tamamı raporda ve atlanması *ifade edilemiyor*, gerçek bozuk medya fixture'ları (siyah/sessiz/donuk/bozuk konteyner), karar tablosu tüm çift permütasyonlar + 3.000 rastgele atamayla tüketildi, fail-closed üç yoldan kanıtlandı, `forbidden_matcher` birleştirildi. **Tek eksik benim WO hatam:** kapsam "ölçüm worker'da" diyordu ama dosya listesine worker dosyalarını koymamıştım; oturum çelişkiyi sessizce çözmek yerine bildirdi (doğru davranış). Talimat W18 dosyasının "Takip 1" bölümünde. Prompt:
```
docs/handoffs/W18-automatic-qc.md dosyasındaki "Takip 1 — Celery bağlantısı" bölümünü oku ve uygula. Aynı dalda (slice/2d-automatic-qc) devam et. Worktree kökünden ve COMPOSE_PROJECT_NAME=sp-w18 ile çalıştır. Merge etme, dalda bırak.
```
2. **W17 — takip düzeltmesi 2: yazılı sayı grameri.** Codex takip-1 turu çekim çapasını kıramadı (161/161 ek zinciri, 12/12 yanlış pozitif temiz) ama **yazılı sayı gramerinde 3 açık** buldu: `bir buçuk lira` (kritik), `yüzbin lira`/`onbir lira`/`beşerlira` (yüksek), `165 T Lye` (orta). Talimat W17 dosyasının "Takip düzeltmesi 2" bölümünde. Prompt:
```
docs/handoffs/W17-latin-fold-pattern-grammar.md dosyasındaki "Takip düzeltmesi 2" bölümünü oku ve uygula. Aynı dalda (fix/w17-latin-fold) devam et; önce git merge main ile dalını güncelle. Worktree kökünden ve COMPOSE_PROJECT_NAME=sp-w17 ile çalıştır. Migration yok. Merge etme, dalda bırak.
```

**Merge sırası:** W18 önce (daha büyük ve `validation.py`'ye dokunuyor), sonra W17 takip 2 (`script.py`) — dosya-ayrık oldukları için ters sıra da olur. Her merge sonrası tam doğrulama, STATUS güncelleme, push. Sonra Codex turu (W18 için ilk kez; W17 için üçüncü).

**Enumerasyon dersinin inceltilmiş hâli (W17 takip 2'de yazılı):** her elle sayılmış küme kötü değil — **kapalı ve dil/standart tarafından sonlu olan** kümeler (Türkçe sayı sözcükleri, Türkçe çekim ekleri) yazılabilir; **açık uçlu** olanlar (confusable çiftleri, görünmez kod noktaları, çekim *biçimleri*, bileşik yazımlar) yazılamaz — onlar kategori kuralı, üretilmiş veri veya gramer ister. Ayrım: "dil yarın buna yenisini ekler mi?"

<!-- arşiv -->
**~~W17 takip düzeltmesi MERGE EDİLDİ (2026-08-02, PM koşusu 947 pytest).~~** Çözüm: çekim listesi kaldırıldı, kalıplar kök + `_SUFFIX` (Türkçe ek alfabesi — `o`/`ö` yok çünkü ünlü uyumu ekte üretmez, `b/f/h/j/p/v` yok çünkü ek ünsüzü değil; "Eurovision"/"Europa"/"Kebap" bu yüzden güvende). Tarih/oran kökleri de kapandı, `yuzde(?!n)` ile "bu yüzden" korundu, sol taraftaki yazılı sayı çekimi de kapandı (yan kazanç: `yuzlerce lira`, `binlerce dolar`). **46.918 varyant / 0 kaçış** (düzeltme öncesi 612) ve taramanın sınırlı hâli jeneratif test olarak eklendi. Ay adları sıradan isim olduğu için `3 martı`/`2 ocakta` reddediliyor — mevcut bilinçli sınırın içinde, ürün tarafı bilsin.

**PM kararı (W17'nin sorusu):** yasak terimlerde **çekim eşleşmesi yapılmayacak** — `şeker` yasakken `şekerli` serbest. Liste markanın, kalıp bizim; kök eşleşmesi `az` yasakken `azalttık`ı da yasaklardı. Ürün tarafı markaya "yasaklamak istediğin biçimleri yaz" der. W18'e yazıldı.

**Sıradaki tetikleme — W18 (Phase 2D otomatik QC)** ([W18-automatic-qc.md](W18-automatic-qc.md), slot `0015`, taban **947**). Prompt:
```
docs/handoffs/W18-automatic-qc.md dosyasındaki iş emrini oku ve uygula. Protokol: docs/handoffs/README.md. Başlamadan önce docs/STATUS.md oku. Worktree kökünden ve COMPOSE_PROJECT_NAME=sp-w18 ile çalıştır. Migration slotu sende (0015). Sahibi olmadığın dosyaya dokunma. Merge etme, dalda bırak.
```
**Paralel tetiklenebilir — kısa Codex teyidi (W17 çekim yüzeyi):**
```
docs/handoffs/W17-latin-fold-pattern-grammar.md dosyasının "Rapor — takip düzeltmesi 1" bölümünü oku. Sen test edensin, özellik yazma. main'de merge edildi. Worktree kökünden ve COMPOSE_PROJECT_NAME=sp-codex ile çalış. Hedef: çekim çapasını atlatmaya çalış — ek alfabesi dışında kalan gerçek Türkçe ekler, kök gibi başlayan kelimeler, kısaltma köklerinin kesme işaretli/işaretsiz biçimleri, sol taraftaki yazılı sayı çekimleri, katlama+çekim bileşimleri; yanlış pozitifleri de ölç (marka adları, "bu yüzden", "Eurovision", tarif metinleri). Bilinçli bırakılanları yeniden raporlama: ay adlarının sıradan isim olması (3 martı, 2 ocakta), iki politika pini, yasak terimlerde çekim yokluğu (PM kararı). Bulgularını aynı dosyanın "Doğrulama" bölümüne tabloyla yaz; araç zinciri sürümlerini yaz.
```

**W18 dönünce:** denetim → merge → tam doğrulama (taban 947) → push → Codex turu. Sonra **2E** (yaşam döngüsü + entitlement + QC'nin önerdiği eylemlerin gerçekleşmesi + render'a voiceover miksajı + `pending` süpürücü) → 2F onay/revizyon → 2G planlayıcı → W06.

<!-- arşiv: kapanmış turlar -->
**~~Codex birleşik turu DÖNDÜ (2026-08-02): redaksiyon TEMİZ — W16 kapandı, dal+worktree silindi.~~** Katlama da tuttu (3.892 varyant, `Cf`/`Cn`/`Co`+confusable+süslü rakam bileşimleri 6/6 ret, `T.L.` ayırıcıları 9/9, meşru aksanlı adlar 8/8 geçti). **Tek kritik: `165 lirayla`** — `_CURRENCY_WORD` elle sayılmış bir çekim listesi (`lira|lirasi|liray[ia]|liradan|liralik`) ve `lirayla` içinde yok. **Aynı enumerasyon hatasının üçüncü tekrarı** (confusable tablosu → görünmez listesi → çekim listesi). Takip düzeltmesi talimatı W17 dosyasının "Takip düzeltmesi 1" bölümünde; sıcak oturum, aynı dal (`fix/w17-latin-fold`). Prompt:
```
docs/handoffs/W17-latin-fold-pattern-grammar.md dosyasındaki "Takip düzeltmesi 1" bölümünü oku ve uygula. Aynı dalda (fix/w17-latin-fold) çalış; önce git merge main ile dalını güncelle. Worktree kökünden ve COMPOSE_PROJECT_NAME=sp-w17 ile çalıştır. Migration yok. Merge etme, dalda bırak.
```

**Düzeltme dönünce:** PM denetler → merge → tam doğrulama (taban **864**) → push → kısa Codex teyidi (yalnız çekim yüzeyi + yanlış pozitif ölçümü). Temizse → **2D (QC)**.

**PM dersi (üç kez ödendi):** kabul kriterine "şu girdiler reddedilsin" yazmak yetmiyor — **kalıp/liste yazan her düzeltmede "bu bir enumerasyon mu?" diye sor.** Elle sayılmış her küme (confusable çifti, görünmez kod noktası, çekim eki, ayırıcı karakter) bir sonraki turda delindi; tutan çözümlerin hepsi kategori/kural/üretilmiş-veri oldu. Yeni WO yazarken bu cümle kabul kriterlerine girsin.

<!-- kapanmış: birleşik Codex turu (aşağıdaki prompt arşiv) -->
**~~Sıradaki tetikleme — birleşik Codex turu (W16 2. tur + W17; ikisi de bağımsız teyit görmedi).~~** Prompt:
```
docs/handoffs/W16-verification-followups-3.md ("Rapor — düzeltme turu 2" dahil) ve docs/handoffs/W17-latin-fold-pattern-grammar.md dosyalarını oku. Sen test edensin, özellik yazma. İkisi de main'de merge edildi. Worktree kökünden ve COMPOSE_PROJECT_NAME=sp-codex ile çalış. Hedefler: (1) dedektörü YENİDEN atlatmaya çalış — ad-tabanlı Latin katlamasının kapsam sınırları (LIGATURE/WITH biçimleri, IPA, fonetik), katlama+görünmez+confusable+süslü rakam kombinasyonları, T.L. grameri çevresinde yeni ayırıcılar, fail-closed reddin tutarlılığı; yanlış pozitifleri de ölç (meşru aksanlı adlar, tarif/madde-fıkra metinleri); (2) redaksiyonu YENİDEN atlatmaya çalış — %(?:25)*XX kalıbının kenarları, karışık büyük/küçük hex, extra+queue+fork kombinasyonları. Bilinçli bırakılanları yeniden raporlama: haritalanmamış harflerin reddi (ürün maliyeti, dokümante), handle()'ı ezen elde kurulmuş handler, iki politika pini. Bulgularını iki dosyanın "Doğrulama" bölümlerine tabloyla yaz; araç zinciri sürümlerini yaz.
```

**Tur dönünce akış:** bulgu → küçükse sıcak oturum (W17 dalı duruyor), büyükse W18. **Temizse → 2D (QC) iş emrini yaz:** Phase 2 planı §3 + `forbidden_matcher` birleştirmesi (`normalize_for_matching`+`contains_unsupported_letter` import edilir) + 2C süre sapması eşikleri. W16+W17 worktree'leri tur temiz dönene kadar durur; temizse ikisi + dalları silinir, eski oturum dizinleri süpürülür. Docker: yalnız ana stack ayakta (sp-* stack/volume/imajları 2026-08-02 temizlendi, ~7.6 GB).

**Kayda geçen protokol notları:** (1) W15 oturumu merge'i kendisi yaptı — içerik doğruydu ama **merge PM'in**; bundan sonra her WO'ya "merge etme, dalda bırak" cümlesi yazılıyor. (2) W15 ilan listesi dışına 4 gerekçeli dosyayla çıktı — WO yazarken kabul kriterinin dokunmayı zorunlu kıldığı dosyaları listeye koy. (3) **Enumerasyon dersi genelleşti:** elle yazılmış confusable/görünmez listeleri her turda delinir; düzeltme şartı artık "örnek değil sınıf" (üretilmiş veri tablosu veya kategori/kural bazlı temizlik). (4) Disk hijyeni: `.claude/worktrees/` altında 14 eski oturum dizini duruyor (git kaydı yok, sadece dizin; `w14-…47d51d` dosya kilidiyle silinemedi) — W16 turu kapanınca topluca süpür.

**Kuyruk (sonrası):** 2E yaşam döngüsü+entitlement (senaryonun `pending` süpürme borcu da orada) → 2F onay+revizyon → 2G planlayıcı → W06 (PG18+Valkey+`pg_dump` taşıyan backup-runner compose profili; D1 kapısını kapatır). ADR kuyruğu 5 kalem + ADR-008 ekleri aşağıda duruyor. Gerçek AI sağlayıcı seçimi W08 benchmark koşusu + route politikası ADR'ı sonrası — **hiçbir ücretli sağlayıcı benchmark'sız bağlanmaz.**

**Açık kararlar:** K1 faturalandırma (KULLANICININ, Phase 3 öncesi) · K3 pazar kapsamı (bloke etmiyor) · K6 ikinci yarı fotoğraf hattı. **Dağıtım kapıları D1–D3** STATUS'ta (D3: üretim kimlik adapter'ı yok, production Settings bugün kurulamıyor).

**Worktree'ler:** `w13-script-generation-733c80` **silindi** (dal merge edilmişti, temizdi); `w14-verification-followups-47d51d` W15'in aktif worktree'si (dal `slice/2c-tts-voiceover`) — W15 merge edilene kadar durur; `tech-methodology-review-fda5e7` PM'in worktree'si. Compose: ana stack `socialpilot-ai`; paralel işler `COMPOSE_PROJECT_NAME=sp-*` + worktree kökünden.

**PM'in kendi kuralları (kısa):** merge + ADR numarası + push PM'de · "bitirdi" beyanı ≠ commit, worktree'ye bak (`git -C <worktree> status`) · dal boşluğu `git log` ile ölçülmez · kabul kriterlerinde denenecek girdiler sayılır, kritik dedektörlerde atlatma senaryoları düşman gözüyle · doğrulama raporuna araç zinciri sürümleri yazılır · her oturum raporu okunduktan sonra STATUS/PM-NOTES aynı turda güncellenir.

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
| K3 | Pazar kapsamı TR / EU-global. **Çerçevelemem yanlıştı ve düzelttim:** Phase 2'yi bloke etmiyor — AI disclosure alanı Meta zorunluluğu nedeniyle TR-only'de de gerekli, C2PA kancası 2A'da açılıyor; K3 yalnızca işaretlemenin katılığını belirliyor | açık ama **bloke etmiyor** |
| ~~K4~~ | Kullanıcı düzenleme modeli → **parametrik düzenleme** olarak karara bağlandı (PM/mimar). Gerekçe Phase 2 planı §2'de; ADR'ı slice 2A yazacak | kapandı |

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

- ~~W07~~ **KAPANDI** (`c199b86`, ADR-013). Özet: → [W07-single-server-resilience.md](W07-single-server-resilience.md). Özet: iki kalem — (1) `compose.yaml`'a servis bazlı CPU/RAM limitleri, render worker'ına düşük öncelik ve concurrency sınırı, geçici dizin temizliğinin zorlanması; (2) sunucu dışına otomatik günlük `pg_dump` + geri yükleme provası (yedek test edilmeden yedek sayılmaz). **Gerekçe:** tek sunucu tek arıza noktası ve üretim veritabanı git'te olmayacak. `compose.yaml` sahibi W01, o yüzden W01 merge sonrası. Deployment topolojisi ADR'ı ile birlikte yazılır.

- ~~W08~~ **KAPANDI** (`aea6a18`). `provider_usage` bulgusu W04 slotuna alındı. Özet: → [W08-provider-benchmark-harness.md](W08-provider-benchmark-harness.md). Özet: gerçek sağlayıcı bağlanmadan ÖNCE — PRD §40.5'teki sabit medya seti (dikey/yatay, gürültülü, Türkçe konuşma, karanlık, titrek, insan yüzü, öncesi/sonrası, logo, küçük metin) üzerinde kabiliyet başına sağlayıcı karşılaştırması: Türkçe ASR doğruluğu, VLM sahne isabeti, Türkçe TTS prozodisi, marka tonu ve yasak kelime uyumu, katı JSON şema sadakati, kabiliyet başına gerçek maliyet. **Gerekçe:** ölçülmeden bağlanan ilk sağlayıcı varsayılan hâline gelir ve kabiliyet routing'inin amacı kaybolur. PRD §17.2'nin aday tablosu maliyete göre seçilmiş; Türkçe kalitesi ve veri bölgesi tartılmamış.
- **Phase 2 kapısı öncesi değerlendirme:** durable execution (DBOS/Temporal) ve LiteLLM'in kabiliyet portları altına konması.

## Yetki sınırı (2026-07-30 itibarıyla)

Kullanıcı "en mantıklısı ve en doğrusuyla devam et her zaman" dedi. Bunun pratik anlamı:

- **PM karara bağlar:** mimari kararlar (ADR), iş emri sırası ve kapsamı, merge, ADR numaralandırma, slice kapanışında push, geri dönüşü kolay teknik tercihler.
- **Kullanıcıya kalır:** para ve hukuk sonucu doğuran, geri dönüşü pahalı olanlar — **K1** (faturalandırma modeli: mağaza komisyonu ve store ilişkisi), **K3** (pazar kapsamı: EU'ya girmek AI Act Md. 50 yükümlülüğü doğurur). Bunlar için karar hazırlanır, önerilir, ama tek başına alınmaz.
- **K4** (kullanıcı düzenleme modeli) Phase 2 timeline şemasıyla birlikte kararlaştırılır — önerisi hazır, ama önünde timeline işi yokken karara bağlamak erken olur.
- Geri dönüşü zor veya dışa dönük her yeni işlem tipi (üretim deploy, ödeme, dış platforma içerik gönderme) yine ayrıca sorulur.

## PM'in kendi hataları (kayda geçsin)

0. **Kabul kriterini testin şekline değil sonucuna bağlamalıydım.** W04'ün kriteri "parasal alanlarda `float` bulunmadığını doğrulayan bir test var" diyordu; oturum testi yazdı, test geçti, ama test yalnızca **kesirli** float'u deniyordu — integral float (`165.0`) açığı görünmez kaldı ve bağımsız doğrulama yakaladı. **Ders:** bir değişmez için kriter yazarken *hangi girdilerin denenmesi gerektiğini* say (integral float, kesirli, string, bool, taşma), yoksa "test var" kriteri kendini onaylayan bir ifadeye dönüşür.


1. **Mimari dokümana güvenip şemayı doğrulamadım.** W08'in kabul kriteri 5'ini `provider_usage` tablosu üzerine yazdım çünkü `ai-provider-routing.md` onu şimdiki zamanda anlatıyordu. Tablo hiç yoktu. Yürüten oturum yakaladı ve doğru davrandı (migration slotu olmadığı için eklemedi, `ProviderUsageRecord` şeklini bıraktı, bildirdi). **Ders:** iş emrinde bir tablo/alan/endpoint adı geçiyorsa, WO'yu yazmadan önce onun **kodda** var olduğu doğrulanır. Doküman niyeti anlatır, şema gerçeği söyler.

2. **W07 ve W08'e aynı dosyayı (`Makefile`) verdim.** Dosya-ayrıklığı garanti etmem gerekirken iki WO'da da Makefile'a dokunma izni bıraktım. Merge'i PM yaptığı için zararsız kaldı ama kural ihlaliydi.

## Sıradaki iş (PM kuyruğu, güncel)

1. ~~W04~~ **KAPANDI** (`5addf69`). Bulgusu: `approver` rolü enum'da yok → W10'a. Yakaladığı regresyon: `AssetResponse` ad çakışması FastAPI'yi iki şemayı da tam nitelikli ada çevirmeye zorluyordu (üretilmiş istemcileri bozan kontrat değişikliği) — yeniden adlandırıldı + `__` içermeme regresyon testi eklendi. Eski özet: → [W04-brand-catalog.md](W04-brand-catalog.md). Migration slotu onda. **Karar:** şema borcu üç kalemi W04'ten çıkarıldı ve W10 oldu — brands diff'i odaklı kalsın, üç ilgisiz kalem karışmasın. W10 slotu W04 kapanınca alır.
2. ~~W05~~ **KAPANDI** (`5addf69`, ADR-014). Eski özet: → [W05-opentelemetry.md](W05-opentelemetry.md). Kritik kısım redaksiyon: auto-instrumentation httpx URL'lerini yazdığı için presigned URL span'lere sızabilir; sentinel testi zorunlu kılındı.
3. **W06 — PostgreSQL 18 + Valkey.** W05 sonrası (`compose.yaml` çakışmasın). Dikkat: W07'nin kaynak limitleri ve `cpu_shares` öncelik sırası korunmalı.
4. **W10 — şema borcu** (yazılacak): `provider_usage` tablosu, `storage_upload_id` genişletmesi, fotoğraf analiz enum'u. W04 slotu boşalınca.
4. **Codex doğrulaması** `main` üzerinde: W07'nin yedek/geri yükleme döngüsü ve scratch guard'ı, W08'in ground-truth metrik hesabı ve maliyet tavanı. Bunlar iddia edilen ama bağımsız sınanmamış yüzeyler.
5. **Phase 2 kapısı öncesi:** K3 ve K4 cevaplanmalı; durable execution (DBOS/Temporal) ve LiteLLM değerlendirmesi; `RenderPort` ADR'ı (K5'in gereği).

## Oturum yeniden kullanımı

Kural [README.md](README.md)'de. PM için özeti: **slice başına yeni oturum**, aynı oturum yalnızca aynı slice'ın düzeltme turu için. Ve **worktree'yi merge'de değil, doğrulama da bittiğinde sil** — bugün W07/W08'in worktree'lerini Codex bitmeden sildim, o yüzden bulguların düzeltmesi taze oturumla yapılacak.

## Sıradaki iş (2026-07-30 sonu itibarıyla)

- ~~W10~~ **YAZILDI** → [W10-schema-debt.md](W10-schema-debt.md). Özet: şema borcu — Slot serbest. Dört kalem: `provider_usage` tablosu, `storage_upload_id` genişletmesi (W01'in kontrol-objesi geçici çözümünü kaldırır), fotoğraf analiz durumu enum'u, **`approver` rolü** (`BusinessRole`'a eklenmesi; W04'ün "her rol için cevap tanımlı" testi eşlemeyi zorlayacak). İş emri yazılacak.
- **W06 — PostgreSQL 18 + Valkey. BEKLETİLDİ.** `compose.yaml` serbest ama hiçbir şeyi bloke etmiyor; `uuidv7()` ve Valkey lisansı gerçek kazançlar ama Phase 2'nin önüne geçmeyi hak etmiyorlar. Phase 2'den sonra. W07'nin kaynak limitleri ve `cpu_shares` sırası korunmalı.
- **Codex doğrulaması: W07 ve W08 GEÇTİ** (ikisi de "teslim edilebilir"). Değerli olan yöntemi: W08'de metrikleri harness'ın fonksiyonlarını **çağırmadan** kendi stdlib betiğiyle yeniden hesaplayıp birebir eşleşme aldı; W07'de yedeği gerçekten geri yükleyip satır sayılarını kaynakla karşılaştırdı. **W04 ve W05 doğrulaması da bitti:** W04'te 4/5 geçti, biri **yüksek ve açık** — parasal alanda integral float (`165.0`) kabul edilip sessizce coerce ediliyor, kesirli (`165.5`) reddediliyor. W05'te 3/4 geçti; trace zinciri outbox üzerinden worker'a geçmiyor. İkisi de **W12**'ye alındı. Eski not: — cross-tenant, para biriminde kayan nokta, cursor sayfa atlama; telemetride presigned URL sızıntısı ve kapalıyken sıfır maliyet.
- ~~Phase 2 kapısı~~ **AÇILDI.** Plan yazıldı, K4 karara bağlandı, K3 çerçevelemesi düzeltildi. 2A kapandı (`258ddc3`, ADR-015/016). 2B iş emri: [W13](W13-script-generation.md) — kalbi doğrulanmış alan bindirmesi: model fiyat/tarih yazamaz, deterministik kalıp tespitiyle yakalanır, kod yerleştirir. Sıradaki 2C (TTS) → 2D (QC) → 2E (yaşam döngüsü + entitlement) → 2F (onay + revizyon) → 2G (planlayıcı); her biri öncekinin merge'inden sonra yazılır.
- **Eski not:** Artık teknik önkoşullar hazır (marka/katalog verisi, gözlemlenebilirlik, benchmark aracı, gerçek medya hattı). Girmeden önce: **K3** ve **K4** cevaplanmalı, `RenderPort` ADR'ı yazılmalı (K5 gereği), durable execution (DBOS/Temporal) ve LiteLLM değerlendirilmeli.

## W13 sonrası kuyruk (2026-07-31)

- ~~W14~~ **KAPANDI** (`4e643fe`, 612 test). Eski özet: Codex bulguları (presigned URL log sızıntısı YÜKSEK — W01'in sentinel testi kütüphane logger'larını taramıyordu, ders: sızıntı testleri **tüm handler çıktısını** kapsamalı; patch fingerprint; 0011 downgrade koruması) + izin hizalaması + doküman borçları.
- **Codex W13: 1 KRİTİK açık** — fabrikasyon dedektörü sayısal kalıpları yakalıyor ama **Türkçe yazım varyantlarını** kaçırıyor. Düzeltme sıcak W13 oturumunda (aynı-slice kuralı). Ders: dedektör kabul kriterinde varyantları ben saymıştım ama *yazıyla* varyantları saymamıştım — sayılı girdi listesi de ancak listeleyenin hayal gücü kadar iyi; **kritik dedektörlerde 'atlatma' senaryolarını ayrı bir düşman gözüyle listelet**.
- **W14'ün kendi Codex turu** W13 düzeltmesinin doğrulamasıyla birleştirilecek (tek tur: imza redaksiyonunu atlatma + fabrikasyon varyantları).
- ~~2C~~ **YAZILDI** → [W15-tts-voiceover.md](W15-tts-voiceover.md); W13 düzeltmesi merge edildi (`7621b61`, 628 test). **Yeni ders:** W13 oturumu düzeltmeyi tamamlayıp commit'lemeden bıraktı — 'bitirdi' beyanı commit değil; PM diff'i gözden geçirip finalize etti. Sıradaki: birleşik Codex turu (W13 varyant düzeltmesi + W14 imza redaksiyonu) W15 ile paralel; üretim davranışı W13 kural onayı 1'deki genel kurala uyacak.
- `forbidden_matcher` birleştirmesi (Türkçe İ/I katlaması timeline tarafına) → **2D (QC) slice'ına** not düşüldü.
- D3 dağıtım kapısı eklendi: üretim kimlik adapter'ı (Firebase/OIDC) yok.

## Öğrenilen dersler (tekrarlanmasın)

1. **Çift iş.** İki oturum `258439d` base'inden aynı slice'ı yaptı; `c43ccad` silinmek zorunda kaldı. Panzehir: WO'da "dokunulacak dosyalar" ilanı + [STATUS.md](../STATUS.md) dosya sahipliği tablosu + tetiklemeden önce durumun `tetiklenmedi` olduğunu doğrulama.
2. **İş emirleri yan yana okunmadan dağıtılmaz.** İlk üç WO'yu yazdıktan sonra `pyproject.toml`, `compose.yaml` ve `docs/index.md` üzerinde üç çakışma çıktı; W02 sıraya alındı, W06 ayrıldı, indeks sahipliği W03'e verildi.
3. **Dal isimleri içerikle uyuşmalı.** `feature/mobile-e2e-demo` medya özet API'si içeriyordu; worktree adı ile checkout edilen dal farklıydı. Kural: `slice/<faz><harf>-<domain>`, slice kapanınca merge + dal silinir.
4. **Compose proje adı paylaşılıyorsa paralel doğrulama güvenilmez.** W02 kendi worktree'sinde `--build` yapınca `main`'in konteynerini kendi imajıyla değiştirdi; Codex de `main`'in kaynağını W02'nin araç zincirinden geçirip W01'e ait olmayan 21 hata bildirdi. Düzeltme: `compose.yaml` artık `${COMPOSE_PROJECT_NAME:-socialpilot-ai}`, kural [README.md](README.md)'de. **Ders:** bir doğrulama raporu, hangi araç zinciri sürümleriyle koştuğunu yazmıyorsa yeniden üretilemez.

5. **Paralel WO'lar aynı ADR numarasını alır.** W02 ve W09 ikisi de ADR-009'u aldı; W09'un dosyası merge sırasında ADR-011'e taşındı. WO'daki "dizini tara" uyarısı yetmiyor çünkü paralel dallar birbirinin dosyasını göremiyor. **Ders:** ADR numarasını **PM merge sırasında** verir; WO'lar dosyayı geçici bir adla yazıp raporda bildirir.

6. **Araç zinciri yükseltmesi ile kod slice'ı paralel çalışırsa birleşimi kimse doğrulamamış olur.** W09 py312/mypy 1.13/ruff 0.8'de yazıldı, W02 py313/mypy 2.3/ruff 0.16'ya taşıdı; ikisi de kendi dalında yeşildi ama birleşik durum kırmızıydı. **Ders:** platform yükseltmesi ile aynı anda kod slice'ı koşturuluyorsa merge sonrası uzlaştırma turu plana baştan yazılır — ya da yükseltme tek başına koşar.

7. **Kapı kapsamı tek tip olmalı.** `mypy .` `scripts/` dizinini denetliyordu ama `ruff check app tests migrations` denetlemiyordu; `scripts/` sessizce sapabiliyordu. Makefile'ın lint/format hedeflerine `scripts` eklendi. **Ders:** yeni bir üst düzey dizin eklendiğinde tüm kapıların kapsamı aynı anda güncellenir.

8. **Doküman durumu git'i yansıtmıyorsa git kazanır.** `main` 16 commit gerideyken dokümanlar Phase 0'ı anlatıyordu. [STATUS.md](../STATUS.md) her slice kapanışında aynı commit'te güncellenir.
