**Vizyon, prensipler ve mimari sınırlar** · PRD bölümleri: §0, §1, §2, §50

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

## 0. Belgenin kullanım talimatı

Bu belge fikir özeti değildir. Uygulamanın ilk üretim sürümünü geliştirmek için kaynak dokümandır.

Codex veya Claude Code bu belgeyi kullanırken:

1. Önce tamamını okumalıdır.
2. Mimari kararları değiştirmeden önce bir ADR (Architecture Decision Record) oluşturmalıdır.
3. Sistemi tek seferde kurmaya çalışmamalıdır.
4. Her aşamada çalışan bir dikey dilim üretmelidir.
5. Her modül için migration, API, servis, test, hata kodu ve gözlemlenebilirlik eklemelidir.
6. Üçüncü taraf sağlayıcıları doğrudan iş mantığına gömmemelidir.
7. Her dış servis için adapter/interface kullanmalıdır.
8. Para harcatan reklam işlemlerinde idempotency, onay ve bütçe guardrail katmanlarını atlamamalıdır.
9. API anahtarlarını mobil uygulamaya, git deposuna veya n8n workflow JSON’una düz metin olarak koymamalıdır.
10. Bu belgedeki platform ve model isimlerini değişebilir kabul etmeli; kabiliyet tabanlı yönlendirme kullanmalıdır.

---

# 1. Ürün vizyonu

Uygulama; küçük ve orta ölçekli işletmelerin fotoğraf, video, ürün, hizmet, marka ve kampanya bilgilerini öğrenerek sosyal medya içeriklerini üretir, düzenler, planlar, yayınlar, reklam kampanyalarını kullanıcının belirlediği sınırlar içinde oluşturur ve performansa göre optimize eder.

Temel vaat:

> İşletmeni tanıt, fotoğraf ve videolarını yükle, paylaşım ve reklam sınırlarını belirle. Sistem içerikleri hazırlasın, videoları profesyonel şekilde kurgulasın, paylaşsın ve reklam bütçesini verdiğin kurallar içinde yönetsin.

Ürün yalnızca bir “AI post üretici” değildir. Ürün şu beş sistemin birleşimidir:

1. **Marka hafızası**
2. **Medya analiz ve video kurgu motoru**
3. **Esnek abonelik ve kullanım hakkı motoru**
4. **Çoklu platform yayınlama ve reklam yönetimi**
5. **Performanstan öğrenen karar sistemi**

---

# 2. Ürün prensipleri

## 2.1 İnsan kontrolü

- Sistem varsayılan olarak kullanıcı onayıyla çalışır.
- Kullanıcı isterse günlük rutin içerikleri otomatikleştirebilir.
- Fiyat, indirim, stok, adres, sağlık iddiası ve yasal beyan gibi kritik bilgiler AI tarafından uydurulamaz.
- Reklam kampanyaları ilk oluşturulduğunda `PAUSED` durumda açılır.
- Tam otomatik reklam modu güvenilir veri oluşmadan aktif edilemez.

## 2.2 Kaynak doğruluğu

- Marka ve ürün verileri doğrulanmış kayıtlar üzerinden kullanılır.
- AI çıktısı gerçek veriyle çelişirse gerçek veri kazanır.
- Kampanya bitiş tarihi geçmişse içerik yayınlanamaz.
- Stok entegrasyonu varsa stokta olmayan ürün için satış reklamı açılamaz.

## 2.3 Sağlayıcı bağımsızlığı

- Qwen, DeepSeek, MiniMax, Seedance, Kling, OpenAI veya başka modeller doğrudan domain koduna yazılmaz.
- Kod `video_understanding`, `script_generation`, `tts`, `image_edit`, `video_generation` gibi kabiliyetleri çağırır.
- Model kimlikleri veritabanındaki veya konfigürasyondaki route kayıtlarından seçilir.
- Her kabiliyet için birincil, ikincil ve acil durum sağlayıcısı tanımlanabilir.

## 2.4 Güvenli otomasyon

- AI reklam bütçesini sınırsız artıramaz.
- Platformlar arası bütçe aktarımı kullanıcı sınırlarına tabidir.
- Harcama işlemlerinde sunucu taraflı bütçe defteri tutulur.
- Aynı kampanyanın tekrar oluşturulmasını engellemek için idempotency zorunludur.
- Tüm reklam değişiklikleri audit log’a yazılır.

## 2.5 Mobil öncelikli deneyim

- Kullanıcı işini büyük ölçüde telefondan yapabilmelidir.
- Uzun video yüklemeleri devam ettirilebilir/resumable olmalıdır.
- Uygulama kapanınca yükleme ve iş durumu kaybolmamalıdır.
- Ön izleme düşük çözünürlüklü proxy üzerinden hızlı açılmalıdır.
- Render veya analiz işlemleri mobil cihazda değil sunucuda yapılmalıdır.

---

# 50. Son karar özeti

Üretilecek sistem:

```text
Flutter mobil uygulama
+
FastAPI modüler monolit
+
PostgreSQL/pgvector
+
Redis/Celery worker'ları
+
S3/R2 doğrudan medya yükleme
+
FFmpeg/OpenCV/PySceneDetect
+
AI provider router
+
n8n orkestrasyon
+
Meta/Instagram, Google Ads ve X adapterları
+
Store uyumlu kredi tabanlı esnek abonelik
+
Sunucu taraflı entitlement ve bütçe guardrail motoru
```

En kritik mimari sınırlar:

1. n8n ana uygulama değildir.
2. Büyük video dosyası n8n veya FastAPI üzerinden geçirilmez.
3. Mobil uygulama API anahtarı tutmaz.
4. Abonelik hakkının kaynağı backend entitlement ledger’dır.
5. Store transaction faturalandırma gerçeğidir.
6. AI fiyat ve kampanya verisini uyduramaz.
7. Reklam kampanyası önce paused oluşturulur.
8. Bütçe guardrail’i AI modelinden bağımsız deterministik koddur.
9. Model ve platform entegrasyonları adapter ile soyutlanır.
10. PostgreSQL sistemin gerçek kaynağıdır.
