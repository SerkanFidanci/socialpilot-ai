**Veritabanı tasarımı** · PRD bölümleri: §28

> Bu dosyadaki bölümler `docs/product/product-requirements.md`'den **birebir** taşındı. Metin değiştirilmez, bölüm numaraları korunur.
> İndeks: [product-requirements.md](../product-requirements.md) · Router: [docs/index.md](../../index.md)

---

# 28. Veritabanı tasarımı

Bütün tablolarda uygun olan yerlerde:

- UUID primary key
- `created_at`
- `updated_at`
- `deleted_at`
- tenant/business scope
- optimistic version
- audit metadata

## 28.1 Identity ve tenant

```text
users
user_identities
user_devices
businesses
business_members
roles
member_roles
business_locations
```

## 28.2 Marka

```text
brand_profiles
brand_assets
brand_colors
brand_fonts
brand_rules
brand_examples
target_audiences
products
product_prices
product_inventory_snapshots
campaign_offers
approved_claims
forbidden_claims
approved_ctas
```

## 28.3 Bağlantılar

```text
connected_accounts
oauth_credentials
connection_capabilities
connection_health_events
external_account_mappings
webhook_subscriptions
```

`oauth_credentials` ayrı şema veya ayrı veritabanında tutulabilir. Alanlar envelope encryption ile şifrelenmelidir.

## 28.4 Medya

```text
media_assets
media_variants
media_upload_sessions
media_processing_jobs
media_scenes
media_keyframes
media_tags
media_embeddings
transcripts
transcript_segments
media_usage_links
media_consent_records
music_assets
music_licenses
```

## 28.5 İçerik

```text
content_templates
content_obligations
content_projects
content_versions
content_scripts
voiceover_assets
content_timelines
render_jobs
render_outputs
quality_checks
approval_requests
approval_decisions
revision_requests
publishing_jobs
published_posts
post_metrics
```

## 28.6 Abonelik ve faturalandırma

```text
plan_catalog
plan_credit_tiers
subscription_quotes
subscriptions
subscription_items
store_products
store_transactions
store_notifications
entitlements
entitlement_windows
usage_reservations
usage_events
credit_ledger
billing_accounts
invoices
refunds
```

## 28.7 Reklam

```text
ad_accounts
advertising_settings
conversion_sources
campaign_blueprints
ad_campaigns
ad_groups
ad_creatives
ad_external_entities
ad_spend_ledger
ad_metrics
optimization_rules
optimization_recommendations
optimization_actions
guardrail_events
landing_page_checks
```

## 28.8 Sistem

```text
jobs
job_attempts
outbox_events
inbox_events
idempotency_keys
audit_logs
notifications
notification_preferences
provider_configs
model_routes
provider_usage
feature_flags
experiments
webhook_events
system_incidents
```

## 28.9 Kritik indexler

- `content_obligations(business_id, planned_publish_at, status)`
- `publishing_jobs(status, scheduled_at)`
- `media_scenes(media_asset_id, start_ms)`
- vector index `media_embeddings.embedding`
- `usage_reservations(entitlement_window_id, status)`
- `ad_spend_ledger(business_id, date, platform)`
- `outbox_events(published_at, occurred_at)`
- `connected_accounts(provider, external_account_id)`
- unique idempotency indexes

## 28.10 Row-level güvenlik

Backend her sorguda business scope uygular. Mümkünse PostgreSQL RLS ek koruma olarak kullanılabilir; tek savunma değildir.
