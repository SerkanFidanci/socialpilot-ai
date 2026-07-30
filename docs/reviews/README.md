# İncelemeler

Teknoloji, metodoloji ve mimari yön değerlendirmeleri. Bir inceleme **karar değildir**:
karar `docs/adr/` altına ADR olarak yazılır, durum `docs/STATUS.md`'ye düşer. Buradaki
dosyalar bir tarihteki yargıyı ve gerekçesini saklar.

## Adlandırma

```
YYYY-MM-DD-konu.md        örn. 2026-07-30-tech-methodology.md
```

Tarih incelemenin yapıldığı gündür ve **değişmez**; sonradan güncellenen bir inceleme yeni
tarihle yeni dosya olur, eskisi yerinde kalır. Konu kısa ve kebab-case yazılır.

## Kurallar

- Her dosya başında **tarih, inceleyen, incelenen commit ve kapsam** bulunur. İncelenen
  commit yazılmazsa yargının neye dair olduğu kaybolur.
- **Dış dünya gerçekleri burada tekrarlanmaz.** Sürüm, fiyat, limit ve mevzuat tarihi tek
  bir yerde yaşar:
  [99-external-platform-facts.md](../product/requirements/99-external-platform-facts.md).
  İnceleme oraya link verir, değeri kopyalamaz.
- İçeriği PM oturumu doldurur; yürüten oturumlar burayı yeniden yazmaz.

## Mevcut incelemeler

| Tarih | Konu |
|---|---|
| 2026-07-30 | [Teknoloji ve metodoloji incelemesi](2026-07-30-tech-methodology.md) |
