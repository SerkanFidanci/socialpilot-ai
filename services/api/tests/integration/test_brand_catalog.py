"""PostgreSQL integration coverage for brand, catalogue and campaign endpoints.

Adversarial focus: cross-tenant reads and writes, the role matrix, replayed idempotency keys,
currency drift, expired campaigns claiming to be active, and cursor pages that skip or repeat.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.infrastructure.identity.local import LocalIdentityVerifier
from app.main import create_app

pytestmark = pytest.mark.integration

KEY = "test-local-identity-signing-key-123"
TABLES = (
    "campaign_offer_products",
    "campaign_offers",
    "product_prices",
    "products",
    "brand_assets",
    "target_audiences",
    "approved_claims",
    "forbidden_claims",
    "approved_ctas",
    "brand_profiles",
    "media_assets",
    "audit_logs",
    "idempotency_keys",
    "business_members",
    "businesses",
    "external_identities",
    "users",
)


def config() -> Settings:
    return Settings(
        app_env="test",
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ["REDIS_URL"],
        celery_broker_url=os.environ["CELERY_BROKER_URL"],
        celery_result_backend=os.environ["CELERY_RESULT_BACKEND"],
        local_identity_signing_key=SecretStr(KEY),
        storage_adapter="fake",
    )


def auth(subject: str, email: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + LocalIdentityVerifier.sign_for_testing(signing_key=KEY, subject=subject, email=email)
    }


async def clear() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"TRUNCATE {', '.join(TABLES)} CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean() -> Generator[None]:
    if os.getenv("RUN_INTEGRATION_TESTS") == "1":
        asyncio.run(clear())
    yield
    if os.getenv("RUN_INTEGRATION_TESTS") == "1":
        asyncio.run(clear())


async def _insert_ready_media(business_id: str, user_id: str) -> str:
    """Seed one media asset that finished ingest.

    The upload control plane is another module's contract; this slice only needs a row a brand
    asset may legally reference, so the fixture writes the end state directly instead of
    re-testing the upload flow.
    """

    asset_id = str(uuid4())
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO media_assets (id, business_id, created_by_user_id,"
                    " storage_object_key, content_type, byte_size, sha256_checksum, status,"
                    " ingest_status, created_at) VALUES (:id, :business_id, :user_id, :key,"
                    " 'image/jpeg', 1024, :checksum, 'uploaded', 'ready_for_analysis', now())"
                ),
                {
                    "id": asset_id,
                    "business_id": business_id,
                    "user_id": user_id,
                    "key": f"tenant/{business_id}/media/{asset_id}/original/seed",
                    "checksum": "b" * 64,
                },
            )
    finally:
        await engine.dispose()
    return asset_id


def ready_media(business_id: str, user_id: str) -> str:
    return asyncio.run(_insert_ready_media(business_id, user_id))


def brand_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "display_name": "Acme Coffee",
        "tone": "sıcak, samimi",
        "communication_language": "tr",
        "default_currency": "TRY",
        "color_palette": ["#101010", "#f5a623"],
        "forbidden_topics": ["politika"],
        "approved_claims": ["taze hazırlanır"],
        "forbidden_claims": ["sağlığa iyi gelir"],
        "approved_ctas": ["Hemen sipariş ver"],
        "target_audiences": [
            {"name": "Genç profesyoneller", "age_min": 25, "age_max": 40, "locations": ["kadikoy"]}
        ],
    }
    body.update(overrides)
    return body


def product_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Soğuk Latte",
        "category": "İçecek",
        "price": {"price_minor": 16500, "currency": "TRY"},
    }
    body.update(overrides)
    return body


def offer_body(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    body: dict[str, Any] = {
        "name": "Yaz kampanyası",
        "starts_at": (now - timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=7)).isoformat(),
        "discount_type": "percentage",
        "discount_percent": 20,
    }
    body.update(overrides)
    return body


def business(client: TestClient, headers: dict[str, str], name: str) -> str:
    created = client.post(
        "/v1/businesses", headers=headers, json={"name": name, "timezone": "Europe/Istanbul"}
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def member(
    client: TestClient, headers: dict[str, str], business_id: str, email: str, role: str
) -> None:
    added = client.post(
        f"/v1/businesses/{business_id}/members",
        headers=headers,
        json={"email": email, "role": role},
    )
    assert added.status_code == 201, added.text


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_brand_document_round_trip_and_tenant_isolation() -> None:
    owner, other = auth("brand-owner", "brand-owner@example.com"), auth("brand-other", "o@e.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = business(client, owner, "Acme")
        other_business_id = business(client, other, "Rival")
        user_id = str(client.get("/v1/me", headers=owner).json()["id"])

        missing = client.get(f"/v1/businesses/{business_id}/brand", headers=owner)
        assert missing.status_code == 404
        assert missing.json()["code"] == "BRAND_PROFILE_NOT_FOUND"

        asset_id = ready_media(business_id, user_id)
        created = client.put(
            f"/v1/businesses/{business_id}/brand",
            headers=owner,
            json=brand_body(assets=[{"role": "logo", "media_asset_id": asset_id}]),
        )
        assert created.status_code == 200, created.text
        document = created.json()
        assert document["color_palette"] == ["#101010", "#F5A623"]
        assert document["assets"] == [{"role": "logo", "media_asset_id": asset_id}]
        assert document["approved_ctas"] == ["Hemen sipariş ver"]

        # A `PUT` replaces the document: entries absent from the new body must disappear.
        replaced = client.put(
            f"/v1/businesses/{business_id}/brand",
            headers=owner,
            json=brand_body(approved_ctas=["Menüyü gör"], target_audiences=[]),
        )
        assert replaced.status_code == 200
        assert replaced.json()["approved_ctas"] == ["Menüyü gör"]
        assert replaced.json()["target_audiences"] == []
        assert replaced.json()["assets"] == []
        assert replaced.json()["id"] == document["id"]

        # Another tenant's brand must be indistinguishable from a brand that does not exist.
        for headers in (other,):
            denied = client.get(f"/v1/businesses/{business_id}/brand", headers=headers)
            assert denied.status_code == 404
            assert denied.json()["code"] == "BUSINESS_NOT_FOUND"
            assert (
                client.put(
                    f"/v1/businesses/{business_id}/brand", headers=headers, json=brand_body()
                ).status_code
                == 404
            )
        assert (
            client.get(f"/v1/businesses/{other_business_id}/brand", headers=owner).status_code
            == 404
        )
        assert (
            client.get(f"/v1/businesses/{uuid4()}/brand", headers=owner).json()["code"]
            == "BUSINESS_NOT_FOUND"
        )


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_brand_asset_must_reference_tenant_media() -> None:
    owner, other = auth("asset-owner", "asset-owner@example.com"), auth("asset-other", "ao@e.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = business(client, owner, "Acme")
        other_business_id = business(client, other, "Rival")
        other_user_id = str(client.get("/v1/me", headers=other).json()["id"])
        foreign_asset = ready_media(other_business_id, other_user_id)

        for asset_id in (str(uuid4()), foreign_asset):
            rejected = client.put(
                f"/v1/businesses/{business_id}/brand",
                headers=owner,
                json=brand_body(assets=[{"role": "logo", "media_asset_id": asset_id}]),
            )
            assert rejected.status_code == 422
            assert rejected.json()["code"] == "BRAND_ASSET_INVALID"
            assert asset_id not in rejected.text


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_role_matrix_for_brand_and_catalogue_writes() -> None:
    owner = auth("role-owner", "role-owner@example.com")
    admin = auth("role-admin", "role-admin@example.com")
    editor = auth("role-editor", "role-editor@example.com")
    viewer = auth("role-viewer", "role-viewer@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = business(client, owner, "Acme")
        for headers, email, role in (
            (admin, "role-admin@example.com", "admin"),
            (editor, "role-editor@example.com", "editor"),
            (viewer, "role-viewer@example.com", "viewer"),
        ):
            assert client.get("/v1/me", headers=headers).status_code == 200
            member(client, owner, business_id, email, role)

        assert (
            client.put(
                f"/v1/businesses/{business_id}/brand", headers=admin, json=brand_body()
            ).status_code
            == 200
        )
        product_id = client.post(
            f"/v1/businesses/{business_id}/products", headers=admin, json=product_body()
        ).json()["id"]

        for headers in (editor, viewer):
            assert (
                client.put(
                    f"/v1/businesses/{business_id}/brand", headers=headers, json=brand_body()
                ).json()["code"]
                == "INSUFFICIENT_PERMISSION"
            )
            assert (
                client.post(
                    f"/v1/businesses/{business_id}/products",
                    headers=headers,
                    json=product_body(name="Espresso"),
                ).status_code
                == 403
            )
            assert (
                client.patch(
                    f"/v1/businesses/{business_id}/products/{product_id}",
                    headers=headers,
                    json={"description": "değişti"},
                ).status_code
                == 403
            )
            assert (
                client.post(
                    f"/v1/businesses/{business_id}/campaign-offers",
                    headers=headers,
                    json=offer_body(),
                ).status_code
                == 403
            )
            # Reads stay allowed for both roles.
            assert (
                client.get(f"/v1/businesses/{business_id}/brand", headers=headers).status_code
                == 200
            )
            assert (
                client.get(f"/v1/businesses/{business_id}/products", headers=headers).status_code
                == 200
            )
            assert (
                client.get(
                    f"/v1/businesses/{business_id}/brand/health", headers=headers
                ).status_code
                == 200
            )


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_product_prices_stay_integer_minor_units_in_one_currency() -> None:
    owner = auth("price-owner", "price-owner@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = business(client, owner, "Acme")
        assert (
            client.put(
                f"/v1/businesses/{business_id}/brand", headers=owner, json=brand_body()
            ).status_code
            == 200
        )
        created = client.post(
            f"/v1/businesses/{business_id}/products", headers=owner, json=product_body()
        )
        assert created.status_code == 201, created.text
        product = created.json()
        product_id = product["id"]
        assert product["price_minor"] == 16500
        assert product["currency"] == "TRY"

        # A decimal price is not a price: the contract only accepts minor units, and the
        # rejection happens in schema validation before any rule or row is touched.
        decimal_price = client.post(
            f"/v1/businesses/{business_id}/products",
            headers=owner,
            json=product_body(name="Mocha", price={"price_minor": 165.5, "currency": "TRY"}),
        )
        assert decimal_price.status_code == 400
        assert decimal_price.json()["code"] == "REQUEST_VALIDATION_FAILED"
        mismatch = client.post(
            f"/v1/businesses/{business_id}/products",
            headers=owner,
            json=product_body(name="Filtre", price={"price_minor": 500, "currency": "EUR"}),
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["code"] == "CURRENCY_MISMATCH"

        repriced = client.patch(
            f"/v1/businesses/{business_id}/products/{product_id}",
            headers=owner,
            json={"price": {"price_minor": 17500, "currency": "TRY"}},
        )
        assert repriced.status_code == 200
        assert repriced.json()["price_minor"] == 17500
        assert (
            client.patch(
                f"/v1/businesses/{business_id}/products/{product_id}",
                headers=owner,
                json={"price": {"price_minor": 100, "currency": "USD"}},
            ).json()["code"]
            == "CURRENCY_MISMATCH"
        )
        # Changing the brand currency while priced products exist would orphan those prices.
        assert (
            client.put(
                f"/v1/businesses/{business_id}/brand",
                headers=owner,
                json=brand_body(default_currency="EUR"),
            ).json()["code"]
            == "CURRENCY_MISMATCH"
        )
        assert (
            client.post(
                f"/v1/businesses/{business_id}/products", headers=owner, json=product_body()
            ).json()["code"]
            == "PRODUCT_NAME_CONFLICT"
        )
        history = asyncio.run(price_rows(product_id))
        assert history == [(16500, "TRY", False), (17500, "TRY", True)]


async def price_rows(product_id: str) -> list[tuple[int, str, bool]]:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT price_minor, currency, effective_to IS NULL FROM product_prices"
                        " WHERE product_id = :product_id ORDER BY effective_from"
                    ),
                    {"product_id": product_id},
                )
            ).all()
    finally:
        await engine.dispose()
    return [(int(row[0]), str(row[1]), bool(row[2])) for row in rows]


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_product_creation_is_idempotent_and_conflicts_on_a_reused_key() -> None:
    owner = auth("idem-owner", "idem-owner@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = business(client, owner, "Acme")
        headers = {**owner, "Idempotency-Key": "product-key-1"}
        first = client.post(
            f"/v1/businesses/{business_id}/products", headers=headers, json=product_body()
        )
        assert first.status_code == 201
        replay = client.post(
            f"/v1/businesses/{business_id}/products", headers=headers, json=product_body()
        )
        assert replay.status_code == 201
        assert replay.json()["id"] == first.json()["id"]
        assert replay.json()["price_minor"] == 16500

        conflict = client.post(
            f"/v1/businesses/{business_id}/products",
            headers=headers,
            json=product_body(name="Another product"),
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
        assert (
            client.get(f"/v1/businesses/{business_id}/products", headers=owner)
            .json()["items"]
            .__len__()
            == 1
        )


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_campaign_offer_activity_is_deterministic_at_the_boundary() -> None:
    owner = auth("camp-owner", "camp-owner@example.com")
    now = datetime.now(UTC)
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = business(client, owner, "Acme")
        product_id = client.post(
            f"/v1/businesses/{business_id}/products", headers=owner, json=product_body()
        ).json()["id"]

        expired = client.post(
            f"/v1/businesses/{business_id}/campaign-offers",
            headers=owner,
            json=offer_body(
                name="Geçmiş kampanya",
                starts_at=(now - timedelta(days=30)).isoformat(),
                ends_at=(now - timedelta(seconds=1)).isoformat(),
            ),
        )
        assert expired.status_code == 201
        assert expired.json()["is_active"] is False
        assert expired.json()["activity"] == "expired"

        future = client.post(
            f"/v1/businesses/{business_id}/campaign-offers",
            headers=owner,
            json=offer_body(
                name="Gelecek kampanya",
                starts_at=(now + timedelta(days=1)).isoformat(),
                ends_at=(now + timedelta(days=2)).isoformat(),
            ),
        )
        assert future.json()["activity"] == "not_started"

        pending = client.post(
            f"/v1/businesses/{business_id}/campaign-offers",
            headers=owner,
            json=offer_body(name="Onay bekleyen", approval_status="pending"),
        )
        assert pending.json()["activity"] == "awaiting_approval"

        live = client.post(
            f"/v1/businesses/{business_id}/campaign-offers",
            headers=owner,
            json=offer_body(
                name="Canlı kampanya",
                product_ids=[product_id],
                discount_type="fixed_amount",
                discount_percent=None,
                discount_amount_minor=2500,
                discount_currency="TRY",
            ),
        )
        assert live.status_code == 201, live.text
        assert live.json()["is_active"] is True
        assert live.json()["product_ids"] == [product_id]

        # The SQL activity filter and the pure rule must agree on the same rows.
        active = client.get(
            f"/v1/businesses/{business_id}/campaign-offers?active_only=true", headers=owner
        ).json()["items"]
        assert [item["id"] for item in active] == [live.json()["id"]]
        listed = client.get(f"/v1/businesses/{business_id}/campaign-offers", headers=owner).json()[
            "items"
        ]
        assert {item["id"] for item in listed if item["is_active"]} == {live.json()["id"]}
        assert len(listed) == 4

        rejections = (
            (
                offer_body(
                    name="Ters pencere",
                    starts_at=(now + timedelta(days=2)).isoformat(),
                    ends_at=(now + timedelta(days=1)).isoformat(),
                ),
                422,
                "CAMPAIGN_WINDOW_INVALID",
            ),
            (
                offer_body(name="Bilinmeyen ürün", product_ids=[str(uuid4())]),
                422,
                "CAMPAIGN_PRODUCT_UNKNOWN",
            ),
            (
                offer_body(
                    name="Yanlış para",
                    product_ids=[product_id],
                    discount_type="fixed_amount",
                    discount_percent=None,
                    discount_amount_minor=2500,
                    discount_currency="EUR",
                ),
                409,
                "CURRENCY_MISMATCH",
            ),
            (
                # A timestamp without an offset names no instant; schema validation refuses it.
                offer_body(name="Naif zaman", starts_at="2026-08-01T00:00:00"),
                400,
                "REQUEST_VALIDATION_FAILED",
            ),
        )
        for body, status, code in rejections:
            response = client.post(
                f"/v1/businesses/{business_id}/campaign-offers", headers=owner, json=body
            )
            assert response.status_code == status, response.text
            assert response.json()["code"] == code


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_product_pages_never_skip_or_repeat_a_row() -> None:
    owner, other = auth("page-owner", "page-owner@example.com"), auth("page-other", "po@e.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = business(client, owner, "Acme")
        other_business_id = business(client, other, "Rival")
        for index in range(7):
            assert (
                client.post(
                    f"/v1/businesses/{business_id}/products",
                    headers=owner,
                    json=product_body(name=f"Ürün {index}", price=None),
                ).status_code
                == 201
            )
        assert (
            client.post(
                f"/v1/businesses/{other_business_id}/products",
                headers=other,
                json=product_body(name="Rakip ürün", price=None),
            ).status_code
            == 201
        )

        collected: list[str] = []
        cursor: str | None = None
        for _ in range(10):
            query = f"?limit=2{'&cursor=' + cursor if cursor else ''}"
            page = client.get(f"/v1/businesses/{business_id}/products{query}", headers=owner).json()
            collected.extend(item["id"] for item in page["items"])
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        assert len(collected) == 7
        assert len(set(collected)) == 7

        every = client.get(f"/v1/businesses/{business_id}/products?limit=100", headers=owner).json()
        assert [item["id"] for item in every["items"]] == collected
        assert every["next_cursor"] is None

        # A cursor is opaque and always re-scoped: it cannot carry a client into another tenant.
        borrowed = client.get(
            f"/v1/businesses/{other_business_id}/products?limit=2", headers=other
        ).json()["items"]
        assert len(borrowed) == 1
        assert borrowed[0]["id"] not in collected

        for bad_cursor in ("not-a-cursor", "YWJj"):
            broken = client.get(
                f"/v1/businesses/{business_id}/products?cursor={bad_cursor}", headers=owner
            )
            assert broken.status_code == 400
            assert broken.json()["code"] == "PAGINATION_CURSOR_INVALID"
        # An oversized cursor and an over-ceiling limit are refused by the transport bound
        # before the primitive sees them; either way the client gets a `400`, never a page.
        for query in ("cursor=" + "a" * 300, "limit=1000", "limit=0"):
            refused = client.get(f"/v1/businesses/{business_id}/products?{query}", headers=owner)
            assert refused.status_code == 400
            assert refused.json()["code"] == "REQUEST_VALIDATION_FAILED"
        assert (
            len(
                client.get(
                    f"/v1/businesses/{business_id}/products?status=archived", headers=owner
                ).json()["items"]
            )
            == 0
        )


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_brand_health_is_advisory_and_blocks_nothing() -> None:
    owner = auth("health-owner", "health-owner@example.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = business(client, owner, "Acme")
        empty = client.get(f"/v1/businesses/{business_id}/brand/health", headers=owner)
        assert empty.status_code == 200
        assert empty.json()["score"] == 0
        assert empty.json()["advisory"] is True
        assert "connected_social_account" in empty.json()["unavailable"]

        # Every write below succeeds while the score is still far from complete.
        assert (
            client.put(
                f"/v1/businesses/{business_id}/brand", headers=owner, json=brand_body()
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/v1/businesses/{business_id}/products", headers=owner, json=product_body()
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/v1/businesses/{business_id}/campaign-offers", headers=owner, json=offer_body()
            ).status_code
            == 201
        )
        improved = client.get(f"/v1/businesses/{business_id}/brand/health", headers=owner).json()
        assert 0 < improved["score"] < 100
        assert set(improved["missing"]) == {"logo_and_colors", "photo_library", "video_library"}
        assert len(improved["components"]) == 11


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_cross_tenant_product_mutation_is_not_disclosed() -> None:
    owner, other = auth("iso-owner", "iso-owner@example.com"), auth("iso-other", "io@e.com")
    with TestClient(create_app(config()), raise_server_exceptions=False) as client:
        business_id = business(client, owner, "Acme")
        other_business_id = business(client, other, "Rival")
        product_id = client.post(
            f"/v1/businesses/{business_id}/products", headers=owner, json=product_body()
        ).json()["id"]

        # Same product id, other tenant's path and other tenant's token: both must be `404`.
        assert (
            client.patch(
                f"/v1/businesses/{other_business_id}/products/{product_id}",
                headers=other,
                json={"description": "ele geçirildi"},
            ).json()["code"]
            == "PRODUCT_NOT_FOUND"
        )
        assert (
            client.patch(
                f"/v1/businesses/{business_id}/products/{product_id}",
                headers=other,
                json={"description": "ele geçirildi"},
            ).json()["code"]
            == "BUSINESS_NOT_FOUND"
        )
        assert (
            client.get(f"/v1/businesses/{other_business_id}/products", headers=other).json()[
                "items"
            ]
            == []
        )
        unchanged = client.get(f"/v1/businesses/{business_id}/products", headers=owner).json()
        assert unchanged["items"][0]["description"] is None
        assert audit_actions(UUID(business_id)) == ["brand.product.created"]


def audit_actions(business_id: UUID) -> list[str]:
    async def load() -> list[str]:
        engine = create_async_engine(os.environ["DATABASE_URL"])
        try:
            async with engine.connect() as connection:
                rows = (
                    await connection.execute(
                        text(
                            "SELECT action FROM audit_logs WHERE business_id = :business_id"
                            " ORDER BY created_at"
                        ),
                        {"business_id": str(business_id)},
                    )
                ).all()
        finally:
            await engine.dispose()
        return [str(row[0]) for row in rows]

    return asyncio.run(load())
