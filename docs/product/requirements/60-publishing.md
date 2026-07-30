**Sosyal hesap bağlantıları ve yayınlama motoru** · PRD bölümleri: §22, §23

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 22. Sosyal hesap bağlantıları

## 22.1 Bağlantı ilkeleri

- OAuth mobil istemcide başlatılır, backend callback ile tamamlanır
- PKCE/state/nonce
- Tokenlar mobilde saklanmaz
- Access/refresh tokenlar şifreli saklanır
- Minimum scope
- Account seçimi
- Capability keşfi
- Token health check
- Re-authentication
- Bağlantıyı kaldırma
- Veri silme webhook/endpoint’i

## 22.2 Meta/Instagram

Bağlantı sonucu:

- Meta kullanıcı/işletme kimliği
- Facebook Page
- Instagram professional account
- Ad account
- Pixel/Dataset
- Catalog
- İzinler
- Token expiry
- Publishing capability
- Insights capability
- Ads capability

İçerik yayınlama ile reklam yönetimi farklı izin ve capability olarak tutulur.

## 22.3 Google

Tek bir “Google hesabı bağlandı” bayrağı yeterli değildir.

Connector modülleri:

- Google Identity
- Google Ads
- YouTube
- GA4
- Merchant Center
- Business Profile

İlk sürümde Google Ads zorunlu, diğerleri feature flag ile eklenir.

Google Ads için:

- OAuth 2.0
- Developer token
- Manager customer ID
- Customer ID
- Accessible customers
- Conversion actions
- Billing setup/readiness
- Test account desteği

## 22.4 X

Ayrı capability’ler:

- Organik gönderi
- Medya yükleme
- Analytics
- Ads account
- Campaign management
- Web conversions

X Ads API erişiminin ayrıca onay gerektirebileceği kabul edilmelidir. Uygulama erişim yoksa organik gönderi özelliğini çalıştırıp reklamı “bağlantı bekliyor” olarak göstermelidir.

## 22.5 Connected account durumları

```text
pending
connected
limited
expired
revoked
error
reconnect_required
disabled
```

## 22.6 Capability matrisi

```json
{
  "publish_image": true,
  "publish_video": true,
  "publish_story": true,
  "read_insights": true,
  "manage_ads": false,
  "read_ads": true,
  "conversion_tracking": false
}
```

UI yalnızca gerçek capability’leri göstermelidir.

---

# 23. Yayınlama motoru

## 23.1 Publishing adapter

```python
class SocialPublishingProvider(Protocol):
    async def capabilities(self, connection: dict) -> dict: ...
    async def upload_media(self, connection: dict, media: dict) -> dict: ...
    async def create_post(self, connection: dict, payload: dict) -> dict: ...
    async def get_post(self, connection: dict, external_id: str) -> dict: ...
    async def delete_post(self, connection: dict, external_id: str) -> None: ...
```

## 23.2 Yayınlama işi

- Platform
- Account
- Content version
- Scheduled time
- Caption
- Hashtags
- Media variants
- First comment
- UTM
- Idempotency key
- Attempt count
- External IDs
- Error details

## 23.3 Retry kuralları

Retry:

- Timeout
- 429
- Geçici 5xx
- Medya processing bekleniyor

Retry edilmez:

- İzin yok
- Hesap kapalı
- Politika reddi
- Geçersiz medya
- Kullanıcı bağlantıyı kaldırmış

## 23.4 Yayınlama doğrulama

“API başarılı cevap verdi” yayınlandı anlamına gelmeyebilir.

- External media status sorgula
- Post external ID kaydet
- URL kaydet
- İlk metrik toplama işini planla
- Başarısız işte kullanıcıyı bilgilendir
- Aynı postu tekrar oluşturma
