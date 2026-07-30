**Güvenlik, KVKK/gizlilik, içerik güvenliği ve telif** · PRD bölümleri: §33, §34, §35

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 33. Güvenlik

## 33.1 Kimlik doğrulama

- Firebase/OIDC token doğrulama
- Kısa ömürlü backend session
- Refresh politikasını auth sağlayıcı yönetir
- Admin için MFA
- Riskli işlemlerde re-authentication
- Device kayıtları

## 33.2 Yetkilendirme

- RBAC
- Business membership
- Resource ownership
- Approval separation
- Ads budget permission
- Billing permission
- Admin scoped permissions

## 33.3 Secret yönetimi

- API key mobilde yok
- `.env` üretimde yok
- Secret manager
- Per-environment secret
- Rotation
- Access log
- n8n credential encryption key
- OAuth token envelope encryption

## 33.4 Ağ

- HTTPS
- WAF
- Rate limit
- Private worker network
- Database public değil
- n8n editor erişimi VPN/SSO
- Webhook signature
- IP allowlist mümkünse
- SSRF koruması
- Signed URL kısa ömür

## 33.5 Uygulama güvenliği

- OWASP ASVS/MASVS yaklaşımı
- Input validation
- SQL injection koruması
- XSS admin paneli
- CSRF OAuth/admin
- File upload doğrulaması
- Zip bomb ve medya parser riskleri
- FFmpeg sandbox
- Dependency scanning
- Container scanning
- SAST
- Secret scanning

## 33.6 Audit log

Aşağıdakiler immutable audit kayıtları üretir:

- Hesap bağlantısı
- Token yenileme
- Üye rolü
- İçerik onayı
- Fiyat/kampanya değişikliği
- Reklam oluşturma
- Bütçe değişikliği
- Kampanya durdurma
- Subscription adjustment
- Admin impersonation
- Veri silme

---

# 34. KVKK, gizlilik ve içerik hakları

Bu bölüm hukuki görüş değildir; uzman incelemesi zorunludur.

## 34.1 Veri sınıfları

- Kullanıcı hesabı
- İşletme bilgisi
- Fotoğraf/video
- Yüz ve ses içeren medya
- Sosyal hesap tokenları
- Reklam verisi
- Faturalandırma verisi
- Performans verisi
- Destek konuşmaları

## 34.2 Gereksinimler

- Aydınlatma metni
- Açık rıza gereken işlemler için ayrı rıza
- Yüz/ses medyası kullanım yetkisi beyanı
- Ses klonlamada ayrı ve geri alınabilir onay
- Çalışan/müşteri görüntülerinin yayın hakkı
- Veri işleme envanteri
- Saklama ve imha politikası
- Kullanıcı veri indirme
- Hesap silme
- Bağlantı kaldırınca token silme
- Yurt dışı sağlayıcı aktarım analizi
- Sağlayıcı DPA ve veri bölgesi kaydı
- Alt işleyen listesi

## 34.3 Veri minimizasyonu

- AI sağlayıcıya tüm orijinali göndermek yerine gerekli kesit
- Hassas metadata temizleme
- EXIF konumu gerekmedikçe silme
- Sağlayıcı log retention ayarlarını kontrol etme
- Eğitim için kullanım varsa opt-out veya uygun sözleşme
- Promptlara gereksiz kişisel veri eklememe

## 34.4 Silme

```text
Kullanıcı silme talebi
→ Aktif işleri durdur
→ Sosyal tokenları iptal et/sil
→ Medyayı soft delete
→ Yasal saklama kapsamını ayır
→ Object storage purge
→ Embedding ve türevleri sil
→ Sağlayıcı deletion API varsa çağır
→ Tamamlama kaydı
```

---

# 35. İçerik güvenliği ve telif

- Yüklenen içeriğin kullanım hakkına sahip olma beyanı
- Lisanslı müzik
- Müzik lisans kapsamı platform/reklam bazında
- Marka ve logo hakları
- Uygunsuz içerik moderasyonu
- Çocuk güvenliği
- Nefret, şiddet, cinsel içerik
- Yanıltıcı reklam
- Sahte yorum
- Deepfake
- Ünlü yüzü/sesi
- Kullanıcı rızası olmadan voice clone yok
- Generative sahne açıkça işaretlenebilir
- Reklam platform politikası kontrolü
