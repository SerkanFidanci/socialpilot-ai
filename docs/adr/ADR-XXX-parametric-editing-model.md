# ADR-XXX: Parametrik düzenleme veri modeli

**Status:** Accepted
**Date:** 2026-07-30
**Karar veren:** PM/mimar oturumu (K4) · yürüten: W11 (slice 2A)

> **Numara PM tarafından verilecek.** Bu dosya `ADR-XXX` olarak yazıldı ve indekse eklenmedi.

## Context

[K4](../STATUS.md) karara bağlandı: kullanıcı piksel sürüklemez, zaten JSON olan timeline'ın
parametrelerini değiştirir. Bu ADR o kararın **veri modelini** kurar; UI'ı değil, onay/revizyon
akışını da değil (2F).

Karar ilk bakışta bir ürün tercihi gibi görünür — aslında iki mühendislik sonucu var ve
ikisi de bu slice'ta ölçülebilir:

1. **§18.3 doğrulaması ancak sınırlı bir düzenleme uzayında zorlanabilir.** Safe-area kuralı
   metnin nereye düşeceğini önceden bilmeyi gerektirir; yasak-kelime kuralı metnin ne olduğunu;
   logo kuralı hangi görselin çerçeveye girdiğini. Serbest x/y, serbest font ve serbest
   kompozisyonla bu soruların hiçbirinin deterministik cevabı yoktur.
2. **Parametrik düzenleme hiç sağlayıcı çağrısı yapmaz.** Bir revizyon aritmetiktir, fatura
   değil. Bu, "saf yeniden render yeni hak tüketmez" kuralının (PRD §12.8, plan §2) hem
   gerekçesi hem de uygulanabilirlik koşulu.

## Decision

### 1. Doküman kapalıdır — bilinmeyen anahtar hatadır

`parse_timeline` PRD §18.2'nin tanımladığı anahtarları kabul eder, geri kalan **her** anahtarı
`TIMELINE_UNKNOWN_FIELD` ile reddeder.

Bu, kararın kod tarafından zorlandığı asıl yerdir. Bir overlay'e `{"x": 120, "y": 400}`
konulduğunda bu "yok sayılan bir alan" değil, bir **parse hatası**dır. Bilinmeyen anahtarları
sessizce atmak iki şeyi bozardı: dokümanın anlamı okuyucusunun sürümüne bağlı hale gelirdi, ve
saklanmış bir timeline'da bir şey yapıyormuş gibi duran ölü alanlar birikirdi.

### 2. Konum: 9'lu ızgara çapası. Ham koordinat alanı yok

`OverlayAnchor` dokuz hücreden ibarettir. Renderer metni güvenli dikdörtgenin *içine* çapalar,
dolayısıyla dikdörtgene sığan bir blok her zaman yasal olarak yerleştirilebilir ve sığmayan
hiçbir zaman yerleştirilemez — doğrulamanın çapayı bilmesine gerek kalmaz.

### 3. Stil: kapalı token registry'si. Serbest font/renk yok

`style_id` `TEXT_STYLES`/`LOGO_STYLES` içinden gelir. Font boyutu **canvas yüksekliğine oranla**
tanımlıdır, mutlak piksel değil. Gerekçe doğrudan §18.3'e bağlı: safe-area kuralı ancak
renderer metnin genişliğini önceden kestirebiliyorsa deterministik olabilir, ve keyfî font
boyutu geçebilen bir çağıran metni her zaman kadraj dışına itebilir.

### 4. Metin: `literal` mı, `verified_field` mı — ve ikisi karışmaz

`text_source` PRD §18.2'nin noktalı referans biçimini birebir kullanır
(`literal`, `verified_campaign.title`, `verified_product.price`, `verified_cta.text`,
`verified_campaign.legal_text`).

- `literal` → yanında `text` taşır, markanın yasak listesine karşı denetlenir.
- `verified_*` → yanında **yalnızca** `reference_id` taşır ve değer
  `product_prices`/`campaign_offers`/`approved_ctas`'tan çözülür.

**Doğrulanmış bir slota düzyazı koymak parse hatasıdır** (`TIMELINE_VERIFIED_FIELD_NOT_LITERAL`).
Bu tam olarak uydurulmuş bir fiyatı kareye sokacak hamledir; sessizce atılan bir alan değil,
reddedilen bir dokümandır. Aynı kural patch yolunda da geçerli — aksi hâlde patch, "model asla
fiyat yazmaz" garantisinin etrafından dolaşmanın yolu olurdu.

