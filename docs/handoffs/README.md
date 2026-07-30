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

## Bir dalın "boş" olduğunu nasıl ölçersin

`git log main..<dal>` boş görünmesi o slice'ın başlamadığı anlamına **gelmez** — oturum commit atmadan saatlerce çalışmış olabilir. 2026-07-30'da W11, W10'un migration slotunu devralmayı önerdi çünkü W10'un dalında commit yoktu; oysa W10'un worktree'sinde `0011_schema_debt.py` dahil kapsamlı commit'lenmemiş iş vardı.

**Slot devri veya çakışma kararı vermeden önce worktree durumuna bak:**

```
git -C <worktree> status --short
ls <worktree>/services/api/migrations/versions/
```

## Yeni oturum mu, aynı oturum mu

**Varsayılan: her slice için yeni oturum.** Sezgiye ters gelir ("o zaten biliyor") ama üç sebeple doğrudur:

1. **Yeniden kullanım daha pahalı.** Bir oturumun her turu birikmiş transcript'in tamamını yeniden gönderir; transcript tek yönlü büyür. Taze bir oturumun doküman okuma maliyeti ise sabit ve küçük (router'daki görev tipine göre 2–6k token). Kapanmış bir slice'ın oturumunda yeni iş başlatmak, taze oturum + doküman okumasından belirgin şekilde pahalıdır.
2. **Bayat dünya.** Yeniden kullanılan oturum kendi eski worktree'sinde, eski commit'te oturur. 2026-07-30'da bu iki kez ısırdı: W03 `c13636b`'den çalıştığı için ADR-008'i kaçırdı; W09 eski araç zincirinde yazıldığı için W02 ile birleşimi kırmızı çıktı.
3. **Sıkıştırma eşit kaybetmez.** Uzun oturum sıkıştırıldığında geriye bildiğini sanan ama hafızası delik bir oturum kalır. Dokümanı okuyan taze oturum yerçekimini yeniden okur.

Zaten sıcak bilgi bilinçli olarak dokümana taşındı (modül `CLAUDE.md`'leri, `STATUS.md`, router — W03'ün işi). Dokümanlaşmış bilgi oturum hafızasından daha iyidir: paylaşılır, doğrulanabilir, sıkıştırmadan sağ çıkar.

**Aynı oturumu sürdür** yalnızca **aynı slice'ın devamı** için: tester bulgusunu düzeltmek, PM geri bildirimini uygulamak, merge öncesi küçük tamamlama. Orada oturum kendi diff'ini biliyor ve yeniden okutmak israftır.

**Bunun operasyonel sonucu:** bir WO'nun worktree'si **merge edildiğinde değil, tamamen kapandığında** (merge **ve** bağımsız doğrulama bittiğinde) silinir. Aksi halde tester bulgusu geldiğinde geri dönecek sıcak oturum kalmaz. 2026-07-30'da W07 ve W08'in worktree'leri Codex doğrulaması bitmeden silindi — o bulguların düzeltmesi taze oturumla, `main`'den yeni dalla yapılacak.

## Doğrulama ortamı (zorunlu)

`docker compose` proje adını worktree'den **türetmez**. Sabit bir proje adıyla herhangi bir
worktree'de `docker compose up --build` çalıştırmak **paylaşılan konteynerleri ele geçirir** ve
başka oturumların doğrulamasını sessizce geçersiz kılar. 2026-07-30'da bu bir kez oldu: W02'nin
yükseltilmiş imajı `main`'in konteynerini değiştirdi, Codex de `main`'in kaynağını W02'nin araç
zincirinden geçirip W01'e ait olmayan 21 hata bildirdi.

**Kural:** kendi worktree'nde compose çalıştırıyorsan proje adını ayır:

```
COMPOSE_PROJECT_NAME=sp-<worktree-adi> docker compose up -d --build
```

Doğrulama sonucunu rapora yazarken **hangi araç zinciri sürümleriyle** koştuğunu da yaz
(`python -m mypy --version`, `ruff --version`). Kapı yeşil/kırmızı bilgisi, sürüm bağlamı
olmadan yeniden üretilemez.

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
