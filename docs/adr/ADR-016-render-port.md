# ADR-016: `RenderPort` ve render adapter sınırı

**Status:** Accepted
**Date:** 2026-07-30
**Karar veren:** PM/mimar oturumu (K5 doğrultusunda) · yürüten: W11 (slice 2A)


## Context

Render, hattın dağıtım şekli hâlâ açık olan tek parçası. [STATUS K5](../STATUS.md) ve
[ADR-013](ADR-013-single-server-deployment-topology.md) tek sunucuda sınırlı-eşzamanlılıklı
FFmpeg'e karar verdi, ama aynı kararlar hacim eşiğinde üç seçeneği açık bıraktı: yönetilen
render servisi (sıfır idle), sıfıra ölçeklenen burst compute, ucuz dedike/spot CPU.

Bu seçenekler yalnızca **render çağrısı bir kabiliyet portunun arkasındaysa** açık kalır.
FFmpeg çağrıları render worker'ının içine gömülürse seçim sessizce ve kalıcı olarak yapılmış
olur; PRD §17.2 bunu zaten öngörüyor ("Montaj: FFmpeg | alternatif: Yönetilen render servisi").

Phase 1 deseni de bunu destekliyor: her dış kabiliyet (kimlik, depolama, sahne tespiti, ASR,
VLM) ADR-004 uyarınca bir port arkasında. Render'ın istisna olması için bir sebep yok — aksine,
maliyet sıralamasında CPU'nun dördüncü sırada olması (K5) portu daha da değerli kılıyor, çünkü
taşınabilirlik bir gün doğrudan para demek.

## Decision

**`RenderPort` birinci sınıf kabiliyet portudur** ([`app/modules/content/render.py`](../../services/api/app/modules/content/render.py)).
FFmpeg **bir** adapter'dır ([`app/infrastructure/render/ffmpeg.py`](../../services/api/app/infrastructure/render/ffmpeg.py)),
port'un kendisi değil.

### 1. Port ne alır, ne döner

Port **`RenderPlan`** alır: tamamen çözümlenmiş, sağlayıcıdan bağımsız ve **zaten doğrulanmış**.
Plan'da doğrulanmış her değer bir tenant kaydından okunmuş, her kaynak worker'ın materialize
ettiği **yerel bir dosya**dır. Port'a imzalı URL, credential veya depolama anahtarı geçmez.

