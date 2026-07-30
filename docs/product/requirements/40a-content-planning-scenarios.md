**İçerik planlayıcı ve içerik senaryoları** · PRD bölümleri: §13, §14

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 13. İçerik planlayıcı

## 13.1 Content obligation

Abonelik kalemleri doğrudan içerik oluşturmaz. Scheduler, abonelikten `content_obligation` kayıtları üretir.

Örnek:

```json
{
  "business_id": "uuid",
  "subscription_item_id": "uuid",
  "content_type": "instagram_reels",
  "period_start": "2026-07-27T00:00:00+03:00",
  "period_end": "2026-07-28T00:00:00+03:00",
  "planned_publish_at": "2026-07-27T18:30:00+03:00",
  "generation_deadline_at": "2026-07-27T12:00:00+03:00",
  "status": "planned"
}
```

## 13.2 Planlama öncelikleri

1. Aktif kampanyalar
2. Abonelik zorunlulukları
3. Marka içerik dengesi
4. Geçmiş performans
5. Medya yeterliliği
6. Aynı ürünün fazla tekrarlanmaması
7. Platform ve format uygunluğu
8. Sessiz saatler
9. Kullanıcı tercihleri
10. Özel günler; gerçek zamanlı doğrulama gerekiyorsa web veya doğrulanmış takvim kaynağı

## 13.3 Haftalık içerik karması

Sistem sürekli satış postu üretmemelidir.

Örnek dağılım:

- %25 ürün/hizmet
- %20 eğitici
- %15 marka hikâyesi
- %15 sosyal kanıt
- %10 eğlence/etkileşim
- %10 kampanya
- %5 kurumsal

Bu oran sektör ve kullanıcı ayarına göre değişebilir.

---

# 14. İçerik senaryoları

Her senaryo aşağıdaki ortak contract’a uymalıdır:

```json
{
  "scenario_code": "product_reels",
  "required_inputs": [],
  "optional_inputs": [],
  "selection_rules": [],
  "script_schema": {},
  "timeline_template": {},
  "quality_checks": [],
  "fallback_strategy": [],
  "approval_requirement": "configured"
}
```

## 14.1 Günlük ürün Reels’i

**Amaç:** Ürün görünürlüğü ve satış.

Gerekli medya:

- Ürün yakın planı
- Hazırlık/kullanım
- Sonuç/servis
- Logo veya CTA kartı

Akış:

```text
Ürün seç
→ Aktif fiyat/kampanya doğrula
→ Uygun sahneleri sırala
→ Hook üret
→ 10–30 saniyelik senaryo
→ Seslendirme
→ Timeline
→ Render
→ Marka/fiyat QC
→ Onay/yayın
```

Fallback:

- Yeterli video yoksa fotoğraf tabanlı motion video
- Ürün yakın planı yoksa çekim önerisi
- Kritik medya eksikse düşük kaliteli içerik üretmek yerine görevi beklet

## 14.2 Haftalık premium reklam videosu

- 30–60 saniye
- Çoklu video
- Profesyonel seslendirme
- Gelişmiş hikâye
- Birden fazla ürün veya hizmet
- Gerekirse generative B-roll
- A/B açılış varyasyonu
- Reklam kullanımına uygun hak ve lisans kontrolü

## 14.3 Günlük X gönderisi

Girdiler:

- Marka tonu
- Günün içerik planı
- Ürün/kampanya
- Karakter limiti
- Link/UTM
- Yasak konular

Çıktılar:

- Ana gönderi
- Alternatif varyasyon
- Görsel önerisi
- Gerekirse thread devamı

X paylaşımı ile X reklamı ayrı görevdir.

## 14.4 X thread

- 3–8 gönderi
- Tek ana tema
- İlk gönderide hook
- Son gönderide CTA
- Zincir kimliklerinin sırayla saklanması
- Yarım kalan thread için retry ve cleanup

## 14.5 Hikâye serisi

- 3–5 slide
- 9:16
- Başlık
- Ürün/hizmet
- Sosyal kanıt
- CTA
- Platform sticker alanı bırakılabilir
- Metin safe-area kontrolü

## 14.6 Carousel

- 4–10 sayfa
- Kapak
- Problem
- Çözüm
- Detaylar
- Kanıt
- CTA
- Renk ve tipografi bütünlüğü
- Metin render katmanında yazılır

## 14.7 Konuşmalı video temizleme

- ASR
- Dolgu kelime/sessizlik tespiti
- Jump cut
- Aktif konuşmacı kadrajı
- Otomatik altyazı
- Gürültü azaltma
- Müzik ducking
- Kullanıcının gerçek sesi korunur
- Ek seslendirme varsayılan olarak eklenmez

## 14.8 Seslendirmeli ürün reklamı

- Önce senaryo ve seslendirme oluşturulur
- Ses segment süreleri çıkarılır
- Her cümleye semantik olarak uygun kesit atanır
- Müzik ve ortam sesi seslendirmeye göre kısılır
- CTA sonunda net görünür

## 14.9 Öncesi/sonrası

- `before`, `process`, `after` sahneleri sınıflandırılır
- Tarih/sıra doğrulanır
- Yanıltıcı değişim yaratacak görüntü manipülasyonu yapılmaz
- Sağlık ve güzellik sektörlerinde iddia kuralları sıkılaştırılır

## 14.10 Kurumsal tanıtım

- Dış cephe/tabela
- Ekip
- Süreç
- Müşteri deneyimi
- Mekân
- Değer önerisi
- İletişim
- Logo

## 14.11 Müşteri yorumu

- Kullanım izni kaydı
- Yorumun kaynağı
- İsim/görsel yayın izni
- Abartılı veya değiştirilen anlam olmamalı
- Hassas bilgi redaksiyonu

## 14.12 Eğitici içerik

- Yalnızca doğrulanmış marka bilgisi
- Sağlık/finans/hukuk iddiasında uzman onayı
- Kaynak gerektiren iddialar için yayın engeli veya insan onayı
- CTA zorunlu değildir

## 14.13 Kampanya duyurusu

- Kampanya kaydı zorunlu
- Tarih ve fiyat doğrulaması
- Otomatik bitiş
- Stok bittiğinde durdurma
- Şube geçerliliği

## 14.14 Lansman

Aşamalar:

1. Teaser
2. Tanıtım
3. Lansman günü
4. Satış
5. Yeniden hedefleme
6. Sonuç raporu

## 14.15 Trend uyarlaması

- Trend verisi güncel kaynaktan gelmelidir
- Markaya uygunluk kontrolü
- Telifli ses/müzik otomatik kullanılmamalı
- Marka güvenli değilse trend reddedilmeli
- Kullanıcı “trend içeriklerine izin ver” seçeneğini açmalıdır

## 14.16 Organik kazananı reklama çevirme

- Organik içerik en az belirlenen gözlem süresini tamamlar
- Marka medyanına göre performans karşılaştırılır
- Reklam kullanım hakkı kontrol edilir
- Kullanıcı bütçe kuralları doğrulanır
- Kampanya `PAUSED` oluşturulur
- Onay politikasına göre aktive edilir
