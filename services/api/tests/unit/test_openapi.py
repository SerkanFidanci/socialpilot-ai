"""Public OpenAPI contract checks."""

from __future__ import annotations

import json

from app.main import create_app


def test_openapi_documents_public_contract_without_internal_leaks() -> None:
    schema = create_app().openapi()
    paths = schema["paths"]
    expected_paths = {
        "/health/live",
        "/health/ready",
        "/v1/me",
        "/v1/businesses",
        "/v1/businesses/{business_id}",
        "/v1/businesses/{business_id}/members",
        "/v1/businesses/{business_id}/members/{member_id}",
        "/v1/businesses/{business_id}/media/uploads",
        "/v1/businesses/{business_id}/media/uploads/{upload_session_id}/parts",
        "/v1/businesses/{business_id}/media/uploads/{upload_session_id}/complete",
        "/v1/businesses/{business_id}/media/uploads/{upload_session_id}/cancel",
        "/v1/businesses/{business_id}/media/{asset_id}",
    }
    assert expected_paths <= set(paths)
    components = schema["components"]
    assert "ProblemDetails" in components["schemas"]
    assert "HTTPBearer" in components["securitySchemes"]
    protected_operation = paths["/v1/me"]["get"]
    assert protected_operation["security"] == [{"HTTPBearer": []}]
    operation_ids = [
        operation["operationId"]
        for path_item in paths.values()
        for operation in path_item.values()
        if isinstance(operation, dict)
    ]
    assert len(operation_ids) == len(set(operation_ids))
    serialized = json.dumps(schema, sort_keys=True)
    assert "storage_upload_id" not in serialized
    assert "storage_object_key" not in serialized
    assert "local_identity_signing_key" not in serialized
