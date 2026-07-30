**Performans, analitik ve bildirimler** · PRD bölümleri: §25, §31

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 25. Performans ve analitik

## 25.1 Organik metrikler

- Impression
- Reach
- Like
- Comment
- Share
- Save
- Profile visit
- Link click
- Video play
- 3-second view
- Completion rate
- Follower growth

## 25.2 Reklam metrikleri

- Spend
- Impression
- Reach
- CPM
- Click
- CTR
- CPC
- Conversion
- CPA
- Lead
- CPL
- Revenue
- ROAS
- Frequency
- Video completion
- Platform learning status

## 25.3 Normalize edilmiş metric modeli

Platform alanları doğrudan UI’a taşınmaz.

```text
metric_definitions
metric_observations
external_metric_mappings
```

```json
{
  "entity_type": "published_post",
  "entity_id": "uuid",
  "metric": "video_completion_rate",
  "value": 0.42,
  "period_start": "...",
  "period_end": "...",
  "source": "instagram",
  "raw_payload_ref": "..."
}
```

## 25.4 Öğrenme döngüsü

- İçerik türü
- Hook tipi
- İnsan yüzü
- Ürün
- Süre
- Ses stili
- Yayın saati
- CTA
- Sahne kategorisi
- Platform
- Hedef kitle

Performans sinyalleri sahne ve içerik özellikleriyle ilişkilendirilir.

İlk aşamada ML modeli yerine kural + istatistik kullanılmalıdır. Yeterli veri oluşmadan “AI öğrendi” iddiası yapılmamalıdır.

---

# 31. Bildirimler

Kanallar:

- Push
- In-app inbox
- E-posta
- Operasyon için Slack/Teams opsiyonel

Bildirim tipleri:

- İçerik hazır
- Onay bekliyor
- Yayınlandı
- Yayın başarısız
- Hesap bağlantısı koptu
- Abonelik yenilendi
- Ödeme başarısız
- Hak azaldı
- Ek medya gerekli
- Reklam onayı
- Bütçe limiti
- Kampanya durduruldu
- Haftalık rapor

Kullanıcı sessiz saat ve kanal seçebilmelidir.
