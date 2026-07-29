# Dış Platform Gerçekleri

**Son doğrulama:** 2026-07-30 · **Sahip:** PM oturumu

> Bu dosya, dışarıdan gelen ve **hafızadan yazılamayacak** gerçekleri tutar: sürümler, fiyatlar, limitler, mevzuat tarihleri. Bir entegrasyona başlayan oturum önce buraya bakar. Buradaki her satırın bir doğrulama tarihi vardır; **6 aydan eski satır güvenilmez sayılır ve resmî kaynaktan yeniden doğrulanır.**
>
> PRD §49'un yerini alır (tarihli referans listesi oraya bakar).

## Platform API sürüm ve yaşam döngüsü

| Platform | Güncel sürüm | Kadans / sunset | Sonuç |
|---|---|---|---|
| Google Ads API | v24.2 (Haz 2026) | **Yılda 4 major** (Ocak/Nisan/Temmuz/Ekim) + aylık minor; her sürüm ~1 yıl. v21 → Ağu 2026, v22 → Eki 2026 sunset | Adapter'da sürüm pinlenir, takvimli yükseltme ve contract test zorunlu. Bakımsız kalırsa reklam modülü sessizce ölür. |
| Meta / Instagram Graph API | v25.0 (Şub 2026) | Çeyreklik sürüm, ~2 yıl destek | Aynı disiplin. |
| X Ads API | Onay kapılı partner programı | Genel X API'sinden ayrı başvuru | Erişim yoksa organik X çalışır, reklam "bağlantı bekliyor" gösterilir (PRD §22.4). |

## Maliyet gerçekleri

| Kalem | Gerçek | Ürün etkisi |
|---|---|---|
| **X API yazma** | Şub 2026'dan beri pay-per-use, **ücretsiz katman yok**. Gönderi **$0.015**, **link içeren gönderi $0.20** (13×), okuma $0.005 | "Günlük X gönderisi + UTM link" → tenant başına ayda ~**$6** saf X maliyeti. Kredi puan tablosunda X gönderisinin 1 puan olması yeniden değerlendirilmeli. Link'i opsiyonel kılan strateji gerekli. |
| Generative video | Seedance 2.0 Fast ~$0.09/sn · Kling 3.0 ~$0.10/sn · Sora 2 / Kling O3 ~$0.15/sn · Veo 3.1 Standard ~$0.75/sn | PRD'nin "Seedance birincil, Kling alternatif" tercihi ~8× maliyet avantajını koruyor. 20 sn'lik B-roll ≈ $1.80. |
| Mağaza komisyonu (TR) | **Türkiye, Google Play user-choice/alternative billing programlarında yok** (EEA/UK/ABD/AU/BR/ID/JP/ZA/KR var). Apple harici link muafiyeti ABD'ye özgü ve SCOTUS'ta | TR'de IAP = %15–30, kaçış yolu yok. Karar **K1**. |

> Fiyatlar ticari karar öncesi resmî sağlayıcı dokümanından teyit edilir; buradaki değerler yön göstericidir.

## Mevzuat ve platform politikası

