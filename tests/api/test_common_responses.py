import pytest
from fastapi import FastAPI

from app.api.v1.common import ApiError, ErrorResponse, error_responses
from app.core.errors import ConflictError, DomainError, NotFoundError


class WidgetNotFoundError(NotFoundError):
    message = "Widget not found"


class GadgetNotFoundError(NotFoundError):
    message = "Gadget not found"


class WidgetLockedError(ConflictError):
    message = "Widget is locked"


def test_error_responses_documents_the_status_of_the_error_category():
    responses = error_responses(WidgetNotFoundError)

    assert set(responses) == {404}
    assert responses[404]["model"] is ErrorResponse
    assert responses[404]["description"] == "Widget not found"


def test_error_responses_examples_are_keyed_by_code_and_show_the_envelope():
    examples = error_responses(WidgetNotFoundError)[404]["content"]["application/json"]["examples"]

    assert examples == {
        "widget_not_found": {"value": {"detail": "Widget not found", "code": "widget_not_found"}},
    }


def test_error_responses_groups_errors_sharing_a_status():
    responses = error_responses(WidgetNotFoundError, GadgetNotFoundError, WidgetLockedError)

    assert set(responses) == {404, 409}
    assert set(responses[404]["content"]["application/json"]["examples"]) == {"widget_not_found", "gadget_not_found"}
    assert responses[404]["description"] == "Widget not found / Gadget not found"
    assert set(responses[409]["content"]["application/json"]["examples"]) == {"widget_locked"}


def test_error_responses_documents_api_errors_next_to_domain_errors():
    unsupported = ApiError(400, code="unsupported_grant_type", detail="Unsupported grant_type")
    invalid_scope = ApiError(400, code="invalid_scope", detail="Invalid requested scope")

    responses = error_responses(unsupported, invalid_scope, WidgetNotFoundError)

    assert set(responses) == {400, 404}
    assert responses[400]["description"] == "Unsupported grant_type / Invalid requested scope"
    assert responses[400]["content"]["application/json"]["examples"] == {
        "unsupported_grant_type": {"value": {"detail": "Unsupported grant_type", "code": "unsupported_grant_type"}},
        "invalid_scope": {"value": {"detail": "Invalid requested scope", "code": "invalid_scope"}},
    }


def test_error_responses_rejects_an_unmapped_error_at_declaration_time():
    with pytest.raises(LookupError, match="DomainError"):
        error_responses(DomainError)


def test_error_responses_plugs_into_a_route_declaration():
    app = FastAPI()

    @app.get("/widgets/{widget_id}", responses=error_responses(WidgetNotFoundError))
    async def read_widget(widget_id: str) -> None: ...

    documented = app.openapi()["paths"]["/widgets/{widget_id}"]["get"]["responses"]["404"]
    assert documented["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
    assert documented["content"]["application/json"]["examples"]["widget_not_found"]["value"]["code"] == (
        "widget_not_found"
    )
