**İşletme/marka profili, ürün ve kampanya kaydı** · PRD bölümleri: §11

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 11. İşletme ve marka profili

## 11.1 İşletme profili bölümleri

1. Genel bilgiler
2. Şubeler
3. Ürün/hizmet kataloğu
4. Marka kimliği
5. Hedef kitle
6. İçerik politikaları
7. Reklam politikaları
8. Kampanyalar
9. Bağlı hesaplar
10. Ekip
11. Onay akışları

## 11.2 Ürün/hizmet kaydı

Her ürün:

```json
{
  "id": "uuid",
  "name": "Soğuk Latte",
  "category": "İçecek",
  "description": "Soğuk süt ve çift espresso",
  "price_minor": 16500,
  "currency": "TRY",
  "status": "active",
  "stock_status": "available",
  "valid_locations": ["kadikoy"],
  "claims": ["taze hazırlanır"],
  "forbidden_claims": ["sağlığa iyi gelir"],
  "media_asset_ids": [],
  "landing_page_url": null
}
```

Fiyatlar `minor unit` olarak tutulmalıdır. Örneğin `16500 = ₺165,00`.

## 11.3 Kampanya kaydı

- Kampanya adı
- Başlangıç/bitiş
- Ürünler
- İndirim türü
- İndirim değeri
- Geçerli şubeler
- Stok limiti
- Kupon kodu
- Yasal metin
- Reklam bütçesi
- Onay durumu
- Otomatik durdurma koşulları

AI kampanya tarihini veya fiyatı yazmaz. Doğrulanmış kayıttan alır.