| Konu | Gerçek | Ne yapılmalı |
|---|---|---|
| **EU AI Act Md. 50** | Şeffaflık yükümlülükleri **2 Ağustos 2026**'dan itibaren. Md. 50(2): sentetik ses/görüntü/video/metin üreten sistemler **makine-okunur işaretleme** ve tespit edilebilirlik sağlamak zorunda. Md. 50(4): deepfake kullanıcısı yapay kökeni ilk maruz kalışta açıkça bildirmek zorunda; sağlayıcının gömdüğü işaret tek başına yetmez. 2 Ağu 2026 öncesi piyasaya sürülmüş sistemlere işaretlemede Aralık 2026'ya kadar geçiş | EU roadmap'te ise C2PA/provenance alanları timeline + render şemasına girer. Karar **K3**. |
| **C2PA kırılganlığı** | C2PA manifest'i **yeniden kodlamada silinir**. Bizim hattımız üretken çıktıyı FFmpeg'den geçirip render ediyor → provenance kaybolur. OpenAI + Google (May 2026) C2PA + SynthID'yi **iki katmanlı** kullanıyor | Render worker'ı çıktıya manifest'i **yeniden iliştirmeli**; görünmez watermark katmanı korunmalı. |
| **Meta AI etiketi** | Temmuz 2026'dan beri FB/IG reklamlarında **otomatik AI etiketleme**; manuel beyan seçeneği yok. Meta üçüncü taraf araçların gömdüğü C2PA'yı okuyup etiketliyor. **Beyan edilmemiş AI içeriği aktif reklam reddi gerekçesi** | `ad_creatives` / `campaign_blueprint` / `publishing_jobs`'a "AI disclosure state" alanı; kreatif QC'ye kontrol; ADS-10 Ad Rejection Handler'a bu red sebebi. |
| **KVKK yurt dışı aktarım** | 6698 m.9 (7499 s. değişiklik): standart sözleşme veya bağlayıcı şirket kuralları; standart sözleşme imzasından sonra **5 iş günü içinde Kurul'a bildirim zorunlu** | Yüz/ses içeren medya biyometrik tartışmasına girer. Her yeni AI sağlayıcısı = DPA + standart sözleşme + bildirim + veri bölgesi kaydı. Sağlayıcı ekleme süreci hukuki checklist'e bağlanır; §34.3 veri minimizasyonu (orijinal yerine kesit/proxy) **uyum gerekçesiyle** zorunlu. |
| Türkiye AI mevzuatı | TCK değişiklik teklifi (23 Tem 2025) komisyonda; yapay zekânın hukuki tanımı ve sorumluluk rejimi | Takip edilir. |
| **n8n lisansı** | Sustainable Use License: ticari bir platformun motoru olmak ve harici kullanıcıların workflow tetiklemesi kısıtlı; bu senaryo için **Embed anlaşması** isteniyor | Karar **K2**. |

## Platform teknik kısıtları

| Kısıt | Gerçek | Mimari sonucu |
|---|---|---|
| **Instagram yayın URL'i** | Meta sunucuları videoyu **verdiğimiz URL'den kendisi çeker**; URL yayın anında kimlik doğrulamasız erişilebilir olmalı. Yönlendirmeli signed URL'ler garanti çalışmıyor | "Yalnızca kısa ömürlü signed URL" duruşumuzla çelişiyor → **ayrı ADR gerekiyor**: tahmin edilemez anahtarlı, kısa TTL'li, lifecycle ile silinen publish delivery yüzeyi (CDN önünde). |
| Instagram container akışı | `POST /media` → `status_code` FINISHED olana kadar poll → `POST /media_publish` | `publishing_jobs`'ta container ID + poll durumu alanı. "API 200 döndü" yayınlandı demek değil (PRD §23.4). |
| Instagram yayın limiti | Hesap başına 24 saatte sınırlı sayıda API yayını (kaynaklara göre 25–100; resmî dokümandan doğrulanmalı). Reels ve hikâyeler aynı kotadan düşer | Abonelik sıklık planlayıcısı bu kotayı bilerek üretmeli. |
| iOS medya formatları | iPhone varsayılanı **HEIC/HEIF** fotoğraf, **`.mov`/HEVC** video (`video/quicktime`) | MIME allowlist'te olmak zorunda — **W01**. |

## Yığın sürüm gerçekleri (2026-07-30)

Python 3.14 kararlı (3.15 → 1 Eki 2026) · FastAPI ~0.13x · uvicorn 0.51 · Alembic 1.18.5 · Celery 5.6.2 (**native asyncio yok** — köprü veya `celery-aio-pool` gerekir) · SQLAlchemy 2.0 kararlı, 2.1 beta · Pydantic 2.13 · PostgreSQL 18 (`uuidv7()`, yeni I/O altyapısı) · Redis 8 AGPL / Valkey 9.1 BSD · pgvector 0.8.x · FFmpeg 8.0 (dahili Whisper filtresi) · Flutter 3.44 · Next.js 16 · `uv` fiili standart paket yöneticisi · OpenTofu greenfield IaC için düşük riskli varsayılan.
