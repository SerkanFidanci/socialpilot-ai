**Kullanıcı tipleri, roller ve yetkiler** · PRD bölümleri: §4

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 4. Kullanıcı tipleri ve roller

## 4.1 Son kullanıcı rolleri

### Owner

- İşletme sahibi
- Aboneliği ve faturalandırmayı yönetir
- Sosyal/reklam hesaplarını bağlar
- Reklam bütçesi sınırlarını belirler
- Üye ekler ve çıkarır
- İşletmeyi silebilir

### Admin

- Marka ve içerik ayarlarını yönetir
- İçerikleri onaylar
- Kampanyaları görüntüler ve sınırlar dahilinde yönetir
- Faturalandırma yöntemini değiştiremez; owner izni gerekir

### Editor

- Medya yükler
- İçerik üretir
- Revizyon ister
- Taslak oluşturur
- Reklam bütçesini değiştiremez

### Viewer

- Takvim, içerik ve raporları görüntüler
- Değişiklik yapamaz

### Approver

- Yalnızca onay bekleyen içerik ve reklamları onaylar/reddeder
- Kurumsal müşteriler için ayrı rol olarak kullanılabilir

## 4.2 İç operasyon rolleri

- `support_agent`
- `content_operator`
- `ads_operator`
- `finance_admin`
- `system_admin`
- `security_auditor`

Operasyon kullanıcısı son kullanıcı hesabına doğrudan giriş yapmamalıdır. Destek amaçlı impersonation gerekiyorsa:

- açık sebep yazılmalı,
- süre sınırlı olmalı,
- kullanıcıya bildirim opsiyonu bulunmalı,
- tüm işlemler audit log’a kaydedilmelidir.
