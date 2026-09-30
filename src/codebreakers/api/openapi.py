"""OpenAPI document generation."""

import json
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from codebreakers.api.problems import ProblemDetails


def install_openapi(app: FastAPI) -> None:
    """Generate the schema once, registering the shared problem+json schemas."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = get_openapi(
                title=app.title,
                version=app.version,
                description=app.description,
                routes=app.routes,
                tags=app.openapi_tags,
            )
            problem = ProblemDetails.model_json_schema(
                ref_template="#/components/schemas/{model}", mode="serialization"
            )
            components = schema.setdefault("components", {}).setdefault("schemas", {})
            components.update(problem.pop("$defs", {}))
            components["ProblemDetails"] = problem
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]


def render_openapi_document() -> str:
    """Render the default application's OpenAPI document as stable JSON."""
    from codebreakers.api.app import create_app  # deferred: app imports this module

    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"
