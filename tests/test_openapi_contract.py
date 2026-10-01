"""Guard the committed OpenAPI document against accidental contract changes."""

from pathlib import Path

import pytest

from codebreakers.api import create_app
from codebreakers.api.openapi import render_openapi_document

OPENAPI_PATH = Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi.json"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


@pytest.mark.unit
def test_committed_openapi_matches_generated() -> None:
    committed = OPENAPI_PATH.read_text(encoding="utf-8")
    hint = "The OpenAPI contract changed. Review the diff, then run `make openapi`."
    assert committed == render_openapi_document(), hint


@pytest.mark.unit
def test_every_operation_is_documented() -> None:
    document = create_app().openapi()
    operation_ids: list[str] = []
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            assert operation.get("operationId"), f"{method} {path} lacks operationId"
            assert operation.get("summary"), f"{method} {path} lacks summary"
            assert "500" in operation["responses"], f"{method} {path} lacks 500"
            operation_ids.append(operation["operationId"])

    assert len(operation_ids) == len(set(operation_ids))
    assert set(operation_ids) == {
        "encrypt_text",
        "decrypt_text",
        "create_analysis",
        "list_analyses",
        "get_analysis",
        "get_liveness",
        "get_readiness",
    }


@pytest.mark.unit
def test_problem_schema_is_registered_and_used() -> None:
    document = create_app().openapi()
    assert "ProblemDetails" in document["components"]["schemas"]
    assert "ValidationIssue" in document["components"]["schemas"]
    encrypt = document["paths"]["/v1/ciphers/{cipher}/encrypt"]["post"]
    content = encrypt["responses"]["422"]["content"]
    assert content["application/problem+json"]["schema"] == {
        "$ref": "#/components/schemas/ProblemDetails"
    }
    assert "HTTPValidationError" not in document["components"]["schemas"]


@pytest.mark.unit
def test_request_schemas_include_examples() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    for name in ("CipherOperationRequest", "AnalysisRequest", "AnalysisJobResponse"):
        assert schemas[name].get("examples"), f"{name} lacks examples"
