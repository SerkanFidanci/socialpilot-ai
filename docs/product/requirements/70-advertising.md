**Reklam otomasyonu** · PRD bölümleri: §24

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 24. Reklam otomasyonu

## 24.1 Ana prensip

AI önerir ve izin verilen sınırlar içinde uygular. Bütçe ve platform gerçekliği backend guardrail motoruyla doğrulanır.

## 24.2 Reklam kullanıcı ayarları

```json
{
  "goals": ["sales", "leads"],
  "platforms": ["meta", "google_ads"],
  "monthly_budget_minor": 2000000,
  "daily_budget_limit_minor": 100000,
  "campaign_budget_limit_minor": 500000,
  "target_locations": ["Istanbul"],
  "age_min": 23,
  "age_max": 50,
  "languages": ["tr"],
  "approval_mode": "semi_automatic",
  "max_cpl_minor": 25000,
  "target_roas": 2.5,
  "budget_change_limit_percent": 20,
  "auto_pause_on_tracking_failure": true,
  "auto_pause_on_site_failure": true,
  "regulated_categories_allowed": false
}
```

Örnek para gösterimleri kullanıcı arayüzünde `tr-TR` formatında olmalıdır: `₺20.000,00`.

## 24.3 Reklam senaryoları

### Marka bilinirliği

- Video/görsel kreatif
- Reach/video views
- Bölge ve hedef kitle
- Frequency kontrolü
- Video izleme metrikleri

### Ürün satışı

- Ürün doğrulama
- Landing page
- Conversion tracking
- Meta sales
- Google Search/Performance Max
- Retargeting

### Lead toplama

- Form veya landing page
- Lead başı maliyet
- CRM webhook
- Spam lead filtreleme
- Offline conversion feedback

### Telefon araması

- Çalışma saatleri
- Call CTA
- Arama dönüşümü
- Mesai dışında durdurma

### Mağaza ziyareti

- Şube konumu
- Yakın çevre
- Açılış saatleri
- Yol tarifi/arama

### Lansman

- Teaser
- Awareness
- Launch
- Conversion
- Retargeting

### Organik kazananı boost etme

- Performans eşiği
- Kullanım hakkı
- Bütçe sınırı
- Kampanya paused oluşturma

### A/B kreatif testi

- Aynı kitle/bütçe
- Tek değişken
- Minimum örnek
- Erken kazanan seçmeme
- İstatistiksel veya kural tabanlı karar

## 24.4 Campaign blueprint

Platform bağımsız kayıt:

```json
{
  "goal": "lead_generation",
  "budget": {
    "type": "daily",
    "amount_minor": 50000,
    "currency": "TRY"
  },
  "schedule": {
    "start_at": "2026-08-01T09:00:00+03:00",
    "end_at": "2026-08-07T23:00:00+03:00"
  },
  "audience": {
    "locations": ["Istanbul"],
    "age_min": 25,
    "age_max": 45,
    "languages": ["tr"]
  },
  "creatives": [],
  "conversion_goal_id": "uuid",
  "guardrails": {
    "max_cpl_minor": 25000,
    "max_daily_spend_minor": 75000
  }
}
```

Adapter bunu platform nesnelerine çevirir.

## 24.5 Kampanya oluşturma sırası

```text
Blueprint oluştur
→ Bağlantı ve yetki doğrula
→ Bütçe ledger kontrolü
→ Dönüşüm takibi doğrula
→ Landing page kontrolü
→ Kreatif QC
→ Platforma PAUSED kampanya oluştur
→ External nesneleri doğrula
→ Kullanıcı/onay politikası
→ ACTIVE
```

## 24.6 Guardrail motoru

Zorunlu kurallar:

- Günlük toplam harcama limiti
- Aylık toplam harcama limiti
- Kampanya başına limit
- Platform başına limit
- Otomatik artış yüzdesi
- Değişiklik cooldown süresi
- Maksimum CPL/CPA
- Minimum ROAS için değerlendirme penceresi
- Yetersiz veri varsa agresif aksiyon yok
- Dönüşüm takibi bozulduysa durdur
- Web sitesi kapalıysa durdur
- Stok yoksa durdur
- Kampanya tarihi bittiyse durdur
- Reklam hesabı para birimi uyuşmazlığı
- Kullanıcı tam otomasyonu kapattıysa yalnızca öneri

## 24.7 Harcama defteri

Platform raporları gecikebilir. Kendi rezervasyon defterimiz bulunmalıdır.

```text
ad_spend_ledger
- business_id
- platform
- campaign_id
- date
- reserved_minor
- reported_spend_minor
- currency
- updated_at
```

Yeni kampanya açmadan önce `reserved + reported` limit kontrolü yapılır.

## 24.8 Optimizasyon aksiyonları

```text
recommend_pause
pause
recommend_budget_decrease
budget_decrease
recommend_budget_increase
budget_increase
replace_creative
start_ab_test
end_ab_test
change_schedule
refresh_audience
```

Her aksiyon:

- Sebep
- Önceki değer
- Yeni değer
- Metrik penceresi
- Güven skoru
- Uygulayan
- Onaylayan
- External API sonucu
- Rollback bilgisi

## 24.9 Acil durdurma

- İşletme bazında
- Platform bazında
- Kampanya bazında
- Global sistem bazında

Acil durdurma n8n’e bağlı olmamalıdır; backend doğrudan adapter çağırabilmelidir.