`RenderResult` döner: master + preview + thumbnail eserleri, dosyanın kendisinden **gözlemlenmiş**
teknik özet (istenen değil, ffprobe'dan okunan) ve provenance durumu.

### 2. Kaynak erişimi: materializer, ikinci bir indirme yolu değil

Worker W09'un `MediaMaterializerPort`'unu kullanır. Sistemde tek indirme yolu vardır ve imzalı
URL depolama adapter'ının dışında hiç var olmaz. Her asset **kendi alt dizinine** materialize
edilir: materializer hedef dosya adını object key'in uzantısından türetir, dolayısıyla aynı
dizindeki iki `.mp4` kaynak birbirini sessizce ezerdi.

Yönetilen bir render servisi adapter'ı eklenirse aynı planı alır ve kendi transferinden
sorumludur; port sözleşmesi değişmez.

### 3. Kabiliyetler bildirilir, doğrulama onları zorlar

Adapter `RenderCapabilities` bildirir: desteklenen profiller, crop modları, geçişler, ses
kaynakları, altyazı kaynakları, azami süre, azami video track sayısı. §18.3 doğrulaması
timeline'ı **render başlamadan** bu kümeye karşı denetler.

Bu, PRD §19.2'nin istediği şeydir ("platform limitleri adapter capability endpoint'inden
kontrol edilmelidir") ve pratik faydası şu: bu slice'ın FFmpeg adapter'ı `fade` geçişini ve
voiceover/music ses kaynaklarını **bildirmiyor** (2C ve sonrası). Sonuç, yarı yolda çöken bir
job değil, dokümante kodlu temiz bir reddir.

### 4. Metin asla komut dizesine girmez

Overlay metni tenant'ın kendi literal'i veya bir kampanya kaydından kopyalanmış olabilir ve
`drawtext`'in kendi ifade dili vardır: iki nokta seçenek ayrıştırmasını değiştirir, `%{...}`
çizim anında genişletilir. Bu yüzden metin **dosyaya yazılır** ve `textfile=` + `expansion=none`
ile referans verilir; byte'lar çizilir, hiç ayrıştırılmaz. Altyazılar aynı yoldan, üretilen bir
ASS dosyasıyla gider — ASS stili kendi içinde taşır, böylece stil filter-string ayrıştırıcısından
geçmek zorunda kalmaz.

### 5. Süreç hijyeni

Her alt süreç `shell=False`, timeout'lu ve **sessiz**: diagnostic'ler özel bir geçici dosyaya
yönlendirilir, yalnızca *boyutu* denetlenir, *içeriği* hiç okunmaz. FFmpeg stderr'e girdi
yollarını ve metadata'yı basar; bunların hiçbiri log satırına, hata gövdesine veya span'e ait
değil. Herhangi bir başarısızlık, koşunun oluşturduğu her dosyayı siler.

### 6. Domain saflığı test edilir

`app/modules/content/` içinde `ffmpeg`/`ffprobe`/`subprocess`/`libx264`/`drawtext`/`popen`
geçen **çalıştırılabilir kod** yoktur ve `app.infrastructure` import edilmez. Bir unit test
dosyaları tokenize ederek bunu zorlar: docstring'de sınırı *anlatmak* serbesttir, ona
*bağlanmak* değil.

### 7. Fabrika

`create_render(settings)` `create_storage`/`create_materializer` desenini izler.
`RENDER_ADAPTER=fake` üretimde hem `Settings` hem fabrika tarafından reddedilir — placeholder
dosya yazan bir dağıtım her metrikte sağlıklı görünürken hiçbir şey teslim etmez, dolayısıyla
bu *olası* değil *imkânsız* olmalıdır. Fake adapter gerçek adapter'la **aynı kabiliyetleri**
bildirir; testin kabul ettiği timeline'ı üretim de kabul eder.

## Consequences

- Dağıtım seçeneği açık kaldı: ikinci adapter yazmak, mevcut hiçbir çağrıyı değiştirmeden yeni
  bir dosya eklemektir (ADR-004'ün "yeni sağlayıcı = yeni dosya" kuralı).
- Doğrulama ile kabiliyet aynı yerde buluştuğu için "desteklenmiyor" bir çalışma zamanı sürprizi
  değil, dokümante bir ret oldu.
- Maliyet: plan'ı kurmak ekstra bir çeviri katmanı. Karşılığında domain FFmpeg'in filtre
  dilinden, geçici dosya düzeninden ve hata metinlerinden tamamen habersiz.
- **Bu slice yalnızca tek bir video track destekliyor** (`max_video_tracks=1`). Kompozisyon
  gerektiğinde kabiliyet artar, port değişmez.

## Rejected alternatives

- **FFmpeg'i doğrudan worker'a gömmek:** reddedildi. K5'in üç dağıtım seçeneğini kapatır ve
  ADR-004'ün her dış kabiliyet için uyguladığı desenden render'ı gerekçesiz istisna yapar.
- **Port'a imzalı URL geçirmek** (adapter kendi indirsin): reddedildi. İkinci bir indirme yolu
  doğurur, imzalı URL'i depolama adapter'ının dışına taşır ve W01'in sızıntı önlemlerini
  render adapter'ında yeniden kurmayı gerektirir.
- **Metni filtre dizesine kaçış karakterleriyle gömmek:** reddedildi. Kaçış doğru yazılsa bile
  tek bir gözden kaçan karakter enjeksiyon demek; `textfile=` + `expansion=none` sorunu
  sınıf olarak ortadan kaldırıyor.
- **Altyazı için SRT + `force_style`:** reddedildi. `force_style` değeri virgül içerir ve filtre
  dizesinde tırnaklanmak zorundadır; ASS stili kendi dosyasında taşıyarak bu ayrıştırma
  yüzeyini tamamen kaldırıyor.
- **Kabiliyetleri konfigürasyondan okumak:** reddedildi. Kabiliyet adapter'ın *kodunun*
  özelliğidir; konfigürasyona alınırsa yanlış ayar bir job'ı yarı yolda çökertir.
