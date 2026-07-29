# Work Order Protokolü

Bu klasör, oturumlar arası iş devri yüzeyidir. Her dosya bir **work order (WO)**: iş emri, yürütme raporu ve doğrulama sonucu aynı dosyada birikir. PM oturumu iş emrini yazar; yürüten oturum aynı dosyaya rapor yazar; test eden oturum doğrulama bölümünü doldurur.

## Yürüten oturum için: nasıl çalışılır

1. **Oku, sırayla, sadece bunları:**
   [`docs/STATUS.md`](../STATUS.md) → bu WO dosyası → WO'nun "Okunacaklar" listesi. Başka doküman açma.
2. **Dalını aç:** WO'da yazan dal adıyla, `main`'den. Başka dalın üzerine kurma.
3. **Uygula.** Yalnızca WO'nun "Dokunulacak dosyalar" listesindeki dosyalara dokun. Listede olmayan bir dosyaya dokunman gerekiyorsa **dur**, WO'nun Rapor bölümüne gerekçeyi yaz ve PM'e bırak.
4. **Doğrula:** `make verify` yeşil olmadan teslim yok. Migration eklediysen `upgrade head → downgrade base → upgrade head`.
5. **Rapor yaz:** bu dosyanın Rapor bölümünü doldur (aşağıdaki şablon).
6. **`docs/STATUS.md`'yi güncelle:** WO satırının durumunu, varsa bloke edici/karar değişikliğini aynı commit'te yaz.
7. **Commit + merge:** slice kapanınca `main`'e merge, dalı sil. Yarım iş `main`'e girmez.

## Bağlam bütçesi kuralları

- **`docs/generated/openapi.json` OKUNMAZ.** 86 KB / ~23k token. Endpoint listesi gerekiyorsa `docs/api/endpoints.md`.
- **`docs/product/product-requirements.md` bütün olarak OKUNMAZ.** Yalnızca WO'nun işaret ettiği bölüm / `docs/product/requirements/` altındaki ilgili dosya.
- Modül içinde çalışıyorsan o modülün `CLAUDE.md`'si otomatik yüklenir; dosyaları tek tek açarak keşfetmeye çalışma.
- Kod aramak için önce modül `CLAUDE.md`'sindeki dosya listesine bak, sonra grep. Geniş `Read` yapma.

## Çakışma kuralları (bunlar ihlal edildiği için bir kez çift iş yapıldı)

- **Dosya-ayrıklık:** iki WO aynı dosyaya dokunamaz. Her WO "Dokunulacak dosyalar"ı önceden ilan eder; PM ayrıklığı garanti eder.
- **Migration slotu:** aynı anda yalnızca **bir** WO Alembic migration ekler. Slot `STATUS.md` tablosunda yazılıdır. Slotu olmayan WO migration eklemez; gerekiyorsa durur ve PM'e bırakır.
- **Aynı base'den aynı işi yapma:** WO'yu tetiklemeden önce `STATUS.md`'de durumunun `tetiklenmedi` olduğunu doğrula. `yürütülüyor` ise dokunma.
- **`main` her zaman çalışan gerçek.** Doğrulaması geçmemiş iş merge edilmez.

## Dal adlandırma

```
slice/<faz><harf>-<domain>     örn. slice/1e-object-storage
```

Slice kapanınca dal silinir. `claude/*` ve serbest isimli dallar bırakılmaz.

## Rapor şablonu (yürüten oturum doldurur)

```markdown
## Rapor — <tarih> · <oturum>

**Dal:** · **Commit'ler:** · **Durum:** tamamlandı | kısmi | bloke

### Yapılanlar
- ...

### Kapsam dışı bıraktıklarım ve nedeni
- ...

### Doğrulama
| Kontrol | Sonuç |
|---|---|
| `make verify` | |
| migration up/down/up | |
| kabul kriteri 1..n | |

### Açıkça belirtmem gerekenler
- (varsayım, bloke edici, yeni karar ihtiyacı, PM'e bırakılan iş)
```

## Doğrulama şablonu (test eden oturum doldurur)

Test eden oturum **özellik yazmaz**. Görevi: `make verify` çalıştırmak, WO'nun kabul kriterlerine karşı **düşmanca** test denemek (tenant sızması, idempotency tekrarı, timeout, kısmi çıktı, yetki atlatma, sınır değerleri), bulguları listelemek.

```markdown
## Doğrulama — <tarih> · <test eden>

| # | Bulgu | Şiddet | Yeniden üretim | Durum |
|---|---|---|---|---|
| 1 | | kritik/orta/düşük | | açık/düzeltildi/kabul edildi |

**Karar:** teslim edilebilir | düzeltme gerekiyor
```
