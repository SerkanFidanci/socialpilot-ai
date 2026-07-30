**AI model yönlendirme ve maliyet kontrolü** · PRD bölümleri: §17, §39

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 17. AI model yönlendirme katmanı

## 17.1 Kabiliyetler

```text
text_strategy
script_generation
caption_generation
structured_timeline
video_understanding
scene_rerank
asr
tts
image_generation
image_edit
video_generation
moderation_text
moderation_image
translation
quality_review
```

## 17.2 Önerilen sağlayıcı adayları

Bu liste kod içinde sabitlenmemelidir.

| Görev | Birincil aday | Alternatif |
|---|---|---|
| Video anlama | Qwen VL / Alibaba Model Studio | Gemini veya başka güçlü VLM |
| Metin/planlama | DeepSeek | Qwen / OpenAI |
| ASR | Qwen ASR veya güçlü Türkçe ASR | OpenAI/ElevenLabs/başka |
| TTS | MiniMax Speech HD | ElevenLabs/Qwen TTS |
| Görsel düzenleme | Qwen Image/Edit | OpenAI Image/Seedream |
| Generative video | Seedance | Kling/Hailuo |
| Kalite kontrol | Farklı sağlayıcıdan güçlü model | Birincil sağlayıcı |
| Montaj | FFmpeg | Yönetilen render servisi |

## 17.3 Provider interface

```python
class VideoUnderstandingProvider(Protocol):
    async def analyze_scene(
        self,
        asset_url: str,
        start_ms: int,
        end_ms: int,
        schema: dict,
        context: dict,
    ) -> dict: ...

class TextGenerationProvider(Protocol):
    async def generate_structured(
        self,
        task: str,
        input_data: dict,
        output_schema: dict,
        quality_tier: str,
    ) -> dict: ...

class TTSProvider(Protocol):
    async def synthesize(
        self,
        text: str,
        voice_profile: dict,
        output_format: str,
    ) -> "AudioResult": ...
```

## 17.4 Route seçimi

Girdiler:

- Task
- Quality tier
- Dil
- Medya süresi
- Kullanıcının planı
- Tenant veri bölgesi
- Sağlayıcı sağlık durumu
- Günlük maliyet bütçesi
- Latency hedefi
- Kullanıcının premium seçimi
- Hassas veri politikası

Örnek route:

```json
{
  "capability": "video_understanding",
  "quality_tier": "professional",
  "primary_provider": "alibaba_qwen",
  "primary_model": "configured-model-id",
  "fallbacks": [
    {"provider": "google", "model": "configured-model-id"}
  ],
  "max_cost_minor": 150,
  "timeout_seconds": 180,
  "retry_policy": "transient_only"
}
```

## 17.5 Model çıktısı güvenliği

- JSON Schema doğrulaması
- Zorunlu alanlar
- Enum değerleri
- Maksimum metin uzunluğu
- Prompt injection savunması
- Kullanıcı medyasındaki metin talimat olarak değil veri olarak kabul edilir
- Modelin ürettiği URL doğrudan fetch edilmez
- Fiyat/tarih/telefon gibi değerler deterministik katmanda birleştirilir
- İkinci kalite kontrolü aynı modelden değil farklı sağlayıcıdan yapılabilir

## 17.6 Prompt versiyonlama

Tablo:

```text
prompt_templates
- id
- code
- version
- system_prompt
- user_template
- output_schema
- active
- experiment_group
- created_at
```

Her content version hangi prompt sürümüyle üretildiğini saklamalıdır.

---

# 39. Maliyet kontrolü

## 39.1 Cost attribution

Her dış çağrı:

- Provider
- Model
- Task
- Input/output
- Business
- Content project
- Subscription
- Tahmini ve gerçek maliyet
- Para birimi
- Fatura tarihi

## 39.2 Bütçe

- Tenant günlük AI bütçesi
- Plan başına maksimum generative video
- Sahne batching
- Proxy kullanımı
- Cache
- Aynı medya analizini tekrar kullanma
- Premium model sadece kritik aşamada
- Render retry limiti
- Failed job cost refund politikasını ayrı tut

## 39.3 Model routing stratejisi

1. Yerel algoritma
2. Fiyat/performans model
3. Güçlü doğrulama modeli
4. Premium ihtiyaçta generative model

Her video karesi güçlü modele gönderilmemelidir.
