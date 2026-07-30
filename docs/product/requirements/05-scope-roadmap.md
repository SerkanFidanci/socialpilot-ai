**Kapsam, fazlar ve kabul kriterleri** · PRD bölümleri: §3, §44, §45

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 3. Kapsam

## 3.1 İlk üretim kapsamı

- iOS ve Android mobil uygulama
- E-posta, Google ve Apple ile giriş
- Bir kullanıcı altında bir veya birden fazla işletme
- İşletme ve marka profili
- Fotoğraf ve çoklu video yükleme
- Video sahne analizi
- Konuşma çözümleme ve altyazı
- Çoklu videodan kesit seçme ve birleştirme
- AI senaryo ve seslendirme
- Reels, hikâye, post, carousel ve X gönderisi
- Esnek abonelik oluşturucu
- Kullanım hakkı/entitlement takibi
- İçerik takvimi
- Kullanıcı onayı
- Instagram/Meta ve X içerik yayınlama
- Meta Ads, Google Ads ve X Ads hesap bağlantısı
- Güvenli reklam kampanyası oluşturma
- Günlük performans toplama
- Bildirimler
- Operasyon yönetim paneli
- n8n tabanlı iş otomasyonları
- FastAPI ve worker tabanlı medya işleme

## 3.2 Sonraki aşama

- YouTube Shorts yayınlama
- Google Business Profile yönetimi
- Merchant Center ve ürün feed entegrasyonu
- TikTok bağlantısı
- CRM entegrasyonları
- E-ticaret stok ve sipariş entegrasyonları
- Tam otomatik bütçe dağıtımı
- Ajans/white-label paneli
- Markaya özel onaylı ses klonlama
- İnsan editör pazaryeri
- Çok dilli küresel kullanım
- C2PA/içerik kimlik doğrulama

## 3.3 MVP dışında bırakılacaklar

- Sıfırdan tam teşekküllü mobil video editörü
- Kullanıcının zaman çizelgesinde kare kare manuel montaj yapması
- Lisanssız müzik kütüphanesi
- Politik reklamlar
- Sağlık, finans, kredi, bahis veya hukuki açıdan yüksek riskli reklam kategorileri
- Kullanıcının açık onayı olmadan ses klonlama
- Tanınmış kişilerin yüz/ses taklidi
- Kullanıcının reklam hesabındaki mevcut tüm kampanyaları kontrolsüz değiştirmek

---

# 44. Uygulama geliştirme aşamaları

## Aşama 0 — Temel platform

- Monorepo
- CI
- FastAPI
- PostgreSQL
- Auth
- Business/role
- Object storage
- Job altyapısı
- Observability
- Admin skeleton

**Çıkış kriteri:** Kullanıcı giriş yapar, işletme oluşturur, medya için resumable upload yapar.

## Aşama 1 — Marka ve medya

- Marka profili
- Ürünler
- Video validation/proxy
- Scene detection
- ASR
- VLM adapter
- Sahne kütüphanesi

**Çıkış kriteri:** 10 video yüklenir; sahneler, transcript ve etiketler görünür.

## Aşama 2 — İçerik üretimi

- Senaryo
- TTS
- Timeline
- FFmpeg
- QC
- Preview
- Revision

**Çıkış kriteri:** Çoklu videodan seslendirmeli profesyonel Reels üretilir.

## Aşama 3 — Abonelik ve hak motoru

- Subscription composer
- Credit quote
- Entitlement windows
- Usage reserve/consume/refund
- Store adapter
- App Store/Play doğrulama

**Çıkış kriteri:** “Günlük 1 Reels + haftalık 1 premium video + günlük 1 X” takvime dönüşür.

## Aşama 4 — Sosyal yayınlama

- Meta/Instagram OAuth
- X OAuth
- Capability
- Scheduled publish
- Retry
- Metrics

**Çıkış kriteri:** Onaylı içerik planlanan saatte yayınlanır.

## Aşama 5 — Reklam temel

- Meta Ads
- Google Ads
- Campaign blueprint
- Paused create
- Approval
- Budget guard
- Metrics

**Çıkış kriteri:** Kullanıcı sınırlarıyla güvenli kampanya oluşturulur ve aktive edilir.

## Aşama 6 — Optimizasyon

- Performance rules
- Recommendation
- Creative fatigue
- A/B
- Auto pause
- Budget changes
- Full audit

**Çıkış kriteri:** Sistem düşük performanslı kampanyayı öneri/onay politikasıyla değiştirir.

## Aşama 7 — X Ads ve gelişmiş platformlar

- X Ads access
- Campaign adapter
- Conversion tracking
- Additional connectors

---

# 45. İlk üretim kabul kriterleri

## Mobil

- Uygulama crash-free hedefi izlenir
- Yükleme ağ kesintisinden sonra devam eder
- İş durumu kaybolmaz
- Ön izleme hızlı açılır
- Türkçe hata mesajı
- Hesap silme görünür

## İçerik

- 10 farklı videodan kesit seçebilir
- Dikey render
- Türkçe altyazı
- Türkçe seslendirme
- Logo
- CTA
- Fiyat doğrulaması
- En az bir revizyon
- Teknik hatada hak iadesi

## Abonelik

- Günlük/haftalık/aylık sıklık
- Aktif günler
- Kalite seçimi
- Otomasyon seçimi
- Kredi hesaplama
- Store transaction doğrulama
- Entitlement server-side

## Sosyal

- OAuth güvenli
- Bağlantı health
- Scheduled publish
- Duplicate yok
- Published external ID
- Retry

## Reklam

- Kampanya önce paused
- Günlük/aylık limit
- Kullanıcı onayı
- Audit
- Emergency stop
- Tracking kontrolü
- Duplicate creation yok