Çözülemeyen referans (uydurma id, başka tenant'ın kaydı, yanlış türe işaret eden referans)
tek bir kuralla `TIMELINE_VERIFIED_FIELD_NOT_FOUND` olur; süresi geçmiş kampanya ayrı kodla
(`TIMELINE_CAMPAIGN_WINDOW_INVALID`) reddedilir.

### 5. Doğrulama metni çözer ve geri verir

`ValidationOutcome.resolved_texts` çizilecek **tam** dizgedir (satır kaydırması dahil). Plan
kurucusu metni yeniden çözümlemez. Eğer çözümleseydi, doğrulanmış bir fiyat yasak-kelime
denetiminden geçip kareye başka bir değer ulaşabilirdi; burada bu yapısal olarak imkânsız.

### 6. Zamanlama segment sınırına snap eder

Bir kesit noktası, tespit edilmiş sahne sınırına tolerans içindeyse o sınıra çekilir. Hareketin
ortasından kesmek hata gibi görünür ve analiz hattı sahne değişimlerinin nerede olduğunu zaten
biliyor. Tolerans dışında çağıranın tam değeri korunur — snap bir asistandır, kural değil.

### 7. Patch yerinde düzenlemez: her revizyon yeni bir satır

`content_timelines` append-only. Bir patch yeni bir revizyon satırı yazar (`root_id` soyu,
`parent_id` ata). Gerekçe: 2F'nin onay akışı reddedilen sürümle yerine önerilen sürümü
karşılaştırmak zorunda, ve 2E'nin hak denetimi "bu yeniden render yeni bir üretim değildi"
iddiasını **kanıtlayabilmeli**. Üzerine yazılmış bir dokümandan ikisi de cevaplanamaz.

Bir clip'in süresi değiştiğinde track bitişik olarak yeniden dizilir ve canvas süresi içeriği
takip eder. Bu slice'ta dokümanın "kasıtlı boşluk" kavramı yok; dürüst davranış track'i
kapatmaktır.

### 8. Hak tüketimi patch anında değil, tetikleyicide belirlenir

`render_outputs.trigger` (`initial` | `revision`) ve ondan türetilen `consumes_entitlement`
render **isteği anında** yazılır, okuma anında hesaplanmaz. 2E'nin defteri bu sütunu okuyacak;
kararın o anda ne olduğu denetlenebilir kalmalı.

## Consequences

- §18.3'ün safe-area, yasak-kelime, doğrulanmış-alan ve logo kuralları **deterministik kod**
  olarak yazılabildi ve render başlamadan çalışıyor; başarısızlık dokümante bir hata kodu.
- Revizyon maliyeti sıfır sağlayıcı çağrısı.
- Metin artık **satır kaydırılıyor** (en fazla 3 satır). Tek satır varsayımı gerçek içerikle
  hemen çöküyordu; kaydırma tanımı doğrulama ile renderer arasında tek bir yerde duruyor,
  dolayısıyla ölçülen blok ile çizilen blok ayrışamaz.
- **Kaçış kapısı ileride açılabilir ve yeri belli:** yeni bir `text_source` değeri, yeni bir
  stil token'ı veya yeni bir çapa kümesi eklemek şemayı genişletir. Açılmayacak olan ham
  koordinat alanıdır — açıldığı gün safe-area kuralı tanım gereği zorlanamaz hale gelir.
- Metin uzunluğu ve stil seçimi sınırlı; "tam istediğim gibi görünsün" isteyen kullanıcı
  karşılanmıyor. Bu bilinçli: PRD §3.3 kare kare montajı zaten dışlıyor.

## Rejected alternatives

- **Serbest x/y + serbest font:** reddedildi. §18.3'ün üç kuralını uygulanamaz hale getirir ve
  PRD §3.3 kapsam dışı bırakıyor.
- **Bilinmeyen anahtarları yok saymak (ileri uyumluluk için):** reddedildi. Ham koordinatın
  saklanmış dokümanda bir şey yapıyormuş gibi durmasına izin verirdi; sürüm alanı zaten
  şema evrimi için var.
- **Doğrulanmış slota yazılan düzyazıyı sessizce atmak:** reddedildi. Çağıran değerin
  geçtiğini sanır; reddetmek tek dürüst davranış.
- **Patch'i yerinde uygulamak (tek satır, `revision++`):** reddedildi. Onay karşılaştırması ve
  hak denetimi geçmiş olmadan cevaplanamaz; geçmiş depolama modeli olduğunda ikisi de bedava.
- **JSON Patch (RFC 6902) kullanmak:** reddedildi. Genel amaçlı bir patch dili tam olarak
  kapatmaya çalıştığımız şeyi geri açar: `/overlays/0/x` gibi keyfî bir yola yazma imkânı.
  Kapalı operasyon kümesi hem doğrulanabilir hem denetlenebilir.
